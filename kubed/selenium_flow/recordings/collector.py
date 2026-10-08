"""The collector: files each owed recording into its session as it finishes.

**The queue is the notes on disk** (``sessions/<name>/recordings/.pending/
<gridId>.json``), written when a recorded browser opens; **the engine is one
task per process**, which runs only while a recording is owed and ends when
none is — the shape the admin broadcast has (AGENTS.md: a bounded wait for
something this server was told to expect is allowed; a loop that tidies is
not). The notes survive a restart, so ``start`` picks up where the last
process left off.

It wakes on a change in the inbox (``watchfiles``: events, or polling on a
network filesystem) and on a timer, and each time sweeps: a file whose name
holds an owed Grid id and ends in ``mfro`` is moved into the session's
recordings and its note deleted; one with no ``mfro`` is moved as it is once
it has been quiet for a minute **and** the browser is gone (a live recording
writes a keyframe at least every ~17 s, so this never files one early); an
owed browser not yet known to have ended is asked about, so a file that never
comes has a deadline to miss.

It owns no browser and takes no session lock. ``expect`` and ``ended`` are
called from worker threads (FastMCP's sync tools, Starlette's routes), so they
write to disk there and hand the rest to the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
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
    checked: float = 0.0


class Collector:
    def __init__(
        self,
        store,
        inbox,
        *,
        alive,
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
        self.alive = alive
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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._stop: anyio.Event | None = None
        # Set by `stop`: a recording expected during shutdown starts no task;
        # its note is on disk, so the next process files it.
        self._stopping = False

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
        for session, grid_id, note in await anyio.to_thread.run_sync(self.store.notes):
            self.owed[grid_id] = Owed(
                session, grid_id, int(note.get("opened") or 0),
                str(note.get("browser") or ""), note.get("ended"),
            )
        if self.owed:
            log.info("recordings: %d owed from before the restart", len(self.owed))
            self._ensure()

    async def stop(self) -> None:
        self._stopping = True
        if self._stop is not None:
            self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _add(self, owed: Owed) -> None:
        self.owed[owed.grid_id] = owed
        self._ensure()

    def _mark_ended(self, grid_id: str) -> None:
        owed = self.owed.get(grid_id)
        if owed is None or owed.ended is not None:
            return
        owed.ended = int(self.clock() * 1000)
        self._write(owed)

    def _ensure(self) -> None:
        if not self.running and self._loop is not None and not self._stopping:
            self._task = self._loop.create_task(self._run())

    async def _run(self) -> None:
        try:
            while self.owed:
                self._stop = anyio.Event()
                await self.sweep()
                if not self.owed:
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
                    await self.sweep()
                    if not self.owed:
                        self._stop.set()
        except asyncio.CancelledError:
            raise
        except Exception:  # logged; the notes keep the queue
            log.exception(
                "recordings: the collector stopped; it resumes on the next recording"
            )
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def sweep(self) -> None:
        """One look: file what is finished, ask about the rest, drop the late."""
        files = await anyio.to_thread.run_sync(self._inbox_files)
        now = self.clock()
        for grid_id, owed in list(self.owed.items()):
            match = next((p for p in files if grid_id in p.name), None)
            if match is not None:
                if await anyio.to_thread.run_sync(mp4.is_complete, match):
                    await self._file(owed, match)
                elif (
                    self._quiet_for(match, now) >= self.idle_after
                    and not await self._is_alive(owed)
                ):
                    log.info(
                        "recordings: %s/%s ends without a trailer; filed as it is",
                        owed.session, name_for(owed.opened),
                    )
                    await self._file(owed, match)
                continue
            if owed.ended is None and now - owed.checked >= self.tick:
                owed.checked = now
                if not await self._is_alive(owed):
                    self._mark_ended(grid_id)
            if owed.ended is not None and now * 1000 - owed.ended >= self.wait * 1000:
                await anyio.to_thread.run_sync(
                    self.store.delete_note, owed.session, grid_id
                )
                self.owed.pop(grid_id, None)
                log.warning(
                    "recording for session %s (opened %s UTC) never reached "
                    "RECORDING_DIR; see the README's Recording section",
                    owed.session,
                    time.strftime("%H:%M", time.gmtime(owed.opened / 1000)),
                )

    async def _is_alive(self, owed: Owed) -> bool:
        try:
            return bool(await anyio.to_thread.run_sync(self.alive, owed.grid_id))
        except Exception:  # noqa: BLE001 - the Grid can blip; assume it lives
            return True

    async def _file(self, owed: Owed, path: Path) -> None:
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
        await anyio.to_thread.run_sync(
            self.store.delete_note, owed.session, owed.grid_id
        )
        self.owed.pop(owed.grid_id, None)
        self._quiet.pop(str(path), None)
        log.info("recordings: filed %s/%s", owed.session, entry["name"])
        if self.on_filed is not None:
            self.on_filed()

    def _quiet_for(self, path: Path, now: float) -> float:
        try:
            info = path.stat()
        except OSError:
            return 0.0
        key = str(path)
        seen = self._quiet.get(key)
        if seen is None or seen[0] != info.st_size or seen[1] != info.st_mtime:
            self._quiet[key] = (info.st_size, info.st_mtime, now)
            return 0.0
        return now - seen[2]

    def _inbox_files(self) -> list[Path]:
        found = []
        for root, dirs, names in os.walk(self.inbox):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                if name.startswith(".") or name.endswith(PARTIAL_SUFFIXES):
                    continue
                found.append(Path(root) / name)
        return found

    def _note(self, owed: Owed) -> dict:
        return {"opened": owed.opened, "ended": owed.ended, "browser": owed.browser}

    def _write(self, owed: Owed) -> None:
        try:
            self.store.write_note(owed.session, owed.grid_id, self._note(owed))
        except OSError as exc:
            log.warning(
                "recordings: could not update a note for %s: %s",
                owed.session, type(exc).__name__,
            )
