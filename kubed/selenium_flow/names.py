"""Who a thing is called, and which names are allowed.

The identity rules, with nothing else in them: what may name a workspace, a flow
or a file, which names are spoken for, and which library a stored key owns. They
live apart from ``flows.library`` because the browser actions, the secrets
catalogue, the config and the workspace layer all need them, and none of those
should have to import the flow store to ask whether a string is a name.
"""

from __future__ import annotations

import re

# The server's MCP name, which is also the namespace every skill it serves sits
# under: skill://<SERVER_NAME>/<skill>/SKILL.md.
SERVER_NAME = "selenium-flow"

# The workspace an unnamed caller shares (stdio has STDIO_WORKSPACE). A caller
# keyed on an MCP transport id would get a new key on every reconnect, and a
# directory per key would fill the disk with folders nobody could reach again.
GLOBAL_WORKSPACE = "global"

# The library a stdio caller owns. A stdio client has no URL and no headers, so
# it cannot name itself; given the read-only shared library it would have
# nowhere to write and no way to get somewhere.
STDIO_WORKSPACE = "stdio"

# The data directory is a root with two homes in it: every workspace's own
# folder under `workspaces/`, and the inbox the Grid's
# recordings arrive in. Neither can collide with a workspace's name, because a
# workspace is one level further down.
WORKSPACES_DIR = "workspaces"
INBOX_DIR = "recordings"

# Where a workspace's own files land. `files` is the Files section (a print, and
# anything kept); `screenshots` and `recordings` hold what the server made until
# it is kept or cleared. Downloads are not a folder here: they are the Grid's.
FILES_DIR = "files"
SCREENSHOTS_DIR = "screenshots"
RECORDINGS_DIR = "recordings"
FOLDERS = (FILES_DIR, SCREENSHOTS_DIR, RECORDINGS_DIR)
# A workspace's own config, in the root of its folder beside them.
CONFIG_FILE = "workspace.yaml"

# The names a file in Files can never take, since workspace://files/<name> would
# then mean a folder, or meant one (`downloads`). One arriving under one
# lands as `name (1)`.
RESERVED_IN_FILES = frozenset({SCREENSHOTS_DIR, "downloads", RECORDINGS_DIR})

# A Grid session id becomes a note's file name. The Grid hands us 32 hex or a
# dashed UUID; anything else is refused before it touches a path.
GRID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$")

# A name becomes a path segment, and the workspace half of one arrives from a URL
# query parameter that anyone who can reach this port can write. Anchored, so
# `..`, `a/b`, a leading dot and an empty string are all refused by the same
# expression.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# A file name is handed to us, by the site that served the download or by
# Chrome deduplicating it into `report (1).pdf`, so `Q3 summary.csv` must be
# keepable. This asks only what a path segment must satisfy: no separator, no
# NUL or control character, and short enough for a filesystem. A leading dot
# (`.` and `..` included) is refused below: the Grid never hands us a dotfile
# (`is_partial` skips Chrome's scratch names), and one would let a listing
# write `.bashrc` into the workspace directory.
FILE_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,255}$")


class InvalidName(ValueError):
    """A workspace or flow name that cannot be used as one.

    A ValueError, so `errors.py` already classifies it as the caller's problem
    and returns 400 rather than 500.
    """


def valid_grid_id(value) -> str:
    """``value`` if it can name a note, else raise."""
    if not isinstance(value, str) or not GRID_ID.match(value):
        raise InvalidName(f"{value!r} is not a Grid session id")
    return value


def candidates(name: str):
    """``name``, then ``name (1)``, ``name (2)`` … the way a browser names a
    second download. Shared by every folder that never overwrites."""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    yield name
    n = 1
    while True:
        yield f"{stem} ({n}).{suffix}" if suffix else f"{stem} ({n})"
        n += 1


def valid_name(name, kind: str = "name") -> str:
    """``name`` if it can be a path segment, else raise.

    A bad name is rejected, never slugged into a good one: a slugged flow is
    written somewhere its caller will not look for it, and nothing says so.

    Surrounding whitespace is trimmed, the boundary coercion values from URL
    query parameters and hand-written JSON get everywhere (``as_bool``,
    ``as_int``): ``" bot "`` and ``"bot"`` name the same library, and
    ``"   "`` is still refused.
    """
    text = "" if name is None else str(name).strip()
    if not NAME.match(text):
        raise InvalidName(
            f"{text!r} is not a usable {kind}: use letters, digits, dots, "
            "dashes and underscores, starting with a letter or digit, "
            "64 characters at most"
        )
    return text


# Workspace names a caller may not claim. `stdio` is the stdio transport's own
# library: a caller that claimed it would read, overwrite and delete another
# client's flows and kept files. `global` is the shared one every workspace
# reads and none may write, so a caller that claimed it would own everyone's
# flows.
RESERVED_WORKSPACES = frozenset({STDIO_WORKSPACE, GLOBAL_WORKSPACE})


def valid_workspace_name(name) -> str:
    """A workspace name a caller is allowed to choose, else raise.

    Everything :func:`valid_name` requires, plus the reserved set. Kept apart
    because ``valid_name`` also validates flow names, and a flow may be called
    ``stdio`` or ``global``.
    """
    workspace = valid_name(name, "workspace name")
    if workspace in RESERVED_WORKSPACES:
        raise InvalidName(
            f"{workspace!r} is a reserved library name — {STDIO_WORKSPACE} belongs "
            f"to the stdio transport and {GLOBAL_WORKSPACE} is the shared library "
            "every workspace reads: choose another workspace name"
        )
    return workspace


def valid_file_name(name) -> str:
    """``name`` if it can be a file inside a workspace directory, else raise.

    Permissive where :func:`valid_name` is strict (see :data:`FILE_NAME`), and
    strict about one thing: the result must be a single, ordinary path segment.
    Traversal is refused here, and again by ``_resolved``, because this value
    reaches the store from a URL path parameter as well as from the Grid's own
    listing.

    Not trimmed: nobody typed a file name, so ``" report.pdf "`` is a different
    file from ``"report.pdf"``, and trimming it would have ``keep_one`` ask the
    Grid for a name it does not have. A name that is nothing but whitespace is
    refused.
    """
    text = "" if name is None else str(name)
    if not text.strip():
        raise InvalidName("a file name cannot be blank")
    if not FILE_NAME.match(text) or text.startswith("."):
        raise InvalidName(
            f"{text!r} is not a usable file name: it must be a single name "
            "with no path separators, no control characters and no leading dot"
        )
    return text


def library_of(key: str) -> str | None:
    """The directory a stored workspace key owns, or None if it cannot have one.

    Only the admin surface needs this: everywhere else a workspace name is
    validated where it arrives. The admin reads the store's keys, which include
    keys written by an older version (`named:desktop`, an MCP transport id) for
    up to a ``WORKSPACE_TTL``. It lists every workspace, so an unusable key gets
    None ("nowhere to keep anything") instead of taking the listing down or
    being shown the shared library as its own.

    ``valid_name`` rather than ``valid_workspace_name``: the reserved names are
    reserved against being claimed by a caller, and the two workspaces that own
    them are the ones this is asked about.
    """
    try:
        return valid_name(key, "workspace name")
    except InvalidName:
        return None


# URIs that moved, tried in order; the first match answers. A None is served:
# nothing to refuse. The last rows are for a saved flow that still names a
# `session://` URI as it was before `session://{name}` read a session (WS-73):
# only the roots it used to have, so any other name reads that session.
MOVED: tuple[tuple[str, str | None], ...] = (
    ("workspace://files/downloads", "session://downloads"),
    ("session://files/downloads", "session://downloads"),
    ("session://downloads", None),
    *(
        (f"session://{root}", f"workspace://{root}")
        for root in ("current", "files", "site-data", "console", "network")
    ),
)


def retired_uri(uri) -> None:
    """Refuse a URI that has moved, naming its new spelling."""
    text = str(uri or "")
    for old, new in MOVED:
        if old.endswith("://"):
            rest = text[len(old):] if text.startswith(old) else None
        elif text == old or text.startswith((old + "/", old + "?")):
            rest = text[len(old):]
        else:
            rest = None
        if rest is None:
            continue
        if new is not None:
            raise ValueError(f"`{text}` is now `{new + rest}`")
        return
