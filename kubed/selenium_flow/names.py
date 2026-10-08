"""Who a thing is called, and which names are allowed.

The identity rules, with nothing else in them: what may name a session, a flow
or a file, which names are spoken for, and which library a stored key owns. They
live apart from ``flows.library`` because the browser actions, the secrets
catalogue, the config and the session layer all need them, and none of those
should have to import the flow store to ask whether a string is a name.
"""

from __future__ import annotations

import re

# The session an unnamed caller shares — with the one exception of stdio, which
# gets STDIO_SESSION below because it cannot name itself and would otherwise
# have nowhere writable. A caller keyed on an
# MCP transport id gets a new key on every reconnect, so a directory per key
# would bury the disk in folders whose flows nobody could ever reach again.
# Routing all of them here instead makes the unnamed case one stable, shared,
# useful place rather than infinite orphans (§F1.2).
#
# It is a legal session name like any other: ?session=global lands in the same
# directory, which is consistent rather than a special case.
GLOBAL_SESSION = "global"

# The library a stdio caller owns. Stdio is one process serving one client, so
# a constant is exactly right — the same reasoning that makes `stdio` a usable
# caller key makes it a usable directory name.
#
# It has to be its own library rather than `global`, and that is not a
# preference: a stdio client has no URL and no headers, so it cannot name
# itself. Resolving it to the read-only shared library would leave it with no
# writable library at all and no way to obtain one, which is a refusal whose
# remedy cannot be performed (§F1.2).
STDIO_SESSION = "stdio"

# The data directory is a root with two homes in it (recordings spec, ruling 6):
# every session's own folder under `sessions/`, and the inbox the Grid's
# recordings arrive in. Neither can collide with a session's name, because a
# session is one level further down.
SESSIONS_DIR = "sessions"
INBOX_DIR = "recordings"

# Where a session's own files land. `files` IS the Files section — a print, and
# anything kept; `screenshots` holds every screenshot until it is kept or
# cleared (§F4.1). Downloads are not a folder here: they are the Grid's.
FILES_DIR = "files"
SCREENSHOTS_DIR = "screenshots"
FOLDERS = (FILES_DIR, SCREENSHOTS_DIR)

# A name becomes a path segment, and the session half of one arrives from a URL
# query parameter that anyone who can reach this port can write. Anchored, so
# `..`, `a/b`, a leading dot and an empty string are all refused by the same
# expression.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# A FILE name plays by different rules, and the difference is about who chose
# it. A session or flow name is picked by a caller, so refusing an untidy one
# costs them a retry. A file name is *handed to us* — by the site that served
# the download, or by Chrome deduplicating it into `report (1).pdf` — so the
# same rule would make perfectly ordinary files unkeepable, and `Q3 summary.csv`
# is not a mistake anyone can fix.
#
# So this asks only what a path segment must satisfy: no separator, no NUL or
# control character, and short enough for a filesystem. `.` and `..` and any
# leading dot are refused below, since a dotfile is never something the Grid
# hands us — `is_partial` already skips Chrome's scratch names — and allowing
# one would let a listing write `.bashrc` into the session directory.
FILE_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,255}$")


class InvalidName(ValueError):
    """A session or flow name that cannot be used as one.

    A ValueError, so `errors.py` already classifies it as the caller's problem
    and returns 400 rather than 500.
    """


def valid_name(name, kind: str = "name") -> str:
    """``name`` if it can be a path segment, else raise.

    **A bad name is rejected, never slugged into a good one.** Slugging saves
    the caller a round trip and costs them the flow: it is written somewhere
    they will not look for it again, and nothing ever says so. A 400 they fix in
    one try is strictly better than a file they cannot find.

    Surrounding whitespace is the one thing trimmed, and it is worth being
    precise about why that is not the same act. Slugging *rewrites a name that
    was refused* into a different, accepted one. Trimming is the ordinary
    boundary coercion this package does everywhere — see ``as_bool`` and
    ``as_int`` — because these values arrive from URL query parameters and JSON
    written by hand, where a trailing space is a typo rather than an intent.
    ``" bot "`` and ``"bot"`` therefore name the same library, deliberately, and
    ``"   "`` is still refused because it names nothing at all.
    """
    text = "" if name is None else str(name).strip()
    if not NAME.match(text):
        raise InvalidName(
            f"{text!r} is not a usable {kind}: use letters, digits, dots, "
            "dashes and underscores, starting with a letter or digit, "
            "64 characters at most"
        )
    return text


# Session names a caller may not claim. `stdio` is here because the stdio
# transport owns that library and cannot name itself anything else: a caller
# that claimed the name would be reading, overwriting and deleting another
# client's flows and kept files — the one guarantee naming a session buys.
#
# Neither can be claimed by a caller naming itself. `stdio` is the transport's
# own library; `global` is the shared one every session reads and none may
# write (§F1.2), so a caller that could claim it would own everyone's flows.
RESERVED_SESSIONS = frozenset({STDIO_SESSION, GLOBAL_SESSION})


def valid_session_name(name) -> str:
    """A session name a caller is allowed to choose, else raise.

    Everything :func:`valid_name` requires, plus the reserved set. Kept apart
    from ``valid_name`` because that one also validates *flow* names, and a flow
    called ``stdio`` or ``global`` is perfectly reasonable — it is only the
    library name that is spoken for.
    """
    session = valid_name(name, "session name")
    if session in RESERVED_SESSIONS:
        raise InvalidName(
            f"{session!r} is a reserved library name — {STDIO_SESSION} belongs "
            f"to the stdio transport and {GLOBAL_SESSION} is the shared library "
            "every session reads: choose another session name"
        )
    return session


def valid_file_name(name) -> str:
    """``name`` if it can be a file inside a session directory, else raise.

    Deliberately permissive where :func:`valid_name` is strict — see
    :data:`FILE_NAME` for why — and strict about exactly one thing: the result
    must be a single, ordinary path segment. Traversal is refused here, and
    refused again by ``_resolved``, because this value reaches the store from a
    URL path parameter as well as from the Grid's own listing.

    **Not trimmed, unlike :func:`valid_name`**, and the difference is the same
    one that motivates this function at all. Trimming a session name is ordinary
    boundary coercion because a caller typed it and a trailing space is a typo.
    Nobody typed a file name: it is whatever the site's ``Content-Disposition``
    or Chrome called the thing. So ``" report.pdf "`` is a *different file* from
    ``"report.pdf"``, and silently trimming it made ``keep_one`` ask the Grid for
    a name it does not have — the copy then could not round-trip through read or
    delete either. A name that is nothing but whitespace names nothing and is
    still refused.
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
    """The directory a stored session key owns, or None if it cannot have one.

    **Only the admin surface needs this.** Everywhere else a session name is
    validated where it arrives (§F2.12), so by the time anything asks, the name
    is already a directory name — which is why the two *other* resolvers that
    used to live here are gone.

    The admin cannot make that assumption, because it reads the store's keys
    rather than a live caller's name. Those include keys written by an older
    version, where a key was `named:desktop` or an MCP transport id, and they
    outlive an upgrade by a whole ``SESSION_TTL``. It lists **every** session
    there is, so one unusable row must not take the listing down: it gets None,
    meaning "this session has nowhere to keep anything", and is shown as having
    no library rather than being shown the shared one as though it were its own.

    ``valid_name`` rather than ``valid_session_name``: the reserved names are
    reserved against being *claimed* by a caller, and the two sessions that
    legitimately own them are exactly the ones this function is asked about.
    """
    try:
        return valid_name(key, "session name")
    except InvalidName:
        return None
