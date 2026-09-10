"""A session's files, offered every way a client might be able to take them.

The files themselves are the Grid's — see ``Grid.files``. This module is only
about presentation, and it offers three renderings of the same list because
clients differ in what they can accept:

- ``session://files`` — a resource, which is what a listing *is*: state to read,
  not an action to perform, and free for a client to pull into context.
- ``session://files/{name}`` — one file, as bytes, with its real media type. A
  client that reads resources can therefore display a screenshot without a URL,
  a token, or a round trip through the model.
- ``session_files`` — a tool returning the same listing, for clients with no
  notion of resources at all. It carries an app config, so a host that can
  render UI draws the file grid instead of printing JSON.

The tool is hidden from clients that read resources, exactly as the session
status is — unless the client can also render apps, in which case the tool is
the only way it gets one, and hiding it would trade a picture for a duplicate.

Everything carries a signed URL as well, because the most common destination is
somewhere that can do none of the above: a chat transcript that renders markdown
images, a link pasted to a colleague, an ``<img>`` on the admin page.
"""

from __future__ import annotations

import logging

from . import admin, links
from .hints import reads

log = logging.getLogger(__name__)

LIST_URI = "session://files"
FILE_URI = "session://files/{name}"
FILES_TOOL = "session_files"

DESCRIPTION = (
    "The files this browsing session has produced: everything downloaded from "
    "a site, plus anything kept with screenshot(save=True) or save_pdf.\n\n"
    "Each entry has a URL that opens in a browser for a while, so an image can "
    "be shown to someone rather than described to them."
)


def listing(actions, sessions, token, session_id=None, base="") -> dict:
    """The file list for a session, resolved the same way for every surface."""
    target = session_id or sessions.describe().get("session_id")
    if not target:
        raise ValueError(
            "session_id is required: this server is not holding one for you"
        )
    entries = actions.grid.files(target)
    files = []
    for entry in entries:
        described = admin.describe(target, entry, token)
        # An app renders on a sandbox origin of the host's choosing, so a path
        # would resolve against the wrong server. Absolute only when the server
        # has been told what it is reachable at.
        if base:
            described["absolute_url"] = links.file_url(
                target, entry.get("name", ""), token, base=base
            )
        files.append(described)
    return {
        "component": "fileGrid",
        "session_id": target,
        "count": len(entries),
        "files": files,
    }


def register(mcp, actions, sessions, token, app_config=None, base="") -> set[str]:
    """Register the two resources and the mirroring tool. Returns tool names."""

    @mcp.resource(LIST_URI, description=DESCRIPTION, mime_type="application/json")
    def files_resource() -> dict:
        return listing(actions, sessions, token, base=base)

    @mcp.resource(
        FILE_URI,
        description=(
            "One file from this session, as bytes. The name comes from the "
            f"{LIST_URI} listing, which also carries each file's real media "
            "type — a template declares one type for every file it serves, so "
            "this is deliberately the generic one."
        ),
        mime_type="application/octet-stream",
    )
    def file_resource(name: str) -> bytes:
        """The file itself.

        Returned as bytes with its real media type rather than as a link, so a
        client that reads resources needs nothing else to display it.
        """
        target = sessions.describe().get("session_id")
        if not target:
            raise ValueError("no session is being held for you")
        return actions.grid.read_file(target, name)

    @mcp.tool(
        name=FILES_TOOL,
        description=DESCRIPTION,
        app=app_config,
        annotations=reads("Files this session has downloaded"),
    )
    def session_files(session_id: str | None = None) -> dict:
        return listing(actions, sessions, token, session_id=session_id, base=base)

    return {FILES_TOOL}
