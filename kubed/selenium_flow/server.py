"""The MCP server itself: wiring, and nothing else.

Assembles a FastMCP instance from a Grid — the tools, the HTTP routes, the
auth — and runs it. The Grid lives in ``browser.py``, the behaviour in
``actions.py``, the two surfaces in ``tools.py`` and ``routes.py``; this module
only connects them, so a new capability never means editing the server.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from . import (
    routes,
    secrets,
)
from .core import pointer
from .core.actions import Actions
from .core.browser import DEFAULT_GRID_URL, Grid
from .flows import api as flowapi
from .flows import library as flows
from .http import admin, files
from .mcp import apps, completions, failures, mirror, prompts, resources, skill, tools
from .session.sessions import SessionManager
from .session.store import SessionStore, from_env

# Root. `ROUTE_PREFIX` moves the WHOLE server, so the default is "no prefix"
# rather than a name for one tree (§F1.11). `/` means the same thing and is what
# an operator types when they mean it.
DEFAULT_ROUTE_PREFIX = "/"


class SeleniumMCP:
    """A browser-automation MCP server backed by Selenium Grid.

    Every capability is exposed twice: as an MCP tool for agents, and as a JSON
    HTTP endpoint under ``/browser`` for everything else. Both call the same
    functions, so the surfaces cannot drift.

    The server holds no browser state — a browser lives on the Grid and the
    caller's session name leads back to it. What it *does* hold is the MCP
    transport session, in this process's memory, and it relies on it: that is
    where a client's `initialize` — who it is, what it can do — is remembered.
    So it runs as one replica.
    """

    def __init__(
        self,
        grid_url: str = DEFAULT_GRID_URL,
        auth_token: str | None = None,
        route_prefix: str = DEFAULT_ROUTE_PREFIX,
        store: SessionStore | None = None,
        pointers=None,
        skill_enabled: bool = True,
        apps_enabled: bool = True,
        flow_data_dir: str | None = None,
        secrets_dirs: str | None = None,
    ):
        self.grid = Grid(grid_url)
        # Redis or memory per SESSION_STORE. The store is only ever a
        # key -> session record map; the browser is on the Grid either way.
        # Resolved before the actions, because the pointer store is derived
        # from it.
        self.store = store if store is not None else from_env()
        # Where the pointer is in each browser, on the same backend as the
        # session record (§F2.3) - built FROM that store rather than from a
        # second reading of the environment, which is the only way the two are
        # guaranteed to agree. An injected Redis store with a memory
        # environment would otherwise share session mappings and keep pointers
        # process-local, so a glide on another replica silently started as a
        # jump (Copilot, #31). Still injectable, for a caller that wants a
        # third thing.
        self.actions = Actions(
            self.grid,
            pointers=pointers if pointers is not None else pointer.matching(self.store),
        )
        self.auth_token = auth_token
        # Where this whole server hangs: "" for root. Every tree below is fixed
        # relative to it, which is the inversion §F1.11 asked for.
        self.prefix = routes.mount(route_prefix)
        self.mcp_path = f"{self.prefix}/mcp"
        # Loaded before anything is told about it: the instructions and the
        # session status both name the skill, and neither may name a resource
        # this server is not serving (Copilot, #36).
        self.skill = skill.load() if skill_enabled else None
        self.sessions = SessionManager(
            self.actions, store=self.store, skill_available=self.skill is not None
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

        # The secrets an agent may bind, or None when none were configured.
        # Read-only and value-free: this holds a catalogue, never a credential.
        named = (secrets_dirs or "").strip()
        self.secrets = (
            secrets.from_env({"SECRETS_DIRS": named}) if named else secrets.from_env()
        )

        # A token turns on auth for both surfaces. Absent, the server is open —
        # correct for a local `docker compose up`, and the reason the deployment
        # always sets one.
        auth = None
        if auth_token:
            auth = StaticTokenVerifier(
                tokens={auth_token: {"client_id": "selenium-flow", "scopes": []}}
            )

        self.mcp = FastMCP(
            "Selenium",
            instructions=tools.instructions(self.skill is not None),
            auth=auth,
        )
        tools.register(self.mcp, self.actions, self.sessions, self.secrets)

        # Everything to read is a resource. A client that cannot read them gets
        # `mirror`'s two tools, which read the same URIs (§F3.6).
        # Templates a person picks, rather than instructions the model reads.
        # A failed run's hint names one, which is how an agent that cannot
        # invoke a prompt still points somebody at the right one (§F2.6).
        self.prompts = prompts.register(self.mcp)

        resources.register(self.mcp, self.sessions)
        if self.skill is not None:
            skill.register(self.mcp, self.skill)
        mirror.register(self.mcp)
        completions.register(self.mcp)

        # A session's files: a resource, and a tool that draws them for a host
        # that renders MCP Apps — for that host the tool is the only route to a
        # picture, and for every other client it is listed nowhere.
        # Where the server's own root is publicly reachable, or "" when nobody
        # said — never the bare mount, which made a relative path look absolute
        # (Copilot, #35). Links carry the mount themselves, signed over the
        # unprefixed path; a base that names it too is forgiven, not doubled.
        base = apps.public_base()
        if self.prefix and base.endswith(self.prefix):
            base = base[: -len(self.prefix)]
        app_config = apps.config_for(base) if apps_enabled else None
        app_tools = files.register(
            self.mcp,
            self.actions,
            self.sessions,
            self.flows,
            auth_token,
            app_config,
            base,
            prefix=self.prefix,
        )
        # How an action keeps a file it made. Wired here because this is where
        # the store, the token and the public base all exist; the behaviour
        # layer takes the function and never the key (§F2.9). No default for
        # `folder`: only `Actions._kept` calls this, and it always names one —
        # a default here would let some future two-argument call silently land
        # in Files.
        self.actions.keep = lambda name, data, folder: files.keep_made(
            self.sessions, self.flows, name, data, auth_token, base, self.prefix, folder
        )
        # And how it reads one back, for `upload_file(file=...)`. Wired here for
        # the same reason: which flow session owns a file is a question about
        # the caller, which the behaviour layer deliberately cannot see.
        self.actions.read_file = lambda uri, session=None: files.read_file(
            self.actions, self.sessions, self.flows, uri, session
        )
        self.apps = (
            apps.register(self.mcp, self.actions, auth_token) if apps_enabled else set()
        )
        app_tools |= self.apps
        # Saved flows. Registered whether or not there is a store, so a client
        # that asks is told flows are not enabled here rather than finding the
        # tool absent — a missing capability and a disabled one look identical
        # from the outside, and only one of them is fixable.
        # One step-schema cache, shared by both flow surfaces. The admin's YAML
        # editor validates a document against exactly what `save_flow` does,
        # rather than against a second copy that could drift from it.
        schemas = flowapi.Schemas(self.mcp)
        flowapi.register(
            self.mcp,
            self.flows,
            self.sessions,
            self.actions,
            auth_token,
            prefix=self.prefix,
            secrets_catalogue=self.secrets,
            schemas=schemas,
            # A failed run points at a skill reference, and with --no-skill
            # there is nothing registered to point at.
            skill_available=self.skill is not None,
        )
        secrets.register(
            self.mcp, self.secrets, self.sessions, auth_token, prefix=self.prefix
        )
        self.mcp.add_middleware(mirror.HideMirrors(app_tools, apps_enabled))
        self.mcp.add_middleware(tools.InstructionsFor(self.skill is not None))
        failures.install(self.mcp)
        # The same sessions the MCP surface uses: one contract, one resolver,
        # and the HTTP surface inherits the reopen-after-reap it never had.
        routes.register(
            self.mcp,
            self.actions,
            self.sessions,
            auth_token,
            self.prefix,
            catalogue=self.secrets,
        )

        # The admin pages and the signed file route. Always on: they are how a
        # person sees what the agents have been doing, and the file route is the
        # only way an image reaches somewhere that cannot send a token.
        admin.register(
            self.mcp,
            self.actions,
            auth_token,
            prefix=self.prefix,
            sessions=self.sessions,
            flow_store=self.flows,
            schemas=schemas,
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
                # The MCP endpoint moves with everything else, `/openapi.*`
                # included. Only the four probes also answer at the root.
                path=self.mcp_path,
            )
