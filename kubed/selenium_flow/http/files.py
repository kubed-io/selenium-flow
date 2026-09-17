"""A session's files: the browser's downloads, plus the ones kept beyond it.

**One list, with a property saying which is which** (§F1.10). The first draft of
this feature had two listings — Grid-backed ephemeral files and disk-backed kept
ones — on the grounds that different lifetimes deserve different surfaces. That
was wrong in the way that adds work: it makes the caller ask two questions and
join the answers.

The two halves really are different things underneath, and the difference is
worth knowing because it decides what may be done to each:

- **Downloads belong to the browser.** They live in the Grid's per-session store,
  which is created with the browser and deleted with it. The Grid's API over
  that store is *list, read-one, delete-all* — there is no write and no per-file
  delete — so those are the only operations offered for them.
- **Kept files belong to the session**, which outlives any browser. They are
  ours, under ``FLOW_DATA_DIR``, so they can be deleted one at a time. Every
  screenshot and print is kept from the start: those bytes are this server's,
  and handing them to the browser as a download only to copy them back ran into
  every download Chrome refuses (§F3.8).

Everything follows from that asymmetry. **Keeping is a copy, never a move**,
because one file cannot be removed from the Grid's store. Which in turn makes
*clearing the downloads* safe rather than dangerous — the objection that would
condemn such a button, that it takes the kept files with it, cannot happen when
kept files are somewhere else by definition. And there is **no unpin**: it would
only be coherent while the browser still held the original, and after that it is
indistinguishable from a delete.

**Keeping is one-way for an agent, deliberately.** There is no `delete_file`
tool. An agent has no real need to reclaim disk — it is not the thing that runs
out of it — and a one-way verb is a simpler promise than a reversible one whose
meaning depends on whether a browser still exists. Removing a kept file is an
*operator* action, through the admin UI, where a person can see what they are
deleting. Nothing collects kept files on their own (§F1.10), so that button is
the only thing that reclaims the space, which is a decision rather than an
oversight.

The listing is offered three ways, because clients differ in what they accept:

- ``session://files`` — a resource, which is what a listing *is*: state to read,
  not an action to perform, and free for a client to pull into context.
- ``session://files/{name}`` — one file, as bytes, with its real media type. A
  client that reads resources can therefore display a screenshot without a URL,
  a token, or a round trip through the model.
- ``session_files`` — a tool returning the same listing, carrying an app config,
  so a host that renders MCP Apps draws the file grid. It is listed only to such
  a host; every other client reads ``session://files``, directly or through
  ``read_resource`` (§F3.6).

Everything carries a signed URL as well, because the most common destination is
somewhere that can do none of the above: a chat transcript that renders markdown
images, a link pasted to a colleague, an ``<img>`` on the admin page.
"""

from __future__ import annotations

import json
import logging
import mimetypes

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..flows import library as flows
from ..mcp.annotations import hints, reads
from . import answer as answer_module
from . import links

log = logging.getLogger(__name__)

LIST_URI = "session://files"
FILE_URI = "session://files/{name}"
FILES_TOOL = "session_files"
KEEP_TOOL = "keep_file"

# Path -> what it does. Its own table, like flowapi's: these are not browser
# actions and must not be counted as though they were.
#
# There is deliberately no `clear` or `delete` here. Both are real capabilities
# and neither has an MCP tool, so the endpoint alone would be exactly the
# one-sided capability this project forbids. The admin UI reaches both —
# DELETE /admin/sessions/{key}/files and .../files/{name} — which is an operator
# surface rather than a caller's, and is where deleting anything belongs.
FILE_ENDPOINTS = ("list", "keep")

# The REST shape of each: method, and the path under the /files prefix. Read by
# the routes and by the published spec, so the two cannot disagree (§F2.13).
FILE_ROUTES = {"list": ("get", ""), "keep": ("put", "/{name}/kept")}

IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")

OFF = (
    "keeping files is not enabled on this server: it was started with no "
    "FLOW_DATA_DIR, so there is nowhere to keep them"
)

DESCRIPTION = (
    "Every file this browsing session has: what the site downloaded, what "
    "screenshot and print saved, and anything kept with keep_file.\n\n"
    "Each entry says whether it is kept. A file that is not kept belongs to the "
    "browser and goes when the browser does; a kept one belongs to the session "
    "and outlives it.\n\n"
    "Each entry has a URL that opens in a browser for a while, so an image can "
    "be shown to someone rather than described to them."
)


def content_type(name: str) -> str:
    """The type to serve a stored file as, guessed from its name."""
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _described(name: str, entry: dict, url: str, kept: bool, base: str) -> dict:
    """One file, as every surface needs it: named, sized, fetchable, and placed.

    One builder for both halves of the list so they cannot describe a file
    differently — the whole point of merging them is that a caller reads one
    shape.
    """
    kind = content_type(name)
    described = {
        "name": name,
        "size": entry.get("size", 0),
        "created": entry.get("creationTime"),
        "content_type": kind,
        "image": kind in IMAGE_TYPES,
        "kept": kept,
        "url": url,
    }
    if not kept:
        # The call that makes this one durable, spelled out. The tool
        # description has said "these die with the browser" since #28 and a
        # pilot still handed somebody a link that would stop working - because
        # what it read was the result, not the docstring. So the result says it
        # too, in the only form that is also an instruction (§F2.10).
        #
        # json.dumps for the argument, not an f-string: `FILE_NAME` permits a
        # double quote, and a browser will happily save `Q4 "final".csv` - which
        # rendered as a call nobody could paste (Copilot, #31).
        described["keep_with"] = f"keep_file({json.dumps(name)})"
    if base:
        # An app renders on a sandbox origin of the host's choosing, so a path
        # would resolve against the wrong server. Absolute only when the server
        # has been told what it is reachable at.
        described["absolute_url"] = base.rstrip("/") + url
    return described


def describe(
    session_id: str, entry: dict, token: str | None, base: str = "", mount: str = ""
) -> dict:
    """One of a browser's downloads, at the path this server serves it on."""
    name = entry.get("name", "")
    return _described(
        name, entry, links.file_url(session_id, name, token, mount), False, base
    )


def describe_kept(
    session: str, entry: dict, token: str | None, base: str = "", mount: str = ""
) -> dict:
    """One file kept beyond the browser that produced it."""
    name = entry.get("name", "")
    url = links.kept_url(session, name, token, mount)
    return _described(name, entry, url, True, base)


def merged(
    actions,
    store,
    session: str,
    session_id: str,
    token: str | None,
    base: str = "",
    downloads: list[dict] | None = None,
    mount: str = "",
) -> list[dict]:
    """Both halves of a session's files as one list, newest first.

    **A name can exist in both places** — the same ``report.pdf`` downloaded
    twice, or kept and then downloaded again. The kept one wins, because it is
    the one that will still be there, and it is the only one with a per-file
    delete.

    A Grid failure is *not* swallowed here. The browser being gone is the
    ordinary case and costs no call at all — ``session_id`` is empty and the
    kept half is returned alone — so an error from a browser we were told is
    live is a real fault, and hiding it behind a short list would make a broken
    Grid look like an empty session.

    ``downloads`` is the Grid's own listing when the caller already has it. The
    admin does, because it has to report what "clear" would remove and this
    merge is precisely where that answer stops being recoverable — a download
    shadowed by a kept file of the same name is gone from the result and still
    very much on the Grid.
    """
    entries: dict[str, dict] = {}
    if downloads is None:
        downloads = actions.grid.files(session_id) if session_id else []
    if session_id:
        for entry in downloads:
            entries[entry.get("name", "")] = describe(
                session_id, entry, token, base, mount
            )
    if store is not None and session:
        for entry in store.files(session):
            entries[entry["name"]] = describe_kept(session, entry, token, base, mount)
    return sorted(entries.values(), key=lambda f: f.get("created") or 0, reverse=True)


def owner(store, name: str) -> str:
    """The session name whose kept files these are, or "" when keeping is off.

    The name is also the flow library's directory: a file and a flow land in
    the same place because they are the same session.
    """
    return name if store is not None else ""


def listing(
    actions,
    sessions,
    store,
    token,
    name: str,
    base="",
    mount: str = "",
) -> dict:
    """The file list for a session, resolved the same way for every surface.

    **This keeps working after the browser is gone**, returning the kept files
    alone. That is the point of keeping one: a listing that emptied when the
    Grid reaped a browser would make the durable half look lost.
    """
    owned = owner(store, name)
    # Never `resolve`: that opens a browser when the record has none, and a
    # listing that opened one would be the leak the status resource refuses to
    # be. `browser` answers "" instead, and the kept files still list.
    target = sessions.browser(name)
    if not target and store is None:
        raise ValueError(
            "session_id is required: this server is not holding one for you"
        )
    files = merged(actions, store, owned, target, token, base, mount=mount)
    return {
        # No `session_id`: the Grid's browser id is how a browser is reached and
        # not part of what a caller is told (E18, and Copilot again on #35).
        "component": "fileGrid",
        "session": owned or None,
        "count": len(files),
        "files": files,
    }


def keep_one(actions, store, session: str, session_id: str, name: str) -> dict:
    """Copy one of the browser's downloads into the session's own store.

    A copy rather than a move, and not by choice: the Grid offers no way to
    delete one file, so the original stays until the browser ends or the
    downloads are cleared (§F1.10).
    """
    if store is None:
        raise ValueError(OFF)
    if not session_id:
        raise ValueError("session_id is required: the file is read from a browser")
    # Refused before the Grid is dialled, so an unusable name costs nothing and
    # says what was wrong with it rather than surfacing as a download failure.
    wanted = flows.valid_file_name(name)
    data = actions.grid.read_file(session_id, wanted)
    entry = store.write_file(session, wanted, data)
    log.info("kept %s/%s (%s bytes)", session, wanted, len(data))
    return {"kept": True, "session": session, **entry}


def keep_made(
    sessions, store, name: str, data: bytes, token, base: str = "", mount: str = ""
) -> dict:
    """Keep bytes this server made — a screenshot, a print — for the caller.

    Never a replacement: a second `screenshot.png` lands as
    `screenshot (1).png`, the way a browser names a second download, because
    overwriting would silently take away a file somebody was handed a link to.
    The name that was used comes back, and it is the one to pass on.
    """
    if store is None:
        raise ValueError(OFF)
    session = owner(store, sessions.name())
    wanted = flows.valid_file_name(name)
    taken = {entry["name"] for entry in store.files(session)}
    stem, dot, suffix = wanted.rpartition(".")
    if not dot or not stem:
        stem, suffix = wanted, ""
    free, n = wanted, 0
    while free in taken:
        n += 1
        free = f"{stem} ({n}).{suffix}" if suffix else f"{stem} ({n})"
    entry = store.write_file(session, free, data)
    return describe_kept(session, entry, token, base, mount)


def read_kept(sessions, store, name: str, session: str | None = None) -> bytes:
    """The bytes of one kept file, for a caller that wants to send it somewhere.

    The other half of `keep_one`, and the reason it exists: a browser could
    download a file and keep it, and there was no way to hand it back to a page
    (§F1.41). `upload_file(kept=...)` is that way, and it reads through here so
    that "which session's files are these" has exactly one answer.

    ``session`` names the library explicitly, which is how a **flow** says it:
    the run supplies the library it was loaded from, so a flow in the shared
    library uploading a kept file reads it from the session that is running,
    not from `global` (Copilot, #31). Omitted, the caller's own name answers.
    """
    if store is None:
        raise ValueError(OFF)
    wanted = flows.valid_file_name(name)
    session = owner(store, session or sessions.name())
    try:
        return store.read_file(session, wanted)
    except FileNotFoundError as exc:
        # A ValueError, not the FileNotFoundError this came from, and both
        # halves of that are deliberate. The message is named rather than
        # passed through, because `[Errno 2] ... /data/flows/x/files/y` answers
        # a question about this server's disk when the caller's question is
        # which name to use. And the TYPE is the caller-error one, because
        # `errors.status_for` does not classify FileNotFoundError and would
        # call this a 500 - telling an n8n node with Retry-On-Fail to send the
        # same wrong name again, and an alert on the 5xx rate to count it as an
        # outage. `upload_file(path=...)` already answers 400 for exactly this
        # (`no file at ...`), and the sibling source must not disagree.
        raise ValueError(
            f"no kept file called {wanted!r}. session://files lists what is kept; "
            "keep_file(name) is what keeps one before the browser goes"
        ) from exc


def delete_one(store, session: str, name: str) -> dict:
    """Remove one kept file. **Operator-only** — there is no tool for this.

    Only a kept file: a download belongs to the browser, and the Grid's store
    has no per-file delete to offer. Clearing empties that store wholesale,
    which is safe precisely because keeping is a copy.
    """
    if store is None:
        raise ValueError(OFF)
    removed = store.delete_file(session, flows.valid_file_name(name))
    return {"deleted": removed, "session": session, "name": name}


def register(
    mcp,
    actions,
    sessions,
    store,
    token,
    app_config=None,
    base="",
    prefix: str = "",
) -> set[str]:
    """Register the resources, the mirroring tool, and the file actions.

    Returns the mirror tool names. ``keep_file`` and ``delete_file`` are not
    mirrors — they are capabilities with no resource behind them — so they stay
    visible to every client.
    """

    @mcp.resource(
        LIST_URI,
        name="Session Files",
        description=DESCRIPTION,
        mime_type="application/json",
    )
    def files_resource() -> dict:
        return listing(
            actions, sessions, store, token, sessions.name(), base=base, mount=prefix
        )

    @mcp.resource(
        FILE_URI,
        name="Session File",
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
        client that reads resources needs nothing else to display it. A kept
        file is served from our own store, so this answers after the browser
        that produced it has gone.
        """
        wanted = flows.valid_file_name(name)
        session = owner(store, sessions.name())
        if store is not None and session:
            try:
                return store.read_file(session, wanted)
            except FileNotFoundError:
                pass
        target = sessions.browser(sessions.name())
        if not target:
            raise ValueError("no session is being held for you")
        return actions.grid.read_file(target, wanted)

    @mcp.tool(
        name=FILES_TOOL,
        description=(
            "Show every file this session has: downloads, saved screenshots and "
            "PDFs, and kept files, each marked kept or not, with a link that "
            "opens in a browser. A file that is not kept goes with the browser."
        ),
        app=app_config,
        annotations=reads("Files this session has"),
    )
    def session_files() -> dict:
        return listing(
            actions, sessions, store, token, sessions.name(), base=base, mount=prefix
        )

    @mcp.tool(
        name=KEEP_TOOL,
        description=(
            "Keep one of this session's files, so it survives the browser "
            "ending, switching or being reaped. A download otherwise goes with "
            "the browser.\n\n"
            "name is as session://files lists it. Keeping it again replaces the "
            "kept copy, so this is safe to repeat. upload_file(kept=name) puts "
            "a kept file back into a page."
        ),
        annotations=hints("Keep a file beyond the browser", idempotent=True),
    )
    def keep_file(name: str) -> dict:
        session = sessions.name()
        return keep_one(
            actions, store, owner(store, session), sessions.resolve(session), name
        )

    _routes(mcp, actions, sessions, store, token, base, prefix)
    return {FILES_TOOL}


def _routes(mcp, actions, sessions, store, token, base, prefix) -> None:
    """The same operations as REST, for callers that are not MCP.

    Their own tree under ``/files``, for the reason ``/flows`` has one: these
    are not browser actions, and counting them as such would break the
    one-to-one promise ``test_surfaces.py`` guards over those.

    A file belongs to a session, so both of these name one the way everything
    else does — a header or ``?session=`` — and neither takes an id.
    """

    files_root = f"{prefix}/files"

    async def answer(request: Request, what: str, call) -> JSONResponse:
        """One request, answered the way every other tree answers one."""
        return await answer_module.answer(request, token, f"files/{what}", call, log)

    @mcp.custom_route(files_root, methods=["GET"], name="files_list")
    async def list_files(request: Request) -> JSONResponse:
        """Every file this session has: the browser's downloads and its kept
        files, still answering after the browser is gone."""
        return await answer(
            request,
            "list",
            lambda name, _body: listing(
                actions, sessions, store, token, name, base=base, mount=prefix
            ),
        )

    @mcp.custom_route(files_root + "/{name}/kept", methods=["PUT"], name="files_keep")
    async def keep(request: Request) -> JSONResponse:
        """Keep one download beyond the browser that made it. A PUT because
        keeping a name that is already kept replaces it."""
        return await answer(
            request,
            "keep",
            lambda name, _body: keep_one(
                actions,
                store,
                owner(store, name),
                sessions.resolve(name),
                request.path_params["name"],
            ),
        )
