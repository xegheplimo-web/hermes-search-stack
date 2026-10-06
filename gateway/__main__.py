"""Entry point: ``python -m gateway`` (HTTP) or ``python -m gateway --mcp-stdio``."""

from __future__ import annotations

import argparse
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gateway",
        description="Hermes Universal Gateway (OpenAI-compatible HTTP surface + MCP).",
    )
    parser.add_argument(
        "--mcp-stdio",
        action="store_true",
        help="Run the MCP server over stdio instead of the HTTP surface.",
    )
    parser.add_argument("--host", default=None, help="Override the bind host (default: from config).")
    parser.add_argument("--port", type=int, default=None, help="Override the bind port (default: from config).")
    return parser.parse_args(argv)


def _run_mcp_stdio() -> int:
    try:
        from gateway.mcp.server import run_stdio
    except ImportError as exc:
        print(
            f"error: cannot run the MCP stdio server ({exc}). "
            "Install the gateway dependencies first (requirements-gateway.txt).",
            file=sys.stderr,
        )
        return 2
    from gateway.app import load_config
    from gateway.core.engine import default_engine

    run_stdio(default_engine(load_config()))
    return 0


def _run_http(args: argparse.Namespace) -> int:
    import uvicorn

    from gateway.app import create_app, load_config

    config = load_config()
    host = args.host or getattr(config, "host", None) or "127.0.0.1"
    port = args.port or getattr(config, "port", None) or 8787
    uvicorn.run(create_app(config=config), host=host, port=port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = _parse_args(argv)
    if args.mcp_stdio:
        return _run_mcp_stdio()
    return _run_http(args)


if __name__ == "__main__":
    raise SystemExit(main())
