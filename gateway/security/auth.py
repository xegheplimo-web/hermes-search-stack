"""Bearer-token authentication for the gateway HTTP surface.

Policy (frozen decision D8): when ``HERMES_GATEWAY_API_KEY`` is set, every
request must carry ``Authorization: Bearer <key>``; when it is unset, only
loopback requests are accepted.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

# Hosts treated as loopback. "testclient" is included because FastAPI's
# TestClient simulates a local caller (its synthetic peer host), so hermetic
# tests exercise the same loopback path as a real local process.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _unauthorized(message: str, code: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": {"message": message, "type": "authentication_error", "code": code}},
        headers={"WWW-Authenticate": "Bearer"},
    )


def api_key_guard(request: Request) -> None:
    """FastAPI dependency enforcing bearer auth or loopback-only access."""
    config = getattr(request.app.state, "config", None)
    expected = getattr(config, "api_key", "") or ""
    if expected:
        header = request.headers.get("authorization", "")
        token = header[len("Bearer ") :] if header.startswith("Bearer ") else ""
        if not token or not secrets.compare_digest(token, expected):
            raise _unauthorized("Invalid or missing API key", "invalid_api_key")
        return
    host = request.client.host if request.client is not None else ""
    if host not in LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "message": "Gateway is configured loopback-only; remote requests are forbidden.",
                    "type": "authentication_error",
                    "code": "loopback_only",
                }
            },
        )
