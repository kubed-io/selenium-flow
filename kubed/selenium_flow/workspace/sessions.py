"""A session's key: telling a workspace's default session from its sub-sessions.

A workspace keeps one default session and may hold sub-sessions, each its own
browser. The store keys a record by the session key: the workspace's name for the
default, ``<workspace>/<name>`` for a sub-session. A name holds no ``/``
(``names.NAME``), so a sub-session's key can never be a workspace's.
"""

from __future__ import annotations

import re

from ..names import valid_name

# The session every workspace has, and the one a caller that names none uses.
DEFAULT = "default"
# What a sub-session's key joins its workspace and name with.
SEP = "/"
# Names that read a resource (`workspace://current`, `session://downloads`) or
# that a `session://` URI used to start with (`names.MOVED`), so a session
# called one would be unaddressable.
RESERVED = frozenset(
    {"current", "downloads", "console", "network", "files", "site-data"}
)


def key_of(workspace: str, name: str) -> str:
    """The store key of ``name`` in ``workspace``: the workspace for the default."""
    return workspace if name == DEFAULT else f"{workspace}{SEP}{name}"


def split(key: str) -> tuple[str, str]:
    """``(workspace, name)`` of a key; a workspace's own is ``(key, "default")``."""
    workspace, sep, name = key.partition(SEP)
    return (workspace, name) if sep else (key, DEFAULT)


def is_sub(key: str) -> bool:
    """Whether ``key`` names a sub-session rather than a workspace's default."""
    return SEP in key


def logged(key: str) -> str:
    """How a log line names the session ``key``: its workspace, and whether it
    is a sub-session, never the sub-session's name, which may be a token's
    subject (WS-49, 3)."""
    workspace, name = split(key)
    return workspace if name == DEFAULT else f"{workspace} (a sub-session)"


# A `session://<name>…` URI: the name is a token's subject for a sub-session,
# so a log line, and an error text (which is logged), shows `session://…`. Left
# as is: a reserved word as written, lower-case and followed by `/`, `?`, `#`
# or the end (`session://downloads…` is the caller's own; the others are roots
# that moved, `names.MOVED`), since a session is named case-sensitively and
# `Downloads` or `downloads-2` is a name; and a placeholder an error text
# writes, `session://{name}` or `session://<session>`, which no name starts
# with.
_STOP = "/?#\\s'\"`"
_WORDS = "|".join(re.escape(word) for word in sorted(RESERVED))
SESSION_NAME = re.compile(
    rf"(?i:session)://(?![{{<])(?!(?:{_WORDS})(?![^{_STOP}]))[^{_STOP}]+"
)


def nameless(text: str) -> str:
    """``text`` with the name of any `session://<name>` URI in it masked."""
    return SESSION_NAME.sub("session://…", text)


def valid_session_name(name) -> str:
    """``name`` if it can name a session, else raise ``ValueError``."""
    text = valid_name(name, "session name")
    if text in RESERVED:
        raise ValueError(f"{text!r} cannot name a session: choose another name")
    return text


# What a session's state is read under: `workspace://<path>` the caller's own,
# `session://<name>/<path>` any session by name.
STATE = ("console", "network", "site-data")
DOWNLOADS = "downloads"


def pointer(acted: str, own: str, path: str) -> str:
    """Where ``path`` (``console?level=error``, ``network/3.7``, ``site-data``,
    ``downloads/a.pdf``) of the session ``acted`` is read by a caller whose own
    session is ``own``: ``workspace://<path>`` (``session://downloads…`` for
    downloads) when they are one, else ``session://<name>/<path>``."""
    if acted != own:
        return f"session://{split(acted)[1]}/{path}"
    downloads = path == DOWNLOADS or path.startswith(DOWNLOADS + "/")
    return f"session://{path}" if downloads else f"workspace://{path}"


def repointed(uri, acted: str, own: str):
    """``uri``, a pointer into the caller's own session's state, made to point
    into ``acted`` instead (`pointer`); anything else as it is."""
    if not isinstance(uri, str) or acted == own:
        return uri
    for root, roots in (("workspace://", STATE), ("session://", (DOWNLOADS,))):
        path = uri[len(root):] if uri.startswith(root) else ""
        if path.partition("/")[0].partition("?")[0] in roots:
            return pointer(acted, own, path)
    return uri
