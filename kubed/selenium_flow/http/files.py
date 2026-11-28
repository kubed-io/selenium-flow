"""A workspace's files as four sections, addressed by path.

Downloads, Screenshots, Recordings and Files never merge into one list. The
section is part of the address, so a name is unique only inside its folder.

- Downloads belong to the browser: the Grid's per-session store, deleted with
  it. The Grid offers list, read-one and delete-all, and nothing else.
- Screenshots, Recordings and Files belong to the workspace, under
  ``DATA_DIR``. Screenshots and Recordings pile up until kept or cleared; Files
  holds what somebody kept, and prints, and has no bulk delete.

Keeping a screenshot or a recording is a move; keeping a download is a copy,
since the Grid cannot delete one file. A Files URI keeps as itself. There is no
tool that deletes: removing a file or clearing a section is an operator's
action in the admin UI.

A listing never opens a browser: ``root``, ``folder`` and ``sections`` read the
record with ``Workspaces.browser(name)``, never ``Workspaces.resolve``.
Downloads are one session's (WS-52): read from the browser of the session
the caller reads (``Workspaces.reading``), or the one a
``session://<name>/downloads`` URI names, while the other sections are
the workspace's, whichever session asks (WS-54). Reading
downloads with no session open is refused with ``NO_DOWNLOADS`` and costs no
Grid call, while a failure from a browser the record says is live is never
swallowed. Only the admin's ``sections`` answers an empty Downloads for an idle
workspace. ``root`` asks the Grid nothing.

``screenshots``, ``recordings`` and ``downloads`` are reserved names inside
Files, because ``workspace://files/{name}`` could not address them; a file
arriving under one lands as ``screenshots (1)`` and so on. A
``workspace://files/downloads`` URI is refused naming ``session://downloads``.

Every address is a resource, a file's bytes, and a view for ``show``, and
every entry carries a signed URL for a place that can do none of those.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import stat
from dataclasses import replace
from typing import Annotated
from urllib.parse import quote, unquote

from kubed.mcp import ResourceContent, ResourceResult
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core.annotations import hints
from ..faults import NoSession, NotFound
from ..mcp import clients, timing
from ..names import RESERVED_IN_FILES, candidates, retired_uri, valid_file_name
from ..workspace import sessions
from . import answer as answer_module
from . import links

log = logging.getLogger(__name__)

FILES, SCREENSHOTS, DOWNLOADS, RECORDINGS = (
    "files", "screenshots", "downloads", "recordings",
)
RESERVED = RESERVED_IN_FILES
ROOT_URI = "workspace://files"
FILE_URI = "workspace://files/{name}"
FOLDER_URI = {
    SCREENSHOTS: f"{ROOT_URI}/{SCREENSHOTS}",
    RECORDINGS: f"{ROOT_URI}/{RECORDINGS}",
    # The session's: a download lives only as long as its browser.
    DOWNLOADS: "session://downloads",
}
ITEM_URI = {k: v + "/{name}" for k, v in FOLDER_URI.items()}
KEEP_TOOL = "keep_file"

LIST_URI = ROOT_URI

# Which session's downloads `keep_file` copies from (WS-52).
Session = Annotated[
    str | None,
    Field(
        description=(
            "Which session's downloads a session://downloads uri is read from, by "
            "name. Leave it out for your own, named after you. A "
            "session://<name>/downloads uri already names its session."
        )
    ),
]

SHAPES = (
    "workspace://files/<name> for a file in Files, "
    "workspace://files/screenshots/<name> for a screenshot, "
    "workspace://files/recordings/<name> for a recording, or "
    "session://downloads/<name> (session://<session>/downloads/<name>) for a "
    "download"
)

# Not browser actions, so a table of their own. No `clear` or `delete`: neither
# has a tool, and only the admin UI deletes.
FILE_ENDPOINTS = ("list", "screenshots", "downloads", "recordings", "keep")

# Method and path under the mount, read by the routes and the published spec.
# The downloads listing is the session's, so it is under /browser.
FILE_ROUTES = {
    "list": ("get", "/files"),
    "screenshots": ("get", "/files/screenshots"),
    "downloads": ("get", "/browser/downloads"),
    "recordings": ("get", "/files/recordings"),
    "keep": ("put", "/files/{folder}/{name}/kept"),
}

# Raster images: shown as pictures, and served unsandboxed because a browser
# only ever decodes them.
RASTER_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}
)
# SVG is shown as a picture too, but carries script, so it is not raster.
IMAGE_TYPES = RASTER_TYPES | {"image/svg+xml"}

OFF = (
    "keeping files is not enabled on this server: it was started with no "
    "DATA_DIR, so there is nowhere to keep them"
)

NOTHING = "nothing to list: this server keeps no files and holds no browser for you"

# Never quotes either name: a refusal's text is logged (WS-82).
SESSION_DIFFERS = (
    "the uri names one session's download and session names another: drop "
    "session, since session://{name}/downloads/<file> already says whose it is"
)

# Raised as `faults.NoSession` by every read of the downloads, never by the
# admin's `sections`.
NO_DOWNLOADS = (
    "no session is open in this workspace; a download lives only as long as "
    "the browser that took it"
)

DESCRIPTION = (
    "The files in Files: prints, and anything kept with keep_file. Also names "
    "two folders — workspace://files/screenshots and workspace://files/recordings "
    "— each with its own listing. Your session's downloads are "
    "session://downloads, a named session's session://{name}/downloads.\n\n"
    "Every entry carries its own uri, to keep with keep_file(uri), and a URL "
    "that opens in a browser for a while, so an image can be shown to someone "
    "rather than described to them."
)


# Absent from older Pythons' default table, and the host's /etc/mime.types
# varies. Unmapped, these would serve as sandboxed octet-stream.
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")


def content_type(name: str) -> str:
    """The type to serve a stored file as, guessed from its name."""
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def uri_of(folder: str, name: str, at: str = "") -> str:
    """The address of one file, its folder included: under ``at`` when its
    folder is read there (``session://<name>/downloads``)."""
    leaf = quote(name, safe="")
    if at:
        return f"{at}/{leaf}"
    return f"{ROOT_URI}/{leaf}" if folder == FILES else f"{FOLDER_URI[folder]}/{leaf}"


def downloads_of(name: str) -> str:
    """``session://<name>/downloads``: one session's downloads, by name."""
    return f"session://{name}/{DOWNLOADS}"


def named_in(uri) -> str | None:
    """The session a ``session://<name>/downloads/<file>`` URI names, or None
    for any other; a ValueError when ``<name>`` cannot name a session."""
    text = str(uri or "")
    if not text.startswith("session://"):
        return None
    parts = text.removeprefix("session://").split("/")
    if len(parts) != 3 or parts[1] != DOWNLOADS or parts[0] == DOWNLOADS:
        return None
    return sessions.valid_session_name(parts[0])


def _quoted(text: str) -> str:
    """An address as a refusal quotes it: nameless (`sessions.nameless`), since
    a refusal's text is logged (WS-82)."""
    return sessions.nameless(repr(text))


def parse_uri(uri) -> tuple[str, str]:
    """``(folder, name)`` for a file's address, or a ValueError naming the shapes.

    A moved URI is refused with its new spelling first.
    """
    retired_uri(uri)
    text = str(uri or "")
    named = named_in(text)
    if named:
        leaf = text.rpartition("/")[2]
        if leaf:
            return DOWNLOADS, valid_file_name(unquote(leaf))
        # Nameless: a refusal's text is logged (WS-82).
        raise ValueError(f"session://{{name}}/downloads/ names no file: use {SHAPES}")
    downloads = FOLDER_URI[DOWNLOADS] + "/"
    if text.startswith(downloads):
        leaf = text[len(downloads):]
        if leaf and "/" not in leaf:
            return DOWNLOADS, valid_file_name(unquote(leaf))
        raise ValueError(f"{_quoted(text)} is not a file: use {SHAPES}")
    prefix = ROOT_URI + "/"
    parts = text[len(prefix):].split("/") if text.startswith(prefix) else []
    if len(parts) == 1 and parts[0] and parts[0] not in RESERVED:
        return FILES, valid_file_name(unquote(parts[0]))
    if len(parts) == 2 and parts[0] in (SCREENSHOTS, RECORDINGS) and parts[1]:
        return parts[0], valid_file_name(unquote(parts[1]))
    raise ValueError(f"{_quoted(text)} is not a file: use {SHAPES}")


def _unreserved(name: str) -> str:
    """A name Files can hold: the folder names are taken there."""
    return f"{name} (1)" if name in RESERVED_IN_FILES else name


def _claim(store, workspace: str, name: str, data: bytes, folder: str) -> dict:
    """Write under the first free name. The exclusive create is the claim, so
    two racing saves cannot both win."""
    for candidate in candidates(name):
        if folder == FILES and candidate in RESERVED_IN_FILES:
            continue
        try:
            return store.create_file(workspace, candidate, data, folder)
        except FileExistsError:
            continue
    raise AssertionError("unreachable")  # candidates is infinite


def describe(
    folder: str, entry: dict, url: str, base: str = "", at: str = ""
) -> dict:
    """One file, as every surface needs it: named, sized, fetchable, and placed.

    The caller builds ``url``, since which id signs it depends on the folder.
    It is made absolute whenever the server knows where it is reachable.
    ``at`` is the URI its folder is read at, when that is not its own.
    """
    name = entry.get("name", "")
    kind = content_type(name)
    if base:
        url = base.rstrip("/") + url
    described = {
        "name": name,
        "uri": uri_of(folder, name, at),
        "size": entry.get("size", 0),
        "created": entry.get("creationTime"),
        "content_type": kind,
        "image": kind in IMAGE_TYPES,
        "url": url,
    }
    if folder in RESERVED:
        # The call that makes it durable, spelled out in the result, which is
        # what a model reads. json.dumps, since a name may carry a double quote.
        described["keep_with"] = f"{KEEP_TOOL}({json.dumps(described['uri'])})"
    return described


def url_for(
    folder: str,
    workspace: str,
    name: str,
    token,
    mount: str = "",
    session_id: str = "",
    ttl: int = links.DEFAULT_TTL,
) -> str:
    """The signed link for one entry: a browser id for a download, the
    workspace name for the rest."""
    if folder == DOWNLOADS:
        return links.file_url(session_id, name, token, mount, ttl)
    if folder == SCREENSHOTS:
        return links.screenshot_url(workspace, name, token, mount, ttl)
    if folder == RECORDINGS:
        return links.recording_url(workspace, name, token, mount, ttl)
    return links.kept_url(workspace, name, token, mount, ttl)


def owner(store, name: str) -> str:
    """The workspace name whose files these are, or "" when keeping is off."""
    return name if store is not None else ""


def listing_of(
    actions,
    store,
    folder: str,
    workspace: str,
    session_id: str,
    token,
    base: str = "",
    mount: str = "",
    downloads: list[dict] | None = None,
    ttl: int = links.DEFAULT_TTL,
    at: str = "",
) -> list[dict]:
    """One folder's entries, described and signed, newest first.

    Downloads come from the Grid only when ``session_id`` is set, or from
    ``downloads`` when the caller already has the listing. A Grid failure is
    raised, so a broken Grid never looks like an empty workspace.
    """
    if folder == DOWNLOADS:
        if downloads is None:
            downloads = actions.grid.files(session_id) if session_id else []
        entries = downloads
    else:
        listed = store is not None and workspace
        entries = store.files(workspace, folder) if listed else []
    described = [
        describe(
            folder,
            entry,
            url_for(
                folder, workspace, entry.get("name", ""), token, mount, session_id, ttl
            ),
            base,
            at,
        )
        for entry in entries
    ]
    return sorted(described, key=lambda f: f.get("created") or 0, reverse=True)


def root(
    actions,
    workspaces,
    store,
    token,
    name: str,
    base: str = "",
    mount: str = "",
    ttl: int = links.DEFAULT_TTL,
) -> dict:
    """``workspace://files``: the Files section's own files, and its two folders
    with a count each. The Grid is never asked."""
    if store is None:
        raise ValueError(OFF)
    owned = owner(store, name)
    file_list = listing_of(
        actions, store, FILES, owned, "", token, base, mount, ttl=ttl
    )
    screenshots_count = len(store.files(owned, SCREENSHOTS)) if owned else 0
    recordings_count = len(store.files(owned, RECORDINGS)) if owned else 0
    return {
        "workspace": owned or None,
        "count": len(file_list),
        "files": file_list,
        "folders": [
            {
                "name": SCREENSHOTS,
                "uri": FOLDER_URI[SCREENSHOTS],
                "count": screenshots_count,
            },
            {
                "name": RECORDINGS,
                "uri": FOLDER_URI[RECORDINGS],
                "count": recordings_count,
            },
        ],
    }


def folder(
    actions,
    workspaces,
    store,
    token,
    name: str,
    which: str,
    base: str = "",
    mount: str = "",
    ttl: int = links.DEFAULT_TTL,
    session: str | None = None,
    at: str = "",
) -> dict:
    """One section's own listing: Files, Screenshots, Recordings or Downloads.

    Downloads are the session's, ``session`` (a key; when None, the
    workspace's ``default``, the session a caller with no principal reaches,
    WS-82): with none open they are refused with ``NO_DOWNLOADS``, before
    anything else is asked. The other three are the workspace's, and are
    ``OFF`` without a ``DATA_DIR``, browser or not. ``at`` is the URI the
    listing is read at, when that is not its folder's own.
    """
    if which not in (FILES, SCREENSHOTS, RECORDINGS, DOWNLOADS):
        raise ValueError(f"{which!r} is not a file section: use {SHAPES}")
    owned = owner(store, name)
    target = ""
    if which == DOWNLOADS:
        # Never `resolve`: see the module docstring.
        target = workspaces.browser(session or _default_of(name))
        if not target:
            raise NoSession(NO_DOWNLOADS)
    elif store is None:
        raise ValueError(OFF)
    entries = listing_of(
        actions, store, which, owned, target, token, base, mount, ttl=ttl, at=at
    )
    return {
        "workspace": owned or None,
        "folder": which,
        "uri": at or (ROOT_URI if which == FILES else FOLDER_URI[which]),
        "count": len(entries),
        "files": entries,
    }


def sections(
    actions,
    workspaces,
    store,
    token,
    name: str,
    base: str = "",
    mount: str = "",
    session_id: str | None = None,
    downloads: list[dict] | None = None,
    ttl: int = links.DEFAULT_TTL,
) -> dict:
    """Every section at once, for the admin page.

    ``session_id``, when given, is the browser to read downloads from and is
    never resolved. ``None`` looks up the caller's own as `folder` does.
    """
    owned = owner(store, name)
    # Never `resolve`: see the module docstring.
    target = session_id if session_id is not None else workspaces.browser(name)
    if not target and store is None:
        raise ValueError(NOTHING)
    return {
        "component": "fileSections",
        "workspace": owned or None,
        "browser": bool(target),
        **{
            which: listing_of(
                actions, store, which, owned, target, token, base, mount, downloads, ttl
            )
            for which in (DOWNLOADS, SCREENSHOTS, RECORDINGS, FILES)
        },
    }


def keep(
    actions, workspaces, store, uri, name: str, session_id=None, session=None,
    at: str = "",
) -> dict:
    """Put one file in Files. A screenshot moves; a download is copied, because
    the Grid cannot delete one file; a Files URI answers with itself. A
    recording moves strictly: if its source cannot be removed the Files copy is
    rolled back and the error raised, never a success with the video in both.

    A download is read from ``session_id``, else from the browser of
    ``session`` (a key; when None, the workspace's ``default``, as `folder`
    reads it). ``at`` is the
    downloads it is said to come from, when not ``session://downloads``.
    """
    if store is None:
        raise ValueError(OFF)
    folder_of, leaf = parse_uri(uri)
    workspace = owner(store, name)
    if folder_of == FILES:
        entry = next((e for e in store.files(workspace) if e["name"] == leaf), None)
        if entry is None:
            raise ValueError(f"no file called {leaf!r} in Files. {ROOT_URI} lists them")
        landed = entry
        log.info("keep %s: already in Files", uri)
    elif folder_of == SCREENSHOTS:
        try:
            data = store.read_file(workspace, leaf, SCREENSHOTS)
        except FileNotFoundError:
            raise ValueError(
                f"no screenshot called {leaf!r}: it may be kept already or cleared. "
                f"{FOLDER_URI[SCREENSHOTS]} lists what there is"
            ) from None
        landed = _claim(store, workspace, leaf, data, FILES)
        try:
            removed = store.delete_file(workspace, leaf, SCREENSHOTS)
        except Exception:
            # The copy must not survive a failed delete. Undoing it is best
            # effort: the original error is the one the caller needs to see.
            try:
                store.delete_file(workspace, landed["name"], FILES)
            except Exception:  # noqa: BLE001 - best effort, the original error wins
                log.warning(
                    "keep %s: could not roll back the claimed copy %s/%s after "
                    "the screenshot delete failed",
                    uri, workspace, landed["name"],
                )
            raise
        if not removed:
            # A concurrent keep or clear took the screenshot after the read:
            # roll back and answer as if `read_file` had found nothing.
            try:
                store.delete_file(workspace, landed["name"], FILES)
            except Exception:  # noqa: BLE001 - best effort, the ValueError wins
                log.warning(
                    "keep %s: could not roll back the claimed copy %s/%s after "
                    "the screenshot was already gone",
                    uri, workspace, landed["name"],
                )
            raise ValueError(
                f"no screenshot called {leaf!r}: it may be kept already or cleared. "
                f"{FOLDER_URI[SCREENSHOTS]} lists what there is"
            )
        log.info("kept %s as %s/%s", uri, workspace, landed["name"])
    elif folder_of == RECORDINGS:
        # A move, without reading the video into memory.
        try:
            source = store.file_path(workspace, leaf, RECORDINGS)
            try:
                is_file = stat.S_ISREG(source.stat().st_mode)
            except NotADirectoryError:
                is_file = False  # only FileNotFoundError propagates as missing
            if not is_file:
                raise FileNotFoundError(leaf)
            landed = store.move_in(workspace, source, leaf, FILES, strict=True)
        except FileNotFoundError:
            raise ValueError(
                f"no recording called {leaf!r}: it may be kept already or cleared. "
                f"{FOLDER_URI[RECORDINGS]} lists what there is"
            ) from None
        log.info("kept %s as %s/%s", uri, workspace, landed["name"])
    else:
        # Never resolved: a reopened browser cannot hold a download it never
        # took, so opening one would only spend a Grid slot.
        if session_id is None:
            session_id = workspaces.browser(session or _default_of(name))
        if not session_id:
            raise NoSession(NO_DOWNLOADS)
        data = actions.grid.read_file(session_id, leaf)
        landed = store.write_file(workspace, _unreserved(leaf), data, FILES)
        # Not by name: a site names a download, and may put a secret in it.
        log.info("kept a download in %s's Files", workspace)
    return {
        "kept": True,
        "from": uri_of(folder_of, leaf, at),
        "uri": uri_of(FILES, landed["name"]),
        "name": landed["name"],
        "size": landed.get("size", 0),
        "created": landed.get("creationTime"),
    }


def kept_from(workspaces, caller, key: str, named: str | None) -> str:
    """Where a download kept from the session ``key`` is said to come from
    (`keep`'s ``at``): that session's downloads as ``caller`` reads them, or
    "" for its own ``session://downloads``. ``named`` is the session the call
    named, by argument or URI; an unnamed call keeps from its own."""
    if not named:
        return ""
    at = sessions.pointer(key, workspaces.own(caller, named, key), DOWNLOADS)
    return "" if at == FOLDER_URI[DOWNLOADS] else at


def keep_made(
    workspace: str,
    store,
    name: str,
    data: bytes,
    token,
    base: str = "",
    mount: str = "",
    folder=FILES,
    ttl: int = links.DEFAULT_TTL,
) -> dict:
    """Keep bytes this server made (a screenshot, a print) for ``workspace``.

    A second `screenshot.png` lands as `screenshot (1).png`, since overwriting
    would take away a file somebody holds a link to. The entry names the file.
    """
    if store is None:
        raise ValueError(OFF)
    workspace = owner(store, workspace)
    wanted = valid_file_name(name)
    entry = _claim(store, workspace, wanted, data, folder)
    url = url_for(folder, workspace, entry["name"], token, mount, ttl=ttl)
    return describe(folder, entry, url, base)


def _default_of(workspace: str) -> str:
    """The key of ``workspace``'s ``default``: a workspace's own key holds no
    browser (WS-7)."""
    return sessions.key_of(workspace, sessions.DEFAULT)


def read_file(
    actions,
    workspaces,
    store,
    uri,
    name: str,
    session: str | None = None,
    caller=None,
) -> tuple[str, bytes]:
    """The bytes of one file, by its address. ``name`` is the workspace to read
    from: a flow run passes the workspace running it, so a shared flow does not
    read from `global`. A download is read from the browser of ``session`` (a
    key; when None, the workspace's ``default``, as `folder` reads it), or of
    the session its URI names, whichever session the call acts in.

    A session the URI names is resolved for ``caller`` (the request's when
    None) as ``session://{name}/downloads`` reads it: one another subject
    holds is `faults.Held`, and one with no record `NoSession` (WS-84).
    """
    folder_of, leaf = parse_uri(uri)
    named = named_in(uri) if folder_of == DOWNLOADS else None
    if named:
        who = caller if caller is not None else clients.caller()
        session = workspaces.reading(replace(who, named=name, pin=named))
    if folder_of == DOWNLOADS:
        target = workspaces.browser(session or _default_of(name))
        if not target:
            raise NoSession(NO_DOWNLOADS)
        return leaf, actions.grid.read_file(target, leaf)
    if store is None:
        raise ValueError(OFF)
    workspace = owner(store, name)
    try:
        return leaf, store.read_file(workspace, leaf, folder_of)
    except FileNotFoundError:
        raise ValueError(f"no file at {uri}. {ROOT_URI} lists what there is") from None


def clear_screenshots(store, workspace: str) -> dict:
    """Empty the screenshots folder. Operator-only: no tool does this."""
    if store is None:
        raise ValueError(OFF)
    cleared = store.clear_folder(workspace, SCREENSHOTS)
    return {"cleared": cleared, "workspace": workspace}


def clear_recordings(store, workspace: str) -> dict:
    """Empty the recordings folder. Operator-only; notes for recordings still
    to come are not in it and are left alone."""
    if store is None:
        raise ValueError(OFF)
    cleared = store.clear_folder(workspace, RECORDINGS)
    return {"cleared": cleared, "workspace": workspace}


def delete_one(store, workspace: str, name: str) -> dict:
    """Remove one file from Files. Operator-only: no tool does this."""
    if store is None:
        raise ValueError(OFF)
    removed = store.delete_file(workspace, valid_file_name(name))
    return {"deleted": removed, "workspace": workspace, "name": name}


FOLDER_NAME = {
    SCREENSHOTS: "Screenshots", RECORDINGS: "Recordings", DOWNLOADS: "Downloads",
}

FOLDER_DESCRIPTION = {
    SCREENSHOTS: (
        "This workspace's saved screenshots, not yet kept. "
        f"{KEEP_TOOL}(uri) moves one into {ROOT_URI}, where it stays until a "
        "person deletes it. Prints land in Files directly, not here."
    ),
    RECORDINGS: (
        "This workspace's recordings: one video per browser opened with "
        "open_session(record=true), filed shortly after that browser ends. "
        f"{KEEP_TOOL}(uri) moves one into {ROOT_URI}. Each entry's url plays it; "
        "reading one by its uri answers that entry, not the video."
    ),
    DOWNLOADS: (
        "Your own session's downloads; session://{name}/downloads lists "
        "another's. They belong to the browser and "
        f"disappear when it ends or the Grid reaps it; {KEEP_TOOL}(uri) copies "
        f"one into {ROOT_URI} before that happens. With no session open, "
        "reading it says so."
    ),
}

ITEM_DESCRIPTION = {
    FILES: (
        "One file from Files, as bytes, carrying its own media type. The name "
        f"comes from the {ROOT_URI} listing. A video (a kept recording) answers "
        "its entry instead, as JSON: its url plays it."
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
    workspaces,
    store,
    token,
    base="",
    prefix: str = "",
    ttl: int = links.DEFAULT_TTL,
) -> set[str]:
    """Register the file resources and ``keep_file``.

    Returns no tool names: ``keep_file`` is a capability with no resource behind
    it, so it is visible to every client, and `show` draws the listings.
    """

    @mcp.resource(
        ROOT_URI,
        name="Workspace Files",
        description=DESCRIPTION,
        mime_type="application/json",
    )
    def files_resource() -> dict:
        return root(
            actions, workspaces, store, token, clients.caller().name,
            base=base, mount=prefix, ttl=ttl,
        )

    def _session(caller, which: str, named: str | None = None) -> str | None:
        """The key of the session whose downloads ``caller`` reads, made by no
        read (`Workspaces.reading`); None for the other
        sections, which are the workspace's."""
        if which != DOWNLOADS and not named:
            return None
        return workspaces.reading(caller, named)

    def _listed(which: str) -> dict:
        caller = clients.caller()
        return folder(
            actions, workspaces, store, token, caller.name, which,
            base=base, mount=prefix, ttl=ttl, session=_session(caller, which),
        )

    def _folder_resource(which: str):
        def read() -> dict:
            return _listed(which)

        return read

    @mcp.resource(
        "session://{name}/downloads",
        name="Session Downloads",
        description=(
            "The downloads of one session's browser, by name, as "
            f"{FOLDER_URI[DOWNLOADS]} lists yours. A name with no session is "
            "not found."
        ),
        mime_type="application/json",
    )
    def session_downloads_resource(name: str) -> dict:
        caller, key = _named(name)
        return folder(
            actions, workspaces, store, token, caller.name, DOWNLOADS,
            base=base, mount=prefix, ttl=ttl, session=key, at=downloads_of(name),
        )

    def _named(name: str):
        """``(caller, key)`` for the session ``name`` of the caller's
        workspace, read as `session://{name}` reads it: a valid name with no
        session behind it is not found."""
        caller = replace(clients.caller(), pin=name)
        try:
            return caller, workspaces.reading(caller)
        except NoSession:
            raise NotFound(
                f"no such session in workspace {caller.name!r}: "
                "workspace://current lists them"
            ) from None

    @mcp.resource(
        "session://{name}/downloads/{file}",
        name="Session Download",
        description=(
            "One download of one session's browser, by name, as bytes: as "
            f"{ITEM_URI[DOWNLOADS]} reads yours. A name with no session is not "
            "found."
        ),
        mime_type="application/octet-stream",
    )
    def session_download_resource(name: str, file: str) -> ResourceResult:
        caller, key = _named(name)
        leaf, data = read_file(
            actions, workspaces, store, uri_of(DOWNLOADS, file), caller.name,
            session=key,
        )
        return ResourceResult([ResourceContent(data, mime_type=content_type(leaf))])

    for which in (SCREENSHOTS, RECORDINGS, DOWNLOADS):
        mcp.resource(
            FOLDER_URI[which],
            name=FOLDER_NAME[which],
            description=FOLDER_DESCRIPTION[which],
            mime_type="application/json",
        )(_folder_resource(which))

    def _entry(which: str, name: str) -> dict | None:
        """One stored file's entry, described and signed, or None."""
        listing = _listed(which)
        return next((f for f in listing["files"] if f["name"] == unquote(name)), None)

    def _item_resource(which: str):
        def read(name: str) -> ResourceResult:
            # The caller is named last: with Files off, or a URI that is not
            # one, naming the workspace would not fix the call.
            folder_of, leaf = parse_uri(uri_of(which, name))
            if folder_of != DOWNLOADS and store is None:
                raise ValueError(OFF)
            if folder_of == FILES and content_type(leaf).startswith("video/"):
                # A kept recording answers its entry, as a recording does.
                entry = _entry(FILES, name)
                if entry is None:
                    raise ValueError(
                        f"no file at {uri_of(which, name)}. {ROOT_URI} lists what "
                        "there is"
                    )
                return ResourceResult(
                    [ResourceContent(json.dumps(entry), mime_type="application/json")]
                )
            caller = clients.caller()
            leaf, data = read_file(
                actions, workspaces, store, uri_of(which, name), caller.name,
                session=_session(caller, folder_of),
            )
            return ResourceResult([ResourceContent(data, mime_type=content_type(leaf))])

        return read

    item_name = {
        FILES: "Workspace File", SCREENSHOTS: "Screenshot", DOWNLOADS: "Download",
    }
    item_uri = {FILES: FILE_URI, **ITEM_URI}
    for which in (FILES, SCREENSHOTS, DOWNLOADS):
        mcp.resource(
            item_uri[which],
            name=item_name[which],
            description=ITEM_DESCRIPTION[which],
            mime_type="application/octet-stream",
        )(_item_resource(which))

    @mcp.resource(
        ITEM_URI[RECORDINGS],
        name="Recording",
        description=(
            f"One recording from {FOLDER_URI[RECORDINGS]}, described: its url plays "
            "it. Not the video's bytes — a model cannot watch one, and a resource "
            "would carry it as base64."
        ),
        mime_type="application/json",
    )
    def recording_resource(name: str) -> dict:
        if store is None:
            raise ValueError(OFF)
        entry = _entry(RECORDINGS, name)
        if entry is None:
            raise ValueError(
                f"no recording called {name!r}. {FOLDER_URI[RECORDINGS]} lists them"
            )
        return entry

    @mcp.tool(
        name=KEEP_TOOL,
        description=(
            "Keep one file in Files, where it stays until a person deletes it.\n\n"
            "uri is as workspace://files, its folders, session://downloads and "
            "session://<name>/downloads list it. A screenshot "
            "or a recording moves out of its folder and lands beside a "
            "same-named file as name (1); a download is copied, since the "
            "browser keeps its own until it ends, and REPLACES a same-named "
            "file in Files. The result is the file's new uri. "
            "upload_file(file=uri) puts any file back into a page.\n\n"
            "A session://downloads uri is your session's download, or the one "
            "session names; a session://<name>/downloads uri already names its "
            "session. Every session has a browser, and so downloads, of its own."
        ),
        annotations=hints("Keep a file in Files", destructive=True, idempotent=False),
    )
    @timing.timed(workspaces, KEEP_TOOL)
    def keep_file(uri: str, session: Session = None) -> dict:
        # The caller is named last, for the reason `_item_resource` gives.
        if store is None:
            raise ValueError(OFF)
        folder_of, _ = parse_uri(uri)
        named = named_in(uri)
        if named and session and sessions.valid_session_name(session) != named:
            raise ValueError(SESSION_DIFFERS)
        caller = clients.caller()
        name = caller.name
        # A named one is read as `session://{name}/downloads` reads it.
        key = _named(named)[1] if named else _session(caller, folder_of, session)
        at = (
            kept_from(workspaces, caller, key, named or session)
            if folder_of == DOWNLOADS
            else ""
        )
        # A download's name is the site's: checked as its listing is, with
        # the secrets of the session it was downloaded in.
        return workspaces.scrubbed(
            key if folder_of == DOWNLOADS and key else name,
            lambda: keep(actions, workspaces, store, uri, name, session=key, at=at),
        )

    _routes(mcp, actions, workspaces, store, token, base, prefix, ttl, _session)
    return set()


def _routes(
    mcp, actions, workspaces, store, token, base, prefix, ttl, session_of
) -> None:
    """The same operations as REST, under ``/files``, with the downloads
    listing at ``/browser/downloads``.

    The literal GETs are registered before the route with path parameters.
    """

    def path(what: str) -> str:
        return prefix + FILE_ROUTES[what][1]

    async def answer(request: Request, what: str, call) -> JSONResponse:
        """One request, answered the way every other tree answers one."""
        label = "browser/downloads" if what == "downloads" else f"files/{what}"
        # A listing names what a site named, so it is scrubbed as its resource is.
        return await answer_module.answer(
            request, token, label, call, log, scrubbed=workspaces.scrubbed,
            configs=workspaces.configs,
        )

    @mcp.custom_route(path("list"), methods=["GET"], name="files_list")
    async def list_files(request: Request) -> JSONResponse:
        """Files' own listing, and the two folders beside it."""
        return await answer(
            request,
            "list",
            lambda caller, _body: root(
                actions, workspaces, store, token, caller.name,
                base=base, mount=prefix, ttl=ttl,
            ),
        )

    def folder_route(which: str):
        """The handler for one folder's listing."""

        async def list_folder(request: Request) -> JSONResponse:
            def listed(caller, _body):
                key = session_of(caller, which)

                def read():
                    return folder(
                        actions, workspaces, store, token, caller.name, which,
                        base=base, mount=prefix, ttl=ttl, session=key,
                    )

                # Downloads are also checked with the secrets of their own
                # session; `answer` checks with the workspace's.
                return workspaces.scrubbed(key, read) if key else read()

            return await answer(request, which, listed)

        return list_folder

    for which, name in (
        (SCREENSHOTS, "files_screenshots"),
        (DOWNLOADS, "browser_downloads"),
        (RECORDINGS, "files_recordings"),
    ):
        mcp.custom_route(path(which), methods=["GET"], name=name)(folder_route(which))

    @mcp.custom_route(path("keep"), methods=["PUT"], name="files_keep")
    async def keep_route(request: Request) -> JSONResponse:
        """Keep a screenshot, a recording or a download in Files. A PUT, since
        keeping again is no failure."""
        which = request.path_params["folder"]
        leaf = request.path_params["name"]

        def call(caller, body):
            if which not in (SCREENSHOTS, RECORDINGS, DOWNLOADS):
                raise ValueError(
                    f"{which!r} is not something to keep: use {SCREENSHOTS}, "
                    f"{RECORDINGS} or {DOWNLOADS}"
                )
            named = body.get("session")
            key = session_of(caller, which, named)

            def kept():
                return keep(
                    actions, workspaces, store, uri_of(which, leaf), caller.name,
                    session=key,
                    at=kept_from(workspaces, caller, key, named)
                    if which == DOWNLOADS
                    else "",
                )

            # A download is also checked with the secrets of the session it
            # was downloaded in; `answer` checks with the workspace's.
            return workspaces.scrubbed(key, kept) if key else kept()

        return await answer(request, "keep", call)
