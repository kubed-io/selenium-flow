"""Where a session's saved flows and files live.

A **flow** is a sequence of tool calls, saved under a name and run later without
the model deciding what to call between the steps. This module is only the
*storage* for them: the naming rules, which session a caller's flows belong to,
and reading and writing the documents and files. Nothing here knows what a step
is or how to run one — see the saga's Chapter 1, §F1.1 through §F1.4. What a
document is as text — parsing it once, saying where it broke, summarising it —
is ``flows.library``.

**The codebase sees a directory and nothing else.** ``DATA_DIR`` points at
one (sessions live under ``DATA_DIR/sessions``) and the installer decides what is
behind it: a folder on a laptop, an
``emptyDir`` in this cluster, a PVC, an NFS mount. That choice is deliberately
not ours, and it is why the backend is two small method sets rather than a
module full of ``open()`` calls — a WebDAV implementation is the next one, so
Nextcloud can hold these (§F1.12). `FlowStore` holds documents and `FileStore`
holds bytes, over one `WorkspaceLayout`; `LocalFlowStore` is both under one root.

Two rules that exist for the backend that does not exist yet:

- **No path arithmetic outside this module.** Callers ask for "the flows of
  session X" and get documents. The moment something elsewhere joins a path with
  ``/``, the WebDAV backend has to reimplement it.
- **No assumption that a read is cheap or local.** ``summaries`` returns names
  and descriptions rather than whole documents precisely so a listing stays one
  cheap operation over a network store.

Unset ``DATA_DIR`` means the feature is off, and deliberately not a
fallback to a temp directory. The point is not durability — an ``emptyDir`` a
redeploy wipes is an accepted backing — it is that *the operator chose where
this lives*. Falling back to ``/tmp`` would put flows on the 64Mi volume the
upload staging area uses, where they would vanish for a reason nobody could
trace to a decision they never made.
"""

from __future__ import annotations

import contextlib
import copy
import errno
import json
import logging
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..names import (
    FILES_DIR,
    FOLDERS,
    INBOX_DIR,
    RECORDINGS_DIR,
    RESERVED_IN_FILES,
    WORKSPACES_DIR,
    InvalidName,
    candidates,
    valid_file_name,
    valid_grid_id,
    valid_name,
)
from .library import dump, summary, view, yaml_complaint

if TYPE_CHECKING:
    from ..config import DataSettings

# The store's old logger name, kept: operators filter Loki by it.
log = logging.getLogger("kubed.selenium_flow.flows.library")

# Flows are YAML on disk and dicts in the API. YAML because a person edits these
# by hand and in the admin UI, and because PyYAML is already a dependency for
# /openapi.yaml (§F1.14).
SUFFIX = ".yaml"
FLOWS_DIR = "flows"
PENDING_DIR = ".pending"
_NO_LINK = frozenset({errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK})


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


def _open_regular(path: Path) -> int:
    """An fd on ``path`` that is a regular file and not reached through a link.

    EINVAL for anything else (a link, a FIFO, a directory): never opened for
    reading past the check, never blocking.
    """
    try:
        fd = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        )
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise OSError(errno.EINVAL, "not a regular file") from exc
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(errno.EINVAL, "not a regular file")
    return fd


open_regular = _open_regular


def _no_link(exc: OSError) -> bool:
    """Whether a failed ``os.link`` means links are unavailable, not a real fault."""
    return isinstance(exc, PermissionError) or exc.errno in _NO_LINK


def _replace(path: Path, text: str | bytes, *, sync: bool = False) -> None:
    """Write ``text`` as ``path`` in one step: a reader sees the old document or
    the new one, never part of either. ``sync`` flushes it to disk before the
    rename, for bytes that have no other copy.

    Writing in place let a crash or an NFS hiccup between the open and the last
    write leave a truncated document, which reads as missing — the previous
    good version gone, silently, and a `global` flow another agent was about to
    run gone from every listing. So the text goes to a name beside the target
    that no listing addresses (a leading dot, and not `.yaml`), and is renamed
    over it: a rename within one directory is atomic, and the new inode is what
    `revision` already expects of a replace.

    A file being replaced keeps its mode, as one rewritten in place did; a new
    one gets the umask's, from an ordinary create.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    if isinstance(text, bytes):
        file = temporary.open("xb")
    else:
        file = temporary.open("x", encoding="utf-8")
    try:
        with file:
            file.write(text)
            if sync:
                file.flush()
                os.fsync(file.fileno())
        # Nothing to replace means the new file keeps the umask's mode.
        with contextlib.suppress(FileNotFoundError):
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
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


class WorkspaceLayout:
    """Where everything a workspace keeps lives: one directory per workspace.

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
        ``DATA_DIR`` at a mount is the installer's business (§F1.12), and
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

    def _workspace_dir(self, workspace: str, *parts: str) -> Path:
        """Somewhere inside one workspace's directory, with the name validated.

        Every path this store builds starts here. The three below each repeated
        `valid_name(workspace, "session name")`, which is the kind of duplication
        that survives until one copy is left out — and the one left out is a
        path built from an unchecked name.
        """
        return self._resolved(valid_name(workspace, "session name"), *parts)

    def workspaces(self) -> list[str]:
        """Every workspace with a directory, for the admin view."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())


class FlowStore(WorkspaceLayout):
    """A session's flow documents: YAML files under ``<session>/flows``."""

    def _flows_dir(self, workspace: str) -> Path:
        return self._workspace_dir(workspace, FLOWS_DIR)

    def _path(self, workspace: str, name: str) -> Path:
        return self._workspace_dir(
            workspace, FLOWS_DIR, f"{valid_name(name, 'flow name')}{SUFFIX}"
        )

    # -- reads ---------------------------------------------------------------

    def names(self, workspace: str) -> list[str]:
        return sorted(name for name, _ in self._flow_entries(workspace))

    def _flow_entries(self, workspace: str) -> list[tuple[str, os.stat_result]]:
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

        return _listed(self._flows_dir(workspace), usable)

    def revision(self, workspace: str) -> str:
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
            for name, info in sorted(self._flow_entries(workspace))
        ]
        return ";".join(stamps) or "0"

    def summaries(self, workspace: str) -> list[dict]:
        """Name, description and parameters for each flow — never the steps.

        A listing exists so a caller can choose one, and the steps are the bulk
        of a document. Keeping them out is what lets a listing stay affordable
        over a store that is not a local disk.
        """
        # Read through the parse cache rather than `get`: a listing takes two
        # small fields and a count, and copying every document whole to find
        # them was most of what a warm listing cost.
        return [
            summary(name, workspace, self._loaded(workspace, name) or {})
            for name in self.names(workspace)
        ]

    def get(self, workspace: str, name: str) -> dict | None:
        """One flow, or None if there is no such flow.

        A file that is not readable as a YAML mapping reads as absent rather
        than raising. Someone hand-edits these, and a broken one should make
        that flow missing, not every listing that walks past it fail.
        """
        flow = valid_name(name, "flow name")
        loaded = self._loaded(workspace, name)
        if loaded is None:
            return None
        # The name on disk wins over any name inside the document: the file is
        # what `get` was asked for, and a document claiming to be something else
        # would make save-then-get return a different flow. It is the *validated*
        # name, so `get(" login ")` reports `login` — the same identifier a
        # listing gives, rather than the caller's spelling of it.
        return {**copy.deepcopy(loaded), "name": flow}

    def _loaded(self, workspace: str, name: str) -> dict | None:
        """The document stored as ``name``, as the parse cache holds it.

        Shared with the cache (`library.view`): read it, never change it, and
        copy any part that leaves. None for anything `get` reads as absent.
        """
        path = self._path(workspace, name)
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
                "flow %s/%s could not be read: %s", workspace, name, yaml_complaint(exc)
            )
            return None
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("flow %s/%s could not be read: %s", workspace, name, exc)
            return None
        if not isinstance(loaded, dict):
            log.warning("flow %s/%s is not a mapping", workspace, name)
            return None
        return loaded

    # -- writes --------------------------------------------------------------

    def save(self, workspace: str, name: str, document: dict) -> dict:
        """Create or replace one flow. Returns what was stored.

        One verb for both, as §F1.5 has it: an agent does not know whether a
        name is taken until it lists, and if it listed then it already knows.
        """
        flow = valid_name(name, "flow name")
        path = self._path(workspace, flow)
        # The validated name, not the caller's: writing `name: " login "` into
        # login.yaml would put an identifier in the file that no lookup returns.
        stored = {**document, "name": flow}
        _replace(path, dump(stored))
        return stored

    def delete(self, workspace: str, name: str) -> bool:
        """Remove one flow. False if it was not there."""
        try:
            self._path(workspace, name).unlink()
        except FileNotFoundError:
            return False
        return True

    # -- the document as text, for the editor --------------------------------

    def read_text(self, workspace: str, name: str) -> str | None:
        """One flow exactly as it sits on disk, or None if it is not there.

        The editor edits **YAML**, not a re-dump of a parsed dict (§F1.14). A
        person writes comments in these, and ordering they chose; round-tripping
        through ``get`` and ``safe_dump`` would silently throw both away the
        first time anybody opened the editor and saved.
        """
        try:
            return self._path(workspace, name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write_text(self, workspace: str, name: str, text: str) -> None:
        """Store one flow's YAML verbatim.

        Verbatim for the same reason ``read_text`` exists: what a person typed
        is what is kept. **The caller validates first** — this writes whatever
        it is handed, and an invalid document reaching disk is how a listing
        starts skipping a flow nobody can see is broken.
        """
        _replace(self._path(workspace, name), text)


class FileStore(WorkspaceLayout):
    """A session's own files: bytes, in the folders :data:`FOLDERS` names.

    Bytes rather than documents, and deliberately the whole of what a file
    store needs: the Grid supplies the only other operations there are, and it
    supplies them for *its* files, not ours. Three folders, kept apart because
    Files is curated while screenshots and recordings are disposable until kept.
    """

    def _files_dir(self, workspace: str, folder: str = FILES_DIR) -> Path:
        return self._workspace_dir(workspace, valid_folder(folder))

    def _file_path(self, workspace: str, name: str, folder: str = FILES_DIR) -> Path:
        return self._resolved(
            valid_name(workspace, "session name"),
            valid_folder(folder),
            valid_file_name(name),
        )

    def file_path(self, workspace: str, name: str, folder: str = FILES_DIR) -> Path:
        """Where one file lives, checked: for a route that streams it from disk."""
        return self._file_path(workspace, name, folder)

    def move_in(
        self, workspace: str, source: Path, name: str, folder: str,
        *, strict: bool = False,
    ) -> dict:
        """Take ``source`` into a folder under the first free name, and remove it.

        Never an overwrite: the claim is a hard link (or, where links cannot
        cross, an exclusive create and a copy), so a racing move cannot win the
        same name. Files skips its reserved names, as `_claim` does.

        ``strict`` is the move contract of Keep: a source that cannot be
        removed (anything but already gone) unclaims the new copy and raises, so
        the file is never in both folders. Without it, the collector's filing
        tolerates a retained inbox original (a sticky or recorder-owned folder).
        """
        source = Path(source)
        # Open the source once and only ever file that inode: a link swapped
        # in after a check would otherwise be copied or linked as itself.
        held = _open_regular(source)
        held_stat = os.fstat(held)
        staged: Path | None = None  # a complete copy, when links are impossible
        try:
            for candidate in candidates(valid_file_name(name)):
                if folder == FILES_DIR and candidate in RESERVED_IN_FILES:
                    continue
                target = self._file_path(workspace, candidate, folder)
                target.parent.mkdir(parents=True, exist_ok=True)
                if staged is None:
                    try:
                        os.link(source, target)
                    except FileExistsError:
                        continue
                    except OSError as exc:
                        if not _no_link(exc):
                            raise
                        # No hard link here (another filesystem, or one that
                        # has none): copy beside the target under a name no
                        # listing addresses, so the final name never holds a
                        # partial file.
                        staged = target.with_name(
                            f".{target.name}.{uuid.uuid4().hex}.tmp"
                        )
                        with staged.open("xb") as out, os.fdopen(
                            os.dup(held), "rb"
                        ) as src:
                            shutil.copyfileobj(src, out, 1024 * 1024)
                        staged.chmod(stat.S_IMODE(held_stat.st_mode))
                        os.utime(
                            staged,
                            ns=(held_stat.st_atime_ns, held_stat.st_mtime_ns),
                        )
                    else:
                        if not os.path.samestat(os.lstat(target), held_stat):
                            with contextlib.suppress(OSError):
                                target.unlink()
                            raise OSError(errno.EINVAL, "not a regular file")
                        self._release(workspace, source, target, strict, held_stat)
                        return self._entry(target)
                if self._claim_staged(staged, target):
                    staged = None
                    self._release(workspace, source, target, strict, held_stat)
                    return self._entry(target)
            raise AssertionError("unreachable")  # candidates is infinite
        finally:
            os.close(held)
            if staged is not None:
                with contextlib.suppress(OSError):
                    staged.unlink()

    @staticmethod
    def _release(
        workspace: str, source: Path, target: Path, strict: bool = False,
        held_stat: os.stat_result | None = None,
    ) -> None:
        """Remove the moved file's source. If a racing move took it first, this
        one lost: unclaim ``target`` so no duplicate stays, and say so.

        With ``strict``, any other failure unclaims too and is raised. Otherwise
        it leaves the move done: ``target`` is claimed and whole,
        and only the original stays behind (a sticky or recorder-owned inbox
        folder). Unclaiming then would be filed again on every sweep, a
        ``(1)``, a ``(2)``, … until the disk is full. The log names the filed
        file, never the source: an inbox name carries the Grid's id.

        With ``held_stat`` (the inode that was pinned and filed), a source path
        that now names a different inode or a non-regular file is left alone:
        the producer replaced it after the pin, the filed copy is the recording
        asked for, and unlinking would delete someone else's newer file. Strict
        mode succeeds too. A window of microseconds remains between this check
        and the unlink (Linux has no unlink-by-descriptor); it is accepted.
        """
        try:
            if held_stat is not None:
                now = os.lstat(source)
                if not (
                    stat.S_ISREG(now.st_mode) and os.path.samestat(now, held_stat)
                ):
                    log.warning(
                        "%s/%s/%s is in place, but its original name now holds "
                        "a different file, which was left alone",
                        workspace, target.parent.name, target.name,
                    )
                    return
            source.unlink()
        except FileNotFoundError:
            with contextlib.suppress(OSError):
                target.unlink()
            raise
        except OSError as exc:
            if strict:
                with contextlib.suppress(OSError):
                    target.unlink()
                raise
            log.warning(
                "%s/%s/%s is in place, but its original could not be removed "
                "(%s) and was left where it was",
                workspace, target.parent.name, target.name, type(exc).__name__,
            )

    @staticmethod
    def _claim_staged(staged: Path, target: Path) -> bool:
        """Give ``staged`` the final name ``target`` if it is free."""
        try:
            os.link(staged, target)
        except FileExistsError:
            return False
        except OSError as exc:
            if not _no_link(exc):
                raise
            # Not even a link inside one directory: claim the name with an
            # exclusive create, then rename the finished file over the claim.
            try:
                os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            except FileExistsError:
                return False
            try:
                staged.replace(target)
            except OSError:
                with contextlib.suppress(OSError):
                    target.unlink()
                raise
            return True
        staged.unlink()
        return True

    def _note_path(self, workspace: str, grid_id: str) -> Path:
        return self._resolved(
            valid_name(workspace, "session name"),
            RECORDINGS_DIR,
            PENDING_DIR,
            f"{valid_grid_id(grid_id)}.json",
        )

    def write_note(self, workspace: str, grid_id: str, note: dict) -> None:
        """Write a recording's note whole: a temp file, then a rename."""
        path = self._note_path(workspace, grid_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace(path, json.dumps(note))

    def delete_note(self, workspace: str, grid_id: str) -> bool:
        try:
            self._note_path(workspace, grid_id).unlink()
        except FileNotFoundError:
            return False
        return True

    def notes(self, on_error=None) -> list[tuple[str, str, dict]]:
        """Every owed recording, as ``(session, grid_id, note)``. A note that is
        not JSON, or names no usable id, is skipped with a warning.

        A storage error is not a broken note, and only a note or folder that is
        gone counts as absent. One inside a session raises, or, given
        ``on_error(session, exc)``, is handed to it and the other sessions are
        read on, so one folder that cannot be read never holds back the rest.
        The data directory itself unreadable always raises. Not `workspaces()`,
        whose ``is_dir`` reads an unreadable folder as no folder (Python 3.14:
        any OSError).
        """
        try:
            children = sorted(self.root.iterdir())
        except FileNotFoundError:
            return []
        found = []
        for child in children:
            try:
                found.extend(self._workspace_notes(child))
            except OSError as exc:
                if on_error is None:
                    raise
                on_error(child.name, exc)
        return found

    def _workspace_notes(self, folder: Path) -> list[tuple[str, str, dict]]:
        """One session's notes, for `notes`; raises a storage error."""
        workspace = folder.name
        try:
            valid_name(workspace)
        except InvalidName:
            return []
        try:
            if not stat.S_ISDIR(folder.lstat().st_mode):
                return []  # a file, or a link: nothing below the root is one
            pending = self._resolved(workspace, RECORDINGS_DIR, PENDING_DIR)
            entries = sorted(p for p in pending.iterdir() if p.suffix == ".json")
        except (FileNotFoundError, NotADirectoryError, InvalidName):
            return []
        found = []
        for entry in entries:
            try:
                info = entry.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue  # a symlink or a folder is never a note
            grid_id = entry.stem
            try:
                valid_grid_id(grid_id)
                note = json.loads(entry.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (InvalidName, ValueError):
                # The file is named by the Grid's id; the log never is.
                log.warning("ignoring a recording note in session %s", workspace)
                continue
            if isinstance(note, dict):
                found.append((workspace, grid_id, note))
        return found

    def _entry(self, path: Path) -> dict:
        """One file, in either Files or Screenshots, shaped like the Grid's own
        listing entry.

        The three sections are read separately, never merged (§F4.6, §F4.7),
        but they still have to agree on both the key names and the *units*: the
        Grid reports milliseconds, and a seconds-based timestamp beside it
        would sort every kept file to 1970 without anything looking wrong.
        """
        return _shaped(path.name, path.stat())

    def files(self, workspace: str, folder: str = FILES_DIR) -> list[dict]:
        """Every file in one folder of this session, newest first.

        Newest first because that is the order the Grid uses, and Files is
        shown interleaved with its entries.
        """

        def usable(name: str) -> str | None:
            if name.startswith("."):
                # Never a caller's: a staged copy (`move_in`) or the notes.
                # Silent, or a stranded one warns on every broadcast tick.
                return None
            # For the reason `_flow_entries` gives: a name the store would
            # refuse to address must not reach a caller that reads it back.
            try:
                return valid_file_name(name)
            except InvalidName:
                log.warning("ignoring file %r: not a usable name", name)
                return None

        found = [
            _shaped(name, info)
            for name, info in _listed(self._files_dir(workspace, folder), usable)
        ]
        return sorted(found, key=lambda f: f["creationTime"], reverse=True)

    def read_file(self, workspace: str, name: str, folder: str = FILES_DIR) -> bytes:
        """One file's bytes. Raises FileNotFoundError if it is not there."""
        return self._file_path(workspace, name, folder).read_bytes()

    def write_file(
        self, workspace: str, name: str, data: bytes, folder: str = FILES_DIR
    ) -> dict:
        """Keep one file, creating it or replacing it. Returns its entry.

        Create-or-replace, the same rule `save` follows: keeping a name that is
        already kept is how someone re-keeps a file they have since downloaded
        again, and the alternative is a second copy under a name nobody chose.
        A new file renamed over the old, never a rewrite: a link already
        streaming the old file keeps its bytes.
        """
        path = self._file_path(workspace, name, folder)
        _replace(path, data, sync=True)
        return self._entry(path)

    def create_file(
        self, workspace: str, name: str, data: bytes, folder: str = FILES_DIR
    ) -> dict:
        """Keep one file under a name nothing has. `FileExistsError` if taken.

        The claim is the create itself (`O_EXCL`), so two saves racing for one
        name cannot both win and the second silently replace the first.
        """
        path = self._file_path(workspace, name, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            file.write(data)
        return self._entry(path)

    def delete_file(self, workspace: str, name: str, folder: str = FILES_DIR) -> bool:
        """Remove one file. False if it was not there."""
        try:
            self._file_path(workspace, name, folder).unlink()
        except FileNotFoundError:
            return False
        return True

    def clear_folder(self, workspace: str, folder: str) -> int:
        """Delete every file in one folder, returning how many went.

        Files is refused: everything in it was put there on purpose, so it is
        emptied one file at a time or not at all (§F4.1). Anything this store
        could not address is left where it is, as `files` skips it.
        """
        if valid_folder(folder) == FILES_DIR:
            raise InvalidName("Files is never cleared wholesale; delete one file")
        removed = 0
        for entry in self.files(workspace, folder):
            if self.delete_file(workspace, entry["name"], folder):
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


# Folders that mark a directory as a workspace's, for the old-layout check.
_WORKSPACE_MARKS = (FLOWS_DIR, FILES_DIR, "screenshots")


def _is_dir(path: Path) -> bool:
    """`Path.is_dir` that lets a storage fault through: 3.14 reports an unreadable
    path as "not a directory", which here would hide an old layout. Only a path
    that is not there, or a non-directory in its way, means absent."""
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _holds_file(folder: Path, suffix: str | tuple[str, ...] = "") -> bool:
    """Whether `folder` holds a regular file directly in it (not a symlink)."""
    if not _is_dir(folder) or _is_symlink(folder):
        return False
    for entry in folder.iterdir():
        if entry.name.lower().endswith(suffix) and not _is_symlink(entry):
            try:
                if stat.S_ISREG(entry.stat().st_mode):
                    return True
            except (FileNotFoundError, NotADirectoryError):
                continue
    return False


def _old_reserved(entry: Path) -> bool:
    """Whether a folder named like a newly reserved name is an old session.

    Only an unmistakable old shape counts: in the new layout these paths hold
    sub-folders only, so a regular file directly in them is the old layout.
    """
    if entry.name == WORKSPACES_DIR:
        return (
            _holds_file(entry / FLOWS_DIR, ".yaml")
            or _holds_file(entry / FILES_DIR)
            or _holds_file(entry / "screenshots")
        )
    # A bare files/ is ambiguous (a transport prefix creates it): not counted.
    # Screenshots were always images; a transport may put videos there.
    return _holds_file(entry / FLOWS_DIR, ".yaml") or _holds_file(
        entry / "screenshots", _IMAGE_SUFFIXES
    )


def old_layout(root: Path, inbox: str | os.PathLike | None = None) -> list[str]:
    """Session folders still at the top of the data directory, sorted.

    Before the recordings release a session lived at ``DATA_DIR/<name>``; it
    lives at ``DATA_DIR/sessions/<name>`` now. One left behind would make every
    flow and file it holds silently vanish, so the boot refuses and names them.
    The recordings inbox is never a session, whatever it holds: ``INBOX_DIR``
    always, and the configured ``inbox`` (``recording.dir``) when it is, or lies
    beneath, a top-level entry. ``sessions`` and ``recordings`` are now reserved
    names: either is reported as an old session only when it holds the old
    shape (regular files directly in its ``flows/``, ``files/`` or
    ``screenshots/``), and is otherwise the new layout or the inbox.
    """
    if not _is_dir(root):
        return []
    reserved = {WORKSPACES_DIR, INBOX_DIR}
    configured = Path(inbox).resolve() if inbox else None
    base = root.resolve()
    found = []
    for entry in root.iterdir():
        if _is_symlink(entry) or not _is_dir(entry):
            continue
        if entry.name in reserved:
            if _old_reserved(entry):
                found.append(entry.name)
            continue
        if configured is not None:
            here = base / entry.name
            if configured == here or here in configured.parents:
                continue
        try:
            valid_name(entry.name)
        except InvalidName:
            continue
        if any(_is_dir(entry / mark) for mark in _WORKSPACE_MARKS):
            found.append(entry.name)
    return sorted(found)


def from_settings(
    data: DataSettings, inbox: str | os.PathLike | None = None
) -> LocalFlowStore | None:
    """The store the config asks for, or None when the data directory is unset."""
    if not data.dir:
        log.info("flows: off (set data.dir to enable them)")
        return None
    root = Path(data.dir)
    from ..config import ConfigError  # local: config imports names, not us

    try:
        stranded = old_layout(root, inbox)
    except OSError as exc:
        # Configured and unusable stops the boot (§F4.12): a data directory
        # that cannot be read must not be mistaken for an empty one.
        raise ConfigError(
            f"data directory {root} cannot be read ({type(exc).__name__}: "
            f"{exc.strerror or 'I/O error'})"
        ) from None
    for name in (WORKSPACES_DIR, INBOX_DIR):
        if name in stranded:
            raise ConfigError(
                f"`{name}` in {root} is a session folder from before the "
                f"sessions/ layout, and `{name}` is now reserved: rename it "
                f"(e.g. to `{name}-old`), then move it into {root / WORKSPACES_DIR}/"
            )
    if stranded:
        raise ConfigError(
            f"{', '.join(stranded)} in {root} are session folders from before "
            f"the sessions/ layout: move them into {root / WORKSPACES_DIR}/"
        )
    log.info("flows: local, under %s", root / WORKSPACES_DIR)
    return LocalFlowStore(root / WORKSPACES_DIR)
