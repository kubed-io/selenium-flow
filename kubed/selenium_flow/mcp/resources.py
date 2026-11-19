"""The workspace status, as a resource.

It is state to read, not an action to perform, so a client can pull it into
context without spending a tool call. A client that cannot read resources reads
it through `mirror.read_resource`, at the same URI.
"""

from __future__ import annotations

from dataclasses import replace

from fastmcp import FastMCP

from .. import faults
from ..capture import CONSOLE_URI, NETWORK_URI
from ..site_data import snapshot as site_data
from ..workspace.workspaces import Caller, Workspaces
from . import clients

RESOURCE_URI = "workspace://current"
SESSION_URI = "session://{name}"

CAPTURE_KEPT = (
    "What was captured is the workspace's, not the browser's: it stays readable "
    "after the session ends, until capture.ttl passes with nothing new or "
    "open_session is called without capture."
)

DESCRIPTION = (
    "The workspace this client named, and your own session (browser) in it. "
    "Any session, by name, is session://{name}.\n\n"
    "Returns the workspace name, which browser it is, the page it is on, the "
    "window size as WxH, whether it is inside a frame, and whether a session is "
    "currently open (live). Read it before judging anything about layout — the "
    "window is not a fixed size and is what decides whether something is "
    "off-screen.\n\n"
    "grid_timeout is how many seconds the Grid lets a session in this "
    "workspace sit idle before it ends it; every call starts that clock "
    "again.\n\n"
    "capture says whether this workspace's browsers capture their console and "
    "network (open_session(capture=true)); capturing is whether that is "
    "happening right now \u2014 false while it is paused, until the next call; "
    "captured counts what was kept since the last navigation. " + CAPTURE_KEPT + "\n\n"
    "config is this workspace's own settings, each with its value and its "
    "source (default or workspace). Only an admin changes them, at config_url "
    "when the server has one.\n\n"
    "tags maps each tag in use on this workspace's flows, and the global flows "
    "it is offered, to how many carry it: reuse one when you tag a flow.\n\n"
    "Reading this never opens a browser: live is false when no session is open."
)

PART_DESCRIPTION = (
    "One key of workspace://current, as {workspace, <part>: value}: for "
    "instance workspace://current/url or workspace://current/grid_timeout. It "
    "is null when the workspace has nothing for that key. A name that is not a "
    "key is refused, and the refusal lists them. Built as workspace://current "
    "is, and reading it never opens a browser."
)


def register(mcp: FastMCP, workspaces: Workspaces) -> None:
    """Register the workspace status resource."""

    @mcp.resource(
        RESOURCE_URI,
        name="Current Workspace",
        description=DESCRIPTION,
        mime_type="application/json",
    )
    def current_workspace_resource() -> dict:
        return workspaces.describe(clients.caller())

    @mcp.resource(
        f"{RESOURCE_URI}/{{part}}",
        name="Current Workspace, one part",
        description=PART_DESCRIPTION,
        mime_type="application/json",
    )
    def current_workspace_part_resource(part: str) -> dict:
        return workspaces.describe_part(clients.caller(), part)

    @mcp.resource(
        site_data.LIST_URI,
        name="Site Data",
        description=SITE_DESCRIPTION,
        mime_type="application/json",
    )
    def site_data_resource() -> dict:
        return site_listing(workspaces, clients.caller())

    @mcp.resource(
        f"{site_data.LIST_URI}/{{site}}",
        name="Site Data for One Site",
        description=ONE_SITE_DESCRIPTION,
        mime_type="application/json",
    )
    def one_site_resource(site: str) -> dict:
        return one_site(workspaces, clients.caller(), site)

    @mcp.resource(
        CONSOLE_URI,
        name="Console",
        description=CONSOLE_DESCRIPTION,
        mime_type="application/json",
    )
    def console_resource() -> dict:
        return workspaces.captured(_key(clients.caller()), "console")

    @mcp.resource(
        f"{CONSOLE_URI}{{?level,pages}}",
        name="Console, narrowed",
        description=CONSOLE_DESCRIPTION,
        mime_type="application/json",
    )
    def console_query_resource(level: str = "all", pages: str = "1") -> dict:
        return workspaces.captured(
            _key(clients.caller()), "console", level=level, pages=pages
        )

    @mcp.resource(
        NETWORK_URI,
        name="Network",
        description=NETWORK_DESCRIPTION,
        mime_type="application/json",
    )
    def network_resource() -> dict:
        return workspaces.captured(_key(clients.caller()), "network")

    @mcp.resource(
        f"{NETWORK_URI}{{?filter,pages}}",
        name="Network, narrowed",
        description=NETWORK_DESCRIPTION,
        mime_type="application/json",
    )
    def network_query_resource(filter: str = "all", pages: str = "1") -> dict:
        return workspaces.captured(
            _key(clients.caller()), "network", filter=filter, pages=pages
        )

    @mcp.resource(
        f"{NETWORK_URI}/{{id}}",
        name="One Request",
        description=REQUEST_DESCRIPTION,
        mime_type="application/json",
    )
    def request_resource(id: str) -> dict:
        return workspaces.captured(_key(clients.caller()), "request", id=id)

    def _key(caller: Caller) -> str:
        """The key of the session ``caller`` reads, never made by reading."""
        return workspaces.reading(caller)

    def _named(name: str) -> Caller:
        """The caller, pinned to the session ``name`` of its workspace: a
        valid name with no session behind it is not found."""
        caller = replace(clients.caller(), pin=name)
        try:
            workspaces.reading(caller)
        except faults.NoSession:
            raise faults.NotFound(
                f"no such session in workspace {caller.name!r}: "
                f"{RESOURCE_URI} lists them"
            ) from None
        return caller

    @mcp.resource(
        SESSION_URI,
        name="Session",
        description=SESSION_DESCRIPTION,
        mime_type="application/json",
    )
    def session_resource(name: str) -> dict:
        return workspaces.describe(_named(name), at=f"session://{name}")

    # The narrowed template first: FastMCP tries them in order and matches the
    # plain one with its query dropped.
    @mcp.resource(
        f"{SESSION_URI}/console{{?level,pages}}",
        name="Session Console, narrowed",
        description=SESSION_CONSOLE_DESCRIPTION,
        mime_type="application/json",
    )
    def session_console_query_resource(
        name: str, level: str = "all", pages: str = "1"
    ) -> dict:
        return workspaces.captured(
            _key(_named(name)), "console", named=True, level=level, pages=pages
        )

    @mcp.resource(
        f"{SESSION_URI}/console",
        name="Session Console",
        description=SESSION_CONSOLE_DESCRIPTION,
        mime_type="application/json",
    )
    def session_console_resource(name: str) -> dict:
        return workspaces.captured(_key(_named(name)), "console", named=True)

    # The narrowed template first: FastMCP tries them in order and matches the
    # plain one with its query dropped.
    @mcp.resource(
        f"{SESSION_URI}/network{{?filter,pages}}",
        name="Session Network, narrowed",
        description=SESSION_NETWORK_DESCRIPTION,
        mime_type="application/json",
    )
    def session_network_query_resource(
        name: str, filter: str = "all", pages: str = "1"
    ) -> dict:
        return workspaces.captured(
            _key(_named(name)), "network", named=True, filter=filter, pages=pages
        )

    @mcp.resource(
        f"{SESSION_URI}/network",
        name="Session Network",
        description=SESSION_NETWORK_DESCRIPTION,
        mime_type="application/json",
    )
    def session_network_resource(name: str) -> dict:
        return workspaces.captured(_key(_named(name)), "network", named=True)

    @mcp.resource(
        f"{SESSION_URI}/network/{{id}}",
        name="Session Request",
        description=SESSION_REQUEST_DESCRIPTION,
        mime_type="application/json",
    )
    def session_request_resource(name: str, id: str) -> dict:
        return workspaces.captured(
            _key(_named(name)),
            "request",
            at=f"session://{name}/network/{id}",
            named=True,
            id=id,
        )

    @mcp.resource(
        f"{SESSION_URI}/site-data",
        name="Session Site Data",
        description=SESSION_SITE_DESCRIPTION,
        mime_type="application/json",
    )
    def session_site_data_resource(name: str) -> dict:
        return site_listing(
            workspaces, _named(name), base=f"session://{name}/site-data"
        )

    @mcp.resource(
        f"{SESSION_URI}/site-data/{{site}}",
        name="Session Site Data for One Site",
        description=SESSION_ONE_SITE_DESCRIPTION,
        mime_type="application/json",
    )
    def session_one_site_resource(name: str, site: str) -> dict:
        return one_site(
            workspaces, _named(name), site, base=f"session://{name}/site-data"
        )


CONSOLE_DESCRIPTION = (
    "What the page logged since the last navigation, newest first: errors, "
    "warnings, info, and uncaught exceptions with where they were thrown. "
    "?level=error or ?level=warn narrows it; ?pages=2 (up to 5) reaches back "
    "over earlier pages. debug entries are counted, not kept. dropped, when "
    "present, counts events lost in a flood, so some may be missing. Present "
    "only for a workspace opened with open_session(capture=true); otherwise it "
    "says so. Reading this never opens a browser. " + CAPTURE_KEPT + " This is "
    "your own session's; session://{name}/console is another's."
)

NETWORK_DESCRIPTION = (
    "Requests the page made since the last navigation, newest first, each with "
    "an id: method, URL, status, type, size and time. ?filter=failed or "
    "?filter=xhr narrows it; ?pages=2 (up to 5) reaches back. Successful "
    "stylesheets, images, fonts, scripts and media are counted, not kept; "
    "dropped, when present, counts events lost in a flood; unmasked, "
    "credentials too long to mask, which an echo may show. "
    "workspace://network/{id} shows one with its headers; network_body(id) "
    "fetches its response body while the browser lives. Credentials are shown "
    "as \u2022\u2022\u2022. " + CAPTURE_KEPT + " This is your own session's; "
    "session://{name}/network is another's."
)

REQUEST_DESCRIPTION = (
    "One captured request in full: its headers (authorization, cookies and any "
    "secret you typed shown as \u2022\u2022\u2022), status, timing and size. "
    "network_body(id) fetches its response body. This is your own session's; "
    "session://{name}/network/{id} is another's, and network_body(id, "
    "session=name) fetches its body."
)

SITE_DESCRIPTION = (
    "What your session's last save_site_data holds: one entry per site with "
    "counts, never a value; the sites the workspace went to come first. Every "
    "browser this session opens has it back before open_session returns.\n\n"
    "workspace://site-data/{site} shows one site in full; "
    "session://{name}/site-data is another session's. Reading this never "
    "opens a browser."
)

ONE_SITE_DESCRIPTION = (
    "One site's saved data: its cookies, and the localStorage and sessionStorage "
    "of each of its origins. "
    "An httpOnly cookie's value is shown as \u2022\u2022\u2022; nothing else is "
    "hidden. The site is a host from the workspace://site-data listing. This "
    "is your own session's; session://{name}/site-data/{site} is another's."
)


SESSION_DESCRIPTION = (
    "One session of this workspace, by name: the same answer as "
    "workspace://current, for that session instead of yours. default is the "
    "workspace's own; workspace://current's sessions lists the rest. Its "
    "console, network, site data and downloads are session://{name}/console, "
    "/network, /site-data and /downloads. A name with no session is not "
    "found. Reading this never opens a browser."
)

NOT_FOUND = " A name with no session is not found."

SESSION_CONSOLE_DESCRIPTION = (
    "One session's console, by name, as workspace://console reads yours: "
    "?level= and ?pages= narrow it the same, and it needs "
    "open_session(session=name, capture=true)." + NOT_FOUND
)

SESSION_NETWORK_DESCRIPTION = (
    "One session's requests, by name, as workspace://network lists yours, "
    "?filter= and ?pages= included. session://{name}/network/{id} shows one "
    "with its headers; network_body(id, session=name) fetches its response "
    "body while the browser lives." + NOT_FOUND
)

SESSION_REQUEST_DESCRIPTION = (
    "One of a session's captured requests in full, by name, as "
    "workspace://network/{id} shows yours. network_body(id, session=name) "
    "fetches its response body." + NOT_FOUND
)

SESSION_SITE_DESCRIPTION = (
    "What one session's last save_site_data holds, by name, as "
    "workspace://site-data lists yours: counts, never a value. "
    "session://{name}/site-data/{site} shows one site in full. Reading this "
    "never opens a browser." + NOT_FOUND
)

SESSION_ONE_SITE_DESCRIPTION = (
    "One site of a session's saved data, by name, as "
    "workspace://site-data/{site} shows yours; httpOnly values masked. The "
    "site is a host from the session://{name}/site-data listing." + NOT_FOUND
)


def site_listing(
    workspaces: Workspaces, caller: Caller, base: str = site_data.LIST_URI
) -> dict:
    """``workspace://site-data`` of the caller's session: its saved data, and
    its own history for the ordering. Reads the record; never opens a browser.
    ``base`` is the URI it is read at."""
    key = workspaces.reading(caller)
    record = workspaces.store.get(key)
    if record is None:
        return site_data.view({}, base=base)
    return site_data.view(record.site_data, workspaces.history(key), base)


def one_site(
    workspaces: Workspaces, caller: Caller, site: str, base: str = site_data.LIST_URI
) -> dict:
    """``workspace://site-data/{site}``, or a ValueError that says where to look."""
    record = workspaces.store.get(workspaces.reading(caller))
    found = site_data.site_view(record.site_data if record else {}, site, base)
    if found is None:
        raise ValueError(
            f"no saved data for {site!r}: {site_data.SITE_LISTINGS}"
        )
    return found
