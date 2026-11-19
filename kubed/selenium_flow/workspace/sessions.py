"""A session's key: telling a workspace's default session from its sub-sessions.

A workspace keeps one default session and may hold sub-sessions, each its own
browser. The store keys a record by the session key: the workspace's name for the
default, ``<workspace>/<name>`` for a sub-session. A name holds no ``/``
(``names.NAME``), so a sub-session's key can never be a workspace's.
"""

from __future__ import annotations

from ..names import valid_name

# The session every workspace has, and the one a caller that names none uses.
DEFAULT = "default"
# What a sub-session's key joins its workspace and name with.
SEP = "/"
# Names that read a resource (`workspace://current`, `session://downloads`), so
# a session called one would be unaddressable.
RESERVED = frozenset({"current", "downloads"})


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


def valid_session_name(name) -> str:
    """``name`` if it can name a session, else raise ``ValueError``."""
    text = valid_name(name, "session name")
    if text in RESERVED:
        raise ValueError(f"{text!r} cannot name a session: choose another name")
    return text
