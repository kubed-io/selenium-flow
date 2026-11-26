"""The MCP server: `create_app` builds it from the settings, as a `QuickMCP`.

Only wiring. The behaviour is in ``core/actions.py``, the two surfaces in
``mcp/tools.py`` and ``routes.py``, so a new capability needs no change here.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from kubed.mcp import QuickMCP
from starlette.middleware import Middleware

from . import capture as capture_module
from . import config, routes, secrets
from .bidi import Channels
from .capture.store import Capture
from .config import Settings
from .core import pointer, tabs
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
    apps,
    clients,
    completions,
    control,
    failures,
    instructions,
    resources,
    scrubbed,
    show,
    skill,
    tools,
    waiting,
)
from .monitor.events import LocalBus
from .monitor.monitor import Monitor
from .names import SERVER_NAME
from .recordings import collector as recording_collector
from .recordings import mounts
from .workspace import config as workspace_config
from .workspace import locks
from .workspace import settings as workspace_settings
from .workspace import store as store_module
from .workspace.store import WorkspaceStore
from .workspace.workspaces import Workspaces, acting

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
            else store_module.from_settings(
                settings.workspace, settings.redis, settings.session
            )
        )
        # The pointers live on the workspace store's backend, derived from the
        # store itself: an injected Redis store beside a memory environment
        # would otherwise keep pointers process-local, and a glide on another
        # replica would start as a jump.
        self.actions = Actions(
            self.grid,
            pointers=pointers if pointers is not None else pointer.matching(self.store),
            tabs=tabs.matching(self.store),
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
        # The server's public root, or "" when unset. Links carry the mount
        # themselves, so a base that ends in it has it cut, not doubled.
        base = (settings.public_base_url or "").strip().rstrip("/")
        if self.prefix and base.endswith(self.prefix):
            base = base[: -len(self.prefix)]
        # Saved flows, or None when no data directory was named (the default).
        # Built before the workspaces: recordings are filed into it. A
        # recording.dir nothing collects from is passed as no inbox, so it
        # cannot hide an old workspace from the move the boot asks for.
        self.flows = flowstore.from_settings(
            settings.data,
            config.recording_dir(settings) if settings.recording.enabled else None,
        )

        # Each workspace's own config, read from its folder. A workspace
        # that has one is kept: its record is written with no expiry.
        self.configs = workspace_config.Configs(
            self.flows, admin=base + self.prefix if base else ""
        )
        self.store.kept = self.configs.kept
        # A store that predates `retain` follows at its next write instead.
        self.configs.retained = getattr(self.store, "retain", self.configs.retained)
        if self.flows is not None:
            self.flows.global_filter = self.configs.global_filter

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
            defaults=workspace_settings.from_settings(settings.session),
            recordings=self.collector,
            monitor=self.monitor,
            capture=self.capture,
            channels=self.actions.channels,
            control=self.controls,
            hints=self.flows.flows_here if self.flows is not None else None,
            catalogue=self.secrets,
            configs=self.configs,
            tags=self.flows.tags_in_use if self.flows is not None else None,
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

        # The name is the skill's namespace too: skill://<SERVER_NAME>/<skill>.
        self.mcp = QuickMCP(
            SERVER_NAME,
            config=settings,
            instructions=instructions.instructions(),
            # kubed-mcp's delivery inputs in this server's spelling too:
            # Sf-Resources, Sf-Listed, Sf-Via and Sf-Resource-Prefix, beside
            # X-MCP-*, the Sf- name winning (DLV-16).
            header_prefix="Sf",
            auth=self.doors.mcp,
            lifespan=lifespan,
        )
        # Built above, because the routes and the workspaces hold it too; the
        # tools are handed it as `Inject[Actions]`.
        self.mcp.singleton(Actions, instance=self.actions)
        # Docket's worker waits for its tasks when it stops, so the controls
        # let every waiter go first.
        self.mcp.add_extension(waiting.Tasks(first=controls.stop))
        tools.register(self.mcp, self.workspaces, self.secrets, live=settings.live)

        # The skill, the prompts a person picks (a failed run's hint names
        # one) and the agents, all from the plugin folder.
        self.plugin = self.mcp.add_plugin(skill.plugin_path())

        # Everything to read is a resource.
        resources.register(self.mcp, self.workspaces)
        # A client that cannot read resources gets kubed-mcp's list_resources
        # and read_resource (DLV-6), over the same URIs; a binary file read
        # there is pointed at the listing that holds its link.
        self.mcp.binary_pointer(resources.where_linked)
        completions.register(self.mcp)

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
        # For `upload_file(file=...)`: a download is the session the call acts
        # in, or the one its URI names, the rest the workspace's.
        self.actions.read_file = lambda uri, workspace=None: files.read_file(
            self.actions,
            self.workspaces,
            self.flows,
            uri,
            workspace or clients.caller().name,
            session=acting() or None,
        )
        # So one save reads every site's storage: the history of the session
        # the call acts in.
        self.actions.visited = lambda: self.workspaces.visited(
            acting() or clients.caller().name
        )
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
        )
        secrets.register(
            self.mcp, self.secrets, auth_token, prefix=self.prefix,
            configs=self.configs,
        )
        self.mcp.add_middleware(apps.HideApps(app_tools, apps_enabled))
        self.mcp.instructions(instructions.for_delivery)
        # Before anything names the workspace to act on it: a workspace whose
        # config is refused answers nothing else.
        self.mcp.add_middleware(clients.Refused(self.configs))
        # Outside `Notices`: a call that may become a task carries its
        # workspace in a header its worker reads.
        self.mcp.add_middleware(clients.Carry())
        # The first added is outermost, so a failed call's notice follows the
        # sentence `Explained` has already written.
        self.mcp.add_middleware(control.Notices(self.controls, self.workspaces))
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

    def run(self) -> None:
        """Serve as the settings say, blocking until the process is stopped.

        `QuickMCP.run` reads the transport, host and port off the settings, and
        uses the path and the middleware only over HTTP.
        """
        if self.settings.transport != "stdio":
            # The access log names each request by its route template, never
            # by its path.
            access_log.quiet(self.mcp.server.http_app(path=self.mcp_path))
        # Only the four probes also answer at the root.
        self.mcp.run(path=self.mcp_path, middleware=http_middleware())


def create_app(
    settings: Settings | None = None,
    *,
    sources: dict[str, str] | None = None,
    store: WorkspaceStore | None = None,
    pointers=None,
) -> SeleniumMCP:
    """The server for ``settings``: its `mcp` is the `QuickMCP` that serves it."""
    return SeleniumMCP(settings, sources=sources, store=store, pointers=pointers)


def http_middleware() -> list[Middleware]:
    """The ASGI middleware the HTTP app is served with (`RelativeSlashRedirects`)."""
    return [Middleware(RelativeSlashRedirects)]
