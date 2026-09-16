"""The session status, as a resource.

It is state to read, not an action to perform, so a client can pull it into
context without spending a tool call. A client that cannot read resources reads
it through `mirror.read_resource`, at the same URI.
"""

from __future__ import annotations

from fastmcp import FastMCP

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
