"""Where a session's saved flows and files live.

A **flow** is a sequence of tool calls, saved under a name and run later without
the model deciding what to call between the steps. This module is only the
*storage* for them: the naming rules, which session a caller's flows belong to,
and reading and writing the documents and files. Nothing here knows what a step
is or how to run one — see the saga's Chapter 1, §F1.1 through §F1.4. What a
document is as text — parsing it once, saying where it broke, summarising it —
is ``flows.library``.

**The codebase sees a directory and nothing else.** ``FLOW_DATA_DIR`` points at
one and the installer decides what is behind it: a folder on a laptop, an
``emptyDir`` in this cluster, a PVC, an NFS mount. That choice is deliberately
not ours, and it is why the backend is two small method sets rather than a
module full of ``open()`` calls — a WebDAV implementation is the next one, so
Nextcloud can hold these (§F1.12). `FlowStore` holds documents and `FileStore`
holds bytes, over one `SessionLayout`; `LocalFlowStore` is both under one root.

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

import copy
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..names import FILES_DIR, FOLDERS, InvalidName, valid_file_name, valid_name
from .library import dump, summary, view, yaml_complaint

if TYPE_CHECKING:
    from ..config import FlowSettings

log = logging.getLogger(__name__)

# Flows are YAML on disk and dicts in the API. YAML because a person edits these
# by hand and in the admin UI, and because PyYAML is already a dependency for
# /openapi.yaml (§F1.14).
SUFFIX = ".yaml"
FLOWS_DIR = "flows"


def valid_folder(folder) -> str:
    """``folder`` if it is one of this store's file folders, else raise."""
    if folder not in FOLDERS:
        raise InvalidName(
            f"{folder!r} is not a file folder: use one of {', '.join(FOLDERS)}"
        )
    return folder


def _listed(directory: Path, usable) -> list[tuple[str, os.stat_result]]:
    """The regular files in one of the store's directories, with their stats.

    ``directory`` has come through `_resolved`, and what is left to refuse per
    entry is a link or a name ``usable`` rejects. That used to be a realpath per
    entry, which was 67ms of a 100ms listing of 142 screenshots; scandir says
    which entries are links without asking (§F4.19).

    The per-entry realpath also caught a directory swapped for a link after
    `_resolved` returned. So the directory is pinned by descriptor first, and
    only listed if its path still resolves to itself and names the directory
    held (Copilot, #45). A link is refused as `_resolved` would refuse it.
    """
    pinned = os.scandir in os.supports_fd
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(directory, flags) if pinned else None
    except FileNotFoundError:
        return []
    except OSError as exc:  # ELOOP or ENOTDIR: a link, or not a directory
        raise InvalidName(f"{directory.name!r} is not a plain directory") from exc
    try:
        if not pinned and not directory.is_dir():
            return []
        held = os.fstat(fd) if pinned else None
        if directory.resolve() != directory or (
            pinned and not os.path.samestat(held, directory.stat())
        ):
            raise InvalidName(f"{directory.name!r} moved under a link while listed")
        found = []
        with os.scandir(fd if pinned else directory) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):  # a link is not
                    continue
                name = usable(entry.name)
                if name is None:
                    continue
                # Without a descriptor to pin, a swap during the listing is
                # still possible, so each entry keeps the old realpath check:
                # slower, only where `scandir(fd)` is missing (Copilot, #45).
                path = directory / entry.name
                if not pinned and path.resolve() != path:
                    raise InvalidName(f"{entry.name!r} does not resolve to itself")
                try:
                    found.append((name, entry.stat(follow_symlinks=False)))
                except FileNotFoundError:
                    # Deleted between the listing and the stat. Its absence is
                    # itself a change, and the next poll will agree.
                    continue
        return found
    finally:
        if fd is not None:
            os.close(fd)


def _replace(path: Path, text: str) -> None:
    """Write ``text`` as ``path`` in one step: a reader sees the old document or
    the new one, never part of either.

    Writing in place let a crash or an NFS hiccup between the open and the last
    write leave a truncated document, which reads as missing — the previous
    good version gone, silently, and a `global` flow another agent was about to
    run gone from every listing. So the text goes to a name beside the target
    that no listing addresses (a leading dot, and not `.yaml`), and is renamed
    over it: a rename within one directory is atomic, and the new inode is what
    `revision` already expects of a replace.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    # An ordinary create, so the umask decides the mode as it did in place.
    file = temporary.open("x", encoding="utf-8")
    try:
        with file:
            file.write(text)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _shaped(name: str, info: os.stat_result) -> dict:
    """A file entry from its name and its stat — see `FileStore._entry`."""
    return {
        "name": name,
        "size": info.st_size,
        "creationTime": int(info.st_mtime * 1000),
    }


class SessionLayout:
    """Where everything a session keeps lives: one directory per session.

    The only place a path is built. Both stores below ask this for their
    directories, so the link and traversal rules are written once.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

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

    def _session_dir(self, session: str, *parts: str) -> Path:
        """Somewhere inside one session's directory, with the name validated.

        Every path this store builds starts here. The three below each repeated
        `valid_name(session, "session name")`, which is the kind of duplication
        that survives until one copy is left out — and the one left out is a
        path built from an unchecked name.
        """
        return self._resolved(valid_name(session, "session name"), *parts)

    def sessions(self) -> list[str]:
        """Every session with a directory, for the admin view."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())


class FlowStore(SessionLayout):
    """A session's flow documents: YAML files under ``<session>/flows``."""

    def _flows_dir(self, session: str) -> Path:
        return self._session_dir(session, FLOWS_DIR)

    def _path(self, session: str, name: str) -> Path:
        return self._session_dir(
            session, FLOWS_DIR, f"{valid_name(name, 'flow name')}{SUFFIX}"
        )

    # -- reads ---------------------------------------------------------------

    def names(self, session: str) -> list[str]:
        return sorted(name for name, _ in self._flow_entries(session))

    def _flow_entries(self, session: str) -> list[tuple[str, os.stat_result]]:
        """Each flow's name and stat, from one pass over the directory.

        Anything this store would refuse to address is skipped rather than
        returned, because every caller of `names` turns a name back into a
        path. A directory is not only written by us: a hand-made
        `.hidden.yaml`, a macOS `._login.yaml` on a network mount, or a
        symlinked entry would otherwise be handed to `get`, raise, and take the
        whole listing down with it.
        """

        def usable(filename: str) -> str | None:
            if not filename.endswith(SUFFIX):
                return None
            stem = filename[: -len(SUFFIX)]
            try:
                return valid_name(stem, "flow name")
            except InvalidName:
                log.warning("ignoring %s: not a usable flow name", filename)
                return None

        return _listed(self._flows_dir(session), usable)

    def revision(self, session: str) -> str:
        """A token that changes whenever this session's flows do.

        Names *and* modification times, because the two answer different
        questions and the admin page needs both: a name appearing or leaving is
        a flow saved, deleted or moved, and an mtime moving is a flow edited in
        place — which a count cannot see and which is precisely what the YAML
        editor does.

        Cheap on purpose. It stats the files a listing already walks, and it is
        asked on a poll, so it must never open one.
        """
        # Inode and size beside the mtime, the way git guards against racy
        # timestamps: two edits inside one clock tick share an mtime, and an
        # atomic replace always brings a new inode (§F4.19).
        stamps = [
            f"{name}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"
            for name, info in sorted(self._flow_entries(session))
        ]
        return ";".join(stamps) or "0"

    def summaries(self, session: str) -> list[dict]:
        """Name, description and parameters for each flow — never the steps.

        A listing exists so a caller can choose one, and the steps are the bulk
        of a document. Keeping them out is what lets a listing stay affordable
        over a store that is not a local disk.
        """
        # Read through the parse cache rather than `get`: a listing takes two
        # small fields and a count, and copying every document whole to find
        # them was most of what a warm listing cost.
        return [
            summary(name, session, self._loaded(session, name) or {})
            for name in self.names(session)
        ]

    def get(self, session: str, name: str) -> dict | None:
        """One flow, or None if there is no such flow.

        A file that is not readable as a YAML mapping reads as absent rather
        than raising. Someone hand-edits these, and a broken one should make
        that flow missing, not every listing that walks past it fail.
        """
        flow = valid_name(name, "flow name")
        loaded = self._loaded(session, name)
        if loaded is None:
            return None
        # The name on disk wins over any name inside the document: the file is
        # what `get` was asked for, and a document claiming to be something else
        # would make save-then-get return a different flow. It is the *validated*
        # name, so `get(" login ")` reports `login` — the same identifier a
        # listing gives, rather than the caller's spelling of it.
        return {**copy.deepcopy(loaded), "name": flow}

    def _loaded(self, session: str, name: str) -> dict | None:
        """The document stored as ``name``, as the parse cache holds it.

        Shared with the cache (`library.view`): read it, never change it, and
        copy any part that leaves. None for anything `get` reads as absent.
        """
        path = self._path(session, name)
        try:
            loaded = view(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        # UnicodeDecodeError is a ValueError, NOT an OSError, so it needs
        # naming here: a hand-edited file with one bad byte — or a binary file
        # dropped in the directory — would otherwise take out every listing that
        # walked past it, which is exactly what this branch exists to prevent.
        except yaml.YAMLError as exc:
            # Sanitised, because the parser's own message quotes the line it
            # choked on and this goes to the log. See `yaml_complaint`.
            log.warning(
                "flow %s/%s could not be read: %s", session, name, yaml_complaint(exc)
            )
            return None
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("flow %s/%s could not be read: %s", session, name, exc)
            return None
        if not isinstance(loaded, dict):
            log.warning("flow %s/%s is not a mapping", session, name)
            return None
        return loaded

    # -- writes --------------------------------------------------------------

    def save(self, session: str, name: str, document: dict) -> dict:
        """Create or replace one flow. Returns what was stored.

        One verb for both, as §F1.5 has it: an agent does not know whether a
        name is taken until it lists, and if it listed then it already knows.
        """
        flow = valid_name(name, "flow name")
        path = self._path(session, flow)
        # The validated name, not the caller's: writing `name: " login "` into
        # login.yaml would put an identifier in the file that no lookup returns.
        stored = {**document, "name": flow}
        _replace(path, dump(stored))
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
        _replace(self._path(session, name), text)


class FileStore(SessionLayout):
    """A session's own files: bytes, in the folders :data:`FOLDERS` names.

    Bytes rather than documents, and deliberately the whole of what a file
    store needs: the Grid supplies the only other operations there are, and it
    supplies them for *its* files, not ours. Two folders, kept apart because
    Files is curated and screenshots are disposable until kept.
    """

    def _files_dir(self, session: str, folder: str = FILES_DIR) -> Path:
        return self._session_dir(session, valid_folder(folder))

    def _file_path(self, session: str, name: str, folder: str = FILES_DIR) -> Path:
        return self._resolved(
            valid_name(session, "session name"),
            valid_folder(folder),
            valid_file_name(name),
        )

    def _entry(self, path: Path) -> dict:
        """One file, in either Files or Screenshots, shaped like the Grid's own
        listing entry.

        The three sections are read separately, never merged (§F4.6, §F4.7),
        but they still have to agree on both the key names and the *units*: the
        Grid reports milliseconds, and a seconds-based timestamp beside it
        would sort every kept file to 1970 without anything looking wrong.
        """
        return _shaped(path.name, path.stat())

    def files(self, session: str, folder: str = FILES_DIR) -> list[dict]:
        """Every file in one folder of this session, newest first.

        Newest first because that is the order the Grid uses, and Files is
        shown interleaved with its entries.
        """

        def usable(name: str) -> str | None:
            # For the reason `_flow_entries` gives: a name the store would
            # refuse to address must not reach a caller that reads it back.
            try:
                return valid_file_name(name)
            except InvalidName:
                log.warning("ignoring file %r: not a usable name", name)
                return None

        found = [
            _shaped(name, info)
            for name, info in _listed(self._files_dir(session, folder), usable)
        ]
        return sorted(found, key=lambda f: f["creationTime"], reverse=True)

    def read_file(self, session: str, name: str, folder: str = FILES_DIR) -> bytes:
        """One file's bytes. Raises FileNotFoundError if it is not there."""
        return self._file_path(session, name, folder).read_bytes()

    def write_file(
        self, session: str, name: str, data: bytes, folder: str = FILES_DIR
    ) -> dict:
        """Keep one file, creating it or replacing it. Returns its entry.

        Create-or-replace, the same rule `save` follows: keeping a name that is
        already kept is how someone re-keeps a file they have since downloaded
        again, and the alternative is a second copy under a name nobody chose.
        """
        path = self._file_path(session, name, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._entry(path)

    def create_file(
        self, session: str, name: str, data: bytes, folder: str = FILES_DIR
    ) -> dict:
        """Keep one file under a name nothing has. `FileExistsError` if taken.

        The claim is the create itself (`O_EXCL`), so two saves racing for one
        name cannot both win and the second silently replace the first.
        """
        path = self._file_path(session, name, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            file.write(data)
        return self._entry(path)

    def delete_file(self, session: str, name: str, folder: str = FILES_DIR) -> bool:
        """Remove one file. False if it was not there."""
        try:
            self._file_path(session, name, folder).unlink()
        except FileNotFoundError:
            return False
        return True

    def clear_folder(self, session: str, folder: str) -> int:
        """Delete every file in one folder, returning how many went.

        Files is refused: everything in it was put there on purpose, so it is
        emptied one file at a time or not at all (§F4.1). Anything this store
        could not address is left where it is, as `files` skips it.
        """
        if valid_folder(folder) == FILES_DIR:
            raise InvalidName("Files is never cleared wholesale; delete one file")
        removed = 0
        for entry in self.files(session, folder):
            if self.delete_file(session, entry["name"], folder):
                removed += 1
        return removed


class LocalFlowStore(FlowStore, FileStore):
    """Flows and files as plain files under one directory.

    A session's files live in the same session directory as its flows rather
    than under a root of their own — one root, one env var, one thing to point
    at Nextcloud. Whatever is mounted there is the installer's business — a
    folder, an ``emptyDir``, a PVC, NFS. This class only ever sees a path.
    """

    kind = "local"


def from_settings(flow: FlowSettings) -> LocalFlowStore | None:
    """The flow store the config asks for, or None when flows are off (the default)."""
    if not flow.data_dir:
        log.info("flows: off (set flow.data_dir to enable them)")
        return None
    log.info("flows: local, under %s", flow.data_dir)
    return LocalFlowStore(flow.data_dir)
