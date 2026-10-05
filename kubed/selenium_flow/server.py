"""The MCP server itself: wiring, and nothing else.

Assembles a FastMCP instance from a Grid — the tools, the HTTP routes, the
auth — and runs it. The Grid lives in ``browser.py``, the behaviour in
``actions.py``, the two surfaces in ``tools.py`` and ``routes.py``; this module
only connects them, so a new capability never means editing the server.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from starlette.middleware import Middleware

from . import config, routes, secrets
from .config import Settings
from .core import pointer
from .core.actions import Actions
from .core.browser import Grid
from .flows import api as flowapi
from .flows import store as flowstore
from .http import access_log, admin, files
from .http import auth as http_auth
from .http.admin import page as admin_page
from .http.slashes import RelativeSlashRedirects
from .mcp import (
    apps,
    clients,
    completions,
    failures,
    mirror,
    prompts,
    resources,
    show,
    skill,
    tools,
)
from .session import settings as session_settings
from .session import store as store_module
from .session.sessions import SessionManager
from .session.store import SessionStore

log = logging.getLogger(__name__)


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
        settings: Settings | None = None,
        *,
        sources: dict[str, str] | None = None,
        store: SessionStore | None = None,
        pointers=None,
    ):
        settings = settings if settings is not None else Settings()
        self.settings = settings
        # Where each value came from, for the Settings tab. Built from the
        # settings themselves when a caller constructed them in code.
        self.sources = (
            dict(sources) if sources is not None else config.sources_for(settings)
        )
        self.grid = Grid(settings.grid.url)
        # Redis or memory per session.store. The store is only ever a
        # key -> session record map; the browser is on the Grid either way.
        # Resolved before the actions, because the pointer store is derived
        # from it.
        self.store = (
            store if store is not None
            else store_module.from_settings(settings.session, settings.redis)
        )
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
        auth_token = (
            settings.auth.token.get_secret_value() if settings.auth.token else None
        )
        self.auth_token = auth_token
        # Where this whole server hangs: "" for root. Every tree below is fixed
        # relative to it, which is the inversion §F1.11 asked for.
        self.prefix = routes.mount(settings.route_prefix)
        self.mcp_path = f"{self.prefix}/mcp"
        # Loaded before anything is told about it: the instructions and the
        # session status both name the skill, and neither may name a resource
        # this server is not serving (Copilot, #36).
        self.skill = skill.load() if settings.mcp.skill else None
        self.sessions = SessionManager(
            self.actions,
            store=self.store,
            skill_available=self.skill is not None,
            defaults=session_settings.from_settings(settings.session),
        )

        # Saved flows, or None when no data directory was named — which is the
        # default, and is the feature being off rather than a degraded mode.
        self.flows = flowstore.from_settings(settings.flow)

        # The secrets an agent may bind, or None when none were configured.
        # Read-only and value-free: this holds a catalogue, never a credential.
        self.secrets = secrets.from_settings(settings.secrets, settings.config_file)

        # The token turns on auth for both surfaces; `oidc` adds a JWT beside it
        # on /mcp only. Absent, the server is open — correct for a local
        # `docker compose up`, and the reason the deployment always sets one.
        auth = http_auth.provider(settings)

        self.mcp = FastMCP(
            "Selenium",
            instructions=tools.first_instructions(self.skill is not None),
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

        # A session's files are resources; `show` draws them, and any other
        # showable resource, for a host that renders MCP Apps — and is listed
        # for no other client.
        # Where the server's own root is publicly reachable, or "" when nobody
        # said — never the bare mount, which made a relative path look absolute
        # (Copilot, #35). Links carry the mount themselves, signed over the
        # unprefixed path; a base that names it too is forgiven, not doubled.
        base = (settings.public_base_url or "").strip().rstrip("/")
        if self.prefix and base.endswith(self.prefix):
            base = base[: -len(self.prefix)]
        if not admin_page.ui_built("admin"):
            log.info(
                "The admin UI is not built, so its URL shows a placeholder: "
                "run `npm --prefix ui run build`."
            )
        # An app is a view of the built shell; without the shell there is none.
        apps_enabled = settings.mcp.apps and apps.available()
        app_config = apps.config_for(base) if apps_enabled else None
        app_tools = files.register(
            self.mcp,
            self.actions,
            self.sessions,
            self.flows,
            auth_token,
            base,
            prefix=self.prefix,
            ttl=settings.link_ttl,
        )
        app_tools |= show.register(self.mcp, app_config)
        # These three are called from inside an action, below every edge, so
        # they ask the edge's own reader who is calling (`clients.caller`).
        #
        # How an action keeps a file it made. Wired here because this is where
        # the store, the token and the public base all exist; the behaviour
        # layer takes the function and never the key (§F2.9). No default for
        # `folder`: only `Actions._kept` calls this, and it always names one —
        # a default here would let some future two-argument call silently land
        # in Files.
        self.actions.keep = lambda name, data, folder: files.keep_made(
            clients.caller().name, self.flows, name, data, auth_token, base,
            self.prefix, folder, ttl=settings.link_ttl,
        )
        # And how it reads one back, for `upload_file(file=...)`. Wired here for
        # the same reason: which flow session owns a file is a question about
        # the caller, which the behaviour layer deliberately cannot see.
        self.actions.read_file = lambda uri, session=None: files.read_file(
            self.actions, self.sessions, self.flows, uri,
            session or clients.caller().name,
        )
        # And where the caller's session has been, so one save reads every
        # site's storage. Wired here for the same reason: which session is
        # calling is a question about the caller.
        self.actions.visited = lambda: self.sessions.visited(clients.caller().name)
        self.apps = (
            apps.register(self.mcp, self.actions, auth_token, base)
            if apps_enabled
            else set()
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
            # A failed run points at a skill reference, and with `--mcp-skill false`
            # there is nothing registered to point at.
            skill_available=self.skill is not None,
        )
        secrets.register(self.mcp, self.secrets, auth_token, prefix=self.prefix)
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
            console_url=settings.grid.console_url,
            prefix=self.prefix,
            sessions=self.sessions,
            flow_store=self.flows,
            schemas=schemas,
            catalogue=self.secrets,
            settings_payload=lambda: config.describe(self.settings, self.sources),
            link_ttl=settings.link_ttl,
            frame_ancestors=settings.security.frame_ancestors,
        )

    def run(
        self, transport: str = "http", host: str = "0.0.0.0", port: int = 8000
    ) -> None:
        """Serve on ``transport``, blocking until the process is stopped."""
        # No banner: it is 25 lines of box art in every pod's log, and printing
        # it is what makes FastMCP ask pypi.org for a newer release on every
        # start (`check_for_updates`). `main` logs the configuration instead.
        if transport == "stdio":
            self.mcp.run(transport="stdio", show_banner=False)
        else:
            # Built from the same routes the app it serves is, so the access
            # log names each request by its template, never by its path.
            access_log.quiet(self.mcp.http_app(path=self.mcp_path))
            self.mcp.run(
                transport="http",
                host=host,
                port=port,
                # The MCP endpoint moves with everything else, `/openapi.*`
                # included. Only the four probes also answer at the root.
                path=self.mcp_path,
                show_banner=False,
                middleware=http_middleware(),
            )


def http_middleware() -> list[Middleware]:
    """The ASGI middleware the HTTP app is served with (`RelativeSlashRedirects`)."""
    return [Middleware(RelativeSlashRedirects)]
