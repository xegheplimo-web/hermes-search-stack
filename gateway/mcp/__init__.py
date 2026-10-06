"""Universal gateway MCP surface (r8-interfaces.md section 7)."""

from .server import TOOL_NAMES, build_http_app, build_mcp, run_stdio
from .tools import FROZEN_TOOL_PARAMS, make_tools

__all__ = [
    "FROZEN_TOOL_PARAMS",
    "TOOL_NAMES",
    "build_http_app",
    "build_mcp",
    "make_tools",
    "run_stdio",
]
