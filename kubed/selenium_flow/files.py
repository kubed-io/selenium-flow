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
  ours, under ``FLOW_DATA_DIR``, so they can be deleted one at a time.

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
import mimetypes

from starlette.requests import Request
from starlette.responses import JSONResponse

from . import auth, errors, flows, links
from .hints import hints, reads

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

IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")

OFF = (
    "keeping files is not enabled on this server: it was started with no "
    "FLOW_DATA_DIR, so there is nowhere to keep them"
)

DESCRIPTION = (
    "Every file this browsing session has: what the site downloaded, what "
    "screenshot(save=True) and save_pdf saved, and anything kept with "
    "keep_file.\n\n"
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
    if base:
        # An app renders on a sandbox origin of the host's choosing, so a path
        # would resolve against the wrong server. Absolute only when the server
        # has been told what it is reachable at.
        described["absolute_url"] = base.rstrip("/") + url
    return described


def describe(session_id: str, entry: dict, token: str | None, base: str = "") -> dict:
    """One of a browser's downloads."""
    name = entry.get("name", "")
    return _described(
        name, entry, links.file_url(session_id, name, token), False, base
    )


def describe_kept(session: str, entry: dict, token: str | None, base: str = "") -> dict:
    """One file kept beyond the browser that produced it."""
    name = entry.get("name", "")
    return _described(name, entry, links.kept_url(session, name, token), True, base)


def merged(
    actions,
    store,
    session: str,
    session_id: str,
    token: str | None,
    base: str = "",
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
    """
    entries: dict[str, dict] = {}
    if session_id:
        for entry in actions.grid.files(session_id):
            entries[entry.get("name", "")] = describe(session_id, entry, token, base)
    if store is not None and session:
        for entry in store.files(session):
            entries[entry["name"]] = describe_kept(session, entry, token, base)
    return sorted(entries.values(), key=lambda f: f.get("created") or 0, reverse=True)


def owner(sessions, store, explicit: str | None = None) -> str:
    """The session name whose kept files these are, or "" when keeping is off.

    Asked through ``flows.session_of`` rather than worked out here, because a
    file and a flow must land in the same session directory — see that function
    for why an unusable name is refused rather than quietly shared.
    """
    if store is None:
        return ""
    return flows.session_of(sessions, explicit)


def listing(
    actions,
    sessions,
    store,
    token,
    session_id=None,
    base="",
    session: str | None = None,
) -> dict:
    """The file list for a session, resolved the same way for every surface.

    **This keeps working after the browser is gone**, returning the kept files
    alone. That is the point of keeping one: a listing that emptied when the
    Grid reaped a browser would make the durable half look lost.
    """
    status = sessions.describe()
    # A reaped browser keeps its id in the session record until something
    # refreshes it, and `describe` reports that id alongside `live: false`.
    # Trusting it would dial the Grid for a browser that is gone and fail the
    # whole listing — in precisely the state kept files exist to survive. An
    # explicitly passed id is still trusted: that caller owns it.
    remembered = status.get("session_id") if status.get("live") else None
    target = session_id or remembered or ""
    owned = owner(sessions, store, session)
    if not target and store is None:
        raise ValueError(
            "session_id is required: this server is not holding one for you"
        )
    files = merged(actions, store, owned, target, token, base)
    return {
        "component": "fileGrid",
        "session_id": target or None,
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
    prefix: str = "/files",
) -> set[str]:
    """Register the resources, the mirroring tool, and the file actions.

    Returns the mirror tool names. ``keep_file`` and ``delete_file`` are not
    mirrors — they are capabilities with no resource behind them — so they stay
    visible to every client.
    """

    @mcp.resource(LIST_URI, description=DESCRIPTION, mime_type="application/json")
    def files_resource() -> dict:
        return listing(actions, sessions, store, token, base=base)

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
        client that reads resources needs nothing else to display it. A kept
        file is served from our own store, so this answers after the browser
        that produced it has gone.
        """
        wanted = flows.valid_file_name(name)
        session = owner(sessions, store)
        if store is not None and session:
            try:
                return store.read_file(session, wanted)
            except FileNotFoundError:
                pass
        target = sessions.describe().get("session_id")
        if not target:
            raise ValueError("no session is being held for you")
        return actions.grid.read_file(target, wanted)

    @mcp.tool(
        name=FILES_TOOL,
        description=DESCRIPTION,
        app=app_config,
        annotations=reads("Files this session has"),
    )
    def session_files(session_id: str | None = None) -> dict:
        # The mode rule every other tool enforces through `sessions.resolve`,
        # applied directly because this must not call it: `resolve` OPENS a
        # browser when the record has none, and a listing that opened one would
        # be the leak the status resource already refuses to be.
        #
        # `ShapeSessionId` hides this argument in saved mode, but that is
        # presentation: a crafted call could still name another caller's browser
        # and be handed its downloads, with signed URLs for each.
        if session_id and sessions.mode() == sessions.SAVED:
            raise ValueError(
                "do not pass session_id: this server is holding a browser for "
                "you, and its files are the ones you get. Omit it."
            )
        return listing(
            actions, sessions, store, token, session_id=session_id, base=base
        )

    @mcp.tool(
        name=KEEP_TOOL,
        description=(
            "Keep one of this session's files beyond the browser that made "
            "it.\n\n"
            "Downloads belong to the browser and the Grid deletes them with it, "
            "including when you switch browser. Keeping copies the file to the "
            "server, where it survives — and where upload_file(path=...) can "
            "attach it to a page in a later session.\n\n"
            "Takes the name exactly as session_files lists it. Keeping a name "
            "that is already kept replaces it, so this is safe to repeat. The "
            "original download stays where it is: the Grid offers no way to "
            "remove a single file."
        ),
        annotations=hints("Keep a file beyond the browser", idempotent=True),
    )
    def keep_file(name: str, session_id: str | None = None) -> dict:
        resolved = sessions.resolve(sessions.key(), session_id)
        return keep_one(actions, store, owner(sessions, store), resolved, name)

    _routes(mcp, actions, sessions, store, token, base, prefix)
    return {FILES_TOOL}


def _routes(mcp, actions, sessions, store, token, base, prefix) -> None:
    """The same operations as plain JSON, for callers that are not MCP.

    Their own route table under ``/files``, for the reason ``/flows`` has one:
    these are not browser actions, and counting them as such would break the
    one-to-one promise ``test_surfaces.py`` guards over those.
    """

    async def handle(request: Request, what: str) -> JSONResponse:
        if not auth.authorized(request, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - an empty body is fine for list
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "body must be a JSON object"}, status_code=400
            )
        try:
            session_id = body.get("session_id") or ""
            session = owner(sessions, store, body.get("session"))
            if what == "list":
                return JSONResponse(
                    listing(
                        actions,
                        sessions,
                        store,
                        token,
                        session_id=session_id,
                        base=base,
                        session=body.get("session"),
                    )
                )
            name = body.get("name")
            if not name:
                raise ValueError("name is required")
            return JSONResponse(keep_one(actions, store, session, session_id, name))
        except Exception as exc:  # errors.py decides what it means
            status = errors.status_for(exc)
            text = errors.message(exc)
            if status == 500:
                # Only the status we do not understand earns a traceback. A 503
                # is a known condition — the Grid is unreachable or refusing —
                # and its frames carry the exception text, which for a Grid
                # refusal is where the Grid URL lives.
                log.exception("files/%s failed", what)
            elif status > 500:
                log.warning("files/%s unavailable (%s): %s", what, status, text)
            else:
                log.info("files/%s refused (%s): %s", what, status, text)
            return JSONResponse({"error": text}, status_code=status)

    for path in FILE_ENDPOINTS:
        _bind(mcp, prefix, path, handle)


def _bind(mcp, prefix, path, handle) -> None:
    @mcp.custom_route(f"{prefix}/{path}", methods=["POST"], name=f"files_{path}")
    async def route(request: Request) -> JSONResponse:
        return await handle(request, path)
