"""One viewer's tunnel: their WebSocket on one side, the hub's VNC socket on the other.

The tunnel answers both RFB handshakes itself (`rfb`), then pipes: the hub's
bytes go to the viewer as they come, and the viewer's go through the gate, which
in Watch lets only requests for pictures through. Text frames from the viewer
are the control channel: presence, and a request to change mode. Presence and
input in Control are what keep the tunnel alive; with none for `grace` seconds
nobody is watching and it ends (spec 2026-10-27, "Presence and teardown").

Every way the tunnel can end is an `Ending`, the first one wins, and `end` only
records it, so it is safe from a timer, the event bus or the registry. A
tunnel closes both sockets and awaits its own tasks before `run` returns.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ..faults import SocketClosed
from . import audit
from .rfb import SHARED, Bytes, Gate, Mode, Refused, server_handshake, viewer_handshake

HEARTBEAT = 10.0
CONTROL_CAP = 1024
REASON_CAP = 123  # a WebSocket close reason is 123 bytes at most
UVICORN_STOPPING = 1012


class Peer(Protocol):
    async def receive(self) -> bytes | str: ...

    async def send(self, data: bytes | str) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass(frozen=True)
class Ending:
    code: int
    reason: str


NOBODY = Ending(4000, "nobody was watching")
BROWSER_ENDED = Ending(4001, "the browser ended")
GRID_CLOSED = Ending(4002, "the Grid closed the live view")
BAD_CONTROL = Ending(4006, "bad control message")
STOPPING = Ending(1001, "the server is stopping")
VIEWER_LEFT = Ending(1000, "the viewer closed")
BROKEN = Ending(1011, "the live view failed")


class _ViewerGone(Exception):
    """The viewer's socket is closed; the ending is already recorded."""


def _cut(reason: str) -> str:
    return reason.encode()[:REASON_CAP].decode("utf-8", "ignore")


class Tunnel:
    def __init__(
        self,
        viewer: Peer,
        *,
        who: str,
        workspace: str,
        session: str,
        grace: float,
        touch: Callable[[], None] = lambda: None,
        may_control: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.monotonic,
        heartbeat: float = HEARTBEAT,
    ):
        self.viewer = viewer
        self.who = who
        self.workspace = workspace
        self.session = session
        self.mode: Mode = "watch"
        self.on_mode: Callable[[], None] = lambda: None
        self._grace = grace
        self._touch = touch
        self._may_control = may_control
        self._clock = clock
        self._heartbeat = heartbeat
        self._last_touch: float | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._ending: Ending | None = None
        self._ended = asyncio.Event()
        self._bytes = Bytes()
        self._gate = Gate()
        self._upstream: Peer | None = None
        self._piping = False
        self._out = asyncio.Lock()
        self._viewer_gone = False
        self._opened_at: float | None = None

    def end(self, ending: Ending) -> None:
        """Record why the tunnel is over; the first one wins. Never blocks."""
        if self._ending is None:
            self._ending = ending
            self._ended.set()

    # presence ----------------------------------------------------------

    def _arm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(
            self._grace, self.end, NOBODY
        )

    def _present(self) -> None:
        self._arm()
        now = self._clock()
        if self._last_touch is None or now - self._last_touch >= self._heartbeat:
            self._last_touch = now
            self._touch()

    # the viewer --------------------------------------------------------

    async def _read_viewer(self) -> None:
        try:
            while True:
                data = await self.viewer.receive()
                if isinstance(data, str):
                    await self._control(data)
                elif self._piping:
                    await self._forward(data)
                else:
                    self._bytes.feed(data)
        except SocketClosed as closed:
            self._viewer_gone = True
            self._bytes.close(_ViewerGone())
            self.end(STOPPING if closed.code == UVICORN_STOPPING else VIEWER_LEFT)
        except Refused as refused:
            self.end(Ending(refused.code, refused.reason))
        except _ViewerGone:
            pass

    async def _forward(self, data: bytes) -> None:
        async with self._out:
            await self._pass(data)

    async def _pass(self, data: bytes) -> None:
        """Gate the viewer's bytes and send what passes; the caller holds `_out`."""
        passed = self._gate.feed(data, self.mode)
        if passed.input:
            self._present()
        if passed.forward:
            assert self._upstream is not None
            try:
                await self._upstream.send(passed.forward)
            except (SocketClosed, OSError):
                self.end(GRID_CLOSED)

    async def _control(self, raw: str) -> None:
        if len(raw.encode()) > CONTROL_CAP:
            self.end(BAD_CONTROL)
            return
        try:
            message = json.loads(raw)
        except ValueError:
            self.end(BAD_CONTROL)
            return
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "presence":
            self._present()
        elif kind == "mode" and message.get("mode") in ("watch", "control"):
            wanted: Mode = message["mode"]
            if wanted == "control" and not self._may_control():
                wanted = "watch"
            changed = wanted != self.mode
            self.mode = wanted
            if changed:
                self.on_mode()
            await self._to_viewer(json.dumps({"type": "mode", "mode": wanted}))
            if changed:
                audit.switched(self.who, self.workspace, self.session, wanted)
        else:
            self.end(BAD_CONTROL)

    async def _to_viewer(self, data: bytes | str) -> None:
        try:
            await self.viewer.send(data)
        except SocketClosed:
            self._viewer_gone = True
            self.end(VIEWER_LEFT)
            raise _ViewerGone from None

    # the hub -----------------------------------------------------------

    async def _connect(
        self, dial: Callable[[], Awaitable[Peer]], password: bytes | None
    ) -> None:
        try:
            try:
                upstream = await dial()
            except (SocketClosed, OSError):
                raise Refused(GRID_CLOSED.code, GRID_CLOSED.reason) from None
            self._upstream = upstream
            frames = _Frames(upstream)
            try:
                await server_handshake(frames.read, upstream.send, password)
                await viewer_handshake(self._bytes.read, self._to_viewer)
                await upstream.send(SHARED)
            except (SocketClosed, OSError):
                raise Refused(GRID_CLOSED.code, GRID_CLOSED.reason) from None
            async with self._out:
                leftover = self._bytes.take()
                self._piping = True
                self._opened_at = self._clock()
                audit.opened(self.who, self.workspace, self.session, self.mode)
                if leftover:
                    await self._pass(leftover)
                if frames.buffer:
                    await self._to_viewer(bytes(frames.buffer))
            while True:
                try:
                    data = await upstream.receive()
                except SocketClosed:
                    raise Refused(GRID_CLOSED.code, GRID_CLOSED.reason) from None
                if isinstance(data, bytes):
                    await self._to_viewer(data)
        except Refused as refused:
            self.end(Ending(refused.code, refused.reason))
        except _ViewerGone:
            pass

    async def _guarded(self, work: Awaitable[None]) -> None:
        """Run a task's work; whatever it did not expect ends the tunnel, loudly."""
        try:
            await work
        except Exception as error:  # noqa: BLE001 - anything unexpected ends the tunnel
            audit.failed(self.workspace, error)
            self.end(BROKEN)

    # the run -----------------------------------------------------------

    async def run(
        self, dial: Callable[[], Awaitable[Peer]], password: bytes | None
    ) -> Ending:
        tasks: list[asyncio.Task] = []
        try:
            try:
                await self.viewer.send(json.dumps({"type": "mode", "mode": "watch"}))
            except SocketClosed:
                self._viewer_gone = True
                self.end(VIEWER_LEFT)
            else:
                self._arm()
                tasks = [
                    asyncio.create_task(self._guarded(self._read_viewer())),
                    asyncio.create_task(self._guarded(self._connect(dial, password))),
                ]
                await self._ended.wait()
        finally:
            if self._timer is not None:
                self._timer.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            ending = self._ending or STOPPING
            await self._close(ending)
        return ending

    async def _close(self, ending: Ending) -> None:
        if self._upstream is not None:
            with contextlib.suppress(SocketClosed, OSError):
                await self._upstream.close(1000)
        if not self._viewer_gone:
            with contextlib.suppress(SocketClosed, OSError):
                await self.viewer.close(ending.code, _cut(ending.reason))
        if self._opened_at is not None:
            audit.left(
                self.who,
                self.workspace,
                self.session,
                self._clock() - self._opened_at,
                self.mode,
                ending.reason,
            )
        else:
            audit.refused(self.who, self.workspace, ending.reason)


class _Frames:
    """Exact reads over the hub's frames, which are not aligned with RFB messages."""

    def __init__(self, upstream: Peer):
        self._upstream = upstream
        self.buffer = bytearray()

    async def read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            frame = await self._upstream.receive()
            if isinstance(frame, bytes):
                self.buffer += frame
        out = bytes(self.buffer[:n])
        del self.buffer[:n]
        return out
