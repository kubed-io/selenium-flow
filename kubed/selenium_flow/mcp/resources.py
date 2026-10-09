"""The workspace status, as a resource.

It is state to read, not an action to perform, so a client can pull it into
context without spending a tool call. A client that cannot read resources reads
it through `mirror.read_resource`, at the same URI.
"""

from __future__ import annotations

from fastmcp import FastMCP

from ..site_data import snapshot as site_data
from ..workspace.workspaces import Workspaces
from . import clients

RESOURCE_URI = "workspace://current"

DESCRIPTION = (
    "The workspace this client named, and the session (browser) open in it.\n\n"
    "Returns the workspace name, which browser it is, the page it is on, the "
    "window size as WxH, whether it is inside a frame, and whether a session is "
    "currently open (live). Read it before judging anything about layout — the "
    "window is not a fixed size and is what decides whether something is "
    "off-screen.\n\n"
    "grid_timeout is how many seconds the Grid lets a session in this "
    "workspace sit idle before it ends it; every call starts that clock "
    "again.\n\n"
    "Reading this never opens a browser: live is false when no session is open."
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
        site_data.LIST_URI,
        name="Site Data",
        description=SITE_DESCRIPTION,
        mime_type="application/json",
    )
    def site_data_resource() -> dict:
        return site_listing(workspaces, clients.caller().name)

    @mcp.resource(
        f"{site_data.LIST_URI}/{{site}}",
        name="Site Data for One Site",
        description=ONE_SITE_DESCRIPTION,
        mime_type="application/json",
    )
    def one_site_resource(site: str) -> dict:
        return one_site(workspaces, clients.caller().name, site)


SITE_DESCRIPTION = (
    "What this workspace's last save_site_data holds: one entry per site with "
    "counts, never a value; the sites the workspace went to come first. Every "
    "browser this workspace opens has it back before open_session returns.\n\n"
    "workspace://site-data/{site} shows one site in full. Reading this never "
    "opens a browser."
)

ONE_SITE_DESCRIPTION = (
    "One site's saved data: its cookies, and the localStorage and sessionStorage "
    "of each of its origins. "
    "An httpOnly cookie's value is shown as \u2022\u2022\u2022; nothing else is "
    "hidden. The site is a host from the workspace://site-data listing."
)


def site_listing(workspaces: Workspaces, name: str) -> dict:
    """``workspace://site-data``. Reads the record; never opens a browser."""
    record = workspaces.store.get(name)
    if record is None:
        return site_data.view({})
    return site_data.view(record.site_data, record.history)


def one_site(workspaces: Workspaces, name: str, site: str) -> dict:
    """``workspace://site-data/{site}``, or a ValueError that says where to look."""
    record = workspaces.store.get(name)
    found = site_data.site_view(record.site_data if record else {}, site)
    if found is None:
        raise ValueError(f"no saved data for {site!r}: {site_data.LIST_URI} lists them")
    return found
