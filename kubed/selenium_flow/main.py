"""Entry point: turn CLI flags and environment into a running server.

Every flag has an environment fallback because the container is configured with
env vars while a developer reaches for flags. The one thing not configured here
is the session store, which reads ``SESSION_STORE`` / ``SESSION_TTL`` /
``REDIS_*`` in ``store.py`` so that choosing a backend stays next to the code
that builds one.
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
        "--no-saved-sessions",
        dest="saved_sessions",
        action="store_false",
        default=os.environ.get("SAVED_SESSIONS", "true").strip().lower()
        not in ("0", "false", "no", "off"),
        help="require session_id on every MCP tool call instead of remembering "
        "the browser per MCP session. The HTTP endpoints are unaffected: they "
        "are always explicit (env: SAVED_SESSIONS)",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=os.environ.get("STATELESS_HTTP", "").strip().lower()
        in ("1", "true", "yes", "on"),
        help="drop MCP transport sessions so any replica can serve any request; "
        "required to run more than one replica (env: STATELESS_HTTP)",
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
        stateless=args.stateless,
        saved_sessions=args.saved_sessions,
    )
    logging.getLogger(__name__).info(
        "grid=%s auth=%s saved-sessions=%s stateless=%s",
        args.grid_url,
        "on" if args.auth_token else "off",
        server.sessions.kind,
        args.stateless,
    )
    server.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
