"""FastAPI application factory for the Hermes Universal Gateway.

Exposes the OpenAI-compatible HTTP surface (models, chat completions,
responses stub), health endpoints, and — when the optional ``mcp`` package
is importable — the MCP streamable-http mount at ``/mcp``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gateway.core.engine import Engine, default_engine
from gateway.openai.chat_completions import router as chat_completions_router
from gateway.openai.models import router as models_router
from gateway.openai.responses import router as responses_router
from gateway.security.admission import (
    ADMISSION_REJECTED_BODY,
    ADMISSION_WAIT_MS,
    Admission,
    AdmissionTimeout,
    GatewayMetrics,
)
from gateway.security.auth import api_key_guard
from gateway.security.rate_limit import RateLimiter, rate_limit_guard

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_RPS = 20.0  # matches GatewayConfig.rate_limit_rps default
DEFAULT_MAX_INFLIGHT = 4  # matches GatewayConfig.admission_max_inflight
DEFAULT_QUEUE_CAP = 16  # matches GatewayConfig.admission_queue_cap
DEFAULT_REQUEST_DEADLINE_S = 180.0  # matches GatewayConfig.request_deadline_s
METRICS_RING_CAPACITY = 200
READYZ_BUDGET_S = 0.5  # bounded readiness probe: never block on a stalled backend

# Expensive answer endpoints behind the admission gate (healthz/readyz/metrics
# deliberately excluded — probes must stay responsive under full load).
_ADMISSION_PATHS = frozenset({"/v1/chat/completions"})

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


def _configured_backend_name(config) -> str:
    """Configured backend name — a pure config read, no engine/backend contact."""
    return str(_config_value(config, "backend", "unknown") or "unknown")


def _engine_status_bounded(engine, timeout_s: float = READYZ_BUDGET_S) -> dict | None:
    """``engine.status()`` in a daemon thread; ``None`` when it outlives the budget.

    ``status()`` pings the backend and probes the cache DB — a stalled backend
    could block it indefinitely, so readiness runs it off the request thread
    with a hard join timeout. A timed-out probe leaks as a daemon thread and
    reports not-ready; liveness is unaffected.
    """
    box: dict = {}

    def _probe() -> None:
        box["status"] = _engine_status(engine)

    worker = threading.Thread(target=_probe, name="readyz-status", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return None
    return box.get("status") or {}


async def _admission_rejected(scope, receive, send) -> None:
    """503 + Retry-After: 1 in the rate_limit OpenAI-error shape."""
    response = JSONResponse(status_code=503, content=ADMISSION_REJECTED_BODY, headers={"Retry-After": "1"})
    await response(scope, receive, send)


class _AdmissionMiddleware:
    """Pure-ASGI admission gate around the expensive answer endpoints.

    Holding the slot at ASGI level keeps it for the whole request lifecycle,
    including StreamingResponse consumption. The queue wait runs in a worker
    thread (``asyncio.to_thread``) so parked requests never block the event
    loop — ``/healthz`` stays sub-second while the backend is saturated.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in _ADMISSION_PATHS:
            await self.app(scope, receive, send)
            return
        state = getattr(scope.get("app"), "state", None)
        admission = getattr(state, "admission", None)
        metrics = getattr(state, "metrics", None)
        if admission is None or not admission.enabled:
            await self.app(scope, receive, send)
            return
        if metrics is not None:
            metrics.record_request()
        started = time.perf_counter()
        timeout = getattr(state, "admission_wait_timeout_s", None)
        try:
            wait_ms = await asyncio.to_thread(admission.acquire, timeout)
        except AdmissionTimeout:
            # Queued past the request deadline: rejected like a full queue,
            # counted both ways — it is a rejection *and* a timeout.
            if metrics is not None:
                metrics.record_rejection()
                metrics.record_timeout()
            await _admission_rejected(scope, receive, send)
            return
        except Exception:  # noqa: BLE001 — AdmissionRejected and anything else
            if metrics is not None:
                metrics.record_rejection()
            await _admission_rejected(scope, receive, send)
            return
        token = ADMISSION_WAIT_MS.set(wait_ms)
        try:
            await self.app(scope, receive, send)
        finally:
            ADMISSION_WAIT_MS.reset(token)
            admission.release()
            if metrics is not None:
                metrics.record_duration((time.perf_counter() - started) * 1000.0)


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

    # R15-D: bounded admission + metrics. Queued requests wait at most
    # ``request_deadline_s`` — a request must never outlive its own deadline
    # sitting in line.
    try:
        admission = Admission(
            max_inflight=int(_config_value(config, "admission_max_inflight", DEFAULT_MAX_INFLIGHT)),
            queue_cap=int(_config_value(config, "admission_queue_cap", DEFAULT_QUEUE_CAP)),
        )
    except (TypeError, ValueError):
        admission = Admission()
    app.state.admission = admission
    try:
        wait_timeout = float(_config_value(config, "request_deadline_s", DEFAULT_REQUEST_DEADLINE_S))
        app.state.admission_wait_timeout_s = wait_timeout if wait_timeout > 0 else None
    except (TypeError, ValueError):
        app.state.admission_wait_timeout_s = DEFAULT_REQUEST_DEADLINE_S
    metrics = GatewayMetrics(capacity=METRICS_RING_CAPACITY) if _config_value(config, "metrics_enabled", True) else None
    app.state.metrics = metrics
    if metrics is not None:
        try:
            engine.metrics = metrics
        except Exception:  # noqa: BLE001 — foreign engine objects may be sealed
            pass
    app.add_middleware(_AdmissionMiddleware)

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
        """Liveness probe: never touches engine/backend — answers <1 s under load.

        ``backend`` reports the *configured* name (a config read, not a ping);
        the resolved/health-checked name lives on ``/readyz``.
        """
        return {
            "status": "ok",
            "version": VERSION,
            "backend": _configured_backend_name(request.app.state.config),
            "uptime_s": round(time.monotonic() - _STARTED_AT, 3),
        }

    @app.get("/readyz")
    def readyz(request: Request) -> dict:
        """Readiness probe — bounded: status() runs off-thread with a join timeout."""
        status = _engine_status_bounded(request.app.state.engine)
        if status is None:
            checks = {
                "backend": {"ok": False, "detail": "status check timed out"},
                "cache": {"ok": False, "detail": "status check timed out"},
                "synth_config": {"ok": False, "detail": "status check timed out"},
            }
            return {"ready": False, "checks": checks}
        checks = {
            "backend": status.get("backend") or {"ok": False, "detail": "status unavailable"},
            "cache": status.get("cache") or {"ok": False},
            "synth_config": status.get("synth") or {"ok": False},
        }
        ready = all(bool(check.get("ok")) for check in checks.values())
        return {"ready": ready, "checks": checks}

    @app.get("/metrics")
    def metrics_endpoint(request: Request) -> dict:
        """Process metrics snapshot (§1.5); 404 when ``metrics_enabled`` is off."""
        sink = getattr(request.app.state, "metrics", None)
        if sink is None:
            raise HTTPException(status_code=404, detail="metrics disabled")
        admission = getattr(request.app.state, "admission", None)
        return sink.snapshot(
            in_flight=admission.in_flight if admission is not None else 0,
            queue_depth=admission.queue_depth if admission is not None else 0,
        )

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
