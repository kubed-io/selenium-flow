"""The collector: files each owed recording into its session as it finishes.

**The queue is the notes on disk** (``sessions/<name>/recordings/.pending/
<gridId>.json``), written when a recorded browser opens; **the engine is one
task per process**, which runs only while a recording is owed and ends when
none is — the shape the admin broadcast has (AGENTS.md: a bounded wait for
something this server was told to expect is allowed; a loop that tidies is
not). The notes survive a restart, so ``start`` picks up where the last
process left off.

It wakes on a change in the inbox (``watchfiles``: events, or polling on a
network filesystem) and on a timer, and each time sweeps: an ``.mp4`` whose name
holds an owed Grid id and ends in ``mfro`` is moved into the session's
recordings and its note deleted; one with no ``mfro`` is moved as it is once
it has been quiet for a minute **and** the browser is gone (a live recording
writes a keyframe at least every ~17 s, so this never files one early); an
owed browser not yet known to have ended is looked for, so a file that never
comes has a deadline to miss.

**Whether a browser is gone is read off the Grid's status — never asked of the
browser.** Any command sent to a session is activity the node counts against
``SE_NODE_SESSION_TIMEOUT``, so a collector that asked each owed browser every
tick would keep every recorded browser alive forever. ``live`` lists the
sessions the Grid is running (``GET /status``, which touches none), once a
tick, shared by every owed browser. An empty listing (a hub restarted before
its nodes registered again) reads as every browser gone, so a browser a later
listing shows running is no longer ended: a blip starts no deadline that sticks.

It owns no browser and takes no session lock. ``expect`` and ``ended`` are
called from worker threads (FastMCP's sync tools, Starlette's routes).
``expect`` writes its note there, in the caller's thread; ``ended`` hands the
change to the loop, and the loop writes every note it changes in a worker
thread, never on itself: ``DATA_DIR`` can be NFS, and a write that stalls on
the loop stalls every request. Writes and deletes of notes take one lock, so a
note filed meanwhile is never written back.

**A note leaves the queue only once it is gone from disk.** Filing a recording
(or giving up on one at its deadline) first rewrites its note to say so
(``filed``, ``dropped``), then deletes it. A delete that fails leaves the
recording owed but *done*: every sweep retries the delete and nothing else, so
it is never matched, filed or timed out again — an inbox copy that could not be
removed is not filed a second time — and a restart that finds the note reads
the same mark. A filing, once its move has begun, runs to its mark and delete
as one task that ``stop`` waits for, so a graceful shutdown mid-copy (a rolling
deploy) leaves nothing half done. What remains: a hard crash between the move
and the mark, or the mark's own write failing as well as the delete, leaves an
unmarked note, which the next process can file again.

**The notes are read at ``start`` and again each tick until every session's
are read.** A storage fault reading one session's stops neither the boot nor
another session's recordings: what could be read is owed at once, the fault is
logged once a streak (the sessions and the error's type), and the task runs
while any read is owed. The read and the merge hold the notes lock, so a read
that began before a sweep filed and deleted a note cannot put it back.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import math
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

import anyio
from watchfiles import awatch

from ..names import RECORDINGS_DIR, InvalidName, valid_grid_id
from . import mp4

log = logging.getLogger(__name__)

# A transport that writes a temporary name and renames it is still copying.
PARTIAL_SUFFIXES = (".partial", ".part", ".tmp")


def name_for(opened_ms: int) -> str:
    """``rec-YYYYMMDD-HHMM.mp4``, UTC, from when the browser opened."""
    return time.strftime("rec-%Y%m%d-%H%M.mp4", time.gmtime(opened_ms / 1000))


@dataclass
class Owed:
    session: str
    grid_id: str
    opened: int
    browser: str
    ended: int | None = None
    # Done, its note still on disk: the recording was filed under this name,
    # or dropped at its deadline. Only the delete is left (see the docstring).
    filed: str | None = None
    dropped: bool = False

    @property
    def done(self) -> bool:
        return self.filed is not None or self.dropped


# The year 3000 in milliseconds: past it, `gmtime` raises in every sweep.
LATEST_MS = 32_503_680_000_000


def _millis(value) -> int:
    """A note's timestamp as an int, or ValueError: a note is a file somebody
    could have edited, so its fields are read, not trusted. A time that is not
    finite or not between 1970 and the year 3000 is refused here, so the note
    is skipped, rather than stopping the boot (``1e999``) or every later sweep
    (``1e20``, which ``name_for`` cannot format)."""
    if isinstance(value, bool):
        raise ValueError("not a timestamp")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("not a timestamp")
        value = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    elif not isinstance(value, int):
        raise ValueError("not a timestamp")
    if not 0 <= value < LATEST_MS:
        raise ValueError("not a timestamp")
    return value


def _owed_from(session: str, grid_id: str, note: dict) -> Owed:
    opened, ended, filed = note.get("opened"), note.get("ended"), note.get("filed")
    return Owed(
        session,
        grid_id,
        0 if opened is None else _millis(opened),
        str(note.get("browser") or ""),
        None if ended is None else _millis(ended),
        None if filed is None else str(filed),
        note.get("dropped") is True,
    )


class Collector:
    def __init__(
        self,
        store,
        inbox,
        *,
        live,
        wait: int,
        polling: bool,
        poll_ms: int,
        on_filed=None,
        clock=time.time,
        tick: float = 30.0,
        idle_after: float = 60.0,
    ):
        self.store = store
        self.inbox = Path(inbox)
        # () -> the Grid session ids currently running. Never a per-session
        # call: see the module docstring.
        self.live = live
        self.wait = wait
        self.polling = polling
        self.poll_ms = poll_ms
        self.on_filed = on_filed
        self.clock = clock
        self.tick = tick
        self.idle_after = idle_after
        self.owed: dict[str, Owed] = {}
        # path -> (size, mtime, first seen at that size and mtime)
        self._quiet: dict[str, tuple[int, float, float]] = {}
        # (when it was taken, the ids, or None when the Grid could not say).
        self._listing: tuple[float, frozenset[str] | None] | None = None
        self._listing_failed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._stop: anyio.Event | None = None
        self._retry: asyncio.TimerHandle | None = None
        self._failing = False
        # Nested directories the last walk could not read (see _inbox_files).
        self._blind = False
        self._blind_logged = False
        self._fault_logged = False
        # Set by `stop`: a recording expected during shutdown starts no task;
        # its note is on disk, so the next process files it.
        self._stopping = False
        # Every note write or delete made from the loop, in order (`_save`).
        self._notes_lock = asyncio.Lock()
        self._saves: set[asyncio.Task] = set()
        # The notes on disk have not been read yet (`_load`).
        self._unread = False
        self._unread_logged = False
        # Grid ids whose note this process has said it cannot remove.
        self._undeleted: set[str] = set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---- from worker threads ----------------------------------------------

    def expect(self, session: str, grid_id: str, browser: str) -> None:
        """A recorded browser opened: note it on disk, then tell the loop."""
        valid_grid_id(grid_id)
        owed = Owed(session, grid_id, int(self.clock() * 1000), browser)
        self.store.write_note(session, grid_id, self._note(owed))
        self._post(lambda: self._add(owed))

    def ended(self, grid_id: str) -> None:
        """A browser was ended. Ignored unless it is owed."""
        self._post(lambda: self._mark_ended(grid_id))

    def _post(self, fn) -> None:
        loop = self._loop
        if loop is None:
            # Not started: nothing runs yet, so there is no task to wake and no
            # thread to cross. Apply it here; `start` re-reads the notes anyway.
            # Safe only because the server's lifespan starts the collector
            # before any request is served, so no worker thread races this.
            fn()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn()
            return
        # A loop closed under a late caller refuses: the note is the queue,
        # and the next process reads it.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(fn)

    # ---- on the loop ------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        # A lock waited on binds to its loop; a restarted server has a new one.
        self._notes_lock = asyncio.Lock()
        self._unread = True
        await self._load()
        if self._busy:
            self._ensure()

    @property
    def _busy(self) -> bool:
        """The task has work: a recording owed, or the notes still to read."""
        return bool(self.owed) or self._unread

    async def _load(self) -> None:
        """Read the notes into ``owed``. Never raises a storage fault: one
        note NFS cannot read must not stop the boot. A session that could not
        be read keeps ``_unread`` set, so the task reads again next tick; the
        rest are owed now. Logged once a streak, by session and the error's
        type alone (a path names a Grid id).

        Under the notes lock from the read to the merge: a sweep's `_forget`
        waits, so this can never add back a note it deleted meanwhile."""
        failed: dict[str, str] = {}

        def fault(session: str, exc: OSError) -> None:
            failed.setdefault(session, type(exc).__name__)

        async with self._notes_lock:
            try:
                notes = await anyio.to_thread.run_sync(
                    functools.partial(self.store.notes, on_error=fault)
                )
            except OSError as exc:  # the data directory itself
                notes, failed = [], {"": type(exc).__name__}
            loaded = 0
            for session, grid_id, note in notes:
                try:
                    owed = _owed_from(session, grid_id, note)
                except ValueError:
                    log.warning(
                        "recordings: ignoring an unreadable note in session %s",
                        session,
                    )
                    continue
                # One already in memory (expected meanwhile, or read by an
                # earlier partial load) is the newer: keep it, since a `_save`
                # or a sweep may hold that object.
                if self.owed.setdefault(grid_id, owed) is owed:
                    loaded += 1
        if loaded:
            log.info("recordings: %d owed from before the restart", loaded)
        self._unread = bool(failed)
        if failed and not self._unread_logged:
            self._unread_logged = True
            where = ", ".join(sorted(k for k in failed if k)) or "the data directory"
            log.warning(
                "recordings: notes cannot be read for %s (%s); retrying every %ss",
                where, ", ".join(sorted(set(failed.values()))), self.tick,
            )
        elif not failed and self._unread_logged:
            self._unread_logged = False
            log.info("recordings: the notes can be read again")

    async def stop(self) -> None:
        self._stopping = True
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None
        if self._stop is not None:
            self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # A note change still being written lands before the process goes.
        for save in list(self._saves):
            with contextlib.suppress(Exception):
                await save

    def _add(self, owed: Owed) -> None:
        self.owed[owed.grid_id] = owed
        self._ensure()

    def _mark_ended(self, grid_id: str) -> None:
        """``ended``'s half on the loop (or, before `start`, inline)."""
        owed = self.owed.get(grid_id)
        if owed is None or owed.ended is not None or owed.done:
            return
        owed.ended = int(self.clock() * 1000)
        if self._loop is None:
            # Not started: this is the caller's own thread, not the loop.
            self._write(owed)
            return
        save = self._loop.create_task(self._save(owed))
        self._saves.add(save)
        save.add_done_callback(self._saves.discard)

    async def _save(self, owed: Owed) -> None:
        """Write ``owed``'s note as it is now, in a worker thread. Skipped once
        it is done: its note is marked, or gone and must stay gone."""
        async with self._notes_lock:
            if self.owed.get(owed.grid_id) is not owed or owed.done:
                return
            await anyio.to_thread.run_sync(self._write, owed)

    async def _forget(self, owed: Owed, filed: str | None = None) -> None:
        """Stop owing ``owed``, filed as ``filed`` or (None) dropped.

        The note is marked done first, then deleted, and only a delete that
        worked takes it out of ``owed``. One that failed leaves it owed and
        done, so the next sweep calls this again and retries the delete alone
        (the mark is already on disk). Under the notes lock throughout, so a
        `_save` waiting for it finds the recording done or gone and writes
        nothing back."""
        async with self._notes_lock:
            if self.owed.get(owed.grid_id) is not owed:
                return
            if not owed.done:
                owed.filed, owed.dropped = filed, filed is None
                # Best effort: if it fails too, only a restart can mistake the
                # note for an owed one (the module docstring).
                await anyio.to_thread.run_sync(self._write, owed)
            try:
                await anyio.to_thread.run_sync(
                    self.store.delete_note, owed.session, owed.grid_id
                )
            except OSError as exc:
                # Once a recording a process, whether this one marked it or
                # found it marked: not persisted, so a restart says it again.
                if owed.grid_id not in self._undeleted:
                    self._undeleted.add(owed.grid_id)
                    log.warning(
                        "recordings: a note for %s could not be removed (%s); "
                        "retrying every %ss",
                        owed.session, type(exc).__name__, self.tick,
                    )
                return
            self._undeleted.discard(owed.grid_id)
            del self.owed[owed.grid_id]

    def _ensure(self) -> None:
        loop = self._loop
        if loop is None or self._stopping:
            return
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if not on_loop:
            # Only the loop starts the task; `_post` brings every caller here.
            return
        if self._retry is not None:
            # A retry still pending is superseded by this start.
            self._retry.cancel()
            self._retry = None
        if not self.running:
            self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        try:
            while self._busy:
                self._stop = anyio.Event()
                await self._swept()
                if not self._busy:
                    break
                async for _changes in awatch(
                    self.inbox,
                    stop_event=self._stop,
                    force_polling=self.polling,
                    poll_delay_ms=self.poll_ms,
                    yield_on_timeout=True,
                    rust_timeout=max(1, int(self.tick * 1000)),
                    watch_filter=None,
                    recursive=True,
                ):
                    await self._swept()
                    if not self._busy:
                        self._stop.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the notes keep the queue; retried
            if not self._failing:
                # Once per streak, and no traceback: a path in one names a
                # Grid id, which the operator's log never does.
                sessions = sorted({o.session for o in self.owed.values()})
                log.warning(
                    "recordings: a sweep failed (%s) with %s; retrying every %ss",
                    type(exc).__name__,
                    f"recordings owed for {', '.join(sessions)}" if sessions
                    else "notes not yet read",
                    self.tick,
                )
            self._failing = True
            if not self._stopping and self._loop is not None:
                self._retry = self._loop.call_later(self.tick, self._ensure)
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _swept(self) -> None:
        if self._unread:
            await self._load()
        await self.sweep()
        if self._failing:
            self._failing = False
            log.info("recordings: sweeping again")

    async def sweep(self) -> None:
        """One look: file what is finished, look for the rest, drop the late."""
        files = await anyio.to_thread.run_sync(self._inbox_files)
        now = self.clock()
        present = {str(path) for path, _size, _mtime in files}
        self._quiet = {k: v for k, v in self._quiet.items() if k in present}
        complete = await anyio.to_thread.run_sync(self._completeness, files)
        for grid_id, owed in list(self.owed.items()):
            if self.owed.get(grid_id) is not owed:
                continue  # filed or dropped while this sweep awaited
            if owed.done:
                await self._forget(owed)  # its note is all that is left
                continue
            match = next(
                (f for f in files
                 if grid_id in f[0].name and f[0].suffix.lower() == ".mp4"),
                None,
            )
            if owed.ended is not None and await self._back(owed, now):
                owed.ended = None
                await self._save(owed)
            quiet = 0.0
            if match is not None:
                path, size, mtime = match
                done = complete.get(str(path))
                if done is None:
                    continue  # unreadable: neither finished nor cut off
                if done:
                    await self._file(owed, path)
                    continue
                quiet = self._quiet_for(path, size, mtime, now)
            settled = quiet >= self.idle_after
            gone = (owed.ended is None or settled) and await self._gone(owed, now)
            if gone and owed.ended is None:
                owed.ended = int(now * 1000)
                await self._save(owed)
            if match is not None:
                if settled and gone:
                    log.info(
                        "recordings: %s/%s ends without a trailer; filed as it is",
                        owed.session, name_for(owed.opened),
                    )
                    await self._file(owed, match[0])
                continue
            if (
                owed.ended is not None
                and not self._blind
                and now * 1000 - owed.ended >= self.wait * 1000
            ):
                await self._forget(owed)
                log.warning(
                    "recording for session %s (opened %s UTC) never reached "
                    "RECORDING_DIR; see the README's Recording section",
                    owed.session,
                    time.strftime("%H:%M", time.gmtime(owed.opened / 1000)),
                )

    async def _listed(self, now: float) -> tuple[float, frozenset[str] | None]:
        """The Grid's listing, taken at most once a tick."""
        listing = self._listing
        if listing is None or now - listing[0] >= self.tick:
            listing = (now, await self._list())
            self._listing = listing
        return listing

    async def _gone(self, owed: Owed, now: float) -> bool:
        """Whether the Grid's listing says this browser is no longer running.

        False whenever it cannot say: the listing failed, or was taken before
        this browser opened (a cached one can be up to a tick old).
        """
        at, ids = await self._listed(now)
        if ids is None or at * 1000 < owed.opened:
            return False
        return owed.grid_id not in ids

    async def _back(self, owed: Owed, now: float) -> bool:
        """Whether a listing taken after ``owed`` was marked ended shows it
        running: what an empty listing from a restarting hub had wrong."""
        at, ids = await self._listed(now)
        return ids is not None and at * 1000 > owed.ended and owed.grid_id in ids

    async def _list(self) -> frozenset[str] | None:
        try:
            ids = frozenset(await anyio.to_thread.run_sync(self.live))
        except Exception as exc:  # noqa: BLE001 - the Grid can blip; assume all live
            if not self._listing_failed:
                log.info(
                    "recordings: the Grid's session list is unavailable (%s); "
                    "every recorded browser counts as running",
                    type(exc).__name__,
                )
            self._listing_failed = True
            return None
        self._listing_failed = False
        return ids

    async def _file(self, owed: Owed, path: Path) -> None:
        """File ``path`` for ``owed``: the move, the mark and the delete as one
        task, shielded and held in ``_saves``. Cancelling the sweep (``stop``)
        does not stop a move already copying in its thread, so without this the
        recording would be filed and its note left unmarked."""
        filing = asyncio.get_running_loop().create_task(self._filing(owed, path))
        self._saves.add(filing)
        filing.add_done_callback(self._saves.discard)
        await asyncio.shield(filing)

    async def _filing(self, owed: Owed, path: Path) -> None:
        try:
            entry = await anyio.to_thread.run_sync(
                self.store.move_in,
                owed.session,
                path,
                name_for(owed.opened),
                RECORDINGS_DIR,
            )
        except (OSError, InvalidName) as exc:
            # The inbox name carries the Grid id; the operator's log never does.
            log.warning(
                "recordings: could not file %s/%s: %s",
                owed.session, name_for(owed.opened), type(exc).__name__,
            )
            return
        await self._forget(owed, entry["name"])
        self._quiet.pop(str(path), None)
        log.info("recordings: filed %s/%s", owed.session, entry["name"])
        if self.on_filed is not None:
            self.on_filed()

    def _quiet_for(self, path: Path, size: int, mtime: float, now: float) -> float:
        """How long ``path`` has kept this size and mtime, as of ``now``."""
        key = str(path)
        seen = self._quiet.get(key)
        if seen is None or seen[0] != size or seen[1] != mtime:
            self._quiet[key] = (size, mtime, now)
            return 0.0
        return now - seen[2]

    def _completeness(self, files) -> dict[str, bool]:
        """Whether each owed file ends in a trailer; a file whose read faulted
        is left out, and the sweep goes blind so no note is dropped meanwhile.
        In a worker thread."""
        names = list(self.owed)
        out: dict[str, bool] = {}
        faults = []
        for path, _size, _mtime in files:
            if path.suffix.lower() != ".mp4" or not any(g in path.name for g in names):
                continue
            try:
                out[str(path)] = mp4.is_complete(path)
            except OSError as exc:
                faults.append(exc)
        if faults:
            self._blind = True
            if not self._fault_logged:
                self._fault_logged = True
                log.warning(
                    "recordings: %d recording(s) cannot be read (%s); they will "
                    "be tried again and nothing owed is dropped while that lasts",
                    len(faults), type(faults[0]).__name__,
                )
        else:
            self._fault_logged = False
        return out

    def _inbox_files(self) -> list[tuple[Path, int, float]]:
        """Every candidate in the inbox with its size and mtime. In a worker
        thread: on a network filesystem a stat can hang."""
        # os.walk swallows a directory it cannot read, which scans as empty and
        # lets an owed recording age past `wait` and be dropped. The inbox root
        # unreadable is a storage fault: raise, so _run logs it once per streak
        # and retries with every expectation kept. A NESTED directory raising
        # too would let one bad subfolder starve every other recording for
        # good, so that one is skipped instead -- but while any subtree is
        # unreadable, sweep() will not drop a late note, since the file could
        # be in there.
        unreadable = []

        def onerror(exc: OSError) -> None:
            if Path(exc.filename or "") == self.inbox:
                raise exc
            unreadable.append(exc)

        found = []
        walk = os.walk(self.inbox, onerror=onerror, followlinks=False)
        for root, dirs, names in walk:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                if name.startswith(".") or name.endswith(PARTIAL_SUFFIXES):
                    continue
                path = Path(root) / name
                try:
                    # lstat: a link is never a recording, whatever it names.
                    info = os.lstat(path)
                except FileNotFoundError:
                    continue  # gone mid-scan: simply gone
                except OSError as exc:
                    unreadable.append(exc)  # present but unseen: blind
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                found.append((path, info.st_size, info.st_mtime))
        self._blind = bool(unreadable)
        if not unreadable:
            self._blind_logged = False
        elif not self._blind_logged:
            self._blind_logged = True
            # Once per streak; no path, which can name a Grid id.
            log.warning(
                "recordings: %d inbox folder(s) cannot be read (%s); recordings "
                "owed will not be dropped while that lasts",
                len(unreadable), type(unreadable[0]).__name__,
            )
        return found

    def _note(self, owed: Owed) -> dict:
        note = {"opened": owed.opened, "ended": owed.ended, "browser": owed.browser}
        if owed.filed is not None:
            note["filed"] = owed.filed
        if owed.dropped:
            note["dropped"] = True
        return note

    def _write(self, owed: Owed) -> None:
        try:
            self.store.write_note(owed.session, owed.grid_id, self._note(owed))
        except OSError as exc:
            log.warning(
                "recordings: could not update a note for %s: %s",
                owed.session, type(exc).__name__,
            )
