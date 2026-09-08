"""The MCP server itself: wiring, and nothing else.

Assembles a FastMCP instance from a Grid — the tools, the HTTP routes, the
auth — and runs it. The Grid lives in ``browser.py``, the behaviour in
``actions.py``, the two surfaces in ``tools.py`` and ``routes.py``; this module
only connects them, so a new capability never means editing the server.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from . import resources, routes, tools
from .actions import Actions
from .browser import DEFAULT_GRID_URL, Grid
from .sessions import SessionManager
from .store import SessionStore, from_env

DEFAULT_ROUTE_PREFIX = "/browser"


class SeleniumMCP:
    """A browser-automation MCP server backed by Selenium Grid.

    Every capability is exposed twice: as an MCP tool for agents, and as a JSON
    HTTP endpoint under ``/browser`` for everything else. Both call the same
    functions, so the surfaces cannot drift.

    The server holds no browser state — a session lives on the Grid and the
    caller carries its id. What it *does* hold, in the default HTTP mode, is the
    MCP transport session, and that lives in this process's memory. So the
    ``/browser`` surface scales to any number of replicas as-is, while the
    ``/mcp`` surface does not: a client whose next request lands on another pod
    is told its session does not exist. Set ``stateless`` to drop MCP sessions
    entirely and make both surfaces replica-safe.
    """

    def __init__(
        self,
        grid_url: str = DEFAULT_GRID_URL,
        auth_token: str | None = None,
        route_prefix: str = DEFAULT_ROUTE_PREFIX,
        stateless: bool = False,
        saved_sessions: bool = True,
        store: SessionStore | None = None,
    ):
        self.grid = Grid(grid_url)
        self.actions = Actions(self.grid)
        self.auth_token = auth_token
        self.stateless = stateless
        # Redis or memory per SESSION_STORE. The store is only ever a
        # key -> session record map; the browser is on the Grid either way.
        self.sessions = SessionManager(
            self.actions,
            store=store if store is not None else from_env(),
            enabled=saved_sessions,
        )

        # A token turns on auth for both surfaces. Absent, the server is open —
        # correct for a local `docker compose up`, and the reason the deployment
        # always sets one.
        auth = None
        if auth_token:
            auth = StaticTokenVerifier(
                tokens={auth_token: {"client_id": "selenium-flow", "scopes": []}}
            )

        self.mcp = FastMCP("Selenium", instructions=tools.INSTRUCTIONS, auth=auth)
        tools.register(self.mcp, self.actions, self.sessions)
        # Session status as a resource, plus the same thing as a tool for
        # clients that cannot read resources at all.
        resources.register(self.mcp, self.sessions)
        # No saved sessions here, deliberately: the HTTP surface takes a session
        # id in and gives one back, so the caller owns it.
        routes.register(
            self.mcp, self.actions, auth_token, route_prefix, self.sessions.kind
        )

    def run(
        self, transport: str = "http", host: str = "0.0.0.0", port: int = 8000
    ) -> None:
        """Serve on ``transport``, blocking until the process is stopped."""
        if transport == "stdio":
            self.mcp.run(transport="stdio")
        else:
            self.mcp.run(
                transport="http",
                host=host,
                port=port,
                stateless_http=self.stateless,
            )
