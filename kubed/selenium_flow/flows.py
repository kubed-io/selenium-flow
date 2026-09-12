"""Where a session's saved flows live.

A **flow** is a sequence of tool calls, saved under a name and run later without
the model deciding what to call between the steps. This module is only the
*storage* for them: the naming rules, which session a caller's flows belong to,
and reading and writing the documents. Nothing here knows what a step is or how
to run one — see the saga's Chapter 1, §F1.1 through §F1.4.

**The codebase sees a directory and nothing else.** ``FLOW_DATA_DIR`` points at
one and the installer decides what is behind it: a folder on a laptop, an
``emptyDir`` in this cluster, a PVC, an NFS mount. That choice is deliberately
not ours, and it is why the backend is a protocol rather than a module full of
``open()`` calls — a WebDAV implementation is the next one, so Nextcloud can
hold these (§F1.12).

Two rules that exist for the backend that does not exist yet:

- **No path arithmetic outside this module.** Callers ask for "the flows of
  session X" and get documents. The moment something elsewhere joins a path with
  ``/``, the WebDAV backend has to reimplement it.
- **No assumption that a read is cheap or local.** ``summaries`` returns names
  and descriptions rather than whole documents precisely so a listing stays one
  cheap operation over a network store.

Unset ``FLOW_DATA_DIR`` means the feature is off, and deliberately not a
fallback to a temp directory. The point is not durability — an ``emptyDir`` a
redeploy wipes is an accepted backing — it is that *the operator chose where
this lives*. Falling back to ``/tmp`` would put flows on the 64Mi volume the
upload staging area uses, where they would vanish for a reason nobody could
trace to a decision they never made.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Protocol

import yaml

log = logging.getLogger(__name__)

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

# Flows are YAML on disk and dicts in the API. YAML because a person edits these
# by hand and in the admin UI, and because PyYAML is already a dependency for
# /openapi.yaml (§F1.14).
SUFFIX = ".yaml"
FLOWS_DIR = "flows"
# Where a kept file lands — a copy of something the browser downloaded, taken
# out of the Grid's store so it outlives the browser (§F1.10).
FILES_DIR = "files"

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
# `global` is deliberately NOT reserved. It is the *shared* library, so naming
# it is how a caller asks for it on purpose, and a collision there is the
# intended behaviour rather than a leak.
RESERVED_SESSIONS = frozenset({STDIO_SESSION})


def valid_session_name(name) -> str:
    """A session name a caller is allowed to choose, else raise.

    Everything :func:`valid_name` requires, plus the reserved set. Kept apart
    from ``valid_name`` because that one also validates *flow* names, and a flow
    called ``stdio`` is perfectly reasonable — it is only the library name that
    is spoken for.
    """
    session = valid_name(name, "session name")
    if session in RESERVED_SESSIONS:
        raise InvalidName(
            f"{session!r} is reserved for the stdio transport's own library, "
            "which no other caller may write to: choose another session name"
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


def named_session(key) -> str | None:
    """The name a caller gave itself, exactly as given, or None if it gave none.

    Accepts a ``CallerKey`` or the bare string a store holds. The admin surface
    only ever has the string — it reads the store's keys rather than a live
    caller — and giving it a second way to unpack one is how the two would come
    to disagree about which session owns a file.
    """
    value = key_value(key)
    if not value.startswith("named:"):
        return None
    return value[len("named:") :]


def key_value(key) -> str:
    """The store-key string behind a ``CallerKey``, a bare string, or nothing.

    One unpacking, because the admin surface holds keys as strings while the
    live path holds them as objects, and two ways to read one is how the two
    come to disagree about which session owns a file.
    """
    if key is None:
        return ""
    return key if isinstance(key, str) else (getattr(key, "value", "") or "")


def session_for(key) -> str:
    """The session whose flows this caller owns.

    A caller that named itself gets its own library, and **stdio gets
    :data:`STDIO_SESSION`** — it cannot name itself, so it is given one. What is
    left — an MCP transport key that changes on every reconnect, a caller with
    no key at all — shares :data:`GLOBAL_SESSION` (§F1.2).

    Note what falls out rather than being special-cased: an unnamed caller *is*
    the global session. It reads and runs the shared library like everyone
    else, and writes nowhere at all — `global` is read-only to every agent
    (§F1.2), so naming yourself is how you get somewhere to write. That closes
    the asymmetry this docstring used to have to describe, where the anonymous
    callers were the only ones who *could* change the shared library.

    Enforcing it is `flowapi.writable`'s job, not this function's: here we only
    decide which directory a key maps to.

    The lenient one of the three resolvers: it shares :func:`library_of`'s rules
    and differs only in what it does with a name no directory can be called.
    """
    session = library_of(key)
    if session is None:
        # A caller may put anything in ?session=. It still keys their *browser*
        # perfectly well — that is an opaque string in a store, not a path — so
        # refusing the browser over it would break a working session to protect
        # a feature they are not using. They get the shared library instead, and
        # the flow tools are where the name is refused out loud.
        log.info(
            "session key %r cannot name a directory; using %s",
            named_session(key),
            GLOBAL_SESSION,
        )
        return GLOBAL_SESSION
    return session


def session_of(sessions, explicit: str | None = None) -> str:
    """Whose library — and whose kept files — this call is about.

    An explicit name is how the HTTP surface says it, because that surface is
    always explicit — the same contract ``/browser/*`` already has. Over MCP it
    comes from the caller's key, and anything unnamed is :data:`GLOBAL_SESSION`
    (§F1.2).

    A caller that NAMED itself and cannot have that name as a directory is
    refused here, out loud. :func:`session_for` falls back to ``global`` for
    such a name, which is right for the browser — an opaque key, and refusing it
    would break a working session — and is wrong here: ``?session=my bot`` got a
    private browser and saved its flows into the shared library, where every
    unnamed caller can overwrite or delete them, while believing they were its
    own. Kept files inherit the same rule for the same reason, which is why this
    lives here rather than beside the flow tools that first needed it.
    """
    if explicit:
        return valid_session_name(explicit)
    # `library_key`, not `key`: which library a caller owns does not depend on
    # whether this server is remembering browsers. See `sessions.library_key`.
    key = sessions.library_key()
    session = library_of(key)
    if session is None:
        # Raised rather than returned, and by the same validator, so the message
        # names the offending value — and says *which* rule it broke, an
        # unusable name and a reserved one being different problems.
        return valid_session_name(named_session(key))
    return session


def library_of(key) -> str | None:
    """The session directory a store key owns, or None if it cannot have one.

    The strict counterpart to :func:`session_for`, and they differ on exactly
    one input: a caller that named itself something no directory can be called.

    :func:`session_for` hands that caller ``global``. That is right for a
    *browser* — the key is an opaque string there, and refusing it would break a
    working session over a feature they are not using — and it is wrong for
    anything **stored**, because the caller's private flows and files would land
    in the shared library, where every unnamed caller can read them. That is the
    bug E6 fixed for flows; this function exists so it cannot come back for
    files.

    :func:`session_of` refuses such a name out loud, which is right on a surface
    where one caller is waiting for one answer. The admin surface cannot refuse:
    it lists **every** session there is, unusable names included, and one bad row
    must not take the listing down. So it gets a third answer — None, meaning
    "this session has nowhere to keep anything" — and shows it as unknown rather
    than borrowing the shared library's.
    """
    if key_value(key) == STDIO_SESSION:
        return STDIO_SESSION
    named = named_session(key)
    if named is None:
        return GLOBAL_SESSION
    try:
        # `valid_session_name`, so a caller cannot claim `stdio` and land in the
        # transport's private library. The check has to be here rather than only
        # at the write gate: reading another client's flows is the same leak.
        return valid_session_name(named)
    except InvalidName:
        return None


def _step_count(document: dict) -> int:
    """How many steps a document has, for a listing.

    Defensive about the type because a person edits these: ``steps: 1`` would
    reach ``len()`` and raise, and ``steps: {}`` would quietly report a mapping's
    size as a number of steps. Either one aborts a listing and hides every other
    flow in the session, which is the failure the corruption handling in `get`
    exists to prevent — so it must not come back in through the summary.
    """
    steps = document.get("steps")
    return len(steps) if isinstance(steps, list) else 0


class FlowStore(Protocol):
    """Reads and writes a session's flow documents and its kept files.

    One protocol, two implementations eventually: this directory, and WebDAV.
    Kept files live in the same session directory and extend this rather than
    getting a store of their own — one root, one env var, one thing to point at
    Nextcloud.

    The file half is bytes rather than documents, and is deliberately the whole
    of what a file store needs: the Grid supplies the only other operations
    there are, and it supplies them for *its* files, not ours.
    """

    kind: str

    def sessions(self) -> list[str]: ...

    def names(self, session: str) -> list[str]: ...

    def summaries(self, session: str) -> list[dict]: ...

    def get(self, session: str, name: str) -> dict | None: ...

    def save(self, session: str, name: str, document: dict) -> dict: ...

    def delete(self, session: str, name: str) -> bool: ...

    def read_text(self, session: str, name: str) -> str | None: ...

    def write_text(self, session: str, name: str, text: str) -> None: ...

    def files(self, session: str) -> list[dict]: ...

    def read_file(self, session: str, name: str) -> bytes: ...

    def write_file(self, session: str, name: str, data: bytes) -> dict: ...

    def delete_file(self, session: str, name: str) -> bool: ...


class LocalFlowStore:
    """Flows as YAML files under one directory.

    Whatever is mounted there is the installer's business — a folder, an
    ``emptyDir``, a PVC, NFS. This class only ever sees a path.
    """

    kind = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    # -- layout: the only place a path is built -----------------------------

    def _resolved(self, *parts: str) -> Path:
        """A path inside the data directory, or refuse.

        ``valid_name`` has already refused anything with a separator in it, so a
        caller cannot traverse out with a name alone. This is the second half,
        and it is not redundant: ``resolve()`` follows **symlinks at every
        level**, so a link left at ``<session>/flows`` pointing somewhere else
        is caught here and nowhere else. Checking only the session directory
        would have let a pre-existing link redirect every read and write under
        it while the boundary still looked guarded.

        "Inside the data directory" turned out to be too weak a guarantee:
        ``bot/flows -> ../research-bot/flows`` resolves to somewhere perfectly
        legal by that rule and still hands one session another's flows, which
        breaks the ownership rule this module's whole layout exists to enforce.

        So the check is equality, not containment: the resolved path must be
        the path we asked for. Nothing below the root may traverse a link.
        The root itself is resolved first and so may be one — pointing
        ``FLOW_DATA_DIR`` at a mount is the installer's business (§F1.12), and
        it is only the parts *we* join on that have to be honest.
        """
        root = self.root.resolve()
        expected = root.joinpath(*parts)
        path = expected.resolve()
        if path != expected:
            raise InvalidName(
                f"{'/'.join(parts)!r} does not resolve to itself inside the data "
                "directory — something on that path is a link"
            )
        return path

    def _session_dir(self, session: str) -> Path:
        """The directory holding one session's things."""
        return self._resolved(valid_name(session, "session name"))

    def _flows_dir(self, session: str) -> Path:
        return self._resolved(valid_name(session, "session name"), FLOWS_DIR)

    def _path(self, session: str, name: str) -> Path:
        return self._resolved(
            valid_name(session, "session name"),
            FLOWS_DIR,
            f"{valid_name(name, 'flow name')}{SUFFIX}",
        )

    def _files_dir(self, session: str) -> Path:
        return self._resolved(valid_name(session, "session name"), FILES_DIR)

    def _file_path(self, session: str, name: str) -> Path:
        return self._resolved(
            valid_name(session, "session name"),
            FILES_DIR,
            valid_file_name(name),
        )

    # -- reads ---------------------------------------------------------------

    def sessions(self) -> list[str]:
        """Every session with a directory, for the admin view."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def names(self, session: str) -> list[str]:
        directory = self._flows_dir(session)
        if not directory.is_dir():
            return []
        found = []
        for path in directory.glob(f"*{SUFFIX}"):
            if not path.is_file():
                continue
            # Anything this store would refuse to address is skipped rather
            # than returned, because every caller of `names` turns a name back
            # into a path. A directory is not only written by us: a hand-made
            # `.hidden.yaml`, a macOS `._login.yaml` on a network mount, or a
            # symlinked entry would otherwise be handed to `get`, raise, and
            # take the whole listing down with it.
            try:
                self._path(session, path.stem)
            except InvalidName:
                log.warning("ignoring %s: not a usable flow name", path.name)
                continue
            found.append(path.stem)
        return sorted(found)

    def summaries(self, session: str) -> list[dict]:
        """Name, description and parameters for each flow — never the steps.

        A listing exists so a caller can choose one, and the steps are the bulk
        of a document. Keeping them out is what lets a listing stay affordable
        over a store that is not a local disk.
        """
        found = []
        for name in self.names(session):
            document = self.get(session, name) or {}
            found.append(
                {
                    "name": name,
                    "session": session,
                    "description": document.get("description", ""),
                    "parameters": document.get("parameters", {}),
                    # Named for what it is. Calling it `steps` would put an int
                    # where the document itself carries a list, and E2 reads
                    # both — one consumer doing summary["steps"][0] is the whole
                    # cost of the shorter name.
                    "step_count": _step_count(document),
                }
            )
        return found

    def get(self, session: str, name: str) -> dict | None:
        """One flow, or None if there is no such flow.

        A file that is not readable as a YAML mapping reads as absent rather
        than raising. Someone hand-edits these, and a broken one should make
        that flow missing, not every listing that walks past it fail.
        """
        flow = valid_name(name, "flow name")
        path = self._path(session, flow)
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        # UnicodeDecodeError is a ValueError, NOT an OSError, so it needs
        # naming here: a hand-edited file with one bad byte — or a binary file
        # dropped in the directory — would otherwise take out every listing that
        # walked past it, which is exactly what this branch exists to prevent.
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            log.warning("flow %s/%s could not be read: %s", session, name, exc)
            return None
        if not isinstance(loaded, dict):
            log.warning("flow %s/%s is not a mapping", session, name)
            return None
        # The name on disk wins over any name inside the document: the file is
        # what `get` was asked for, and a document claiming to be something else
        # would make save-then-get return a different flow. It is the *validated*
        # name, so `get(" login ")` reports `login` — the same identifier a
        # listing gives, rather than the caller's spelling of it.
        return {**loaded, "name": flow}

    # -- writes --------------------------------------------------------------

    def save(self, session: str, name: str, document: dict) -> dict:
        """Create or replace one flow. Returns what was stored.

        One verb for both, as §F1.5 has it: an agent does not know whether a
        name is taken until it lists, and if it listed then it already knows.
        """
        flow = valid_name(name, "flow name")
        path = self._path(session, flow)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The validated name, not the caller's: writing `name: " login "` into
        # login.yaml would put an identifier in the file that no lookup returns.
        stored = {**document, "name": flow}
        path.write_text(
            yaml.safe_dump(stored, sort_keys=False, width=100, allow_unicode=True),
            encoding="utf-8",
        )
        return stored

    def delete(self, session: str, name: str) -> bool:
        """Remove one flow. False if it was not there."""
        try:
            self._path(session, name).unlink()
        except FileNotFoundError:
            return False
        return True

    # -- the document as text, for the editor --------------------------------

    def read_text(self, session: str, name: str) -> str | None:
        """One flow exactly as it sits on disk, or None if it is not there.

        The editor edits **YAML**, not a re-dump of a parsed dict (§F1.14). A
        person writes comments in these, and ordering they chose; round-tripping
        through ``get`` and ``safe_dump`` would silently throw both away the
        first time anybody opened the editor and saved.
        """
        try:
            return self._path(session, name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write_text(self, session: str, name: str, text: str) -> None:
        """Store one flow's YAML verbatim.

        Verbatim for the same reason ``read_text`` exists: what a person typed
        is what is kept. **The caller validates first** — this writes whatever
        it is handed, and an invalid document reaching disk is how a listing
        starts skipping a flow nobody can see is broken.
        """
        path = self._path(session, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    # -- kept files ----------------------------------------------------------

    def _entry(self, path: Path) -> dict:
        """One kept file, shaped exactly like the Grid's own listing entry.

        The two listings are merged into one array (§F1.10), so they have to
        agree on both the key names and the *units*: the Grid reports
        milliseconds, and a seconds-based timestamp beside it would sort every
        kept file to 1970 without anything looking wrong.
        """
        info = path.stat()
        return {
            "name": path.name,
            "size": info.st_size,
            "creationTime": int(info.st_mtime * 1000),
        }

    def files(self, session: str) -> list[dict]:
        """Every file kept for this session, newest first.

        Newest first because that is the order the Grid uses, and these are
        shown interleaved with its entries.
        """
        directory = self._files_dir(session)
        if not directory.is_dir():
            return []
        found = []
        for path in directory.iterdir():
            if not path.is_file():
                continue
            # Anything this store would refuse to address is skipped rather than
            # returned, for the reason `names` gives: every caller turns a name
            # back into a path, so an entry that cannot round-trip would be
            # handed to `read_file`, raise, and take the listing down with it.
            try:
                self._file_path(session, path.name)
            except InvalidName:
                log.warning("ignoring kept file %r: not a usable name", path.name)
                continue
            found.append(self._entry(path))
        return sorted(found, key=lambda f: f["creationTime"], reverse=True)

    def read_file(self, session: str, name: str) -> bytes:
        """One kept file's bytes. Raises FileNotFoundError if it is not there."""
        return self._file_path(session, name).read_bytes()

    def write_file(self, session: str, name: str, data: bytes) -> dict:
        """Keep one file, creating it or replacing it. Returns its entry.

        Create-or-replace, the same rule `save` follows: keeping a name that is
        already kept is how someone re-keeps a file they have since downloaded
        again, and the alternative is a second copy under a name nobody chose.
        """
        path = self._file_path(session, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._entry(path)

    def delete_file(self, session: str, name: str) -> bool:
        """Remove one kept file. False if it was not there."""
        try:
            self._file_path(session, name).unlink()
        except FileNotFoundError:
            return False
        return True


def from_env(env: dict | None = None) -> FlowStore | None:
    """The flow store the environment asks for, or None if flows are off.

    None is a real answer and the default one. The tools that need a store say
    so when there is not one, rather than this inventing somewhere to write.
    """
    env = os.environ if env is None else env
    root = str(env.get("FLOW_DATA_DIR", "")).strip()
    if not root:
        log.info("flows: off (set FLOW_DATA_DIR to enable them)")
        return None
    log.info("flows: local, under %s", root)
    return LocalFlowStore(root)
