"""Backend implementations for the gateway ``SearchBackend`` protocol.

``create_backend`` resolves ``config.backend``: ``stub`` / ``standalone`` /
``hermes`` directly; ``auto`` prefers the Hermes sidecar bridge when the Hermes
runtime python can be resolved, else the standalone keyless backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gateway.backends.stub import StubBackend
from gateway.protocols import SearchBackend

if TYPE_CHECKING:
    from gateway.config import GatewayConfig

__all__ = ["StubBackend", "create_backend"]


def create_backend(config: GatewayConfig) -> SearchBackend:
    """Instantiate the backend named by ``config.backend`` (``auto`` resolves)."""
    name = (config.backend or "auto").strip().lower()
    if name == "stub":
        return StubBackend()
    if name == "standalone":
        from gateway.backends.standalone import StandaloneBackend

        return StandaloneBackend()
    if name in ("hermes", "auto"):
        try:
            from gateway.backends.hermes_bridge import HermesBridge, resolve_hermes_python

            hermes_python = resolve_hermes_python(config.hermes_python)
            return HermesBridge(
                hermes_python=hermes_python,
                hermes_home=config.home,
                repo_root=config.root,
            )
        except Exception:
            if name == "hermes":
                raise
        # auto: fall back to the standalone keyless backend
        from gateway.backends.standalone import StandaloneBackend

        return StandaloneBackend()
    raise ValueError(f"unknown gateway backend {config.backend!r} (expected auto|hermes|standalone|stub)")
