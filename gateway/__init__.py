"""Universal Gateway for the hermes-search-stack (R8).

An optional subsystem that exposes the whole search stack behind one product
surface: an OpenAI-compatible HTTP API, an MCP server, and a single
``hermes-search`` model id routed through cache -> fast -> deep flows.

Contract: ``analysis/r8-interfaces.md`` (frozen 2026-10-06). The gateway core
(``gateway.core``, ``gateway.backends``, ``gateway.bridge``) is framework-free;
FastAPI lives only in ``gateway/app.py`` + ``gateway/openai/*`` (R8-B), the MCP
SDK only in ``gateway/mcp/*`` (R8-C), and Hermes internals are imported only by
``gateway/bridge/worker.py`` running under the Hermes runtime venv.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
