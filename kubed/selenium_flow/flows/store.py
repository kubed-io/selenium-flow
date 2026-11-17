"""Where a workspace's saved flows and files live, under ``DATA_DIR/workspaces``.

This is storage only: names, which workspace owns what, and reading and writing
documents and bytes. What a document is as text is ``flows.library``.
`FlowStore` holds documents and `FileStore` holds bytes, over one
`WorkspaceLayout`; `LocalFlowStore` is both under one root.

What is behind ``DATA_DIR`` (a folder, an ``emptyDir``, a PVC, NFS) is the
installer's choice. So no path is built outside this module, and no read is
assumed cheap or local: ``summaries`` returns no steps.

Unset ``DATA_DIR`` turns flows off; there is no fallback to a temporary
directory the operator never chose.
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
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import yaml
from cachetools import LRUCache

from ..names import (
    CONFIG_FILE,
    FILES_DIR,
    FOLDERS,
    GLOBAL_WORKSPACE,
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
from .places import Places
from .shape import Shape

if TYPE_CHECKING:
    from ..config import DataSettings
    from .tags import Filter

# The store's old logger name: operators filter logs by it.
log = logging.getLogger("kubed.selenium_flow.flows.library")

SUFFIX = ".yaml"
FLOWS_DIR = "flows"
PENDING_DIR = ".pending"
# A hint names a flow by its resource URI. Equal to `flows.api.FLOW_URI`, which
# this module cannot import (api imports the store); a test holds them together.
HINT_URI = "flow://flows/{name}"
HINT_CAP = 10  # most flows one result names
LIBRARIES = 256  # libraries whose placed flows are kept
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

    ``directory`` has come through `_resolved`; each entry that is a link, or
    whose name ``usable`` rejects, is skipped. scandir says which entries are
    links without a realpath per entry. A directory swapped for a link after
    `_resolved` is caught by pinning it by descriptor and listing it only while
    its path still resolves to itself and names the directory held.
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
                # Without a descriptor to pin, each entry is checked instead.
                path = directory / entry.name
                if not pinned and path.resolve() != path:
                    raise InvalidName(f"{entry.name!r} does not resolve to itself")
                try:
                    found.append((name, entry.stat(follow_symlinks=False)))
                except FileNotFoundError:  # deleted since the listing
                    continue
        return found
    finally:
        if fd is not None:
            os.close(fd)


def open_regular(path: Path) -> int:
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


def _unlink(path: Path) -> bool:
    """Remove ``path``. False if it was not there."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _no_link(exc: OSError) -> bool:
    """Whether a failed ``os.link`` means links are unavailable, not a real fault."""
    return isinstance(exc, PermissionError) or exc.errno in _NO_LINK


def _replace(path: Path, text: str | bytes, *, sync: bool = False) -> None:
    """Write ``text`` as ``path`` in one step: a reader sees the old document or
    the new one, never part of either. ``sync`` flushes it to disk before the
    rename, for bytes that have no other copy.

    The text goes to a name beside the target that no listing addresses (a
    leading dot, and not `.yaml`) and is renamed over it. A replaced file keeps
    its mode; a new one gets the umask's.
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
        with contextlib.suppress(FileNotFoundError):
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _shaped(name: str, info: os.stat_result) -> dict:
    """A file entry from its name and its stat (`FileStore._entry`)."""
    return {
        "name": name,
        "size": info.st_size,
        "creationTime": int(info.st_mtime * 1000),
    }


class WorkspaceLayout:
    """Where everything a workspace keeps lives: one directory per workspace.

    The only place a path is built, so the link and traversal rules are
    written once.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _resolved(self, *parts: str) -> Path:
        """A path inside the data directory, or refuse.

        ``valid_name`` refuses a separator; this catches a link at any level
        below the root. The check is equality, not containment:
        ``bot/flows -> ../research-bot/flows`` stays inside the data directory
        and would still hand one workspace another's flows. The root itself may
        be a link, as a mount often is.
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
        """Somewhere inside one workspace's directory, with the name validated."""
        return self._resolved(valid_name(workspace, "workspace name"), *parts)

    def config_path(self, workspace: str) -> Path:
        """Where a workspace's own config is. The folder is checked and the
        file is not: its reader opens it without following a link."""
        return self._workspace_dir(workspace) / CONFIG_FILE

    def write_config(self, workspace: str, text: str) -> None:
        """Replace a workspace's config whole. The caller validates it first."""
        _replace(self.config_path(workspace), text)

    def workspaces(self) -> list[str]:
        """Every workspace with a directory, for the admin view."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())


class Placed(NamedTuple):
    """A flow that declares where it applies, and the hint that names it."""

    name: str
    places: Places
    hint: dict  # {name, uri, description, parameters, required[, shared]}


class Library(NamedTuple):
    """What one library's flows say, read once per revision."""

    names: frozenset[str]
    placed: tuple[Placed, ...]
    tags: tuple[tuple[str, tuple[str, ...]], ...]  # (name, its tags), tagged only


def _every(tags: Iterable[str]) -> bool:
    return True


class FlowStore(WorkspaceLayout):
    """A workspace's flow documents: YAML files under ``<workspace>/flows``."""

    def __init__(self, root: str | Path):
        super().__init__(root)
        # library -> (revision, answer). Per library, so `global` is read once
        # per version however many workspaces ask. No TTL.
        self._hints: LRUCache = LRUCache(maxsize=LIBRARIES)
        self._hints_lock = threading.Lock()
        # A workspace's `flows.global` filter, or None for every flow
        # (`workspace.config.Configs.global_filter`, wired at boot).
        self.global_filter: Callable[[str], Filter | None] = lambda _workspace: None

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

        A name this store would refuse to address is skipped (a hand-made
        `.hidden.yaml`, a macOS `._login.yaml`, a link): every caller of
        `names` turns a name back into a path, and one raising would fail the
        whole listing.
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
        """A token that changes whenever this workspace's flows do.

        Asked on a poll, so it stats the files and never opens one.
        """
        # Inode and size beside the mtime, as git guards against racy
        # timestamps: two edits in one clock tick share an mtime, and an atomic
        # replace always brings a new inode.
        stamps = [
            f"{name}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"
            for name, info in sorted(self._flow_entries(workspace))
        ]
        return ";".join(stamps) or "0"

    def flows_here(self, workspace: str, url: str) -> list[dict]:
        """The flows that say they apply to ``url``, as hints, at most ten.

        The workspace's own come first, then ``global``'s, each in name order.
        A shared flow is left out when the workspace has a flow of that name at
        all, placed or not, because ``run_flow`` by that name runs the
        workspace's. A flow whose places cannot be read is never hinted and is
        logged by name, never by value. Answers come from a cache keyed on each
        library's `revision`, so a warm call reads no file; the caller gets a
        copy.
        """
        own = self._library(workspace)
        found = [p for p in own.placed if p.places.applies(url)]
        if workspace != GLOBAL_WORKSPACE:
            shared = self._library(GLOBAL_WORKSPACE)
            offered = self._shared_offered(workspace, own, shared)
            found += [
                p for p in shared.placed if p.name in offered and p.places.applies(url)
            ]
        return [copy.deepcopy(p.hint) for p in found[:HINT_CAP]]

    def offered(self, workspace: str) -> Callable[[Iterable[str]], bool]:
        """Which `global` flows ``workspace`` is offered, as a test of a flow's
        tags: its config's `flows.global`, else every one of them."""
        found = self.global_filter(workspace)
        return _every if found is None else found.passes

    def _shared_offered(self, workspace: str, own: Library, shared: Library) -> set:
        """The names of the `global` flows ``workspace`` is offered: those it
        has no flow of the same name for, that `offered` lets through."""
        offer = self.offered(workspace)
        tagged = dict(shared.tags)
        return {
            name
            for name in shared.names
            if name not in own.names and offer(tagged.get(name, ()))
        }

    def tags_in_use(self, workspace: str) -> dict[str, int]:
        """``{tag: count}`` over the flows ``workspace`` is offered, in tag order.

        Built from each library's cache, so a warm read parses no flow.
        """
        own = self._library(workspace)
        counts = Counter(tag for _, carried in own.tags for tag in carried)
        if workspace != GLOBAL_WORKSPACE:
            shared = self._library(GLOBAL_WORKSPACE)
            offered = self._shared_offered(workspace, own, shared)
            counts.update(
                tag for name, carried in shared.tags if name in offered
                for tag in carried
            )
        return dict(sorted(counts.items()))

    def _library(self, library: str) -> Library:
        """Every flow name in ``library``, the flows that declare places, and
        each tagged flow's tags.

        Cached under the library's `revision`. The stat and the build run
        outside the lock; two threads may build the same version, and either
        answer is right.
        """
        stamp = self.revision(library)
        with self._hints_lock:
            cached = self._hints.get(library)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        names, placed, tagged = [], [], []
        for name in self.names(library):
            doc = self._loaded(library, name)
            if doc is None:
                continue
            names.append(name)
            shape = Shape(doc)
            if shape.tags:
                tagged.append((name, tuple(shape.tags)))
            try:
                places = Places.parse(doc)
            except ValueError:
                log.warning(
                    "flow %s/%s is never hinted: its sites or paths cannot be read",
                    library,
                    name,
                )
                continue
            if places is None:
                continue
            description = doc.get("description")
            hint = {
                "name": name,
                "uri": HINT_URI.format(name=name),
                "description": description if isinstance(description, str) else "",
                "parameters": list(shape.properties),
                "required": list(shape.required),
            }
            if library == GLOBAL_WORKSPACE:
                hint["shared"] = True
            placed.append(Placed(name, places, hint))
        answer = Library(frozenset(names), tuple(placed), tuple(tagged))
        with self._hints_lock:
            self._hints[library] = (stamp, answer)
        return answer

    def summaries(self, workspace: str) -> list[dict]:
        """Name, description and parameters for each flow, never the steps."""
        # Through the parse cache, not `get`, which copies each document whole.
        return [
            summary(name, workspace, self._loaded(workspace, name) or {})
            for name in self.names(workspace)
        ]

    def get(self, workspace: str, name: str) -> dict | None:
        """One flow, or None if there is no such flow.

        A file that is not readable as a YAML mapping reads as absent, so one
        broken hand edit never fails a listing.
        """
        flow = valid_name(name, "flow name")
        loaded = self._loaded(workspace, name)
        if loaded is None:
            return None
        # The validated file name wins over any name inside the document, so
        # `get(" login ")` reports `login`, as a listing does.
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
        except yaml.YAMLError as exc:
            # The parser's own message quotes the line (`yaml_complaint`).
            log.warning(
                "flow %s/%s could not be read: %s", workspace, name, yaml_complaint(exc)
            )
            return None
        # UnicodeDecodeError is a ValueError, not an OSError.
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("flow %s/%s could not be read: %s", workspace, name, exc)
            return None
        if not isinstance(loaded, dict):
            log.warning("flow %s/%s is not a mapping", workspace, name)
            return None
        return loaded

    # -- writes --------------------------------------------------------------

    def save(self, workspace: str, name: str, document: dict) -> dict:
        """Create or replace one flow. Returns what was stored."""
        flow = valid_name(name, "flow name")
        path = self._path(workspace, flow)
        # The validated name: `name: " login "` is an identifier no lookup
        # returns.
        stored = {**document, "name": flow}
        _replace(path, dump(stored))
        return stored

    def delete(self, workspace: str, name: str) -> bool:
        """Remove one flow. False if it was not there."""
        return _unlink(self._path(workspace, name))

    # -- the document as text, for the editor --------------------------------

    def read_text(self, workspace: str, name: str) -> str | None:
        """One flow exactly as it sits on disk, comments and key order kept, or
        None if it is not there."""
        try:
            # newline="": its line endings as written, never translated.
            with self._path(workspace, name).open(encoding="utf-8", newline="") as file:
                return file.read()
        except FileNotFoundError:
            return None

    def write_text(self, workspace: str, name: str, text: str) -> None:
        """Store one flow's YAML verbatim. The caller validates it first."""
        _replace(self._path(workspace, name), text)


class FileStore(WorkspaceLayout):
    """A workspace's own files: bytes, in the folders :data:`FOLDERS` names.

    Files is curated; screenshots and recordings are disposable until kept.
    """

    def _files_dir(self, workspace: str, folder: str = FILES_DIR) -> Path:
        return self._workspace_dir(workspace, valid_folder(folder))

    def _file_path(self, workspace: str, name: str, folder: str = FILES_DIR) -> Path:
        return self._resolved(
            valid_name(workspace, "workspace name"),
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
        held = open_regular(source)
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
                        # No hard link here: copy beside the target under a
                        # name no listing addresses, so the final name never
                        # holds a partial file.
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
            valid_name(workspace, "workspace name"),
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
        return _unlink(self._note_path(workspace, grid_id))

    def notes(self, on_error=None) -> list[tuple[str, str, dict]]:
        """Every owed recording, as ``(workspace, grid_id, note)``. A note that is
        not JSON, or names no usable id, is skipped with a warning.

        A storage error is not a broken note, and only a note or folder that is
        gone counts as absent. One inside a workspace raises, or, given
        ``on_error(workspace, exc)``, is handed to it and the other workspaces are
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
        """One workspace's notes, for `notes`; raises a storage error."""
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
                log.warning("ignoring a recording note in workspace %s", workspace)
                continue
            if isinstance(note, dict):
                found.append((workspace, grid_id, note))
        return found

    def _entry(self, path: Path) -> dict:
        """One file, shaped like the Grid's own listing entry, its time in
        milliseconds as the Grid's is."""
        return _shaped(path.name, path.stat())

    def files(self, workspace: str, folder: str = FILES_DIR) -> list[dict]:
        """Every file in one folder of this workspace, newest first, as the
        Grid lists its own."""

        def usable(name: str) -> str | None:
            if name.startswith("."):
                # Never a caller's: a staged copy (`move_in`) or the notes.
                # Silent, or a stranded one warns on every broadcast tick.
                return None
            # As `_flow_entries` skips one.
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

        A new file is renamed over the old, so a link already streaming the old
        one keeps its bytes.
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
        return _unlink(self._file_path(workspace, name, folder))

    def clear_folder(self, workspace: str, folder: str) -> int:
        """Delete every file in one folder, returning how many went.

        Files is refused: it is emptied one file at a time. Anything `files`
        skips is left where it is.
        """
        if valid_folder(folder) == FILES_DIR:
            raise InvalidName("Files is never cleared wholesale; delete one file")
        removed = 0
        for entry in self.files(workspace, folder):
            if self.delete_file(workspace, entry["name"], folder):
                removed += 1
        return removed


class LocalFlowStore(FlowStore, FileStore):
    """Flows and files as plain files under one directory, each workspace's
    in one folder."""

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
    """Whether a folder named like a newly reserved name is an old workspace.

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
    """Workspace folders still at the top of the data directory, sorted.

    Before the recordings release a workspace lived at ``DATA_DIR/<name>``; it
    lives at ``DATA_DIR/workspaces/<name>`` now. One left behind would make every
    flow and file it holds silently vanish, so the boot refuses and names them.
    The recordings inbox is never a workspace, whatever it holds: ``INBOX_DIR``
    always, and the configured ``inbox`` (``recording.dir``) when it is, or lies
    beneath, a top-level entry. ``workspaces`` and ``recordings`` are now reserved
    names: either is reported as an old workspace only when it holds the old
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


# The folder workspaces lived in before they were called workspaces. Moved
# once, at boot.
_OLD_DIR = "sessions"


def _move_old_folder(root: Path) -> None:
    """Rename DATA_DIR/sessions to DATA_DIR/workspaces when only the old exists.

    One rename on one filesystem, so a crash leaves one name or the other,
    never half of each. Both present is a merge only a person can do. A
    `sessions` that has the old flat shape is a workspace, and stays put.
    """
    from ..config import ConfigError  # local: config imports names, not us

    old, new = root / _OLD_DIR, root / WORKSPACES_DIR
    if not _is_dir(old):
        return
    if (
        _holds_file(old / FLOWS_DIR, ".yaml")
        or _holds_file(old / FILES_DIR)
        or _holds_file(old / "screenshots")
    ):
        # A flat-layout workspace named `sessions` (released 0.3.0 shape), not
        # the unreleased sessions/ folder: leave it for old_layout to report.
        return
    if os.path.lexists(new):
        raise ConfigError(
            f"{old} and {new} both exist: merge {_OLD_DIR}/ into "
            f"{WORKSPACES_DIR}/ by hand, then remove {_OLD_DIR}/"
        )
    try:
        old.rename(new)
    except OSError as exc:
        raise ConfigError(
            f"could not move {old} to {new} ({type(exc).__name__}: "
            f"{exc.strerror or 'I/O error'}): rename it by hand, then restart"
        ) from None
    log.info("data: moved %s to %s", old, new)


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
        _move_old_folder(root)
        stranded = old_layout(root, inbox)
    except OSError as exc:
        # A data directory that cannot be read is not an empty one.
        raise ConfigError(
            f"data directory {root} cannot be read ({type(exc).__name__}: "
            f"{exc.strerror or 'I/O error'})"
        ) from None
    for name in (WORKSPACES_DIR, INBOX_DIR):
        if name in stranded:
            raise ConfigError(
                f"`{name}` in {root} is a workspace folder from before the "
                f"workspaces/ layout, and `{name}` is now reserved: rename it "
                f"(e.g. to `{name}-old`), then move it into {root / WORKSPACES_DIR}/"
            )
    if stranded:
        raise ConfigError(
            f"{', '.join(stranded)} in {root} are workspace folders from before "
            f"the workspaces/ layout: move them into {root / WORKSPACES_DIR}/"
        )
    log.info("flows: local, under %s", root / WORKSPACES_DIR)
    return LocalFlowStore(root / WORKSPACES_DIR)
