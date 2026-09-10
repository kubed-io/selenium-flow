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

# The session every caller that did not name itself shares. A caller keyed on an
# MCP transport id gets a new key on every reconnect, so a directory per key
# would bury the disk in folders whose flows nobody could ever reach again.
# Routing all of them here instead makes the unnamed case one stable, shared,
# useful place rather than infinite orphans (§F1.2).
#
# It is a legal session name like any other: ?session=global lands in the same
# directory, which is consistent rather than a special case.
GLOBAL_SESSION = "global"

# Flows are YAML on disk and dicts in the API. YAML because a person edits these
# by hand and in the admin UI, and because PyYAML is already a dependency for
# /openapi.yaml (§F1.14).
SUFFIX = ".yaml"
FLOWS_DIR = "flows"
# Not used yet: kept files land here in E4. Named now so the layout lives in one
# place rather than being invented twice.
FILES_DIR = "files"

# A name becomes a path segment, and the session half of one arrives from a URL
# query parameter that anyone who can reach this port can write. Anchored, so
# `..`, `a/b`, a leading dot and an empty string are all refused by the same
# expression.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class InvalidName(ValueError):
    """A session or flow name that cannot be used as one.

    A ValueError, so `errors.py` already classifies it as the caller's problem
    and returns 400 rather than 500.
    """


def valid_name(name, kind: str = "name") -> str:
    """``name`` if it can be a path segment, else raise.

    **Rejected, never sanitised.** Slugging a bad name into a good one saves the
    caller a round trip and costs them the flow: it is written somewhere they
    will not look for it again, and nothing ever says so. A 400 they fix in one
    try is strictly better than a file they cannot find.
    """
    text = "" if name is None else str(name).strip()
    if not NAME.match(text):
        raise InvalidName(
            f"{text!r} is not a usable {kind}: use letters, digits, dots, "
            "dashes and underscores, starting with a letter or digit, "
            "64 characters at most"
        )
    return text


def session_for(key) -> str:
    """The session whose flows this caller owns.

    A caller that named itself gets its own library. Everything else — an MCP
    transport key that changes on every reconnect, stdio, a caller with no key
    at all — shares :data:`GLOBAL_SESSION` (§F1.2).

    Note what falls out rather than being special-cased: an unnamed caller *is*
    the global session, so it writes there directly, while a named session's
    flows reach the shared library only by an admin promoting one. That
    asymmetry is real and is documented in §F1.2 — it is not an accident here.
    """
    if key is None:
        return GLOBAL_SESSION
    value = getattr(key, "value", "") or ""
    if not value.startswith("named:"):
        return GLOBAL_SESSION
    named = value[len("named:") :]
    try:
        return valid_name(named, "session name")
    except InvalidName:
        # A caller may put anything in ?session=. It still keys their *browser*
        # perfectly well — that is an opaque string in a store, not a path — so
        # refusing the browser over it would break a working session to protect
        # a feature they are not using. They get the shared library instead, and
        # the flow tools are where the name is refused out loud.
        log.info(
            "session key %r cannot name a directory; using %s", named, GLOBAL_SESSION
        )
        return GLOBAL_SESSION


class FlowStore(Protocol):
    """Reads and writes flow documents for a session.

    One protocol, two implementations eventually: this directory, and WebDAV.
    Kept files (E4) belong to the same session directory and will extend this
    rather than getting a store of their own — one root, one env var, one thing
    to point at Nextcloud.
    """

    kind: str

    def sessions(self) -> list[str]: ...

    def names(self, session: str) -> list[str]: ...

    def summaries(self, session: str) -> list[dict]: ...

    def get(self, session: str, name: str) -> dict | None: ...

    def save(self, session: str, name: str, document: dict) -> dict: ...

    def delete(self, session: str, name: str) -> bool: ...


class LocalFlowStore:
    """Flows as YAML files under one directory.

    Whatever is mounted there is the installer's business — a folder, an
    ``emptyDir``, a PVC, NFS. This class only ever sees a path.
    """

    kind = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    # -- layout: the only place a path is built -----------------------------

    def _session_dir(self, session: str) -> Path:
        """The directory holding one session's things.

        ``valid_name`` has already refused anything with a separator in it, so
        the resolve check below is belt and braces rather than the defence. It
        stays because this is the one place a caller's string becomes a
        filesystem path, and the cost of being wrong here is the whole disk.
        """
        name = valid_name(session, "session name")
        path = (self.root / name).resolve()
        if path != self.root.resolve() / name:
            raise InvalidName(f"{session!r} does not resolve inside the data directory")
        return path

    def _path(self, session: str, name: str) -> Path:
        flow = valid_name(name, "flow name")
        return self._session_dir(session) / FLOWS_DIR / f"{flow}{SUFFIX}"

    # -- reads ---------------------------------------------------------------

    def sessions(self) -> list[str]:
        """Every session with a directory, for the admin view."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def names(self, session: str) -> list[str]:
        directory = self._session_dir(session) / FLOWS_DIR
        if not directory.is_dir():
            return []
        return sorted(p.stem for p in directory.glob(f"*{SUFFIX}") if p.is_file())

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
                    "steps": len(document.get("steps") or []),
                }
            )
        return found

    def get(self, session: str, name: str) -> dict | None:
        """One flow, or None if there is no such flow.

        A file that is not readable as a YAML mapping reads as absent rather
        than raising. Someone hand-edits these, and a broken one should make
        that flow missing, not every listing that walks past it fail.
        """
        path = self._path(session, name)
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, yaml.YAMLError) as exc:
            log.warning("flow %s/%s could not be read: %s", session, name, exc)
            return None
        if not isinstance(loaded, dict):
            log.warning("flow %s/%s is not a mapping", session, name)
            return None
        # The name on disk wins over any name inside the document: the file is
        # what `get` was asked for, and a document claiming to be something else
        # would make save-then-get return a different flow.
        return {**loaded, "name": name}

    # -- writes --------------------------------------------------------------

    def save(self, session: str, name: str, document: dict) -> dict:
        """Create or replace one flow. Returns what was stored.

        One verb for both, as §F1.5 has it: an agent does not know whether a
        name is taken until it lists, and if it listed then it already knows.
        """
        path = self._path(session, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        stored = {**document, "name": name}
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
