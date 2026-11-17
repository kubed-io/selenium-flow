"""The MCP server: builds the FastMCP instance from the settings and runs it.

Only wiring. The behaviour is in ``core/actions.py``, the two surfaces in
``mcp/tools.py`` and ``routes.py``, so a new capability needs no change here.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from starlette.middleware import Middleware

from . import capture as capture_module
from . import config, routes, secrets
from .bidi import Channels
from .capture.store import Capture
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
from .live import pages as pages_module
from .live.control import Controls
from .live.pages import Pages
from .live.tunnels import Tunnels
from .mcp import (
    agents,
    apps,
    clients,
    completions,
    control,
    failures,
    instructions,
    mirror,
    prompts,
    resources,
    scrubbed,
    show,
    skill,
    tools,
    waiting,
)
from .monitor.events import LocalBus
from .monitor.monitor import Monitor
from .recordings import collector as recording_collector
from .recordings import mounts
from .workspace import locks
from .workspace import settings as workspace_settings
from .workspace import store as store_module
from .workspace.store import WorkspaceStore
from .workspace.workspaces import Workspaces

log = logging.getLogger(__name__)


class SeleniumMCP:
    """A browser-automation MCP server backed by Selenium Grid.

    Every capability is an MCP tool and a JSON endpoint under ``/browser``, and
    both call the same functions.

    A browser lives on the Grid and the caller's workspace name leads back to
    it. The MCP transport session lives in this process's memory, and it holds
    what a client's `initialize` said (who it is, what it can do), so the server
    runs as one replica.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        sources: dict[str, str] | None = None,
        store: WorkspaceStore | None = None,
        pointers=None,
    ):
        settings = settings if settings is not None else Settings()
        self.settings = settings
        # Where each value came from, for the Settings tab.
        self.sources = (
            dict(sources) if sources is not None else config.sources_for(settings)
        )
        self.grid = Grid(settings.grid.url)
        # Before the actions: the pointer store is derived from it.
        self.store = (
            store
            if store is not None
            else store_module.from_settings(settings.workspace, settings.redis)
        )
        # The pointers live on the workspace store's backend, derived from the
        # store itself: an injected Redis store beside a memory environment
        # would otherwise keep pointers process-local, and a glide on another
        # replica would start as a jump.
        self.actions = Actions(
            self.grid,
            pointers=pointers if pointers is not None else pointer.matching(self.store),
        )
        # Applied under every open's own capabilities.
        self.actions.config_capabilities = dict(settings.session.capabilities)
        auth_token = (
            settings.auth.token.get_secret_value() if settings.auth.token else None
        )
        self.auth_token = auth_token
        # Where this whole server is mounted: "" for root.
        self.prefix = routes.mount(settings.route_prefix)
        self.mcp_path = f"{self.prefix}/mcp"
        # Loaded first: the instructions and the workspace status name the
        # skill only when it is served.
        self.skill = skill.load() if settings.mcp.skill else None

        # Saved flows, or None when no data directory was named (the default).
        # Built before the workspaces: recordings are filed into it. A
        # recording.dir nothing collects from is passed as no inbox, so it
        # cannot hide an old workspace from the move the boot asks for.
        self.flows = flowstore.from_settings(
            settings.data,
            config.recording_dir(settings) if settings.recording.enabled else None,
        )

        # One bus and one monitor in this process. The monitor watches the
        # Grid's status listing, holds a BiDi socket only for a reason that
        # needs its events, and keeps a browser alive for `session.keep_alive`
        # after its last call. Built before the collector and the workspaces,
        # which tell it what they open, end and owe.
        self.bus = LocalBus()
        # The monitor's two sinks, built before it: captured console and
        # network events, and the pages a person reaches in the live view.
        self.capture = Capture(ttl=settings.capture.ttl)
        self.pages = Pages()
        self.monitor = Monitor(
            self.bus,
            listing=self.grid.listing,
            socket_url=self.grid.bidi_url,
            events_for={
                "capture": capture_module.EVENTS,
                "control": pages_module.EVENTS,
            },
            collectors_for={"capture": capture_module.COLLECTOR},
            sinks={"capture": self.capture, "control": self.pages},
            ping=self.grid.ping,
            keep_alive=settings.session.keep_alive,
        )
        self.pages.connection = self.monitor.connection
        # Control is granted once the calls before the request have had their
        # turn on the session's lock (polled, never waited on) and the pages
        # the person reaches are followed.
        self.controls = Controls(
            behind=locks.behind, monitor=self.monitor, pages=self.pages
        )
        # A browser ending closes its live viewers and releases its control.
        self.tunnels = Tunnels(self.controls)
        self.bus.subscribe("session.ended", self.tunnels.ended, name="live")
        # Site data's save and restore speak BiDi, reusing the monitor's
        # connection when it holds one.
        self.actions.channels = Channels(
            self.grid.bidi_url,
            held=self.monitor.connection,
            loop=lambda: self.monitor.loop,
        )
        # A save's spare tab never reaches a caller's console or network log.
        self.actions.hide = self.capture.hidden
        self.actions.capture = self.capture
        self.actions.control = self.controls

        # The Grid films, the operator delivers to the inbox, the collector
        # files. Built before the workspaces, which tell it about every
        # recorded browser; None when recording is off.
        self.collector = None
        problem = config.recording_problem(settings)
        if problem:
            # `load` checks this too; Settings built in code skips `load`.
            raise config.ConfigError(problem)
        if settings.recording.enabled:
            inbox = Path(config.recording_dir(settings))
            if not inbox.is_dir() or not os.access(inbox, os.R_OK | os.W_OK | os.X_OK):
                raise config.ConfigError(
                    f"recording.dir {inbox} must exist and be readable and writable "
                    "by this server: filing a recording moves it out"
                )
            self.collector = recording_collector.Collector(
                self.flows,
                inbox,
                monitor=self.monitor,
                wait=settings.recording.wait,
                polling=mounts.polling(settings.recording.watch, inbox),
                poll_ms=settings.recording.poll,
                settle=settings.recording.settle,
            )
            log.info(
                "recordings: on, inbox %s, %s",
                inbox,
                "polling" if self.collector.polling else "events",
            )
            collector_ended = self.collector.ended
            self.bus.subscribe(
                "session.ended",
                lambda event: collector_ended(event.session_id),
                name="recordings",
            )

        # The catalogue of secrets an agent may bind (names, never values), or
        # None. Built before the workspaces, whose every answer is checked for
        # the secrets in scope where it ran.
        self.secrets = secrets.from_settings(settings.secrets, settings.config_file)

        self.workspaces = Workspaces(
            self.actions,
            store=self.store,
            skill_available=self.skill is not None,
            defaults=workspace_settings.from_settings(settings.session),
            recordings=self.collector,
            monitor=self.monitor,
            keep_alive=settings.session.keep_alive,
            capture=self.capture,
            channels=self.actions.channels,
            control=self.controls,
            hints=self.flows.flows_here if self.flows is not None else None,
            catalogue=self.secrets,
        )
        # Capture holds a page's events until the secrets in scope there are
        # in its masker. With secrets off it holds nothing.
        if self.secrets is not None:
            self.capture.heard = self.workspaces.capture_heard
        # A browser that ended (quit, reaped, or found gone) takes its origin
        # and its secret forms with it.
        browser_ended = self.workspaces.browser_ended
        self.bus.subscribe(
            "session.ended",
            lambda event: browser_ended(event.workspace, event.session_id),
            name="secret-scope",
        )

        # The token turns on auth for both surfaces; without one the server is
        # open. `oidc` adds a JWT beside it on /mcp, and the admin door also
        # admits an admin-UI JWT when `oidc.client_id` is set.
        self.doors = http_auth.doors(settings)

        # These live on the server's event loop: the bus starts first, so
        # nothing is announced to a loop that is not there, and the monitor
        # before the collector, which asks it to watch what is still owed.
        collector, monitor, bus = self.collector, self.monitor, self.bus
        tunnels, controls = self.tunnels, self.controls
        scrubber = self.workspaces.scrubber

        @asynccontextmanager
        async def lifespan(_server):
            controls.start()
            bus.start()
            await monitor.start()
            if collector is not None:
                await collector.start()
            try:
                yield {}
            finally:
                # First, every waiter is let go before anything it waits on
                # stops. `waiting.Tasks` has already done it; again here is a
                # no-op.
                controls.stop()
                if collector is not None:
                    await collector.stop()
                tunnels.stop()
                await monitor.stop()
                await bus.stop()
                scrubber.close()

        self.mcp = FastMCP(
            "Selenium",
            instructions=instructions.first_instructions(self.skill is not None),
            auth=self.doors.mcp,
            lifespan=lifespan,
        )
        # Docket's worker waits for its tasks when it stops, so the controls
        # let every waiter go first.
        self.mcp.add_extension(waiting.Tasks(first=controls.stop))
        tools.register(
            self.mcp, self.actions, self.workspaces, self.secrets, live=settings.live
        )

        # Templates a person picks, rather than instructions the model reads. A
        # failed run's hint names one, so an agent that cannot invoke a prompt
        # still points somebody at the right one.
        self.prompts = prompts.register(self.mcp)

        # Everything to read is a resource.
        resources.register(self.mcp, self.workspaces)
        if self.skill is not None:
            skill.register(self.mcp, self.skill)
            # An agent's first step is to read the skill, so agents are served
            # only with it, and declared even when there are none.
            agents.register(self.mcp, agents.load(skills={self.skill.skill_info.name}))
        # A client that cannot read resources gets `mirror`'s two tools, which
        # read the same URIs.
        mirror.register(self.mcp)
        completions.register(self.mcp)

        # The server's public root, or "" when unset. Links carry the mount
        # themselves, so a base that ends in it has it cut, not doubled.
        base = (settings.public_base_url or "").strip().rstrip("/")
        if self.prefix and base.endswith(self.prefix):
            base = base[: -len(self.prefix)]
        if not admin_page.ui_built("admin"):
            log.info(
                "The admin UI is not built, so its URL shows a placeholder: "
                "run `npm --prefix ui run build`."
            )
        apps_enabled = settings.mcp.apps and apps.available()
        app_config = apps.config_for(base) if apps_enabled else None
        app_tools = files.register(
            self.mcp,
            self.actions,
            self.workspaces,
            self.flows,
            auth_token,
            base,
            prefix=self.prefix,
            ttl=settings.link_ttl,
        )
        app_tools |= show.register(self.mcp, app_config)
        # These three are called from inside an action, which cannot see the
        # caller, so they ask `clients.caller`. Wired here, where the store,
        # the token and the public base exist; the behaviour layer never holds
        # the key. `folder` has no default: a call that forgot it would land
        # in Files.
        self.actions.keep = lambda name, data, folder: files.keep_made(
            clients.caller().name,
            self.flows,
            name,
            data,
            auth_token,
            base,
            self.prefix,
            folder,
            ttl=settings.link_ttl,
        )
        # For `upload_file(file=...)`.
        self.actions.read_file = lambda uri, workspace=None: files.read_file(
            self.actions,
            self.workspaces,
            self.flows,
            uri,
            workspace or clients.caller().name,
        )
        # So one save reads every site's storage.
        self.actions.visited = lambda: self.workspaces.visited(clients.caller().name)
        self.apps = (
            apps.register(self.mcp, self.actions, auth_token, base)
            if apps_enabled
            else set()
        )
        app_tools |= self.apps
        # Registered with no store too, so a caller is told flows are off
        # rather than finding the tools absent. One step-schema cache serves
        # `save_flow` and the admin's YAML editor alike.
        schemas = flowapi.Schemas(self.mcp)
        flowapi.register(
            self.mcp,
            self.flows,
            self.workspaces,
            self.actions,
            auth_token,
            prefix=self.prefix,
            secrets_catalogue=self.secrets,
            schemas=schemas,
            # A failed run's hint points at a skill page only when one is served.
            skill_available=self.skill is not None,
        )
        secrets.register(self.mcp, self.secrets, auth_token, prefix=self.prefix)
        self.mcp.add_middleware(mirror.HideMirrors(app_tools, apps_enabled))
        self.mcp.add_middleware(instructions.InstructionsFor(self.skill is not None))
        # Outside `Notices`: a call that may become a task carries its
        # workspace in a header its worker reads.
        self.mcp.add_middleware(clients.Carry())
        # The first added is outermost, so a failed call's notice follows the
        # sentence `Explained` has already written.
        self.mcp.add_middleware(control.Notices(self.controls))
        failures.install(self.mcp)
        # Inside `failures`, so a read that cannot be scrubbed answers the
        # fixed sentence and `Explained` passes it on as it is.
        scrubbed.install(self.mcp, self.workspaces)
        routes.register(
            self.mcp,
            self.actions,
            self.workspaces,
            auth_token,
            self.prefix,
            catalogue=self.secrets,
            live=settings.live,
        )

        # Always on: the signed file route is the only way an image reaches
        # somewhere that cannot send a token.
        broadcast = admin.register(
            self.mcp,
            self.actions,
            auth_token,
            console_url=settings.grid.console_url,
            prefix=self.prefix,
            workspaces=self.workspaces,
            flow_store=self.flows,
            schemas=schemas,
            catalogue=self.secrets,
            settings_payload=lambda: config.describe(self.settings, self.sources),
            link_ttl=settings.link_ttl,
            frame_ancestors=settings.security.frame_ancestors,
            door=self.doors.admin,
            oidc_page=admin_page.sign_in(settings.oidc),
            capture=self.capture,
            live=settings.live,
            tunnels=self.tunnels,
            monitor=self.monitor,
        )
        # Open admin pages refresh at once on a filed recording, an opened or
        # an ended session; their poll covers the rest.
        if self.collector is not None:
            self.collector.on_filed = broadcast.poke
        self.bus.subscribe(
            ("session.opened", "session.ended"),
            lambda _event: broadcast.poke(),
            name="admin",
        )

    def run(
        self, transport: str = "http", host: str = "0.0.0.0", port: int = 8000
    ) -> None:
        """Serve on ``transport``, blocking until the process is stopped."""
        # No banner: printing it makes FastMCP ask pypi.org for a newer
        # release on every start.
        if transport == "stdio":
            self.mcp.run(transport="stdio", show_banner=False)
        else:
            # The access log names each request by its route template, never
            # by its path.
            access_log.quiet(self.mcp.http_app(path=self.mcp_path))
            self.mcp.run(
                transport="http",
                host=host,
                port=port,
                # Only the four probes also answer at the root.
                path=self.mcp_path,
                show_banner=False,
                middleware=http_middleware(),
            )


def http_middleware() -> list[Middleware]:
    """The ASGI middleware the HTTP app is served with (`RelativeSlashRedirects`)."""
    return [Middleware(RelativeSlashRedirects)]
