"""Entry point: turn CLI flags and environment into a running server.

Every flag has an environment fallback because the container is configured with
env vars while a developer reaches for flags. Nothing else in the package reads
the environment, so this file is the whole configuration surface.
"""

from __future__ import annotations

import argparse
import logging
import os

from .browser import DEFAULT_GRID_URL
from .server import DEFAULT_ROUTE_PREFIX, SeleniumMCP


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="selenium-flow",
        description="Drive a Selenium Grid browser over MCP and HTTP.",
    )
    parser.add_argument(
        "--grid-url",
        default=os.environ.get("GRID_URL", DEFAULT_GRID_URL),
        help="Selenium Grid hub URL (env: GRID_URL)",
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("MCP_AUTH_TOKEN", ""),
        help="bearer token required on every request; empty disables auth "
        "(env: MCP_AUTH_TOKEN)",
    )
    parser.add_argument(
        "--route-prefix",
        default=os.environ.get("ROUTE_PREFIX", DEFAULT_ROUTE_PREFIX),
        help="path prefix for the plain HTTP endpoints (env: ROUTE_PREFIX)",
    )
    parser.add_argument(
        "--transport",
        default=os.environ.get("TRANSPORT", "http"),
        choices=["stdio", "http"],
        help="transport to serve on (env: TRANSPORT)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="bind address for http transport (env: HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="port for http transport (env: PORT)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="python logging level (env: LOG_LEVEL)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``selenium-flow`` console script."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())
    server = SeleniumMCP(
        grid_url=args.grid_url,
        auth_token=args.auth_token or None,
        route_prefix=args.route_prefix,
    )
    server.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
