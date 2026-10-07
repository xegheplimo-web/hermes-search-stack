"""MCP server for the Hermes universal gateway (r8-interfaces.md section 7).

Builds an ``MCPServer`` (official ``mcp`` Python SDK v2 API) exposing the seven
frozen tools over stdio and streamable HTTP. ``gateway/app.py`` mounts the
streamable-HTTP app at ``/mcp`` (see :func:`build_http_app`).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .tools import TOOL_NAMES, make_tools

__all__ = ["TOOL_NAMES", "build_http_app", "build_mcp", "run_stdio"]


def build_mcp(
    engine: Any,
    *,
    backend: Any | None = None,
    store_db: str | None = None,
    places_db: str | None = None,
) -> MCPServer:
    """Build the gateway MCP server bound to *engine*.

    *backend* overrides ``engine.backend`` when given; *store_db* overrides
    the engine config's store path; *places_db* overrides the default
    ``<repo_root>/data/places.db`` path. The returned server exposes
    ``streamable_http_app()`` for HTTP mounting and ``run()`` for stdio.
    """
    server = MCPServer(name="hermes-search")
    for tool_fn in make_tools(engine, backend=backend, store_db=store_db, places_db=places_db).values():
        server.tool()(tool_fn)
    return server


def build_http_app(
    engine: Any,
    *,
    backend: Any | None = None,
    store_db: str | None = None,
    places_db: str | None = None,
    path: str = "/",
) -> Any:
    """Return the Starlette app serving MCP over streamable HTTP.

    Mount the result in ``gateway/app.py`` at ``/mcp``
    (``app.mount("/mcp", build_http_app(engine))``); *path* is the
    in-app route (``"/"`` under a mount) and rarely needs changing.
    """
    return build_mcp(engine, backend=backend, store_db=store_db, places_db=places_db).streamable_http_app(
        streamable_http_path=path
    )


def run_stdio(
    engine: Any,
    *,
    backend: Any | None = None,
    store_db: str | None = None,
    places_db: str | None = None,
) -> None:
    """Serve the gateway MCP tools over stdio (blocking)."""
    build_mcp(engine, backend=backend, store_db=store_db, places_db=places_db).run(transport="stdio")
