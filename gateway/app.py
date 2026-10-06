"""FastAPI application factory for the Hermes Universal Gateway.

Exposes the OpenAI-compatible HTTP surface (models, chat completions,
responses stub), health endpoints, and — when the optional ``mcp`` package
is importable — the MCP streamable-http mount at ``/mcp``.
"""

from __future__ import annotations

import contextlib
import logging
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gateway.core.engine import Engine, default_engine
from gateway.openai.chat_completions import router as chat_completions_router
from gateway.openai.models import router as models_router
from gateway.openai.responses import router as responses_router
from gateway.security.auth import api_key_guard
from gateway.security.rate_limit import RateLimiter, rate_limit_guard

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_RPS = 20.0  # matches GatewayConfig.rate_limit_rps default

_STARTED_AT = time.monotonic()


class _FallbackConfig:
    """Attribute-free config shim; field access falls back to defaults."""


def load_config():
    """Best-effort load of ``gateway.config.GatewayConfig`` from the environment.

    Returns None when the config module is unavailable (e.g. partial
    checkout); callers fall back to per-field defaults.
    """
    try:
        from gateway.config import GatewayConfig
    except Exception:
        return None
    for loader_name in ("from_env", "from_environment"):
        loader = getattr(GatewayConfig, loader_name, None)
        if callable(loader):
            try:
                return loader()
            except Exception:
                break
    try:
        return GatewayConfig()
    except Exception:
        return None


def _config_value(config, name: str, default):
    """Read a config field tolerating missing modules or fields."""
    return getattr(config, name, default) if config is not None else default


def _engine_status(engine) -> dict:
    """Best-effort ``engine.status()`` snapshot; never raises."""
    try:
        return engine.status()
    except Exception:
        return {}


def _backend_name(engine) -> str:
    status = _engine_status(engine)
    name = (status.get("backend") or {}).get("name")
    return str(name) if name else "unknown"


def _build_mcp_server(engine: Engine):
    """Build the MCP server when the optional ``mcp`` package is importable.

    Lazy import with grace: any failure (package missing, server module
    absent, unexpected server type) returns None instead of crashing app
    creation.
    """
    try:
        import mcp  # noqa: F401

        from gateway.mcp.server import build_mcp
    except Exception:
        return None
    try:
        server = build_mcp(engine)
    except Exception as exc:
        logger.warning("Skipping MCP server: %s", exc)
        return None
    builder = getattr(server, "streamable_http_app", None)
    if not callable(builder):
        logger.warning("MCP server exposes no streamable_http_app(); skipping /mcp mount")
        return None
    return server


def _mcp_lifespan(server):
    """FastAPI lifespan that runs the MCP streamable-HTTP session manager.

    Starlette does not run the lifespan of mounted sub-apps, so the session
    manager's task group (normally started by the sub-app's own lifespan via
    ``streamable_http_app()``) must be started by the parent app — otherwise
    requests fail with "Task group is not initialized".
    """

    @contextlib.asynccontextmanager
    async def lifespan(app):  # noqa: ANN001 - FastAPI lifespan signature
        manager = getattr(server, "session_manager", None)
        if manager is None:
            yield
            return
        async with manager.run():
            yield

    return lifespan


def create_app(config=None, engine=None) -> FastAPI:
    """Build the gateway FastAPI app.

    ``config`` and ``engine`` are injectable for tests; when omitted they
    are loaded from the environment via ``load_config()`` and
    ``default_engine()``.
    """
    if config is None:
        config = load_config()
    if engine is None:
        engine = default_engine(config)

    mcp_server = _build_mcp_server(engine)
    app = FastAPI(
        title="Hermes Universal Gateway",
        version=VERSION,
        lifespan=_mcp_lifespan(mcp_server) if mcp_server is not None else None,
    )
    app.state.config = config
    app.state.engine = engine
    try:
        rps = float(_config_value(config, "rate_limit_rps", DEFAULT_RPS))
    except (TypeError, ValueError):
        rps = DEFAULT_RPS
    app.state.rate_limiter = RateLimiter(rps=rps)

    protected = [Depends(api_key_guard), Depends(rate_limit_guard)]
    app.include_router(models_router, dependencies=protected)
    app.include_router(chat_completions_router, dependencies=protected)
    app.include_router(responses_router, dependencies=protected)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # Pass through OpenAI-shaped error bodies unchanged; keep the default
        # {"detail": ...} shape for anything else.
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers or {})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers or {})

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # OpenAI clients expect 400 (not 422) for malformed request bodies.
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Invalid request body",
                    "type": "invalid_request_error",
                    "code": "invalid_request",
                }
            },
        )

    @app.get("/healthz")
    def healthz(request: Request) -> dict:
        """Liveness probe."""
        return {
            "status": "ok",
            "version": VERSION,
            "backend": _backend_name(request.app.state.engine),
            "uptime_s": round(time.monotonic() - _STARTED_AT, 3),
        }

    @app.get("/readyz")
    def readyz(request: Request) -> dict:
        """Readiness probe with per-component checks."""
        status = _engine_status(request.app.state.engine)
        checks = {
            "backend": status.get("backend") or {"ok": False, "detail": "status unavailable"},
            "cache": status.get("cache") or {"ok": False},
            "synth_config": status.get("synth") or {"ok": False},
        }
        ready = all(bool(check.get("ok")) for check in checks.values())
        return {"ready": ready, "checks": checks}

    if mcp_server is not None:
        try:
            # The MCP streamable-http app owns the "/mcp" path internally, so
            # it is mounted at "/" (a Starlette mount prefix strip would hide
            # the child's "/mcp" route). Mounted last, it never shadows the
            # gateway's own routes.
            app.mount("/", mcp_server.streamable_http_app())
        except Exception as exc:
            logger.warning("Skipping MCP /mcp mount: %s", exc)
    return app
