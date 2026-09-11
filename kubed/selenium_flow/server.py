"""The MCP server itself: wiring, and nothing else.

Assembles a FastMCP instance from a Grid — the tools, the HTTP routes, the
auth — and runs it. The Grid lives in ``browser.py``, the behaviour in
``actions.py``, the two surfaces in ``tools.py`` and ``routes.py``; this module
only connects them, so a new capability never means editing the server.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from . import admin, apps, files, flowapi, flows, resources, routes, skill, tools
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
        skill_enabled: bool = True,
        apps_enabled: bool = True,
        flow_data_dir: str | None = None,
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

        # Saved flows, or None when no data directory was named — which is the
        # default, and is the feature being off rather than a degraded mode.
        # An explicit directory beats the environment, the way every flag here
        # does; see flows.py for why there is no fallback location.
        # Stripped before it is judged, so an explicit directory and one out of
        # the environment agree about what "unset" means. Without this a
        # FLOW_DATA_DIR of "   " reached here through the CLI flag's default and
        # became a directory named three spaces, while from_env called the same
        # value off.
        directory = (flow_data_dir or "").strip()
        self.flows = flows.LocalFlowStore(directory) if directory else flows.from_env()

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

        # Resources, each with a tool that mirrors it for clients which cannot
        # read resources. The mirrors are collected rather than hidden
        # individually so one middleware makes the whole decision, per request.
        mirrors = resources.register(self.mcp, self.sessions)
        self.skill = skill.load() if skill_enabled else None
        if self.skill is not None:
            mirrors |= skill.register(self.mcp, self.skill)

        # A session's files, and the Grid's running sessions: each a resource
        # with a tool that mirrors it. Those tools also carry the app config,
        # which is why they are exempt from hiding for a client that can render
        # one — for that client the tool is the only route to a picture.
        base = apps.public_base()
        app_config = apps.config_for(base) if apps_enabled else None
        app_tools = files.register(
            self.mcp, self.actions, self.sessions, auth_token, app_config, base
        )
        self.apps = (
            apps.register(self.mcp, self.actions, auth_token) if apps_enabled else set()
        )
        app_tools |= self.apps
        mirrors |= app_tools
        # Saved flows. Registered whether or not there is a store, so a client
        # that asks is told flows are not enabled here rather than finding the
        # tool absent — a missing capability and a disabled one look identical
        # from the outside, and only one of them is fixable.
        mirrors |= flowapi.register(self.mcp, self.flows, self.sessions, auth_token)
        self.mcp.add_middleware(
            resources.HideMirrorTools(mirrors, app_tools if apps_enabled else set())
        )
        # Shapes session_id per request, so the advertised schema matches the
        # mode the caller is actually in rather than the union of both.
        self.mcp.add_middleware(resources.ShapeSessionId(self.sessions))
        # No saved sessions here, deliberately: the HTTP surface takes a session
        # id in and gives one back, so the caller owns it.
        routes.register(
            self.mcp, self.actions, auth_token, route_prefix, self.sessions.kind
        )

        # The admin pages and the signed file route. Always on: they are how a
        # person sees what the agents have been doing, and the file route is the
        # only way an image reaches somewhere that cannot send a token.
        admin.register(self.mcp, self.actions, auth_token, sessions=self.sessions)

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
