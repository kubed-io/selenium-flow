"""A session's files as three sections, addressed by path (§F4.6, §F4.7).

**Downloads, Screenshots and Files never merge into one list.** The first
draft of this feature merged everything so a caller only had to ask once
(§F1.10); that ruling is superseded, and for the same reason it existed — a
folder that lists its own sub-folders costs a caller nothing it does not want,
and ``session://files/screenshots/{name}`` is a shape every model already
knows. In a path, the section is part of the address, so a name only has to
be unique inside its own folder, and there is no ``section=`` argument on
``keep_file`` for a name that lives in two places at once.

The three sections still divide along the line §F1.10 drew, because the line
was right even though the merge built on top of it was not:

- **Downloads belong to the browser.** They live in the Grid's per-session
  store, created with the browser and deleted with it. The Grid's API over
  that store is *list, read-one, delete-all* — there is no write and no
  per-file delete — so those are the only operations offered for them.
- **Screenshots and Files belong to the session**, which outlives any
  browser. They are ours, under ``FLOW_DATA_DIR``. Screenshots pile up until
  kept or cleared in bulk; Files holds only what somebody chose to keep, or a
  print, and gets no bulk delete because everything in it is there on purpose
  (§F4.1).

**Keeping a screenshot is a move; keeping a download is a copy.** A
screenshot already lives in our own directory, so keeping one can simply take
it out of ``screenshots/`` and into ``files/``. A download cannot move the
same way: the Grid offers no way to delete one file, so the bytes are copied
and the original stays on the Grid until the browser ends or the downloads
are cleared (§F1.10). A URI that already names a file in Files answers with
that file, unchanged.

**Keeping is one-way for an agent, deliberately.** There is no ``delete_file``
tool, in either direction. An agent has no real need to reclaim disk — it is
not the thing that runs out of it — and a one-way verb is a simpler promise
than a reversible one whose meaning depends on whether a browser still
exists. Removing a file, or clearing a section in bulk, is an *operator*
action through the admin UI, where a person can see what they are deleting.

**A listing never opens a browser.** ``root``, ``folder`` and ``sections`` all
read the session's record with ``sessions.browser(name)``, never
``sessions.resolve``: ``resolve`` would start a browser to answer a question
about files, and a listing that did that would be the leak the status
resource already refuses to be. With no browser attached, Downloads answers
empty rather than failing, because that is the ordinary case and costs no
Grid call at all — which is also why a genuine failure from a browser the
record says is live is never swallowed.

``screenshots`` and ``downloads`` are **reserved names inside Files**:
without that rule, a file arriving there under either name could never be
addressed by ``session://files/{name}``, since those two path segments
already mean the folders. One arriving under a reserved name lands as
``screenshots (1)`` or ``downloads (1)``, the same rule every other name
clash in a folder follows.

Every address is offered three ways, because clients differ in what they
accept: a resource (state to read, free for a client to pull into context), a
file's bytes directly (so a client that reads resources can display a
screenshot with no URL, no token, and no round trip through the model), and
``session_files``, a tool that returns all three sections at once for a host
that renders MCP Apps. Everything also carries a signed URL, because the most
common destination is somewhere that can do none of the above: a chat
transcript that renders markdown images, a link pasted to a colleague, an
``<img>`` on the admin page.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from urllib.parse import quote, unquote

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..flows import library as flows
from ..mcp.annotations import hints, reads
from . import answer as answer_module
from . import links

log = logging.getLogger(__name__)

FILES, SCREENSHOTS, DOWNLOADS = "files", "screenshots", "downloads"
RESERVED = frozenset({SCREENSHOTS, DOWNLOADS})
ROOT_URI = "session://files"
FILE_URI = "session://files/{name}"
FOLDER_URI = {
    SCREENSHOTS: f"{ROOT_URI}/{SCREENSHOTS}",
    DOWNLOADS: f"{ROOT_URI}/{DOWNLOADS}",
}
ITEM_URI = {k: v + "/{name}" for k, v in FOLDER_URI.items()}
FILES_TOOL = "session_files"
KEEP_TOOL = "keep_file"

# The root listing's resource URI, named for the call sites that read like a
# listing rather than an address: `register`'s own resource, and the spec's
# `x-mcp-resource` for the `list` operation.
LIST_URI = ROOT_URI

SHAPES = (
    "session://files/<name> for a file in Files, "
    "session://files/screenshots/<name> for a screenshot, or "
    "session://files/downloads/<name> for a download"
)

# Path -> what it does. Its own table, like flowapi's: these are not browser
# actions and must not be counted as though they were.
#
# There is deliberately no `clear` or `delete` here. Both are real capabilities
# and neither has an MCP tool, so the endpoint alone would be exactly the
# one-sided capability this project forbids. The admin UI reaches both —
# DELETE /admin/sessions/{key}/files/downloads, .../files/screenshots and
# .../files/{name} — which is an operator surface rather than a caller's, and
# is where deleting anything belongs.
FILE_ENDPOINTS = ("list", "screenshots", "downloads", "keep")

# The REST shape of each: method, and the path under the /files prefix. Read by
# the routes and by the published spec, so the two cannot disagree (§F2.13).
# `keep` names the folder in the path, because keeping is only ever offered for
# the two folders a file can be kept OUT of — a Files URI already answers with
# itself (§F4.7).
FILE_ROUTES = {
    "list": ("get", ""),
    "screenshots": ("get", "/screenshots"),
    "downloads": ("get", "/downloads"),
    "keep": ("put", "/{folder}/{name}/kept"),
}

IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")

OFF = (
    "keeping files is not enabled on this server: it was started with no "
    "FLOW_DATA_DIR, so there is nowhere to keep them"
)

NOTHING = "nothing to list: this server keeps no files and holds no browser for you"

DESCRIPTION = (
    "The files in Files: prints, and anything kept with keep_file. Also names "
    "two folders — session://files/screenshots and session://files/downloads "
    "— each with its own listing.\n\n"
    "Every entry carries its own uri, to keep with keep_file(uri), and a URL "
    "that opens in a browser for a while, so an image can be shown to someone "
    "rather than described to them."
)


def content_type(name: str) -> str:
    """The type to serve a stored file as, guessed from its name."""
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def uri_of(folder: str, name: str) -> str:
    """The address of one file. The folder is part of it, so a name only has to
    be unique inside its folder (§F4.6)."""
    leaf = quote(name, safe="")
    return f"{ROOT_URI}/{leaf}" if folder == FILES else f"{FOLDER_URI[folder]}/{leaf}"


def parse_uri(uri) -> tuple[str, str]:
    """``(folder, name)`` for a file's address, or a ValueError naming the shapes."""
    text = str(uri or "")
    prefix = ROOT_URI + "/"
    parts = text[len(prefix):].split("/") if text.startswith(prefix) else []
    if len(parts) == 1 and parts[0] and parts[0] not in RESERVED:
        return FILES, flows.valid_file_name(unquote(parts[0]))
    if len(parts) == 2 and parts[0] in RESERVED and parts[1]:
        return parts[0], flows.valid_file_name(unquote(parts[1]))
    raise ValueError(f"{text!r} is not a file: use {SHAPES}")


def _unreserved(name: str) -> str:
    """A name Files can hold: the folder names are taken there (§F4.6)."""
    return f"{name} (1)" if name in RESERVED else name


def _candidates(name: str):
    """``name``, then ``name (1)``, ``name (2)`` … the way a browser names a
    second download."""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    yield name
    n = 1
    while True:
        yield f"{stem} ({n}).{suffix}" if suffix else f"{stem} ({n})"
        n += 1


def _claim(store, session: str, name: str, data: bytes, folder: str) -> dict:
    """Write under the first free name. The exclusive create is the claim, so
    two racing saves cannot both win (Copilot, #40)."""
    for candidate in _candidates(name):
        if folder == FILES and candidate in RESERVED:
            continue
        try:
            return store.create_file(session, candidate, data, folder)
        except FileExistsError:
            continue
    raise AssertionError("unreachable")  # _candidates is infinite


def describe(folder: str, entry: dict, url: str, base: str = "") -> dict:
    """One file, as every surface needs it: named, sized, fetchable, and placed.

    One builder for every folder so they cannot describe a file differently —
    the URL is built by the caller, because which id signs it (a session name
    for Files and Screenshots, a browser id for Downloads) is a fact about the
    folder that this function does not need to know.
    """
    name = entry.get("name", "")
    kind = content_type(name)
    described = {
        "name": name,
        "uri": uri_of(folder, name),
        "size": entry.get("size", 0),
        "created": entry.get("creationTime"),
        "content_type": kind,
        "image": kind in IMAGE_TYPES,
        "url": url,
    }
    if folder in RESERVED:
        # The call that makes this one durable, spelled out. The tool
        # description has said "these die with the browser" since #28 and a
        # pilot still handed somebody a link that would stop working - because
        # what it read was the result, not the docstring. So the result says it
        # too, in the only form that is also an instruction (§F2.10).
        #
        # json.dumps for the argument, not an f-string: a name may carry a
        # double quote, and a browser will happily save `Q4 "final".csv` - which
        # rendered as a call nobody could paste (Copilot, #31).
        described["keep_with"] = f"{KEEP_TOOL}({json.dumps(described['uri'])})"
    if base:
        # An app renders on a sandbox origin of the host's choosing, so a path
        # would resolve against the wrong server. Absolute only when the server
        # has been told what it is reachable at.
        described["absolute_url"] = base.rstrip("/") + url
    return described


def url_for(
    folder: str,
    session: str,
    name: str,
    token,
    mount: str = "",
    session_id: str = "",
) -> str:
    """The signed link for one entry, keyed by whichever id its folder uses.

    Downloads are the Grid's, reached by a browser id; Files and Screenshots
    are ours, reached by the session name that outlives any browser (§F1.10).
    """
    if folder == DOWNLOADS:
        return links.file_url(session_id, name, token, mount)
    if folder == SCREENSHOTS:
        return links.screenshot_url(session, name, token, mount)
    return links.kept_url(session, name, token, mount)


def owner(store, name: str) -> str:
    """The session name whose files these are, or "" when keeping is off.

    The name is also the flow library's directory: a file and a flow land in
    the same place because they are the same session.
    """
    return name if store is not None else ""


def listing_of(
    actions,
    store,
    folder: str,
    session: str,
    session_id: str,
    token,
    base: str = "",
    mount: str = "",
    downloads: list[dict] | None = None,
) -> list[dict]:
    """One folder's entries, described and signed, newest first.

    Downloads come from the Grid, and only when there is a browser to ask —
    ``session_id`` empty costs no call at all, which is the ordinary case for a
    session whose browser has gone. ``downloads`` lets a caller that already
    has the Grid's listing (the admin, reporting what "clear" would remove)
    pass it in rather than pay for it twice.

    A Grid failure is *not* swallowed here. The browser being gone is free;
    an error from a browser the caller was told is live is a real fault, and
    hiding it behind a short list would make a broken Grid look like an empty
    session.
    """
    if folder == DOWNLOADS:
        if downloads is None:
            downloads = actions.grid.files(session_id) if session_id else []
        entries = downloads
    else:
        entries = store.files(session, folder) if store is not None and session else []
    described = [
        describe(
            folder,
            entry,
            url_for(folder, session, entry.get("name", ""), token, mount, session_id),
            base,
        )
        for entry in entries
    ]
    return sorted(described, key=lambda f: f.get("created") or 0, reverse=True)


def root(
    actions, sessions, store, token, name: str, base: str = "", mount: str = ""
) -> dict:
    """``session://files``: the Files section's own files, and its two folders.

    The two folders are named with a count each rather than expanded, so a
    caller who only wants to know whether there is anything to look at need
    not pay for either listing.
    """
    owned = owner(store, name)
    # Never `resolve`: see the module docstring.
    target = sessions.browser(name)
    if not target and store is None:
        raise ValueError(NOTHING)
    file_list = listing_of(actions, store, FILES, owned, target, token, base, mount)
    screenshots_count = (
        len(store.files(owned, SCREENSHOTS)) if store is not None and owned else 0
    )
    downloads_list = listing_of(
        actions, store, DOWNLOADS, owned, target, token, base, mount
    )
    return {
        "session": owned or None,
        "count": len(file_list),
        "files": file_list,
        "folders": [
            {
                "name": SCREENSHOTS,
                "uri": FOLDER_URI[SCREENSHOTS],
                "count": screenshots_count,
            },
            {
                "name": DOWNLOADS,
                "uri": FOLDER_URI[DOWNLOADS],
                "count": len(downloads_list),
                "browser": bool(target),
            },
        ],
    }


def folder(
    actions,
    sessions,
    store,
    token,
    name: str,
    which: str,
    base: str = "",
    mount: str = "",
) -> dict:
    """One section's own listing: Files, Screenshots or Downloads."""
    if which not in (FILES, SCREENSHOTS, DOWNLOADS):
        raise ValueError(f"{which!r} is not a file section: use {SHAPES}")
    owned = owner(store, name)
    # Never `resolve`: see the module docstring.
    target = sessions.browser(name)
    if not target and store is None:
        raise ValueError(NOTHING)
    entries = listing_of(actions, store, which, owned, target, token, base, mount)
    result = {
        "session": owned or None,
        "folder": which,
        "uri": ROOT_URI if which == FILES else FOLDER_URI[which],
        "count": len(entries),
        "files": entries,
    }
    if which == DOWNLOADS:
        result["browser"] = bool(target)
    return result


def sections(
    actions,
    sessions,
    store,
    token,
    name: str,
    base: str = "",
    mount: str = "",
    session_id: str | None = None,
    downloads: list[dict] | None = None,
) -> dict:
    """All three sections at once, for `session_files` and the admin page.

    ``session_id``, when given, is the browser to read downloads from and is
    never resolved — the same ruling `keep` follows, so an operator surface
    can ask for a specific browser without ever opening one. ``None`` resolves
    the caller's own the way `root` and `folder` do.
    """
    owned = owner(store, name)
    # Never `resolve`: see the module docstring.
    target = session_id if session_id is not None else sessions.browser(name)
    if not target and store is None:
        raise ValueError(NOTHING)
    return {
        "component": "fileSections",
        "session": owned or None,
        "browser": bool(target),
        "downloads": listing_of(
            actions, store, DOWNLOADS, owned, target, token, base, mount, downloads
        ),
        "screenshots": listing_of(
            actions, store, SCREENSHOTS, owned, target, token, base, mount
        ),
        "files": listing_of(actions, store, FILES, owned, target, token, base, mount),
    }


def keep(actions, sessions, store, uri, name=None, session_id=None) -> dict:
    """Put one file in Files. A screenshot moves; a download is copied, because
    the Grid cannot delete one file (§F1.10); a Files URI answers with itself.
    """
    if store is None:
        raise ValueError(OFF)
    folder_of, leaf = parse_uri(uri)
    session = owner(store, name or sessions.name())
    if folder_of == FILES:
        entry = next((e for e in store.files(session) if e["name"] == leaf), None)
        if entry is None:
            raise ValueError(f"no file called {leaf!r} in Files. {ROOT_URI} lists them")
        landed = entry
        log.info("keep %s: already in Files", uri)
    elif folder_of == SCREENSHOTS:
        try:
            data = store.read_file(session, leaf, SCREENSHOTS)
        except FileNotFoundError:
            raise ValueError(
                f"no screenshot called {leaf!r}: it may be kept already or cleared. "
                f"{FOLDER_URI[SCREENSHOTS]} lists what there is"
            ) from None
        landed = _claim(store, session, leaf, data, FILES)
        try:
            removed = store.delete_file(session, leaf, SCREENSHOTS)
        except Exception:
            # A move is a copy plus a delete, and if the delete fails the copy
            # must not survive it — otherwise the file is in both folders and
            # the caller was told the move failed. Undoing the claim is best
            # effort: a failure here is logged, not raised over the original,
            # because the original is the one the caller needs to see.
            try:
                store.delete_file(session, landed["name"], FILES)
            except Exception:  # noqa: BLE001 - best effort, the original error wins
                log.warning(
                    "keep %s: could not roll back the claimed copy %s/%s after "
                    "the screenshot delete failed",
                    uri, session, landed["name"],
                )
            raise
        if not removed:
            # False, not an exception: a concurrent keep or clear took the
            # screenshot out from under this one between the read above and
            # the delete just now. The claimed copy in Files is exactly as
            # unearned as it would have been had `read_file` found nothing at
            # all, so it is rolled back and this answers the same way that
            # race's other ordering already does.
            try:
                store.delete_file(session, landed["name"], FILES)
            except Exception:  # noqa: BLE001 - best effort, the ValueError wins
                log.warning(
                    "keep %s: could not roll back the claimed copy %s/%s after "
                    "the screenshot was already gone",
                    uri, session, landed["name"],
                )
            raise ValueError(
                f"no screenshot called {leaf!r}: it may be kept already or cleared. "
                f"{FOLDER_URI[SCREENSHOTS]} lists what there is"
            )
        log.info("kept %s as %s/%s", uri, session, landed["name"])
    else:
        # `session_id`, when given, is the browser to read from and is never
        # resolved — the caller (the admin) can then ask for a specific
        # browser without this ever opening one. `sessions.browser`, not
        # `sessions.resolve`, for the same reason when it is not given: a
        # reopened browser cannot hold a download it never took, so resolving
        # would only spend a Grid slot to answer the same refusal.
        if session_id is None:
            session_id = sessions.browser(name or sessions.name())
        if not session_id:
            raise ValueError(
                "a download is read from a browser, and none is open: "
                "open_session first"
            )
        data = actions.grid.read_file(session_id, leaf)
        landed = store.write_file(session, _unreserved(leaf), data, FILES)
        log.info("kept %s as %s/%s", uri, session, landed["name"])
    return {
        "kept": True,
        "from": uri_of(folder_of, leaf),
        "uri": uri_of(FILES, landed["name"]),
        "name": landed["name"],
        "size": landed.get("size", 0),
        "created": landed.get("creationTime"),
    }


def keep_made(
    sessions,
    store,
    name: str,
    data: bytes,
    token,
    base: str = "",
    mount: str = "",
    folder=FILES,
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
    entry = _claim(store, session, wanted, data, folder)
    url = url_for(folder, session, entry["name"], token, mount)
    return describe(folder, entry, url, base)


def read_file(actions, sessions, store, uri, name=None) -> tuple[str, bytes]:
    """The bytes of one file, by its address, for a caller that wants to send
    it somewhere. ``name`` names the library explicitly, the way a **flow**
    does: the run supplies the library it was loaded from, so a flow in the
    shared library reads a file from the session that is running, not from
    `global` (Copilot, #31). Omitted, the caller's own name answers.
    """
    folder_of, leaf = parse_uri(uri)
    if folder_of == DOWNLOADS:
        target = sessions.browser(name or sessions.name())
        if not target:
            raise ValueError("a download is read from a browser, and none is open")
        return leaf, actions.grid.read_file(target, leaf)
    if store is None:
        raise ValueError(OFF)
    session = owner(store, name or sessions.name())
    try:
        return leaf, store.read_file(session, leaf, folder_of)
    except FileNotFoundError:
        raise ValueError(f"no file at {uri}. {ROOT_URI} lists what there is") from None


def clear_screenshots(store, session: str) -> dict:
    """Empty the screenshots folder. **Operator-only** — there is no tool for
    this, the same as clearing downloads (§F4.1)."""
    if store is None:
        raise ValueError(OFF)
    return {"cleared": store.clear_folder(session, SCREENSHOTS), "session": session}


def delete_one(store, session: str, name: str) -> dict:
    """Remove one file from Files. **Operator-only** — there is no tool for
    this.

    Only Files: a download belongs to the browser, and the Grid's store has no
    per-file delete to offer; a screenshot is cleared wholesale, not deleted
    one at a time. Clearing empties a folder in one call, which is safe for
    Screenshots precisely because keeping one is what takes it out of that
    folder first.
    """
    if store is None:
        raise ValueError(OFF)
    removed = store.delete_file(session, flows.valid_file_name(name))
    return {"deleted": removed, "session": session, "name": name}


FOLDER_NAME = {SCREENSHOTS: "Screenshots", DOWNLOADS: "Downloads"}

FOLDER_DESCRIPTION = {
    SCREENSHOTS: (
        "This session's saved screenshots, not yet kept. "
        f"{KEEP_TOOL}(uri) moves one into {ROOT_URI}, where it stays until a "
        "person deletes it. Prints land in Files directly, not here."
    ),
    DOWNLOADS: (
        "This session's browser downloads. They belong to the browser and "
        f"disappear when it ends or the Grid reaps it; {KEEP_TOOL}(uri) copies "
        f"one into {ROOT_URI} before that happens."
    ),
}

ITEM_DESCRIPTION = {
    FILES: (
        "One file from Files, as bytes. The name comes from the "
        f"{ROOT_URI} listing, which also carries each file's real media type — "
        "a template declares one type for every file it serves, so this is "
        "deliberately the generic one."
    ),
    SCREENSHOTS: (
        f"One screenshot, as bytes, from {FOLDER_URI[SCREENSHOTS]}. It answers "
        "after the browser that took it has gone, because a screenshot is "
        "already ours (§F1.10)."
    ),
    DOWNLOADS: (
        f"One download, as bytes, from {FOLDER_URI[DOWNLOADS]} — read from the "
        f"browser itself, so this answers only while it is open. {KEEP_TOOL}(uri) "
        "copies it somewhere that outlives the browser."
    ),
}


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

    Returns the mirror tool names. ``keep_file`` is not a mirror — it is a
    capability with no resource behind it — so it stays visible to every
    client.
    """

    @mcp.resource(
        ROOT_URI,
        name="Session Files",
        description=DESCRIPTION,
        mime_type="application/json",
    )
    def files_resource() -> dict:
        return root(
            actions, sessions, store, token, sessions.name(), base=base, mount=prefix
        )

    def _folder_resource(which: str):
        def read() -> dict:
            return folder(
                actions, sessions, store, token, sessions.name(), which,
                base=base, mount=prefix,
            )

        return read

    for which in (SCREENSHOTS, DOWNLOADS):
        mcp.resource(
            FOLDER_URI[which],
            name=FOLDER_NAME[which],
            description=FOLDER_DESCRIPTION[which],
            mime_type="application/json",
        )(_folder_resource(which))

    def _item_resource(which: str):
        def read(name: str) -> bytes:
            return read_file(actions, sessions, store, uri_of(which, name))[1]

        return read

    item_name = {
        FILES: "Session File", SCREENSHOTS: "Screenshot", DOWNLOADS: "Download",
    }
    item_uri = {FILES: FILE_URI, **ITEM_URI}
    for which in (FILES, SCREENSHOTS, DOWNLOADS):
        mcp.resource(
            item_uri[which],
            name=item_name[which],
            description=ITEM_DESCRIPTION[which],
            mime_type="application/octet-stream",
        )(_item_resource(which))

    @mcp.tool(
        name=FILES_TOOL,
        description=(
            "All three sections this session has — files, screenshots and "
            "downloads — for a host that renders a component instead of "
            "reading resources. Each entry carries its own uri and a link that "
            "opens in a browser for a while."
        ),
        app=app_config,
        annotations=reads("Files this session has"),
    )
    def session_files() -> dict:
        return sections(
            actions, sessions, store, token, sessions.name(), base=base, mount=prefix
        )

    @mcp.tool(
        name=KEEP_TOOL,
        description=(
            "Keep one file in Files, where it stays until a person deletes it.\n\n"
            "uri is as session://files and its folders list it. A screenshot "
            "moves out of session://files/screenshots and lands beside a "
            "same-named file as name (1); a download is copied, since the "
            "browser keeps its own until it ends, and REPLACES a same-named "
            "file in Files. The result is the file's new uri. "
            "upload_file(file=uri) puts any file back into a page."
        ),
        annotations=hints("Keep a file in Files", destructive=True, idempotent=False),
    )
    def keep_file(uri: str) -> dict:
        return keep(actions, sessions, store, uri)

    _routes(mcp, actions, sessions, store, token, base, prefix)
    return {FILES_TOOL}


def _routes(mcp, actions, sessions, store, token, base, prefix) -> None:
    """The same operations as REST, for callers that are not MCP.

    Their own tree under ``/files``, for the reason ``/flows`` has one: these
    are not browser actions, and counting them as such would break the
    one-to-one promise ``test_surfaces.py`` guards over those.

    A file belongs to a session, so all of these name one the way everything
    else does — a header or ``?session=`` — and none takes an id.

    The two literal GETs are registered before the one route with a path
    parameter, so `/files/screenshots` and `/files/downloads` are never at the
    mercy of a template that could otherwise be tried first.
    """

    files_root = f"{prefix}/files"

    async def answer(request: Request, what: str, call) -> JSONResponse:
        """One request, answered the way every other tree answers one."""
        return await answer_module.answer(request, token, f"files/{what}", call, log)

    @mcp.custom_route(files_root, methods=["GET"], name="files_list")
    async def list_files(request: Request) -> JSONResponse:
        """Files' own listing, and the two folders beside it."""
        return await answer(
            request,
            "list",
            lambda name, _body: root(
                actions, sessions, store, token, name, base=base, mount=prefix
            ),
        )

    @mcp.custom_route(
        files_root + "/screenshots", methods=["GET"], name="files_screenshots"
    )
    async def list_screenshots(request: Request) -> JSONResponse:
        """This session's saved screenshots, not yet kept."""
        return await answer(
            request,
            "screenshots",
            lambda name, _body: folder(
                actions, sessions, store, token, name, SCREENSHOTS,
                base=base, mount=prefix,
            ),
        )

    @mcp.custom_route(
        files_root + "/downloads", methods=["GET"], name="files_downloads"
    )
    async def list_downloads(request: Request) -> JSONResponse:
        """This session's browser downloads."""
        return await answer(
            request,
            "downloads",
            lambda name, _body: folder(
                actions, sessions, store, token, name, DOWNLOADS,
                base=base, mount=prefix,
            ),
        )

    @mcp.custom_route(
        files_root + "/{folder}/{name}/kept", methods=["PUT"], name="files_keep"
    )
    async def keep_route(request: Request) -> JSONResponse:
        """Keep a screenshot or a download beyond what made it. A PUT because
        keeping a name that is already kept is not a failure: a screenshot
        lands beside a same-named file in Files as name (1), and a download
        replaces one."""
        which = request.path_params["folder"]
        leaf = request.path_params["name"]

        def call(name, _body):
            if which not in (SCREENSHOTS, DOWNLOADS):
                raise ValueError(
                    f"{which!r} is not something to keep: use {SCREENSHOTS} or "
                    f"{DOWNLOADS}"
                )
            return keep(actions, sessions, store, uri_of(which, leaf), name=name)

        return await answer(request, "keep", call)
