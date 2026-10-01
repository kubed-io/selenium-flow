"""The session status, as a resource.

It is state to read, not an action to perform, so a client can pull it into
context without spending a tool call. A client that cannot read resources reads
it through `mirror.read_resource`, at the same URI.
"""

from __future__ import annotations

from fastmcp import FastMCP

from ..core import site_data
from ..session.sessions import SessionManager

RESOURCE_URI = "session://current"

DESCRIPTION = (
    "The browser session this client is currently using.\n\n"
    "Returns the session name, which browser it is, the page it is on, the "
    "window size as WxH, whether it is inside a frame, and whether a browser is "
    "currently open (live). Read it before judging anything about layout — the "
    "window is not a fixed size and is what decides whether something is "
    "off-screen.\n\n"
    "Reading this never opens a browser: live is false when none is held yet."
)


def register(mcp: FastMCP, sessions: SessionManager) -> None:
    """Register the session status resource."""

    @mcp.resource(
        RESOURCE_URI,
        name="Current Session",
        description=DESCRIPTION,
        mime_type="application/json",
    )
    def current_session_resource() -> dict:
        return sessions.describe()

    @mcp.resource(
        site_data.LIST_URI,
        name="Site Data",
        description=SITE_DESCRIPTION,
        mime_type="application/json",
    )
    def site_data_resource() -> dict:
        return site_listing(sessions, sessions.name())

    @mcp.resource(
        f"{site_data.LIST_URI}/{{site}}",
        name="Site Data for One Site",
        description=ONE_SITE_DESCRIPTION,
        mime_type="application/json",
    )
    def one_site_resource(site: str) -> dict:
        return one_site(sessions, sessions.name(), site)


SITE_DESCRIPTION = (
    "The sites this session has saved cookies or storage for: one entry per "
    "site with counts, never a value. Saved by save_site_data, and handed back "
    "to every browser this session opens.\n\n"
    "session://site-data/{site} shows one site in full. Reading this never "
    "opens a browser."
)

ONE_SITE_DESCRIPTION = (
    "One site's saved data: its cookies and its localStorage and sessionStorage. "
    "An httpOnly cookie's value is shown as \u2022\u2022\u2022; nothing else is "
    "hidden. The site is a host from the session://site-data listing."
)


def _saved(sessions: SessionManager, name: str) -> dict:
    record = sessions.store.get(name)
    return record.site_data if record else {}


def site_listing(sessions: SessionManager, name: str) -> dict:
    """``session://site-data``. Reads the record; never opens a browser."""
    return site_data.view(_saved(sessions, name))


def one_site(sessions: SessionManager, name: str, site: str) -> dict:
    """``session://site-data/{site}``, or a ValueError that says where to look."""
    found = site_data.site_view(_saved(sessions, name), site)
    if found is None:
        raise ValueError(f"no saved data for {site!r}: {site_data.LIST_URI} lists them")
    return found
