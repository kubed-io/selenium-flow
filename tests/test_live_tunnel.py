import asyncio
import contextlib
import json
import logging
import struct

import pytest

from kubed.selenium_flow.faults import SocketClosed
from kubed.selenium_flow.live import tunnel as tunnel_module
from kubed.selenium_flow.live.rfb import RFB_38
from kubed.selenium_flow.live.tunnel import (
    BAD_CONTROL,
    BROKEN,
    BROWSER_ENDED,
    GRID_CLOSED,
    NOBODY,
    STOPPING,
    VIEWER_LEFT,
    Ending,
    Tunnel,
)
from kubed.selenium_flow.live.tunnels import Tunnels
from kubed.selenium_flow.monitor.events import SessionEnded

from .fakes import FakePeer

pytestmark = pytest.mark.unit

LOG = "kubed.selenium_flow.live"
SERVER_INIT = bytes(range(24))
REQUEST = b"\x03\x01" + bytes(8)  # FramebufferUpdateRequest
GRID_ID = "g-0123456789abcdef"


def key(sym: str) -> bytes:
    return struct.pack(">BBxxI", 4, 1, ord(sym))


def cut(text: bytes) -> bytes:
    return struct.pack(">B3xI", 6, len(text)) + text


QEMU_KEY = bytes([255, 0]) + bytes(10)


async def vnc_node(peer, password_asked=True, failure=None):
    """x11vnc on a FakePeer: handshake, then ServerInit."""
    peer.feed(RFB_38)
    assert await peer.next_sent() == RFB_38
    peer.feed(b"\x01\x02" if password_asked else b"\x01\x01")
    if password_asked:
        assert await peer.next_sent() == b"\x02"
        peer.feed(bytes(16))
        assert len(await peer.next_sent()) == 16
        if failure is not None:
            data = failure.encode()
            peer.feed(struct.pack(">I", 1) + struct.pack(">I", len(data)) + data)
            return
    else:
        assert await peer.next_sent() == b"\x01"
    peer.feed(bytes(4))
    assert await peer.next_sent() == b"\x01"  # ClientInit: shared
    peer.feed(SERVER_INIT)


async def page(peer, client_init=b"\x01"):
    """noVNC's side of the downstream handshake; returns what the viewer got first."""
    first = await peer.next_sent()
    assert await peer.next_sent() == RFB_38
    peer.feed(RFB_38)
    assert await peer.next_sent() == bytes([1, 1])
    peer.feed(b"\x01")
    assert await peer.next_sent() == bytes(4)
    peer.feed(client_init)
    assert await peer.next_sent() == SERVER_INIT
    return first


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(viewer=None, **kw):
    viewer = viewer or FakePeer()
    node = FakePeer()
    kw.setdefault("grace", 5)
    kw.setdefault("clock", Clock())
    tunnel = Tunnel(
        viewer, who="drk", workspace="w1", session="session started X", **kw
    )

    async def dial():
        return node

    return tunnel, viewer, node, dial


async def start(tunnel, viewer, node, dial, **node_kw):
    task = asyncio.create_task(tunnel.run(dial, b"secret"))
    results = await asyncio.gather(vnc_node(node, **node_kw), page(viewer))
    return task, results[1]


async def tick(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


def text(**fields):
    return json.dumps(fields)


async def in_control(viewer):
    viewer.feed(text(type="mode", mode="control"))
    assert json.loads(await viewer.next_sent()) == {"type": "mode", "mode": "control"}


async def test_a_tunnel_opens_in_watch_and_says_so(caplog):
    tunnel, viewer, node, dial = make()
    with caplog.at_level(logging.INFO, logger=LOG):
        task, first = await start(tunnel, viewer, node, dial)
        await tick()
        assert json.loads(first) == {"type": "mode", "mode": "watch"}
        assert tunnel.mode == "watch"
        assert caplog.messages == [
            "live: drk opened the live view of w1 (session started X) in watch"
        ]
        tunnel.end(VIEWER_LEFT)
        await task


async def test_client_init_goes_upstream_as_shared_whatever_the_viewer_said():
    tunnel, viewer, node, dial = make()
    task = asyncio.create_task(tunnel.run(dial, b"secret"))
    await asyncio.gather(
        vnc_node(node), page(viewer, client_init=b"\x00")
    )  # vnc_node asserts b"\x01"
    tunnel.end(VIEWER_LEFT)
    await task


async def test_watch_forwards_no_key_and_control_does_after_the_answer():
    tunnel, viewer, node, dial = make()
    task, _ = await start(tunnel, viewer, node, dial)
    viewer.feed(key("a"))
    viewer.feed(REQUEST)
    assert await node.next_sent() == REQUEST  # the key never arrived
    await in_control(viewer)
    viewer.feed(key("b"))
    assert await node.next_sent() == key("b")
    viewer.feed(text(type="mode", mode="watch"))
    assert json.loads(await viewer.next_sent())["mode"] == "watch"
    viewer.feed(key("c"))
    viewer.feed(REQUEST)
    assert await node.next_sent() == REQUEST
    tunnel.end(VIEWER_LEFT)
    await task


async def test_a_refused_control_is_answered_watch():
    tunnel, viewer, node, dial = make(may_control=lambda: False)
    task, _ = await start(tunnel, viewer, node, dial)
    viewer.feed(text(type="mode", mode="control"))
    assert json.loads(await viewer.next_sent()) == {"type": "mode", "mode": "watch"}
    viewer.feed(key("a"))
    viewer.feed(REQUEST)
    assert await node.next_sent() == REQUEST
    assert tunnel.mode == "watch"
    tunnel.end(VIEWER_LEFT)
    await task


async def test_the_grace_ends_a_quiet_tunnel_with_4000():
    tunnel, viewer, node, dial = make(grace=0.2)
    task, _ = await start(tunnel, viewer, node, dial)
    assert (await task) == NOBODY
    assert viewer.closed == (4000, "nobody was watching")
    assert node.closed == (1000, "")


async def test_presence_and_control_input_keep_it_and_watch_input_does_not():
    tunnel, viewer, node, dial = make(grace=0.2)
    task, _ = await start(tunnel, viewer, node, dial)
    for _ in range(4):  # 0.32 s, past the 0.2 s grace
        viewer.feed(text(type="presence"))
        await asyncio.sleep(0.08)
    assert not task.done()
    for _ in range(4):
        viewer.feed(REQUEST)  # watch: pictures only, not presence
        await asyncio.sleep(0.08)
    assert (await task) == NOBODY

    tunnel, viewer, node, dial = make(grace=0.2)
    task, _ = await start(tunnel, viewer, node, dial)
    await in_control(viewer)
    for _ in range(4):
        viewer.feed(key("a"))
        await asyncio.sleep(0.08)
    assert not task.done()
    assert (await task) == NOBODY


async def test_touch_is_called_at_most_once_a_heartbeat():
    clock = Clock()
    touches = []
    tunnel, viewer, node, dial = make(
        clock=clock, touch=lambda: touches.append(clock.now)
    )
    task, _ = await start(tunnel, viewer, node, dial)
    for _ in range(3):
        viewer.feed(text(type="presence"))
        await tick()
        clock.now += 3
    assert touches == [1000.0]
    clock.now = 1010.5
    viewer.feed(text(type="presence"))
    await tick()
    assert touches == [1000.0, 1010.5]
    tunnel.end(VIEWER_LEFT)
    await task


@pytest.mark.parametrize(
    "frame",
    [text(type="x"), "not json", "a" * 2048, "[1]", text(type="mode", mode="x")],
)
async def test_a_bad_control_frame_closes_4006(frame):
    tunnel, viewer, node, dial = make()
    task, _ = await start(tunnel, viewer, node, dial)
    viewer.feed(frame)
    assert (await task) == BAD_CONTROL
    assert viewer.closed == (4006, "bad control message")


async def test_the_hub_closing_closes_the_viewer_4002():
    tunnel, viewer, node, dial = make()
    task, _ = await start(tunnel, viewer, node, dial)
    node.hang_up(1006)
    assert (await task) == GRID_CLOSED
    assert viewer.closed == (4002, "the Grid closed the live view")


async def test_a_dial_that_fails_closes_4002_and_logs_refused(caplog):
    tunnel, viewer, _node, _dial = make()

    async def dial():
        raise SocketClosed(None, "OSError")

    with caplog.at_level(logging.INFO, logger=LOG):
        assert (await tunnel.run(dial, None)) == GRID_CLOSED
    assert viewer.closed == (4002, "the Grid closed the live view")
    assert "refused drk the live view of w1" in caplog.text
    assert "left" not in caplog.text


async def test_a_wrong_password_closes_4003_with_the_node_s_reason():
    tunnel, viewer, node, dial = make()
    task = asyncio.create_task(tunnel.run(dial, b"secret"))
    await vnc_node(node, failure="Authentication failed")
    ending = await task
    assert (ending.code, ending.reason) == (4003, "Authentication failed")
    assert viewer.closed == (4003, "Authentication failed")


async def test_the_viewer_leaving_closes_upstream(caplog):
    tunnel, viewer, node, dial = make()
    with caplog.at_level(logging.INFO, logger=LOG):
        task, _ = await start(tunnel, viewer, node, dial)
        viewer.hang_up(1001)
        assert (await task) == VIEWER_LEFT
    assert node.closed == (1000, "")
    assert viewer.closed is None
    assert (
        "left the live view of w1" in caplog.text and "the viewer closed" in caplog.text
    )


async def test_uvicorn_stopping_reads_as_the_server_stopping(caplog):
    tunnel, viewer, node, dial = make()
    with caplog.at_level(logging.INFO, logger=LOG):
        task, _ = await start(tunnel, viewer, node, dial)
        viewer.hang_up(1012)
        assert (await task) == STOPPING
    assert "the server is stopping" in caplog.text


async def test_the_browser_ending_mid_handshake_closes_4001(caplog):
    tunnel, viewer, node, dial = make()
    tunnels = Tunnels()
    tunnels.add("g1", tunnel)
    with caplog.at_level(logging.INFO, logger=LOG):
        task = asyncio.create_task(tunnel.run(dial, b"secret"))
        node.feed(RFB_38)
        assert await node.next_sent() == RFB_38  # now waiting on the security types
        tunnels.ended(SessionEnded("w1", "g1", "ended", 0.0))
        assert (await task) == BROWSER_ENDED
    assert viewer.closed == (4001, "the browser ended")
    assert node.closed == (1000, "")
    assert "refused" in caplog.text and "left" not in caplog.text


async def test_stop_ends_every_tunnel_1001():
    tunnels = Tunnels()
    runs = []
    for grid_id in ("g1", "g2"):
        tunnel, viewer, node, dial = make()
        tunnels.add(grid_id, tunnel)
        runs.append((viewer, asyncio.create_task(tunnel.run(dial, b"secret")), node))
    await tick()
    tunnels.stop()
    for viewer, task, _ in runs:
        assert (await task) == STOPPING
        assert viewer.closed == (1001, "the server is stopping")


async def test_a_long_reason_is_cut_to_123_bytes():
    tunnel, viewer, node, dial = make()
    task = asyncio.create_task(tunnel.run(dial, b"secret"))
    node.feed(RFB_38)
    await node.next_sent()
    reason = "é" * 100
    node.feed(b"\x00" + struct.pack(">I", len(reason.encode())) + reason.encode())
    ending = await task
    assert ending.reason == reason  # the ending is whole
    code, said = viewer.closed
    assert code == 4003 and len(said.encode()) <= 123 and reason.startswith(said)
    assert len(said.encode()) == 122  # never half a character


async def test_a_mode_change_calls_on_mode():
    tunnel, viewer, node, dial = make()
    calls = []
    tunnel.on_mode = lambda: calls.append(tunnel.mode)
    task, _ = await start(tunnel, viewer, node, dial)
    viewer.feed(text(type="mode", mode="watch"))
    await viewer.next_sent()
    assert calls == []
    await in_control(viewer)
    viewer.feed(text(type="mode", mode="control"))
    await viewer.next_sent()
    viewer.feed(text(type="mode", mode="watch"))
    await viewer.next_sent()
    assert calls == ["control", "watch"]
    tunnel.end(VIEWER_LEFT)
    await task


async def test_nothing_the_viewer_typed_and_no_grid_id_is_logged(caplog):
    tunnel, viewer, node, dial = make()
    tunnels = Tunnels()
    tunnels.add(GRID_ID, tunnel)
    with caplog.at_level(logging.DEBUG):
        task, _ = await start(tunnel, viewer, node, dial)
        await in_control(viewer)
        for message in (key("Z"), cut(b"hunter2"), QEMU_KEY):
            viewer.feed(message)
            assert await node.next_sent() == message
        tunnel.end(VIEWER_LEFT)
        await task
    assert caplog.records
    for needle in ("hunter2", GRID_ID, "exp=", "sig=", "secret"):
        assert needle not in caplog.text


class Exploding(FakePeer):
    """A hub whose sends fail with something no adapter should raise."""

    explode = False

    async def send(self, data):
        if self.explode:
            raise RuntimeError("secret detail http://grid/?sig=abc")
        await super().send(data)


async def broken(task, viewer, node, caplog):
    assert (await task) == BROKEN
    assert viewer.closed == (1011, "the live view failed")
    assert node.closed == (1000, "")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "failed" in warnings[0].getMessage()
    assert "secret detail" not in caplog.text and "sig=" not in caplog.text


async def test_a_touch_that_raises_ends_1011_not_silently(caplog):
    def touch():
        raise ConnectionError("secret detail http://grid/?sig=abc")

    tunnel, viewer, node, dial = make(touch=touch)
    with caplog.at_level(logging.INFO, logger=LOG):
        task, _ = await start(tunnel, viewer, node, dial)
        viewer.feed(text(type="presence"))
        await broken(task, viewer, node, caplog)
    assert "ConnectionError" in caplog.text


async def test_an_upstream_send_that_raises_ends_1011(caplog):
    tunnel, viewer, _, _ = make()
    node = Exploding()

    async def dial():
        return node

    with caplog.at_level(logging.INFO, logger=LOG):
        task, _ = await start(tunnel, viewer, node, dial)
        node.explode = True
        viewer.feed(REQUEST)
        await broken(task, viewer, node, caplog)
    assert "RuntimeError" in caplog.text


async def test_a_reason_from_outside_is_one_capped_line_in_the_log(caplog):
    tunnel, _viewer, node, dial = make()
    reason = "bad\nnews" + "x" * 500
    with caplog.at_level(logging.INFO, logger=LOG):
        task = asyncio.create_task(tunnel.run(dial, b"secret"))
        await vnc_node(node, failure=reason)
        await task
    (line,) = caplog.messages
    assert "\n" not in line and "bad?news" in line
    assert len(line) < 400


async def test_a_control_ask_before_the_handshake_is_answered_watch(caplog):
    tunnel, viewer, _node, dial = make()
    tunnels = Tunnels()
    tunnels.add(GRID_ID, tunnel)
    with caplog.at_level(logging.INFO, logger=LOG):
        task = asyncio.create_task(tunnel.run(dial, b"secret"))
        assert json.loads(await viewer.next_sent()) == {"type": "mode", "mode": "watch"}
        viewer.feed(text(type="mode", mode="control"))  # the node has not spoken
        assert json.loads(await viewer.next_sent()) == {"type": "mode", "mode": "watch"}
        assert tunnel.mode == "watch"
        assert tunnels.controlled("w1") is None and tunnels.notice("w1") is None
        tunnel.end(Ending(4003, "Authentication failed"))
        await task
    assert tunnels.notice("w1") is None
    assert "switched" not in caplog.text


class Stalled(FakePeer):
    """A viewer whose close waits on a transport that never drains."""

    def __init__(self):
        super().__init__()
        self.closing = asyncio.Event()

    async def close(self, code=1000, reason=""):
        self.closing.set()
        await asyncio.Event().wait()


async def test_control_is_handed_back_before_a_stalled_viewer_closes(monkeypatch):
    monkeypatch.setattr(tunnel_module, "CLOSE_TIMEOUT", 0.3)
    viewer = Stalled()
    tunnel, viewer, node, dial = make(viewer)
    tunnels = Tunnels()
    tunnels.add(GRID_ID, tunnel)
    task, _ = await start(tunnel, viewer, node, dial)
    await in_control(viewer)
    assert tunnels.notice("w1").startswith("drk took control")
    tunnel.end(NOBODY)
    await asyncio.wait_for(viewer.closing.wait(), 1)
    assert not task.done()  # the close is still pending
    assert tunnels.controlled("w1") is None
    assert tunnels.notice("w1").startswith("control of this browser was handed back")
    assert (await asyncio.wait_for(task, 1)) == NOBODY


async def test_a_node_that_never_speaks_ends_4002_within_the_timeout(monkeypatch):
    monkeypatch.setattr(tunnel_module, "HANDSHAKE_TIMEOUT", 0.2)
    tunnel, viewer, node, dial = make()
    ending = await asyncio.wait_for(tunnel.run(dial, b"secret"), 1)
    assert ending == Ending(4002, "the Grid's VNC server did not answer")
    assert viewer.closed == (4002, "the Grid's VNC server did not answer")
    assert node.closed == (1000, "")


async def test_a_viewer_that_never_answers_ends_4003_within_the_timeout(monkeypatch):
    monkeypatch.setattr(tunnel_module, "HANDSHAKE_TIMEOUT", 0.3)
    tunnel, viewer, node, dial = make()
    node_side = asyncio.create_task(vnc_node(node))
    ending = await asyncio.wait_for(tunnel.run(dial, b"secret"), 1)
    node_side.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError, AssertionError):
        await node_side
    assert ending == Ending(4003, "the viewer did not finish the VNC handshake")
    assert viewer.closed == (4003, "the viewer did not finish the VNC handshake")
    assert node.closed == (1000, "")


class Lingering(FakePeer):
    """A hub socket whose receive takes a turn to let go once cancelled."""

    async def receive(self):
        try:
            return await super().receive()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            raise


async def test_a_run_cancelled_while_its_tasks_wind_down_raises_the_callers_cancel():
    """anyio's cancel scopes (Starlette's TestClient, a task group) swallow only
    their own cancellation: a task's, raised in its place, escapes the scope."""
    tunnel, *_ = make()
    node = Lingering()

    async def dial():
        return node

    task = asyncio.create_task(tunnel.run(dial, b"secret"))
    await tick()  # the hub's side waits in the handshake
    tunnel.end(VIEWER_LEFT)
    await asyncio.sleep(0)  # the run wakes and waits for its tasks to finish
    task.cancel("the caller's")
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task
    assert cancelled.value.args == ("the caller's",)
    await tick()
