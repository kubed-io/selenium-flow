# The session monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One monitor and one bus in the process: which sessions this server opened, which ended and why, which it still owes; the Grid's idle timeout read at every open and shown on the workspace; liveness moved out of the recordings collector.

**Architecture:** A new kernel package `monitor/` — `events.py` (value-free event types and a never-blocking bus), `bidi.py` (an asyncio BiDi socket holder on `websockets`), `monitor.py` (the watch registry, one `/status` listing per look, sockets held to a deadline). `Workspaces` announces every session opened and ended; the collector watches what it owes and hears ends from the bus; the admin broadcast is poked by them. `Grid.listing()` and `Grid.session_timeout()` read `/status`, never a session.

**Tech Stack:** Python 3.10+, asyncio, anyio, `websockets>=15` (asyncio client and, in tests, server), pytest + pytest-asyncio (`asyncio_mode = "auto"`), Svelte 5 + vitest for the card.

**Spec:** `docs/superpowers/specs/2026-10-09-session-monitor-design.md` (cited as *spec*; its rulings as *ruling n*). Programme: `docs/superpowers/specs/2026-10-09-workspaces-and-observability-design.md`.

**This plan is written against E1's code** (#60, branch `issue-60-workspaces` at `8d33c6e`), and reconciled with it on 2026-10-09: every path, name, anchor and find-and-replace block below was applied, task by task, to a scratch copy of that tree, and the suite passed after each task (see *Reconciled with E1*, at the end). Names used: `kubed/selenium_flow/workspace/workspaces.py` (`Workspaces`, `Caller`), `workspace/store.py` (`Workspace`, the record), `http/admin/workspaces.py` (`Broadcast`, `workspaces_payload`), `tests/test_workspaces.py`, `tests/golden/admin-workspaces.json`, `ui/src/lib/WorkspaceSummary.svelte`, `WorkspaceRow` in `ui/src/lib/types.ts`, the collector's `Owed.workspace`, the server's `self.workspaces`. If E1 changes again before it merges, run `git grep -n -E "class Workspaces\b|class Workspace\b|class Broadcast|WorkspaceRow|def workspaces_payload"` before Task 1 and use E1's spelling wherever this plan names one. Where a block quotes a comment, it is E1's comment as it stands at `8d33c6e`.

## Global Constraints

Copied from the spec; every task's requirements include these.

- The word is *workspace* for the named record and *session* for the live browser inside it, which holds the Grid's session (`session_id`) (spec, Status; ruling 16).
- Events are `session.opened`, `session.ended` (cause `ended | gone | lost`) and `call.finished`; their id field is `session_id`; `call.finished` is declared and **never emitted** in E2 (ruling 8).
- Events carry no values: every field is a `str`, `int`, `float`, `bool` or `None`, from the allowed set; no subscriber logs a `session_id` (programme R6; rulings 14, 15).
- Publishing never blocks a call; a subscriber is a plain function run on the loop with a bounded queue of 1 000, overflow dropped and counted (ruling 12).
- `monitor/` imports no protocol library, no `selenium`, nothing from `mcp/`, `http/`, `routes`, `server`, `spec`, `workspace/` or `core/`; `websockets` is imported by `monitor/bidi.py` and nowhere else (spec §1; ruling 1).
- Liveness is the `/status` listing, never a session command and never a socket: gone is two listings with nodes, taken after the watch began, that miss it — or one after its socket dropped; a failed listing or one with no nodes says nothing (rulings 3, 4).
- A socket is held only for a reason in `events_for`; E2 passes none, so no socket is held in production; never `network.addIntercept` (ruling 7; spec §4).
- `grid_timeout` is whole seconds or `null`, on the workspace record, kept when the session ends, replaced at every open and reopen; never fails an open (ruling 10).
- No settings: `LISTING_TICK = 30.0`, `MISSES = 2`, `DEFAULT_TIMEOUT = 300`, `ENDED_MEMORY = 4096`, `QUEUE_LIMIT = 1000`, `OPEN_TIMEOUT = 5.0`, `REPLY_TIMEOUT = 5.0`, `PING_INTERVAL = 20.0`, `MAX_MESSAGE = 16 * 2**20` (ruling 13).
- The note queue, its lock, the inbox watch and every filing rule of the collector stay exactly as they are (ruling 5).
- No new integration flow (AGENTS.md "Integration tests: less is more").
- The changelog gets one line (spec §12), and only in `[Unreleased]`.

**Commands.** CI runs plain `pytest`, `ruff check .` and the UI suite. In a sandbox where an installed `kubed` package shadows the tree, prefix Python with `PYTHONPATH=$PWD:$DEPS` (the deps directory); the commands below are written for CI's environment, run from the repo root. UI: `npm --prefix ui test`, `npm --prefix ui run -s check`, `npm --prefix ui run -s lint`.

---

## File structure

| Path | Task | Responsibility |
|---|---|---|
| `kubed/selenium_flow/monitor/__init__.py` | 1, 4 | the package's exports |
| `kubed/selenium_flow/monitor/events.py` | 1 | `Event`, `SessionOpened`, `SessionEnded`, `CallFinished`, `EVENTS`, `Bus`, `LocalBus`, `Subscription` |
| `kubed/selenium_flow/monitor/bidi.py` | 2 | `BidiSocket`, `BidiError`: one held BiDi connection |
| `kubed/selenium_flow/monitor/monitor.py` | 4 | `Monitor`, `Watch`: the registry, the listing, the sockets |
| `kubed/selenium_flow/core/browser.py` | 3 | `Grid.listing()`, `Grid.session_timeout()`, `Grid.bidi_url()` |
| `kubed/selenium_flow/workspace/store.py` | 5 | `Workspace.grid_timeout` |
| `kubed/selenium_flow/workspace/workspaces.py` | 5 | read the timeout; announce opened, ended, lost |
| `kubed/selenium_flow/mcp/resources.py`, `spec/schemas.py` | 5 | `grid_timeout` described and in the status schema |
| `kubed/selenium_flow/recordings/collector.py` | 6 | liveness out; `monitor` watch/release in |
| `kubed/selenium_flow/server.py` | 6 | the bus, the monitor, the lifespan, the subscriptions |
| `kubed/selenium_flow/http/admin/workspaces.py` | 7 | `grid_timeout` on the admin row |
| `ui/src/lib/types.ts`, `format.ts`, `WorkspaceSummary.svelte` | 7 | the `idle timeout` fact |
| `pyproject.toml` | 1, 2 | the package; `websockets>=15` |
| `tests/test_monitor_events.py`, `test_monitor_bidi.py`, `test_monitor.py`, `test_grid_listing.py`, `test_workspace_monitor.py` | 1–5 | new |
| `tests/test_boundaries.py`, `test_recording_collector.py`, `test_recording_open.py`, `test_admin_events.py`, goldens | 1, 2, 4–7 | changed |
| `AGENTS.md`, `README.md`, `skills/selenium-flow/references/WORKSPACES.md`, `wiki/`, `CHANGELOG.md` | 8 | the doctrine and the docs |

---

### Task 1: The events and the bus

**Files:**
- Create: `kubed/selenium_flow/monitor/__init__.py`, `kubed/selenium_flow/monitor/events.py`
- Modify: `pyproject.toml` (the packages list), `tests/test_boundaries.py`
- Test: `tests/test_monitor_events.py`

**Interfaces:**
- Produces: `Event` (frozen dataclass, `KIND: ClassVar[str]`, property `kind`); `SessionOpened(workspace: str, session_id: str, browser: str, grid_timeout: int | None, reopened: bool, at: float)`; `SessionEnded(workspace: str, session_id: str, cause: Literal["ended","gone","lost"], at: float)`; `CallFinished(workspace, session_id, browser, tool: str, surface: Literal["mcp","http","flow"], outcome: Literal["ok","4xx","5xx"], started: float, duration: float)`; `EVENTS: dict[str, type[Event]]`; `QUEUE_LIMIT = 1000`; `class Bus(Protocol)` with `publish(event) -> None` and `subscribe(kinds, handler, *, name, limit=QUEUE_LIMIT) -> Subscription`; `LocalBus` (`start()` on the loop, `async stop()`, `publish`, `subscribe`); `Subscription` (`kinds`, `handler`, `name`, `limit`, `dropped`, `queue`, `cancel()`).

- [ ] **Step 1: Write the failing test**

`tests/test_monitor_events.py`:

```python
"""The events carry no values, and the bus never blocks the call that publishes."""

import asyncio
import dataclasses
import logging
import threading
import types
import typing

import pytest

from kubed.selenium_flow.monitor import events as events_module
from kubed.selenium_flow.monitor.events import (
    EVENTS,
    LocalBus,
    SessionEnded,
    SessionOpened,
)

pytestmark = pytest.mark.unit

GID = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"

# Every field any event may carry. A new one is a decision about what leaves a
# call, so it is added here on purpose, never by accident (programme R6).
ALLOWED = {
    "workspace",
    "session_id",
    "browser",
    "grid_timeout",
    "reopened",
    "cause",
    "at",
    "tool",
    "surface",
    "outcome",
    "started",
    "duration",
}
SCALARS = {str, int, float, bool, type(None)}


def _scalar(hint) -> bool:
    if hint in SCALARS:
        return True
    origin = typing.get_origin(hint)
    if origin is typing.Literal:
        return all(isinstance(a, str) for a in typing.get_args(hint))
    if origin in (typing.Union, types.UnionType):
        return all(_scalar(a) for a in typing.get_args(hint))
    return False


def test_events_carry_no_values():
    for kind, cls in EVENTS.items():
        assert kind == cls.KIND
        hints = typing.get_type_hints(cls, vars(events_module))
        for field in dataclasses.fields(cls):
            assert field.name in ALLOWED, f"{kind}.{field.name} is not an allowed field"
            assert _scalar(hints[field.name]), f"{kind}.{field.name} is not a scalar"


def test_the_kinds_are_the_three_the_programme_names():
    assert set(EVENTS) == {"session.opened", "session.ended", "call.finished"}


def opened(at=1.0):
    return SessionOpened("bot", GID, "chrome", 300, False, at)


def test_before_start_an_event_is_delivered_inline_to_its_kind_only():
    bus, got = LocalBus(), []
    bus.subscribe("session.opened", got.append, name="t")
    bus.subscribe("session.ended", lambda e: got.append("wrong"), name="u")
    bus.publish(opened())
    assert got == [opened()]


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="no such event"):
        LocalBus().subscribe("browser.closed", print, name="t")


async def test_on_the_loop_a_handler_runs_after_publish_returns():
    bus, got = LocalBus(), []
    bus.start()
    bus.subscribe(("session.opened", "session.ended"), got.append, name="t")
    bus.publish(opened())
    assert got == []  # never inside the publisher's call
    await asyncio.sleep(0)
    assert got == [opened()]
    await bus.stop()


async def test_from_a_worker_thread_the_handler_runs_on_the_loop():
    bus, threads = LocalBus(), []
    bus.start()
    bus.subscribe(
        "session.opened", lambda e: threads.append(threading.current_thread()), name="t"
    )
    await asyncio.to_thread(bus.publish, opened())
    for _ in range(20):
        if threads:
            break
        await asyncio.sleep(0.01)
    assert threads == [threading.current_thread()]
    await bus.stop()


async def test_after_stop_nothing_is_delivered():
    bus, got = LocalBus(), []
    bus.start()
    bus.subscribe("session.opened", got.append, name="t")
    await bus.stop()
    bus.publish(opened())
    await asyncio.sleep(0)
    assert got == []


async def test_a_full_queue_drops_and_counts_and_logs_once(caplog):
    bus, got = LocalBus(), []
    bus.start()
    sub = bus.subscribe("session.opened", got.append, name="slow", limit=2)
    with caplog.at_level(logging.WARNING):
        for n in range(5):
            bus.publish(opened(at=float(n)))
    await asyncio.sleep(0)
    assert [e.at for e in got] == [0.0, 1.0] and sub.dropped == 3
    assert caplog.text.count("slow is 2 events behind") == 1
    await bus.stop()


async def test_a_raising_handler_is_logged_by_type_once_and_others_still_hear(caplog):
    bus, got = LocalBus(), []
    bus.start()

    def broken(event):
        raise RuntimeError(f"secret {event.session_id}")

    bus.subscribe("session.ended", broken, name="broken")
    bus.subscribe("session.ended", got.append, name="fine")
    with caplog.at_level(logging.WARNING):
        bus.publish(SessionEnded("bot", GID, "gone", 1.0))
        bus.publish(SessionEnded("bot", GID, "gone", 2.0))
        await asyncio.sleep(0)
    assert len(got) == 2
    assert caplog.text.count("broken failed on session.ended (RuntimeError)") == 1
    assert GID not in caplog.text
    await bus.stop()


def test_a_cancelled_subscription_hears_nothing():
    bus, got = LocalBus(), []
    sub = bus.subscribe("session.opened", got.append, name="t")
    sub.cancel()
    bus.publish(opened())
    assert got == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/test_monitor_events.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'kubed.selenium_flow.monitor'`.

- [ ] **Step 3: Write the package and the bus**

`kubed/selenium_flow/monitor/__init__.py` (for now; Task 2 and Task 4 extend it):

```python
"""The session monitor: which sessions are open, ended and owed, said on a bus.

See `events.py` for the bus, and the session monitor spec
(docs/superpowers/specs/2026-10-09-session-monitor-design.md) for why it is
the way it is.
"""

from .events import (
    EVENTS,
    Bus,
    CallFinished,
    Event,
    LocalBus,
    SessionEnded,
    SessionOpened,
    Subscription,
)

__all__ = [
    "EVENTS",
    "Bus",
    "CallFinished",
    "Event",
    "LocalBus",
    "SessionEnded",
    "SessionOpened",
    "Subscription",
]
```

`kubed/selenium_flow/monitor/events.py`:

```python
"""What the monitor says, and the bus that carries it.

**Events carry no values** (programme R6). Every field is a scalar — a name,
the session's ``session_id``, a kind, a time — never an argument, a URL, a
header, a body or a secret, so any subscriber is safe by construction.
`EVENTS` lists every type, and a test holds the rule over all of them.

**Publishing never blocks a call.** `publish` is called from worker threads
(FastMCP's sync tools, Starlette's routes) and hands the event to the event
loop; each subscriber has a bounded queue, drained by a callback the loop
schedules, and an event that does not fit is dropped and counted. A subscriber
is a plain function run on the loop: it must not block, and one that needs to
await starts its own task. No task waits on the bus, so nothing here runs when
nothing is published (AGENTS.md, "Refresh, not cleanup").

`Bus` is the interface; `LocalBus` is this process's. A Redis bus could stand
behind the same two methods for a second replica (programme R5); it is not
built.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections import deque
from collections.abc import Callable, Iterable
from typing import ClassVar, Literal, Protocol

log = logging.getLogger(__name__)

# How many events one subscriber may fall behind before the next is dropped.
QUEUE_LIMIT = 1000

Cause = Literal["ended", "gone", "lost"]
Surface = Literal["mcp", "http", "flow"]
Outcome = Literal["ok", "4xx", "5xx"]


@dataclasses.dataclass(frozen=True)
class Event:
    """The base of every event: its kind, and nothing else."""

    KIND: ClassVar[str] = ""

    @property
    def kind(self) -> str:
        return self.KIND


@dataclasses.dataclass(frozen=True)
class SessionOpened(Event):
    """A session this server opened is bound to its workspace."""

    KIND: ClassVar[str] = "session.opened"
    workspace: str
    session_id: str
    browser: str
    grid_timeout: int | None
    reopened: bool
    at: float


@dataclasses.dataclass(frozen=True)
class SessionEnded(Event):
    """A session has ended: ``ended`` (we quit it), ``gone`` (the Grid's
    listing stopped showing it) or ``lost`` (a call found it gone)."""

    KIND: ClassVar[str] = "session.ended"
    workspace: str
    session_id: str
    cause: Cause
    at: float


@dataclasses.dataclass(frozen=True)
class CallFinished(Event):
    """One call finished. Declared here, emitted by its first consumer (the
    session monitor spec, ruling 8): once per call, at the recipe's host."""

    KIND: ClassVar[str] = "call.finished"
    workspace: str
    session_id: str
    browser: str
    tool: str
    surface: Surface
    outcome: Outcome
    started: float
    duration: float


EVENTS: dict[str, type[Event]] = {
    cls.KIND: cls for cls in (SessionOpened, SessionEnded, CallFinished)
}


class Subscription:
    """One subscriber: the kinds it hears, its handler and its queue."""

    def __init__(self, bus, kinds, handler, name: str, limit: int):
        self.kinds = frozenset(kinds)
        self.handler = handler
        self.name = name
        self.limit = limit
        self.dropped = 0
        self.queue: deque[Event] = deque()
        self._bus = bus
        self._scheduled = False
        self._overflowing = False
        self._failing = False

    def cancel(self) -> None:
        """Hear nothing more."""
        self._bus._remove(self)


class Bus(Protocol):
    def publish(self, event: Event) -> None: ...

    def subscribe(
        self,
        kinds: str | Iterable[str],
        handler: Callable[[Event], None],
        *,
        name: str,
        limit: int = QUEUE_LIMIT,
    ) -> Subscription: ...


class LocalBus:
    """The bus in this process. ``start`` binds it to the running loop."""

    def __init__(self):
        # Replaced, never mutated: a publisher iterating it in another thread
        # sees one list or the other, never one half changed.
        self._subs: tuple[Subscription, ...] = ()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopped = False

    def start(self) -> None:
        """Deliver on the running loop from now on. Called on it."""
        self._loop = asyncio.get_running_loop()
        self._stopped = False

    async def stop(self) -> None:
        """Deliver nothing more; what is queued already is handed out."""
        self._stopped = True
        await asyncio.sleep(0)
        self._loop = None

    def subscribe(
        self,
        kinds: str | Iterable[str],
        handler: Callable[[Event], None],
        *,
        name: str,
        limit: int = QUEUE_LIMIT,
    ) -> Subscription:
        kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
        unknown = set(kinds) - set(EVENTS)
        if unknown:
            raise ValueError(f"no such event: {', '.join(sorted(unknown))}")
        sub = Subscription(self, kinds, handler, name, limit)
        self._subs = (*self._subs, sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        self._subs = tuple(s for s in self._subs if s is not sub)

    def publish(self, event: Event) -> None:
        """Hand ``event`` to every subscriber of its kind. Never blocks."""
        if self._stopped:
            return
        loop = self._loop
        if loop is None:
            # Not started: tests, and nothing in production (the lifespan
            # starts the bus before any request). Delivered here and now.
            self._fan_out(event, inline=True)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fan_out(event)
            return
        # A loop closed under a late caller refuses; the event is dropped.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._fan_out, event)

    def _fan_out(self, event: Event, inline: bool = False) -> None:
        for sub in self._subs:
            if event.kind not in sub.kinds:
                continue
            if len(sub.queue) >= sub.limit:
                sub.dropped += 1
                if not sub._overflowing:
                    sub._overflowing = True
                    log.warning(
                        "bus: %s is %d events behind; dropping %s",
                        sub.name,
                        sub.limit,
                        event.kind,
                    )
                continue
            sub.queue.append(event)
            if inline or self._loop is None:
                self._drain(sub)
            elif not sub._scheduled:
                sub._scheduled = True
                self._loop.call_soon(self._drain, sub)

    def _drain(self, sub: Subscription) -> None:
        sub._scheduled = False
        while sub.queue:
            event = sub.queue.popleft()
            try:
                sub.handler(event)
            except Exception as exc:  # noqa: BLE001 - one subscriber, not the bus
                if not sub._failing:
                    sub._failing = True
                    # The type alone: a message could carry what an event never does.
                    log.warning(
                        "bus: %s failed on %s (%s)",
                        sub.name,
                        event.kind,
                        type(exc).__name__,
                    )
            else:
                sub._failing = False
        sub._overflowing = False
```

In `pyproject.toml`, `[tool.setuptools] packages`, after `"kubed.selenium_flow.mcp",` add:

```toml
  "kubed.selenium_flow.monitor",
```

In `tests/test_boundaries.py`:

- in `NO_PROTOCOL`, after `*walk("recordings"),` add `*walk("monitor"),`;
- in `NO_SELENIUM`, after `"recordings.mp4",` add `"monitor.events",`;
- `KERNEL` gains `"monitor"` as its last entry: `("core", "workspace", "flows", "site_data", "recordings", "monitor")`;
- in `test_the_walk_finds_the_layers`, after its docstring, below `assert "kubed.selenium_flow.flows.engine" in NO_PROTOCOL`: `assert "kubed.selenium_flow.monitor.events" in NO_PROTOCOL` (not above the docstring, which would stop being one).

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `pytest tests/test_monitor_events.py tests/test_boundaries.py tests/test_packaging.py -q`
Expected: all pass (10 in `test_monitor_events.py`).

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/monitor pyproject.toml tests/test_monitor_events.py tests/test_boundaries.py
git commit -m "Monitor: value-free session events and a bus that never blocks the call

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The held BiDi socket

**Files:**
- Create: `kubed/selenium_flow/monitor/bidi.py`
- Modify: `kubed/selenium_flow/monitor/__init__.py`, `pyproject.toml` (dependency), `tests/test_boundaries.py`
- Test: `tests/test_monitor_bidi.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `BidiSocket(url, *, on_event: Callable[[str, dict], None] | None = None, on_close: Callable[[], None] | None = None, connect=None)` with `async open()`, `async command(method, params=None, *, timeout=REPLY_TIMEOUT) -> dict`, `async subscribe(events: Sequence[str]) -> str`, `async unsubscribe(subscription: str)`, `async reopen()`, `async close()` (never raises), properties `is_open`, `dropped`, attribute `subscriptions: dict[str, tuple[str, ...]]`; `BidiError(error, message="")` with `.error`; constants `OPEN_TIMEOUT`, `REPLY_TIMEOUT`, `PING_INTERVAL`, `MAX_MESSAGE`, `FORBIDDEN`.

- [ ] **Step 1: Write the failing tests**

`tests/test_monitor_bidi.py`:

```python
"""The held BiDi socket, against a real WebSocket server on localhost."""

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from kubed.selenium_flow.monitor.bidi import BidiError, BidiSocket

pytestmark = pytest.mark.unit


class FakeBrowser:
    """Enough BiDi to hold a socket against: subscribe, unsubscribe, a few
    commands, an error, silence, and events on demand. ``received`` is every
    command in the order it arrived, across connections."""

    def __init__(self):
        self.received = []
        self.connections = []
        self.subscriptions = 0
        self.server = None
        self.url = ""

    async def handler(self, ws):
        self.connections.append(ws)
        async for raw in ws:
            message = json.loads(raw)
            self.received.append(message["method"])
            method, cid = message["method"], message["id"]
            if method == "session.subscribe":
                self.subscriptions += 1
                reply = {"subscription": f"sub-{self.subscriptions}"}
            elif method == "session.unsubscribe":
                reply = {}
            elif method == "browsingContext.getTree":
                reply = {"contexts": []}
            elif method == "silence":
                continue  # what a socket whose browser ended does (measured)
            else:
                await ws.send(
                    json.dumps(
                        {
                            "id": cid,
                            "type": "error",
                            "error": "unknown command",
                            "message": f"Unknown command '{method}'.",
                        }
                    )
                )
                continue
            await ws.send(json.dumps({"id": cid, "type": "success", "result": reply}))

    async def event(self, method="log.entryAdded", params=None):
        await self.connections[-1].send(
            json.dumps({"type": "event", "method": method, "params": params or {}})
        )

    async def __aenter__(self):
        self.server = await serve(self.handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/session/x/se/bidi"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
async def browser():
    async with FakeBrowser() as fake:
        yield fake


async def test_a_command_is_answered_by_its_id(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    assert await sock.command("browsingContext.getTree") == {"contexts": []}
    await sock.close()


async def test_an_error_reply_raises_bidi_error(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    with pytest.raises(BidiError, match="unknown command"):
        await sock.command("nope.nothing")
    await sock.close()


async def test_a_command_with_no_reply_times_out(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    with pytest.raises(asyncio.TimeoutError):
        await sock.command("silence", timeout=0.1)
    await sock.close()


async def test_an_intercept_is_refused_before_anything_is_sent(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    with pytest.raises(ValueError, match="never sent"):
        await sock.command("network.addIntercept", {"phases": ["beforeRequestSent"]})
    await sock.close()
    assert "network.addIntercept" not in browser.received


async def test_events_reach_on_event(browser):
    got = []
    sock = BidiSocket(browser.url, on_event=lambda m, p: got.append((m, p)))
    await sock.open()
    sub = await sock.subscribe(["log.entryAdded"])
    assert sub == "sub-1" and sock.subscriptions == {"sub-1": ("log.entryAdded",)}
    await browser.event(params={"level": "info"})
    for _ in range(50):
        if got:
            break
        await asyncio.sleep(0.01)
    assert got == [("log.entryAdded", {"level": "info"})]
    await sock.close()


async def test_close_unsubscribes_first(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    await sock.subscribe(["log.entryAdded"])
    await sock.close()
    assert browser.received == ["session.subscribe", "session.unsubscribe"]
    assert not sock.is_open and not sock.dropped


async def test_a_server_close_fails_waiting_commands_and_calls_on_close(browser):
    closed = []
    sock = BidiSocket(browser.url, on_close=lambda: closed.append(1))
    await sock.open()
    waiting = asyncio.ensure_future(sock.command("silence", timeout=5))
    await asyncio.sleep(0.05)
    await browser.connections[-1].close()
    with pytest.raises(ConnectionError):
        await waiting
    for _ in range(50):
        if closed:
            break
        await asyncio.sleep(0.01)
    assert closed == [1] and sock.dropped and not sock.is_open
    await sock.close()


async def test_reopen_asks_again_for_the_same_events(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    await sock.subscribe(["log.entryAdded", "network.responseCompleted"])
    await browser.connections[-1].close()
    await asyncio.sleep(0.05)
    await sock.reopen()
    assert len(browser.connections) == 2
    assert sock.subscriptions == {
        "sub-2": ("log.entryAdded", "network.responseCompleted")
    }
    await sock.close()
```

Append to `tests/test_boundaries.py`:

```python
# ---- one socket library, one importer -----------------------------------------

# The held BiDi socket is the monitor's (session monitor spec, ruling 1). A
# second module holding a socket of its own would be a second set of rules about
# pings, deadlines and intercepts, so `websockets` has exactly one importer.
WEBSOCKETS_HOME = "monitor/bidi.py"


def websockets_importers() -> set[str]:
    """Every module under the package that imports ``websockets``."""
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module or ""]
            else:
                continue
            if any(n == "websockets" or n.startswith("websockets.") for n in names):
                found.add(path.relative_to(PACKAGE).as_posix())
    return found


def test_only_the_monitor_holds_a_socket():
    assert websockets_importers() == {WEBSOCKETS_HOME}
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_monitor_bidi.py tests/test_boundaries.py::test_only_the_monitor_holds_a_socket -q`
Expected: `ModuleNotFoundError: No module named 'kubed.selenium_flow.monitor.bidi'`, and the boundary test fails with `set() == {'monitor/bidi.py'}`.

- [ ] **Step 3: Write the socket**

`kubed/selenium_flow/monitor/bidi.py`:

```python
"""One held WebDriver BiDi connection to one session's browser.

The per-call socket site data uses (`Grid.bidi`) is Selenium's: a thread per
connection and replies found by polling, right for one command and wrong for a
listener held on the event loop. This is the held one, on ``websockets``'
asyncio client — the only module that imports it (`tests/test_boundaries.py`).

**A socket is a channel, never a liveness signal.** Measured against the
cluster's Grid (session monitor spec, Verify first): a held socket stays open
after its session is deleted and after the Grid reaps it, the hub still answers
its pings, and a command sent on it is never answered. So the monitor closes a
socket when its watch ends, and a command that gets no reply is the sign of a
zombie socket, not of a session to declare gone.

**Never an intercept.** An intercept belongs to the Grid session, outlives
the socket, and leaves every request it matches hanging with nobody to answer.
`command` refuses one before anything is sent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Sequence

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)

# Seconds to open a socket, and to wait for one command's reply.
OPEN_TIMEOUT = 5.0
REPLY_TIMEOUT = 5.0
# A ping is not activity (measured): it costs the browser nothing and notices a
# dead path to the hub. It proves the hub, never the browser.
PING_INTERVAL = 20.0
# A network event or a body can be large; the default 1 MiB is not enough.
MAX_MESSAGE = 16 * 2**20

FORBIDDEN = frozenset({"network.addIntercept"})


class BidiError(Exception):
    """The browser answered a command with an error."""

    def __init__(self, error: str, message: str = ""):
        super().__init__(f"{error}: {message}" if message else error)
        self.error = error


def _connect(url: str):
    return ws_connect(
        url,
        open_timeout=OPEN_TIMEOUT,
        ping_interval=PING_INTERVAL,
        ping_timeout=PING_INTERVAL,
        max_size=MAX_MESSAGE,
    )


class BidiSocket:
    """A held socket: commands by id, events to ``on_event``.

    ``on_event(method, params)`` and ``on_close()`` run on the loop and must
    not block. ``connect(url)`` returns an awaitable connection; it is the
    seam a test replaces.
    """

    def __init__(
        self,
        url: str,
        *,
        on_event: Callable[[str, dict], None] | None = None,
        on_close: Callable[[], None] | None = None,
        connect=None,
    ):
        self.url = url
        self.on_event = on_event
        self.on_close = on_close
        self._connect = connect or _connect
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._closing = False
        self._closed = True
        # subscription id -> the events it named, so a reopen can ask again.
        self.subscriptions: dict[str, tuple[str, ...]] = {}

    @property
    def is_open(self) -> bool:
        return self._ws is not None and not self._closed

    @property
    def dropped(self) -> bool:
        """Closed, and not by us."""
        return self._closed and not self._closing and self._ws is not None

    async def open(self) -> None:
        self._closing = False
        self._ws = await asyncio.wait_for(self._connect(self.url), OPEN_TIMEOUT)
        self._closed = False
        self._reader = asyncio.get_running_loop().create_task(self._read(self._ws))

    async def command(
        self, method: str, params: dict | None = None, *, timeout: float = REPLY_TIMEOUT
    ) -> dict:
        """The result of one command, or BidiError / TimeoutError /
        ConnectionError."""
        if method in FORBIDDEN:
            raise ValueError(
                f"{method} is never sent: an intercept outlives the socket and "
                "hangs every request it matches"
            )
        if not self.is_open:
            raise ConnectionError("the BiDi socket is not open")
        self._next_id += 1
        cid = self._next_id
        reply = asyncio.get_running_loop().create_future()
        self._pending[cid] = reply
        try:
            await self._ws.send(
                json.dumps({"id": cid, "method": method, "params": params or {}})
            )
            return await asyncio.wait_for(reply, timeout)
        finally:
            self._pending.pop(cid, None)

    async def subscribe(self, events: Sequence[str]) -> str:
        """Subscribe to ``events`` across the browser; the subscription's id."""
        result = await self.command("session.subscribe", {"events": list(events)})
        subscription = str(result.get("subscription") or "")
        if not subscription:
            raise BidiError("no subscription id", "session.subscribe returned none")
        self.subscriptions[subscription] = tuple(events)
        return subscription

    async def unsubscribe(self, subscription: str) -> None:
        await self.command("session.unsubscribe", {"subscriptions": [subscription]})
        self.subscriptions.pop(subscription, None)

    async def close(self) -> None:
        """Unsubscribe each subscription, then close. Never raises."""
        self._closing = True
        if self.is_open:
            for subscription in list(self.subscriptions):
                with contextlib.suppress(Exception):
                    await self.unsubscribe(subscription)
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(), OPEN_TIMEOUT)
        reader, self._reader = self._reader, None
        if reader is not None and not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        self._closed = True

    async def reopen(self) -> None:
        """Open again and ask for the same events: an id is its connection's."""
        events = sorted({e for named in self.subscriptions.values() for e in named})
        await self.close()
        self.subscriptions = {}
        await self.open()
        if events:
            await self.subscribe(events)

    async def _read(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                cid = message.get("id")
                if cid is not None:
                    reply = self._pending.get(cid)
                    if reply is None or reply.done():
                        continue
                    if message.get("type") == "error":
                        reply.set_exception(
                            BidiError(
                                str(message.get("error", "")),
                                str(message.get("message", "")),
                            )
                        )
                    else:
                        reply.set_result(message.get("result") or {})
                elif message.get("type") == "event" and self.on_event is not None:
                    try:
                        self.on_event(
                            str(message.get("method", "")), message.get("params") or {}
                        )
                    except Exception as exc:  # noqa: BLE001 - one event, not the socket
                        log.debug(
                            "bidi: an event handler failed (%s)", type(exc).__name__
                        )
        except ConnectionClosed:
            pass
        finally:
            self._closed = True
            for reply in self._pending.values():
                if not reply.done():
                    reply.set_exception(ConnectionError("the BiDi socket closed"))
            if not self._closing and self.on_close is not None:
                with contextlib.suppress(Exception):
                    self.on_close()
```

In `kubed/selenium_flow/monitor/__init__.py` add `from .bidi import BidiError, BidiSocket` above the `.events` import, and `"BidiError", "BidiSocket",` to `__all__` (sorted).

In `pyproject.toml`, `[project] dependencies`, after `"sse-starlette>=3.3",` add:

```toml
  # The session monitor's held BiDi socket (monitor/bidi.py, the only
  # importer): an asyncio client with pings. It already arrives with fastmcp,
  # whose floor this is; declared because the module imports it.
  "websockets>=15",
```

In `tests/test_boundaries.py`, `NO_SELENIUM`, after `"monitor.events",` add `"monitor.bidi",`.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `pytest tests/test_monitor_bidi.py tests/test_boundaries.py tests/test_packaging.py -q`
Expected: all pass (8 in `test_monitor_bidi.py`).

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/monitor pyproject.toml tests/test_monitor_bidi.py tests/test_boundaries.py
git commit -m "Monitor: a held BiDi socket on websockets, never an intercept

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The Grid's listing, a session's timeout, its BiDi address

**Files:**
- Modify: `kubed/selenium_flow/core/browser.py`
- Test: `tests/test_grid_listing.py`

**Interfaces:**
- Produces: `Grid.listing() -> tuple[int, dict[str, int | None]]` (node count; Grid session id → its node's `sessionTimeout` in seconds or None), `Grid.session_timeout(session_id) -> int | None`, `Grid.bidi_url(session_id) -> str`; module function `_seconds(ms) -> int | None`. `Grid.bidi` now builds its address with `bidi_url`.

- [ ] **Step 1: Write the failing tests**

`tests/test_grid_listing.py`:

```python
"""What `/status` says about running browsers, and where a browser's BiDi is."""

import pytest

from kubed.selenium_flow.core.browser import Grid

pytestmark = pytest.mark.unit

# The shape the cluster's Grid 4.48.0 answered with on 2026-10-09, trimmed.
STATUS = {
    "value": {
        "ready": True,
        "nodes": [
            {
                "id": "node-a",
                "sessionTimeout": 300000,
                "slots": [
                    {"session": {"sessionId": "aaa"}},
                    {"session": None},
                ],
            },
            {
                "id": "node-b",
                "sessionTimeout": 90000,
                "slots": [{"session": {"sessionId": "bbb"}}],
            },
            {"id": "node-c", "slots": [{"session": {"sessionId": "ccc"}}]},
        ],
    }
}


def grid(payload=STATUS, url="http://grid:4444"):
    g = Grid(url)
    g.status = lambda: payload
    return g


def test_the_listing_counts_nodes_and_maps_each_browser_to_its_timeout():
    assert grid().listing() == (3, {"aaa": 300, "bbb": 90, "ccc": None})


def test_a_grid_with_no_nodes_lists_nothing():
    assert grid({"value": {"ready": False, "nodes": []}}).listing() == (0, {})


@pytest.mark.parametrize("value", [0, -1, "300000", True, None])
def test_a_timeout_that_is_not_a_positive_number_is_none(value):
    payload = {
        "value": {
            "nodes": [
                {"sessionTimeout": value, "slots": [{"session": {"sessionId": "a"}}]}
            ]
        }
    }
    assert grid(payload).listing() == (1, {"a": None})


def test_session_timeout_reads_the_browsers_own_node():
    g = grid()
    assert g.session_timeout("bbb") == 90
    assert g.session_timeout("gone") is None


@pytest.mark.parametrize(
    ("url", "socket"),
    [
        ("http://grid:4444", "ws://grid:4444/session/abc/se/bidi"),
        ("https://grid.example/wd/", "wss://grid.example/wd/session/abc/se/bidi"),
    ],
)
def test_the_bidi_url_is_derived_from_the_grids_own(url, socket):
    assert Grid(url).bidi_url("abc") == socket
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_grid_listing.py -q`
Expected: FAIL, `AttributeError: 'Grid' object has no attribute 'listing'` (and `bidi_url`).

- [ ] **Step 3: Implement**

In `kubed/selenium_flow/core/browser.py`, above `def is_partial(name: str) -> bool:` add:

```python
def _seconds(ms) -> int | None:
    """A node's ``sessionTimeout`` (milliseconds) in whole seconds, or None."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return int(ms) // 1000
```

Replace the head of `Grid.bidi` — from `@contextlib.contextmanager` down to and including the line `driver.caps = {"webSocketUrl": f"{socket}/session/{session_id}/se/bidi"}` — with:

```python
    def bidi_url(self, session_id: str) -> str:
        """The browser's BiDi socket, as the Grid proxies it.

        Derived from ``url``, never read from the browser's ``webSocketUrl``
        capability: the Grid fills that in with its own in-cluster name
        (measured: ``ws://selenium-grid-selenium-hub.flow:4444/...``).
        """
        parts = urlsplit(self.url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        root = urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        return f"{root}/session/{session_id}/se/bidi"

    @contextlib.contextmanager
    def bidi(self, session_id: str):
        """A reattached driver that speaks BiDi: ``.storage``, ``.network``,
        ``.browsing_context``, ``.script``.

        The socket address is derived, not discovered (`bidi_url`).
        """
        driver = self.reconnect(session_id)
        # The socket's timeout and polling are on the shared connection's
        # config, set once by `reconnect`.
        driver.caps = {"webSocketUrl": self.bidi_url(session_id)}
```

(the `try: yield driver / finally:` that follows is unchanged).

Above `def files(self, session_id: str) -> list[dict]:` add:

```python
    def listing(self) -> tuple[int, dict[str, int | None]]:
        """How many nodes are registered, and every running browser mapped to
        its node's idle timeout in seconds (None when the node does not say).

        One ``GET /status``, which touches no browser: a command sent to a
        session is activity the node counts, and would keep it alive.
        """
        nodes = self.status()["value"].get("nodes") or []
        running: dict[str, int | None] = {}
        for node in nodes:
            timeout = _seconds(node.get("sessionTimeout"))
            for slot in node.get("slots") or []:
                session_id = (slot.get("session") or {}).get("sessionId")
                if session_id:
                    running[session_id] = timeout
        return len(nodes), running

    def session_timeout(self, session_id: str) -> int | None:
        """Seconds this browser's node lets it sit idle before reaping it, or
        None when the Grid does not list it or does not say."""
        return self.listing()[1].get(session_id)
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `pytest tests/test_grid_listing.py tests/test_grid_connections.py tests/test_site_data_restore.py tests/test_spare_tab.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/core/browser.py tests/test_grid_listing.py
git commit -m "Grid: read running sessions and their idle timeout off /status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The monitor

**Files:**
- Create: `kubed/selenium_flow/monitor/monitor.py`
- Modify: `kubed/selenium_flow/monitor/__init__.py`, `tests/test_boundaries.py`
- Test: `tests/test_monitor.py`

**Interfaces:**
- Consumes: `LocalBus`, `SessionOpened`, `SessionEnded`, `Bus` (Task 1); `BidiSocket` (Task 2).
- Produces: `Watch` (dataclass: `session_id`, `workspace`, `browser`, `owed: set[str]`, `since`, `touched`, `timeout`, `misses`, `socket`, `dropped`, property `deadline`); `Monitor(bus, *, listing, socket_url=None, events_for=None, connect=None, on_bidi=None, clock=time.time, tick=LISTING_TICK)` with `opened(workspace, session_id, browser, grid_timeout, *, reopened=False)`, `ended(workspace, session_id, cause) -> bool`, `watch(session_id, workspace, reason, *, browser="", grid_timeout=None)`, `release(session_id, reason)`, `touch(session_id, at=None)`, `watching(session_id) -> frozenset[str]`, `socket_held(session_id) -> bool`, `async look()`, `async start()`, `async stop()`, `running`, `watches: dict[str, Watch]`; constants `LISTING_TICK`, `MISSES`, `DEFAULT_TIMEOUT`, `ENDED_MEMORY`.

- [ ] **Step 1: Write the failing tests**

`tests/test_monitor.py`:

```python
"""The monitor: watches by the Grid's listing, holds sockets to a deadline."""

import asyncio
import logging
from typing import ClassVar

import pytest

from kubed.selenium_flow.monitor.events import LocalBus
from kubed.selenium_flow.monitor.monitor import DEFAULT_TIMEOUT, MISSES, Monitor

pytestmark = pytest.mark.unit

GID = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"
OTHER = "0123456789abcdef0123456789abcdef"


class Clock:
    def __init__(self, now=1_791_500_000.0):
        self.now = now

    def __call__(self):
        return self.now


class Listing:
    """``GET /status`` as `Grid.listing` returns it, counting calls."""

    def __init__(self, running=None, nodes=1, fails=False):
        self.running = dict(running or {})
        self.nodes = nodes
        self.fails = fails
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.fails:
            raise ConnectionError("grid down")
        return self.nodes, dict(self.running)


class FakeSocket:
    """Stands in for `BidiSocket`: records what the monitor asked of it."""

    made: ClassVar[list] = []

    def __init__(self, url, *, on_event=None, on_close=None):
        self.url, self.on_event, self.on_close = url, on_event, on_close
        self.is_open = False
        self.dropped = False
        self.subscribed = []
        self.closed = 0
        self.fail_open = False
        FakeSocket.made.append(self)

    async def open(self):
        if self.fail_open:
            raise OSError("refused")
        self.is_open = True

    async def subscribe(self, events):
        self.subscribed.append(list(events))
        return "sub-1"

    async def close(self):
        self.is_open = False
        self.closed += 1

    def drop(self):
        self.is_open, self.dropped = False, True
        self.on_close()


@pytest.fixture
def parts():
    FakeSocket.made = []
    bus, heard = LocalBus(), []
    bus.subscribe(("session.opened", "session.ended"), heard.append, name="t")
    listing, clock = Listing({GID: 300}), Clock()
    monitor = Monitor(
        bus,
        listing=listing,
        socket_url=lambda gid: f"ws://grid/session/{gid}/se/bidi",
        events_for={"capture": ("log.entryAdded",)},
        connect=FakeSocket,
        clock=clock,
        tick=0.05,
    )
    return monitor, listing, clock, heard


async def test_opened_is_announced(parts):
    monitor, _listing, _clock, heard = parts
    monitor.opened("bot", GID, "chrome", 300)
    assert [(e.kind, e.workspace, e.grid_timeout, e.reopened) for e in heard] == [
        ("session.opened", "bot", 300, False)
    ]


async def test_one_listing_a_look_however_many_are_watched(parts):
    monitor, listing, _clock, _heard = parts
    listing.running[OTHER] = 600
    monitor.watch(GID, "a", "recording")
    monitor.watch(OTHER, "b", "recording")
    await monitor.look()
    assert listing.calls == 1
    assert monitor.watches[OTHER].timeout == 600


async def test_two_misses_are_gone_once(parts):
    monitor, listing, _clock, heard = parts
    monitor.watch(GID, "bot", "recording")
    listing.running.clear()
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 1
    await monitor.look()
    assert MISSES == 2
    assert [(e.kind, e.cause) for e in heard] == [("session.ended", "gone")]
    assert GID not in monitor.watches
    await monitor.look()
    assert len(heard) == 1


async def test_a_listing_again_resets_the_misses(parts):
    monitor, listing, _clock, heard = parts
    monitor.watch(GID, "bot", "recording")
    listing.running.clear()
    await monitor.look()
    listing.running[GID] = 300
    await monitor.look()
    listing.running.clear()
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 1


@pytest.mark.parametrize(
    "listing",
    [
        Listing({}, nodes=0),
        Listing(fails=True),
    ],
    ids=["no nodes", "failed"],
)
async def test_a_listing_that_cannot_say_says_nothing(parts, listing, caplog):
    monitor, _listing, _clock, heard = parts
    monitor.listing = listing
    monitor.watch(GID, "bot", "recording")
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            await monitor.look()
    assert heard == [] and GID in monitor.watches
    assert GID not in caplog.text


async def test_a_listing_older_than_the_watch_says_nothing(parts):
    monitor, listing, clock, heard = parts
    listing.running.clear()
    monitor.watch(GID, "bot", "recording")
    clock.now -= 10  # this look's listing was taken before the watch began
    await monitor.look()
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 0


async def test_ended_is_announced_once_and_drops_the_watch(parts):
    monitor, _listing, _clock, heard = parts
    monitor.watch(GID, "bot", "recording")
    assert monitor.ended("bot", GID, "ended") is True
    assert monitor.ended("bot", GID, "gone") is False
    assert [(e.kind, e.cause) for e in heard] == [("session.ended", "ended")]
    assert GID not in monitor.watches
    monitor.watch(GID, "bot", "recording")  # nothing left to owe
    assert GID not in monitor.watches


async def test_release_of_the_last_reason_drops_the_watch(parts):
    monitor, _listing, _clock, _heard = parts
    monitor.watch(GID, "bot", "recording")
    monitor.watch(GID, "bot", "capture")
    monitor.release(GID, "recording")
    assert monitor.watching(GID) == {"capture"}
    monitor.release(GID, "capture")
    assert GID not in monitor.watches and monitor.watching(GID) == frozenset()


async def test_the_task_runs_only_while_something_is_watched(parts):
    monitor, listing, _clock, heard = parts
    await monitor.start()
    assert not monitor.running
    monitor.watch(GID, "bot", "recording")
    assert monitor.running
    listing.running.clear()
    for _ in range(100):
        if not monitor.running:
            break
        await asyncio.sleep(0.01)
    assert not monitor.running and [e.cause for e in heard] == ["gone"]
    await monitor.stop()


async def test_a_watch_before_start_is_taken_up_by_start(parts):
    monitor, _listing, _clock, _heard = parts
    monitor.watch(GID, "bot", "recording")
    await monitor.start()
    assert monitor.running
    await monitor.stop()
    assert not monitor.running


async def test_recording_alone_holds_no_socket(parts):
    monitor, _listing, _clock, _heard = parts
    monitor.watch(GID, "bot", "recording")
    await monitor.look()
    assert FakeSocket.made == [] and not monitor.socket_held(GID)


async def test_a_reason_with_events_holds_a_socket_until_the_deadline(parts):
    monitor, _listing, clock, _heard = parts
    monitor.watch(GID, "bot", "capture", grid_timeout=300)
    await monitor.look()
    [sock] = FakeSocket.made
    assert sock.url == f"ws://grid/session/{GID}/se/bidi"
    assert sock.subscribed == [["log.entryAdded"]] and monitor.socket_held(GID)
    clock.now += 299
    await monitor.look()
    assert monitor.socket_held(GID)
    clock.now += 2  # past touched + timeout
    await monitor.look()
    assert not monitor.socket_held(GID) and sock.closed == 1
    assert GID in monitor.watches  # still watched by the listing


async def test_a_touch_brings_the_socket_back(parts):
    monitor, _listing, clock, _heard = parts
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    clock.now += DEFAULT_TIMEOUT + 1
    await monitor.look()
    assert not monitor.socket_held(GID)
    monitor.touch(GID)
    await monitor.look()
    assert monitor.socket_held(GID) and len(FakeSocket.made) == 2


async def test_a_dropped_socket_counts_as_a_miss(parts):
    monitor, listing, _clock, heard = parts
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    FakeSocket.made[0].drop()
    await monitor.look()  # still listed: the socket is opened again
    assert heard == [] and monitor.socket_held(GID) and len(FakeSocket.made) == 2
    FakeSocket.made[1].drop()
    listing.running.clear()
    await monitor.look()  # one miss after a drop is enough
    assert [e.cause for e in heard] == ["gone"]


async def test_a_socket_that_cannot_open_is_tried_again_and_logged_once(parts, caplog):
    monitor, _listing, _clock, _heard = parts
    original = FakeSocket.open

    async def refused(self):
        raise OSError("refused")

    FakeSocket.open = refused
    try:
        monitor.watch(GID, "bot", "capture")
        with caplog.at_level(logging.INFO):
            await monitor.look()
            await monitor.look()
    finally:
        FakeSocket.open = original
    assert len(FakeSocket.made) == 2 and not monitor.socket_held(GID)
    assert caplog.text.count("no BiDi socket for bot") == 1
    assert GID not in caplog.text
    await monitor.look()
    assert monitor.socket_held(GID)


async def test_ending_closes_the_socket(parts):
    monitor, _listing, _clock, _heard = parts
    await monitor.start()
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    [sock] = FakeSocket.made
    monitor.ended("bot", GID, "ended")
    await asyncio.sleep(0)
    assert sock.closed == 1
    await monitor.stop()


async def test_stop_closes_every_socket(parts):
    monitor, listing, _clock, _heard = parts
    listing.running[OTHER] = 300
    await monitor.start()
    monitor.watch(GID, "a", "capture")
    monitor.watch(OTHER, "b", "capture")
    await monitor.look()
    await monitor.stop()
    assert [s.closed for s in FakeSocket.made] == [1, 1]
    assert not monitor.running


async def test_watch_and_ended_from_worker_threads(parts):
    monitor, _listing, _clock, heard = parts
    await monitor.start()
    await asyncio.to_thread(monitor.watch, GID, "bot", "recording")
    await asyncio.sleep(0.01)
    assert GID in monitor.watches
    await asyncio.to_thread(monitor.ended, "bot", GID, "ended")
    await asyncio.sleep(0.01)
    assert GID not in monitor.watches and [e.cause for e in heard] == ["ended"]
    await monitor.stop()
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_monitor.py -q`
Expected: `ModuleNotFoundError: No module named 'kubed.selenium_flow.monitor.monitor'`.

- [ ] **Step 3: Write the monitor**

`kubed/selenium_flow/monitor/monitor.py`:

```python
"""The session monitor: which sessions this server opened, which ended and
why, and which it still owes something.

A *session* is the live browser a workspace holds, and it holds the Grid's
session, whose id is its ``session_id``. **Every session is announced; only
owed ones are watched.** `opened` and `ended` publish ``session.opened`` and
``session.ended`` for every session the workspaces open or end. A session is
*watched* only while something is owed on it — a recording (``"recording"``),
and from E3 capture — and only watched ones cost anything: one task, which runs
while a watch exists and ends with the last (AGENTS.md: a bounded wait for
something this server was told to expect).

**Whether a watched session still runs is read off the Grid's status, never
asked of the browser.** Each look takes one ``GET /status`` listing
(``listing``, in a worker thread), shared by every watch; a WebDriver command
would be activity and would stop the Grid ever reaping the browser. A session
is **gone** when two listings in a row, each showing at least one node and
taken after its watch began, do not list it — or one, after its socket
dropped. A listing that failed or shows no nodes (a hub restarting before its
nodes register again) says nothing.

**A socket is a channel** (`bidi.py`): held only for a reason that names BiDi
events (``events_for``), until the watch's deadline — its last activity plus
its node's ``sessionTimeout`` — and then closed, so a busy page's events cannot
keep the browser alive past the Grid's own timer (programme R3). The Grid
reaps; this server ends no session on a timer. A socket outlives its session
(measured), so the monitor closes it when the watch ends.

Calls come from worker threads (FastMCP's sync tools, Starlette's routes) and
are handed to the loop, as the recordings collector's are. Nothing here logs a
``session_id`` (AGENTS.md).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import anyio

from .bidi import BidiSocket
from .events import Bus, SessionEnded, SessionOpened

log = logging.getLogger(__name__)

# Seconds between listings: the Grid's own clean-up runs every 30 s.
LISTING_TICK = 30.0
# Listings in a row that must miss a session before it is gone.
MISSES = 2
# The Grid's default idle timeout, for a watch whose node did not say.
DEFAULT_TIMEOUT = 300
# Session ids remembered as ended, so each is announced once.
ENDED_MEMORY = 4096


@dataclass
class Watch:
    """One session the monitor owes something, and what it knows of it."""

    session_id: str
    workspace: str
    browser: str
    owed: set[str]
    since: float
    touched: float
    timeout: int | None = None
    misses: int = 0
    socket: BidiSocket | None = field(default=None, repr=False)
    dropped: bool = False

    @property
    def deadline(self) -> float:
        return self.touched + (self.timeout or DEFAULT_TIMEOUT)


class Monitor:
    """The watch registry, the listing and the held sockets.

    ``listing()`` returns ``(nodes, {session_id: timeout_seconds | None})``
    from one ``GET /status`` (`Grid.listing`). ``socket_url(session_id)`` is
    the browser's BiDi address (`Grid.bidi_url`). ``events_for`` maps a reason
    to the BiDi events it needs: a watch holds a socket only while it owes such
    a reason. ``connect`` builds a socket; a test seam. ``on_bidi(session_id,
    method, params)`` hears what a socket delivers.
    """

    def __init__(
        self,
        bus: Bus,
        *,
        listing: Callable[[], tuple[int, Mapping[str, int | None]]],
        socket_url: Callable[[str], str] | None = None,
        events_for: Mapping[str, tuple[str, ...]] | None = None,
        connect: Callable[..., BidiSocket] | None = None,
        on_bidi: Callable[[str, str, dict], None] | None = None,
        clock: Callable[[], float] = time.time,
        tick: float = LISTING_TICK,
    ):
        self.bus = bus
        self.listing = listing
        self.socket_url = socket_url
        self.events_for = dict(events_for or {})
        self.connect = connect or BidiSocket
        self.on_bidi = on_bidi
        self.clock = clock
        self.tick = tick
        self.watches: dict[str, Watch] = {}
        self._ended: OrderedDict[str, None] = OrderedDict()
        self._ended_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._nudge: asyncio.Event | None = None
        self._stopping = False
        self._closing: set[asyncio.Task] = set()
        self._listing_failed = False
        self._socket_failed: set[str] = set()

    # ---- from any thread ---------------------------------------------------

    def opened(
        self,
        workspace: str,
        session_id: str,
        browser: str,
        grid_timeout: int | None,
        *,
        reopened: bool = False,
    ) -> None:
        self.bus.publish(
            SessionOpened(
                workspace, session_id, browser, grid_timeout, reopened, self.clock()
            )
        )

    def ended(self, workspace: str, session_id: str, cause: str) -> bool:
        """Announce that ``session_id`` ended, once. False if it already was."""
        with self._ended_lock:
            if session_id in self._ended:
                return False
            self._ended[session_id] = None
            while len(self._ended) > ENDED_MEMORY:
                self._ended.popitem(last=False)
        self.bus.publish(SessionEnded(workspace, session_id, cause, self.clock()))
        self._post(lambda: self._drop(session_id))
        return True

    def watch(
        self,
        session_id: str,
        workspace: str,
        reason: str,
        *,
        browser: str = "",
        grid_timeout: int | None = None,
    ) -> None:
        """Owe ``reason`` on this session, watching it from now."""
        now = self.clock()

        def apply() -> None:
            with self._ended_lock:
                if session_id in self._ended:
                    return  # nothing left to owe
            w = self.watches.get(session_id)
            if w is None:
                w = self.watches[session_id] = Watch(
                    session_id,
                    workspace,
                    browser,
                    set(),
                    since=now,
                    touched=now,
                    timeout=grid_timeout,
                )
            w.owed.add(reason)
            if grid_timeout:
                w.timeout = grid_timeout
            self._ensure()

        self._post(apply)

    def release(self, session_id: str, reason: str) -> None:
        """Owe ``reason`` no more; a watch owing nothing is dropped."""

        def apply() -> None:
            w = self.watches.get(session_id)
            if w is None:
                return
            w.owed.discard(reason)
            if not w.owed:
                self._drop(session_id)
            else:
                self._wake()

        self._post(apply)

    def touch(self, session_id: str, at: float | None = None) -> None:
        """A call reached this session: its deadline moves on."""
        at = self.clock() if at is None else at

        def apply() -> None:
            w = self.watches.get(session_id)
            if w is not None and at > w.touched:
                w.touched = at
                self._wake()

        self._post(apply)

    # ---- on the loop -------------------------------------------------------

    def watching(self, session_id: str) -> frozenset[str]:
        w = self.watches.get(session_id)
        return frozenset(w.owed) if w else frozenset()

    def socket_held(self, session_id: str) -> bool:
        w = self.watches.get(session_id)
        return bool(w and w.socket and w.socket.is_open)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        self._nudge = asyncio.Event()
        if self.watches:
            self._ensure()

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for w in self.watches.values():
            if w.socket is not None:
                sock, w.socket = w.socket, None
                await sock.close()
        for closing in list(self._closing):
            with contextlib.suppress(Exception):
                await closing
        self._loop = None

    async def look(self) -> None:
        """One pass: the listing, what it says is gone, and the sockets."""
        now = self.clock()
        try:
            nodes, running = await anyio.to_thread.run_sync(self.listing)
        except Exception as exc:  # noqa: BLE001 - the Grid can blip; it says nothing
            if not self._listing_failed:
                self._listing_failed = True
                log.info(
                    "monitor: the Grid's status is unavailable (%s); every watched "
                    "session counts as running",
                    type(exc).__name__,
                )
            nodes, running = 0, {}
        else:
            if self._listing_failed:
                self._listing_failed = False
                log.info("monitor: the Grid's status answers again")
        for w in list(self.watches.values()):
            if self.watches.get(w.session_id) is not w:
                continue  # dropped while this look awaited
            if w.socket is not None and w.socket.dropped:
                w.socket, w.dropped = None, True
            if nodes and now >= w.since:
                if w.session_id in running:
                    w.misses = 0
                    w.dropped = False
                    if running[w.session_id]:
                        w.timeout = running[w.session_id]
                else:
                    w.misses += 1
                    if w.misses >= MISSES or w.dropped:
                        self.ended(w.workspace, w.session_id, "gone")  # drops it
                        continue
            await self._socket(w, now)

    # ---- inside -------------------------------------------------------------

    def _wanted(self, w: Watch) -> list[str]:
        return sorted({e for reason in w.owed for e in self.events_for.get(reason, ())})

    async def _socket(self, w: Watch, now: float) -> None:
        """Hold a socket while events are owed and the deadline has not passed."""
        wanted = self._wanted(w)
        sock = w.socket
        if not wanted or now >= w.deadline:
            if sock is not None:
                w.socket = None
                await sock.close()
            return
        if sock is None and self.socket_url is not None:
            try:
                sock = self.connect(
                    self.socket_url(w.session_id),
                    on_event=functools.partial(self._on_event, w.session_id),
                    on_close=self._wake,
                )
                await sock.open()
                await sock.subscribe(wanted)
            except Exception as exc:  # noqa: BLE001 - tried again next look
                if sock is not None:
                    await sock.close()
                if w.workspace not in self._socket_failed:
                    self._socket_failed.add(w.workspace)
                    log.info(
                        "monitor: no BiDi socket for %s (%s); trying again",
                        w.workspace,
                        type(exc).__name__,
                    )
                return
            self._socket_failed.discard(w.workspace)
            w.socket = sock

    def _on_event(self, session_id: str, method: str, params: dict) -> None:
        if self.on_bidi is not None:
            self.on_bidi(session_id, method, params)

    def _drop(self, session_id: str) -> None:
        """Forget the watch; its socket is closed in a task `stop` waits for."""
        w = self.watches.pop(session_id, None)
        if w is None or w.socket is None:
            return
        sock, w.socket = w.socket, None
        if self._loop is None:
            return  # not started: nothing was ever opened
        closing = self._loop.create_task(sock.close())
        self._closing.add(closing)
        closing.add_done_callback(self._closing.discard)

    def _post(self, fn) -> None:
        loop = self._loop
        if loop is None:
            fn()  # not started: applied here; `start` takes it from there
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn()
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(fn)

    def _wake(self) -> None:
        if self._nudge is not None:
            self._nudge.set()

    def _ensure(self) -> None:
        loop = self._loop
        if loop is None or self._stopping or self.running:
            return
        self._task = loop.create_task(self._run())

    def _wait(self) -> float:
        now = self.clock()
        deadlines = [w.deadline - now for w in self.watches.values() if w.socket]
        return max(0.05, min([self.tick, *deadlines]))

    async def _run(self) -> None:
        try:
            while self.watches:
                # Replaced before the look, so a wake during it is not lost.
                self._nudge = asyncio.Event()
                try:
                    await self.look()
                except Exception as exc:  # noqa: BLE001 - logged; the next look retries
                    log.warning("monitor: a look failed (%s)", type(exc).__name__)
                if not self.watches:
                    break
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._nudge.wait(), self._wait())
        finally:
            if self._task is asyncio.current_task():
                self._task = None
```

`kubed/selenium_flow/monitor/__init__.py`, whole:

```python
"""The session monitor: which sessions are open, ended and owed, said on a bus.

See `monitor.py` for the watch, `events.py` for the bus, `bidi.py` for the held
socket, and the session monitor spec (docs/superpowers/specs/
2026-10-09-session-monitor-design.md) for why each is the way it is.
"""

from .bidi import BidiError, BidiSocket
from .events import (
    EVENTS,
    Bus,
    CallFinished,
    Event,
    LocalBus,
    SessionEnded,
    SessionOpened,
    Subscription,
)
from .monitor import Monitor, Watch

__all__ = [
    "EVENTS",
    "BidiError",
    "BidiSocket",
    "Bus",
    "CallFinished",
    "Event",
    "LocalBus",
    "Monitor",
    "SessionEnded",
    "SessionOpened",
    "Subscription",
    "Watch",
]
```

In `tests/test_boundaries.py`, `NO_SELENIUM`, after `"monitor.bidi",` add `"monitor.monitor",`.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `pytest tests/test_monitor.py tests/test_monitor_events.py tests/test_monitor_bidi.py tests/test_boundaries.py -q`
Expected: all pass (19 in `test_monitor.py`).

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/monitor tests/test_monitor.py tests/test_boundaries.py
git commit -m "Monitor: watch owed sessions by the Grid's listing, sockets to a deadline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The workspace keeps the Grid's timeout and announces its sessions

**Files:**
- Modify: `kubed/selenium_flow/workspace/store.py`, `kubed/selenium_flow/workspace/workspaces.py`, `kubed/selenium_flow/mcp/resources.py`, `kubed/selenium_flow/spec/schemas.py`, `tests/test_recording_open.py`, `tests/golden/openapi.json`
- Test: `tests/test_workspace_monitor.py`

**Interfaces:**
- Consumes: `Grid.session_timeout` (Task 3); the monitor's `opened` / `ended` signatures (Task 4) — `Workspaces` takes anything with those two methods.
- Produces: `Workspace.grid_timeout: int | None = None`; `Workspaces(..., monitor=None)`; `Workspaces.remember(..., grid_timeout: int | None = None)`; `describe()` returns `grid_timeout`; private `Workspaces._timeout_of(session_id)`, `Workspaces._ended(name, session_id, cause)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_workspace_monitor.py`:

```python
"""What the workspaces tell the monitor, and the Grid's idle timeout they keep."""

import pytest

from kubed.selenium_flow.workspace.store import Workspace
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

pytestmark = pytest.mark.unit


class Announced:
    """Stands in for the monitor: what the manager announced, in order."""

    def __init__(self):
        self.calls = []

    def opened(self, workspace, session_id, browser, grid_timeout, *, reopened=False):
        self.calls.append(
            ("opened", workspace, session_id, browser, grid_timeout, reopened)
        )

    def ended(self, workspace, session_id, cause):
        self.calls.append(("ended", workspace, session_id, cause))
        return True


class Grid:
    def __init__(self):
        self.alive = set()
        self.timeouts = {}
        self.timeout_fails = False

    def is_alive(self, sid):
        return sid in self.alive

    def session_timeout(self, sid):
        if self.timeout_fails:
            raise ConnectionError("status unavailable")
        return self.timeouts.get(sid)

    def reconnect(self, sid):
        raise ConnectionError("not in this test")


class Actions:
    def __init__(self):
        self.grid = Grid()
        self.n = 0
        self.quit_fails = False

    def open_session(self, *, on_created=None, **kwargs):
        self.n += 1
        sid = f"grid{self.n:04d}"
        self.grid.alive.add(sid)
        self.grid.timeouts.setdefault(sid, 300)
        return {
            "session_id": sid,
            "browser": kwargs.get("browser") or "chrome",
            "url": "about:blank",
            "title": "",
            "width": 1280,
            "height": 900,
        }

    def end_browser(self, sid):
        if self.quit_fails:
            raise ConnectionError("grid unreachable")
        self.grid.alive.discard(sid)


def manager(monitor=None):
    return Workspaces(Actions(), monitor=monitor)


def caller(name="bot"):
    return Caller(name, "query")


def test_an_open_keeps_the_grids_timeout_and_announces_the_browser():
    announced = Announced()
    m = manager(announced)
    m.open_browser(caller(), browser="firefox")
    assert m.store.get("bot").grid_timeout == 300
    assert announced.calls == [("opened", "bot", "grid0001", "firefox", 300, False)]


def test_a_timeout_the_grid_will_not_give_is_none_and_the_open_goes_on():
    m = manager()
    m.actions.grid.timeout_fails = True
    result = m.open_browser(caller())
    assert result["workspace"] == "bot" and m.store.get("bot").grid_timeout is None


def test_the_timeout_survives_the_browser_and_is_described():
    m = manager()
    m.open_browser(caller())
    m.end_browser(caller())
    record = m.store.get("bot")
    assert not record.attached and record.grid_timeout == 300
    assert m.describe(caller())["grid_timeout"] == 300


def test_a_status_with_no_record_has_no_timeout():
    assert manager().describe(caller())["grid_timeout"] is None


def test_end_announces_ended_and_a_failed_quit_announces_nothing():
    announced = Announced()
    m = manager(announced)
    m.open_browser(caller())
    m.end_browser(caller())
    assert announced.calls[-1] == ("ended", "bot", "grid0001", "ended")
    m.open_browser(caller())
    m.actions.quit_fails = True
    m.end_browser(caller())
    assert [c for c in announced.calls if c[0] == "ended"] == [
        ("ended", "bot", "grid0001", "ended")
    ]


def test_a_reap_found_by_a_call_is_lost_and_the_reopen_is_announced():
    announced = Announced()
    m = manager(announced)
    m.open_browser(caller())
    m.actions.grid.alive.clear()  # the Grid reaped it
    m.actions.grid.timeouts["grid0002"] = 600  # a node with another timeout
    assert m.resolve("bot") == "grid0002"
    assert announced.calls[1:] == [
        ("ended", "bot", "grid0001", "lost"),
        ("opened", "bot", "grid0002", "chrome", 600, True),
    ]
    assert m.store.get("bot").grid_timeout == 600


def test_a_racing_opens_loser_is_announced_ended_and_never_opened():
    announced = Announced()
    m = manager(announced)
    m.store.set("bot", Workspace(session_id="winner"))
    kept = m.remember("bot", "loser", replacing=None)
    assert kept == "winner"
    assert announced.calls == [("ended", "bot", "loser", "ended")]


def test_without_a_monitor_nothing_is_announced_and_nothing_fails():
    m = manager()
    m.open_browser(caller())
    m.end_browser(caller())
    assert m.store.get("bot").grid_timeout == 300


def test_a_stored_timeout_is_read_back_only_when_it_is_a_positive_int():
    assert Workspace.from_json('{"grid_timeout": 300}').grid_timeout == 300
    for raw in ('"300"', "0", "-5", "true", "1.5", "null"):
        record = Workspace.from_json(f'{{"grid_timeout": {raw}}}')
        assert record.grid_timeout is None, raw
```

In `tests/test_recording_open.py`:

1. Above `class Grid:` add the same `Announced` class as in `tests/test_workspace_monitor.py` above, but recording three-tuples:

```python
class Announced:
    """Stands in for the monitor: what the manager announced, in order."""

    def __init__(self):
        self.calls = []

    def opened(self, workspace, session_id, browser, grid_timeout, *, reopened=False):
        self.calls.append(("opened", session_id, reopened))

    def ended(self, workspace, session_id, cause):
        self.calls.append(("ended", session_id, cause))
        return True
```

2. `def manager(recorder=None):` becomes:

```python
def manager(recorder=None, monitor=None):
    return Workspaces(Actions(), recordings=recorder, monitor=monitor)
```

3. In `Recorder`, delete the `ended` method and the `finished` list (`self.expected, self.discarded = [], []`): the manager no longer tells the collector about an end; the bus does (Task 6).

4. `test_an_explicit_open_after_end_does_not_inherit_record`: its first two lines become `rec, announced = Recorder(), Announced()` and `m = manager(rec, announced)`; its last assertion `assert rec.finished == ["grid0001"]` becomes `assert ("ended", "grid0001", "ended") in announced.calls`.

5. `test_a_quit_that_failed_does_not_start_the_collectors_clock`: the same two first lines; `assert rec.finished == []` becomes `assert [c for c in announced.calls if c[0] == "ended"] == []`.

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_workspace_monitor.py tests/test_recording_open.py -q`
Expected: FAIL — `TypeError: Workspaces.__init__() got an unexpected keyword argument 'monitor'`, and `Workspace` has no `grid_timeout`.

- [ ] **Step 3: Implement**

`kubed/selenium_flow/workspace/store.py`, class `Workspace` — after the `reopened: dict = field(default_factory=dict)` field add:

```python
    # Seconds the Grid lets this workspace's session sit idle before reaping
    # it, read from its node at the last open (session monitor spec, ruling
    # 10). Kept when the session ends; None when the Grid did not say.
    grid_timeout: int | None = None
```

In `Workspace.from_json`, in the `cls(...)` call, after `reopened=reopened if isinstance(reopened, dict) else {},` add:

```python
                grid_timeout=_timeout(data.get("grid_timeout")),
```

Above `def _visits(raw) -> list[dict]:` add:

```python
def _timeout(raw) -> int | None:
    """A stored ``grid_timeout``: a positive whole number of seconds, or None."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw
```

(`detached()` uses `replace(self, session_id="")`, so it keeps the field with no change.)

`kubed/selenium_flow/workspace/workspaces.py`, class `Workspaces`:

1. `__init__` gains the parameter `monitor=None` after `recordings=None,`, and after `self.recordings = recordings`:

```python
        # Who announces each session opened and ended (`monitor.Monitor`), or
        # None, which announces nothing: a unit test's manager.
        self.monitor = monitor
```

and the comment above `self.recordings` says *"Told on open; it owns no browser."* (it is no longer told on end).

2. `describe`: in the initial `status` dict, after `"window": None,` add `"grid_timeout": None,`; after `status["window"] = record.window` add:

```python
        # The idle timeout the Grid gave the last session: kept when it has
        # ended, like the window, because it is what the next one will get.
        status["grid_timeout"] = record.grid_timeout
```

3. Above `def act(` add:

```python
    def _timeout_of(self, session_id: str) -> int | None:
        """The idle timeout the Grid gives this session, or None when it will
        not say. Never fails the open that asks (session monitor spec, §6)."""
        try:
            return self.actions.grid.session_timeout(session_id)
        except Exception as exc:  # noqa: BLE001 - a fact to show, not to need
            log.info("the Grid's idle timeout is unknown: %s", type(exc).__name__)
            return None

    def _ended(self, name: str, session_id: str, cause: str) -> None:
        """Announce that ``session_id`` ended, when there is a monitor."""
        if self.monitor is not None:
            self.monitor.ended(name, session_id, cause)
```

4. `resolve`, after the `log.info("browser %s is gone from the grid, reopening …", …)` call and before `saved = self._restorable(record)`:

```python
        self._ended(name, record.session_id, "lost")
```

and replace its `kept = self.remember(name, opened["session_id"], …, report=opened.get("site_data"),)` call with:

```python
        timeout = self._timeout_of(opened["session_id"])
        kept = self.remember(
            name,
            opened["session_id"],
            opened.get("url", record.url or ""),
            replay,
            replacing=record.session_id,
            report=opened.get("site_data"),
            grid_timeout=timeout,
        )
        if kept == opened["session_id"] and self.monitor is not None:
            self.monitor.opened(
                name, kept, replay.get("browser") or DEFAULT_BROWSER, timeout,
                reopened=True,
            )
```

5. `open_browser`: replace its `kept = self.remember(name, opened["session_id"], opened.get("url", ""), resolved, replacing=ended, forget_site_data=forgotten is not None,)` with:

```python
        timeout = self._timeout_of(opened["session_id"])
        kept = self.remember(
            name, opened["session_id"], opened.get("url", ""), resolved,
            replacing=ended, forget_site_data=forgotten is not None,
            grid_timeout=timeout,
        )
```

and right after the `if kept != opened["session_id"]: … return self._held(name)` block, before `noted = None`:

```python
        if self.monitor is not None:
            self.monitor.opened(
                name, kept, resolved.get("browser") or DEFAULT_BROWSER, timeout
            )
```

6. `remember`: the signature gains `grid_timeout: int | None = None,` after `report: dict | None = None,`; in `bound`, the `replace(...)` call gains `grid_timeout=grid_timeout,` after its `reopened=…` argument; and the racing-open branch's `try: self.actions.end_browser(session_id) / except …: log.info(…)` gains:

```python
        else:
            self._ended(name, session_id, "ended")
```

7. `end_browser`: replace

```python
        if quit_ok and self.recordings is not None:
            self.recordings.ended(target)
```

with

```python
        if quit_ok:
            self._ended(name, target, "ended")
```

and its comment with: *"Only a confirmed quit (a 404 counts: Grid.quit treats it as done) is announced; after a failure the session may still run, and the monitor's listing finds it later if it is watched."*

`kubed/selenium_flow/mcp/resources.py`, `DESCRIPTION`: before its last line, `"Reading this never opens a browser: live is false when no session is open."`, insert (E1's words: the live browser is a *session*, spec ruling 18)

```python
    "grid_timeout is how many seconds the Grid lets a session in this "
    "workspace sit idle before it ends it; every call starts that clock "
    "again.\n\n"
```

`kubed/selenium_flow/spec/schemas.py`, `RESPONSES["current_workspace"]["properties"]`: after the `"window"` property (the dict whose description is `"Window size as WxH, when one is known."`) add

```python
            "grid_timeout": {
                "type": ["integer", "null"],
                "description": (
                    "Seconds the Grid lets a session in this workspace sit idle"
                    " before it ends it, read from its node at the last open."
                    " Null when the Grid did not say."
                ),
            },
```

- [ ] **Step 4: Run the tests, then regenerate the one golden this moves**

Run: `pytest tests/test_workspace_monitor.py tests/test_recording_open.py tests/test_workspaces.py tests/test_history.py tests/test_routes.py -q`
Expected: all pass.

Run: `GOLDEN_UPDATE=1 pytest tests/test_golden.py -q -p no:randomly && git diff --stat tests/golden`
Expected: only `tests/golden/openapi.json` changes, and `git diff tests/golden/openapi.json` shows exactly the `grid_timeout` property and the resource description's new sentence. (Measured on E1's tree at `8d33c6e`: those two hunks, 8 lines added and 1 changed, and nothing else.) Then `pytest tests/test_golden.py -q` passes.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/workspace kubed/selenium_flow/mcp/resources.py kubed/selenium_flow/spec/schemas.py tests/test_workspace_monitor.py tests/test_recording_open.py tests/golden/openapi.json
git commit -m "Workspaces: keep the Grid's idle timeout, announce sessions opened and ended

grid_timeout is read from /status at every open and reopen, kept on the
workspace and reported by workspace://current. A changed golden: the
status schema gains the field.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The collector hears ends from the monitor; the server wires it all

**Files:**
- Modify: `kubed/selenium_flow/recordings/collector.py`, `kubed/selenium_flow/server.py`, `tests/test_recording_collector.py`, `tests/test_admin_events.py`
- Test: the two test files above

**Interfaces:**
- Consumes: `LocalBus` (Task 1), `Monitor` (Task 4), `Grid.listing` / `Grid.bidi_url` (Task 3), `Workspaces(..., monitor=)` (Task 5).
- Produces: `Collector(store, inbox, *, wait, polling, poll_ms, on_filed=None, monitor=None, clock=time.time, tick=30.0, idle_after=60.0, settle=10.0)` — no `live`; `SeleniumMCP.bus: LocalBus`, `SeleniumMCP.monitor: Monitor`; subscriptions named `"recordings"` (`session.ended` → `collector.ended(event.session_id)`) and `"admin"` (`session.opened`, `session.ended` → `broadcast.poke()`).

- [ ] **Step 1: Rewrite the collector's tests for ends announced by the monitor, and add the new ones**

Save this script outside the repo (e.g. `/tmp/rewrite_collector_tests.py`) and run it from the repo root: `python3 /tmp/rewrite_collector_tests.py tests/test_recording_collector.py`. It replaces the fake listing with a `Watcher`, deletes the four listing tests (they are the monitor's now: Task 4), turns *"the Grid stopped listing it"* into `c.ended(GID)` and drops it where the test had already ended the session, and stops with an `AssertionError` if the file is not in the shape it expects. Dry-run against E1's file at `8d33c6e` (1 267 lines): it applies unchanged, leaving 233 lines changed (76 added, 157 removed), and the rewritten file passes once Step 3 and Step 4 are in. If the file changes again before this runs and an assertion fires, make that one edit by hand, as described in its comments.

```python
"""Rewrite tests/test_recording_collector.py for ends announced by the monitor.

Run once from the repo root:
    python3 /tmp/rewrite_collector_tests.py tests/test_recording_collector.py
It fails loudly (AssertionError) if the file is not in the shape it expects.
"""

import re
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/test_recording_collector.py")
s = path.read_text()


def cut_test(name):
    global s
    i = s.index(f"async def {name}(")
    j = s.index("\n\n\n", i)
    s = s[:i] + s[j + 3:]


# 1. The fixture: a Watcher in place of the fake listing.
fixture = re.compile(
    r"@pytest\.fixture\ndef parts\(tmp_path\):\n(?P<store>    store = .*\n)"
    r"(?P<inbox>    inbox = .*\n    inbox\.mkdir\(\)\n)    alive = \{GID\}\n"
    r".*?    return c, store, inbox, alive, filed, clock\n",
    re.S,
)
m = fixture.search(s)
assert m, "the parts fixture is not where it was"
s = s[: m.start()] + (
    "class Watcher:\n"
    '    """Stands in for the monitor: what the collector asked it to watch."""\n'
    "\n"
    "    def __init__(self):\n"
    "        self.watched, self.released = [], []\n"
    "\n"
    '    def watch(self, grid_id, workspace, reason, *, browser=""):\n'
    "        self.watched.append((grid_id, workspace, reason, browser))\n"
    "\n"
    "    def release(self, grid_id, reason):\n"
    "        self.released.append((grid_id, reason))\n"
    "\n"
    "\n"
    "@pytest.fixture\n"
    "def parts(tmp_path):\n"
    + m["store"] + m["inbox"] +
    "    monitor = Watcher()\n"
    "    filed = []\n"
    "    clock = Clock()\n"
    "    c = collector_module.Collector(\n"
    "        store, inbox, wait=600, polling=True, poll_ms=50,\n"
    "        on_filed=lambda: filed.append(1), monitor=monitor, clock=clock,\n"
    "        tick=0.1,\n"
    "        # Filed at once: the settle has tests of its own, below.\n"
    "        idle_after=60.0, settle=0,\n"
    "    )\n"
    "    return c, store, inbox, monitor, filed, clock\n"
) + s[m.end():]

# 2. Unpacking: the fourth element is the monitor now.
s = re.sub(
    r"^(    c, \w+, \w+, )_?alive(, \w+, \w+ = parts)$",
    lambda m: m.group(1) + "_monitor" + m.group(2), s, flags=re.M,
)

# 3. No more `live=` anywhere; no more "the Grid lists it again".
s = re.sub(r"live=(lambda: \{GID\}|set|live), ", "", s)
s = re.sub(r"^\s+alive\.add\([^)]*\)\n", "", s, flags=re.M)

# 4. The listing's tests are the monitor's now (tests/test_monitor.py).
for name in (
    "test_one_listing_a_tick_however_many_are_owed",
    "test_a_grid_that_cannot_list_means_every_browser_lives",
    "test_a_browser_seen_running_again_is_no_longer_ended",
    "test_a_cut_off_file_whose_browser_is_gone_marks_it_ended",
):
    cut_test(name)
i = s.index("class Listing:")
s = s[:i] + s[s.index("\n\n\n", i) + 3:]
assert s.count("def collector(store, inbox, live, clock, **kw):") == 1
s = s.replace(
    "def collector(store, inbox, live, clock, **kw):",
    "def collector(store, inbox, clock, **kw):",
)
s = re.sub(r"Listing\([^)]*\), ", "", s)

# 5. A reap noticed by the tick is an end the monitor announced.
old = re.compile(
    r"async def test_a_reap_is_noticed_by_the_tick\(parts\):\n"
    r"    c, store, _inbox, _monitor, _filed, clock = parts\n"
    r"    c\.expect\(\"bot\", GID, \"chrome\"\)\n"
    r"    alive\.clear\(\)\n"
    r"    await c\.sweep\(\)\n"
)
assert old.search(s), "test_a_reap_is_noticed_by_the_tick moved"
s = old.sub(
    "async def test_an_end_the_monitor_announces_is_written_to_the_note(parts):\n"
    "    c, store, _inbox, _monitor, _filed, clock = parts\n"
    '    c.expect("bot", GID, "chrome")\n'
    "    c.ended(GID)  # the monitor's session.ended: any cause\n",
    s,
)
assert s.count("        alive.clear()  # GID reaped: the sweep's path") == 1
s = s.replace(
    "        alive.clear()  # GID reaped: the sweep's path",
    "        c.ended(GID)  # the monitor's session.ended, on the loop",
)

# 6. A cleared listing after an explicit end said nothing new; alone, it was
#    the end.
s = re.sub(
    r"(    c\.ended\(GID\)\n(?:    c\.ended\(nofile\)\n)?    await asyncio\.sleep\(0\)\n)"
    r"    alive\.clear\(\)\n",
    r"\1", s,
)
s = s.replace("    alive.clear()\n", "    c.ended(GID)\n")

assert not re.search(r"\balive\b|Listing\(|live=", s), "a listing survived"
path.write_text(s)
print("rewritten", path)
```

Then, in `test_a_server_with_recording_on_needs_a_usable_inbox`, after its last assertion add:

```python
    assert server.collector.monitor is server.monitor
```

Append to `tests/test_recording_collector.py`, two blank lines after its last test (`test_a_first_expect_that_cannot_be_written_owes_nothing` at `8d33c6e`):

```python
async def test_an_expected_browser_is_watched_and_released_once_filed(parts):
    c, _store, inbox, monitor, filed, _clock = parts
    c.expect("bot", GID, "chrome")
    assert monitor.watched == [(GID, "bot", "recording", "chrome")]
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == [1] and monitor.released == [(GID, "recording")]


async def test_a_provisional_discard_is_watched_too(parts):
    """Its deadline needs an end as much as a kept one's does."""
    c, _store, _inbox, monitor, _filed, _clock = parts
    c.expect("bot", GID, "chrome", discard=True)
    assert monitor.watched == [(GID, "bot", "recording", "chrome")]


async def test_start_watches_what_the_notes_still_owe(tmp_path):
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    ended = "0123456789abcdef0123456789abcdef"
    done = "fedcba9876543210fedcba9876543210"
    store.write_note("a", GID, {"opened": 1_791_500_000_000, "browser": "chrome"})
    store.write_note("b", ended, {"opened": 1_791_500_000_000, "ended": 1})
    store.write_note("c", done, {"opened": 1_791_500_000_000, "filed": "x.mp4"})
    monitor = Watcher()
    c = collector(store, inbox, Clock(), monitor=monitor)
    await c.start()
    assert monitor.watched == [(GID, "a", "recording", "chrome")]
    await c.stop()


def test_an_end_on_the_bus_reaches_the_collector(tmp_path):
    from kubed.selenium_flow import config
    from kubed.selenium_flow.monitor.events import SessionEnded
    from kubed.selenium_flow.server import SeleniumMCP

    (tmp_path / "recordings").mkdir()
    server = SeleniumMCP(config.Settings(
        grid={"url": "http://grid.invalid:4444"},
        data={"dir": str(tmp_path)},
        recording={"enabled": True},
    ))
    server.collector.expect("bot", GID, "chrome")
    assert server.monitor.watching(GID) == {"recording"}
    server.bus.publish(SessionEnded("bot", GID, "gone", 1.0))
    assert server.collector.owed[GID].ended is not None
```

Append to `tests/test_admin_events.py`:

```python
def test_a_session_opening_or_ending_pokes_the_admin_broadcast(monkeypatch):
    """An open page shows a session appear or end now, not a poll later."""
    from kubed.selenium_flow import config
    from kubed.selenium_flow.http.admin.workspaces import Broadcast
    from kubed.selenium_flow.monitor.events import SessionEnded, SessionOpened
    from kubed.selenium_flow.server import SeleniumMCP

    pokes = []
    monkeypatch.setattr(Broadcast, "poke", lambda self: pokes.append(self))
    server = SeleniumMCP(config.Settings(grid={"url": "http://grid.invalid:4444"}))
    gid = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"
    server.bus.publish(SessionOpened("bot", gid, "chrome", 300, False, 1.0))
    server.bus.publish(SessionEnded("bot", gid, "ended", 2.0))
    assert len(pokes) == 2 and isinstance(pokes[0], Broadcast)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_recording_collector.py tests/test_admin_events.py -q`
Expected: FAIL — `TypeError: Collector.__init__() got an unexpected keyword argument 'monitor'` across the file, and `AttributeError: 'SeleniumMCP' object has no attribute 'bus'`.

- [ ] **Step 3: Implement the collector**

In `kubed/selenium_flow/recordings/collector.py`:

1. Module docstring: replace the paragraph that begins *"**Whether a browser is gone is read off the Grid's status — never asked of the browser.**"* (through *"a blip starts no deadline that sticks."*) with:

```
**Whether a session is gone is the monitor's to say** (`monitor/`). Every
owed session is watched for ``"recording"`` — when it is expected, and at
``start`` for each note still owed and not ended — and released once its note
is gone from disk. The monitor reads the Grid's status, never the browser, and
announces an end as ``session.ended``, which the server hands to ``ended``.
```

2. `Collector.__init__`: delete the `live,` parameter; add `monitor=None,` after `on_filed=None,`; replace

```python
        # () -> the Grid session ids currently running. Never a per-session
        # call: see the module docstring.
        self.live = live
```

with

```python
        # Watches each owed session and says when it ends (`monitor.Monitor`:
        # `watch`, `release`); None watches nothing, for a test.
        self.monitor = monitor
```

and delete the two fields `self._listing` (with its comment) and `self._listing_failed`.

3. `expect`: replace its last line `self._post(lambda: self._add(owed))` with

```python
        self._watch(owed)
        self._post(lambda: self._add(owed))
```

4. Replace `ended` with `ended` plus `_watch`:

```python
    def ended(self, grid_id: str) -> None:
        """A session ended (the monitor's ``session.ended``, any cause).
        Ignored unless it is owed."""
        self._post(lambda: self._mark_ended(grid_id))

    def _watch(self, owed: Owed) -> None:
        """Ask the monitor to say when this session ends."""
        if self.monitor is not None:
            self.monitor.watch(
                owed.grid_id, owed.workspace, "recording", browser=owed.browser
            )
```

5. `_load`, inside the loop: replace

```python
                if self.owed.setdefault(grid_id, owed) is owed:
                    loaded += 1
```

with

```python
                if self.owed.setdefault(grid_id, owed) is owed:
                    loaded += 1
                    if not owed.done and owed.ended is None:
                        self._watch(owed)
```

6. `_forget`, at its end: replace

```python
            if self.owed.get(owed.grid_id) is owed:  # else expected again meanwhile
                del self.owed[owed.grid_id]
```

with

```python
            if self.owed.get(owed.grid_id) is owed:  # else expected again meanwhile
                del self.owed[owed.grid_id]
                if self.monitor is not None:
                    self.monitor.release(owed.grid_id, "recording")
```

7. `sweep`: delete

```python
            if owed.ended is not None and await self._back(owed, now):
                owed.ended = None
                await self._save(owed)
```

and replace

```python
            idle = quiet >= self.idle_after
            gone = (owed.ended is None or idle) and await self._gone(owed, now)
            if gone and owed.ended is None:
                owed.ended = int(now * 1000)
                await self._save(owed)
            if match is not None:
                if idle and gone:
```

with

```python
            idle = quiet >= self.idle_after
            if match is not None:
                if idle and owed.ended is not None:
```

8. Delete the methods `_listed`, `_gone`, `_back` and `_list` whole.

- [ ] **Step 4: Wire the server**

In `kubed/selenium_flow/server.py`:

1. Imports, after the `from .mcp import (…)` block:

```python
from .monitor.events import LocalBus
from .monitor.monitor import Monitor
```

2. Just before the recordings comment *"Recordings (recordings spec): the Grid films …"*:

```python
        # Which sessions are open, ended and owed (session monitor spec): one
        # bus, one monitor, in this process. The monitor watches by the Grid's
        # status listing and holds a BiDi socket only for a reason that needs
        # its events; E2 has none. Built before the collector and the
        # workspaces, which tell it what they open, end and owe.
        self.bus = LocalBus()
        self.monitor = Monitor(
            self.bus, listing=self.grid.listing, socket_url=self.grid.bidi_url
        )
```

3. In `recording_collector.Collector(…)`, replace the `live=lambda: {…},` argument and its comment with:

```python
                # Says when each owed session ends, as `session.ended` below.
                monitor=self.monitor,
```

and after the `log.info("recordings: on, inbox %s, %s", …)` call, inside the same `if settings.recording.enabled:` block:

```python
            collector_ended = self.collector.ended
            self.bus.subscribe(
                "session.ended",
                lambda event: collector_ended(event.session_id),
                name="recordings",
            )
```

4. `Workspaces(…)` gains `monitor=self.monitor,` after `recordings=self.collector,`.

5. Replace the lifespan block (from the comment *"The collector's task lives on the server's event loop …"* through the end of `async def lifespan`) with:

```python
        # The bus, the monitor and the collector live on the server's event
        # loop, so they start and stop with it: the bus first, so nothing is
        # announced to a loop that is not there, and the monitor before the
        # collector, which asks it to watch what its notes still owe. The two
        # tasks run only while something is owed.
        collector, monitor, bus = self.collector, self.monitor, self.bus

        @asynccontextmanager
        async def lifespan(_server):
            bus.start()
            await monitor.start()
            if collector is not None:
                await collector.start()
            try:
                yield {}
            finally:
                if collector is not None:
                    await collector.stop()
                await monitor.stop()
                await bus.stop()
```

6. At the end of `__init__`, after `self.collector.on_filed = broadcast.poke`'s `if` block:

```python
        # So does a session opening or ending; the page's poll stays for
        # everything else.
        self.bus.subscribe(
            ("session.opened", "session.ended"),
            lambda _event: broadcast.poke(),
            name="admin",
        )
```

- [ ] **Step 5: Run the tests to make sure they pass**

Run: `pytest tests/test_recording_collector.py tests/test_admin_events.py tests/test_recording_open.py tests/test_recording_surfaces.py tests/test_shutdown.py tests/test_main.py -q`
Expected: all pass. Then the whole suite, `pytest -q -n 4`, and `ruff check .`: all pass (no golden moves in this task).

- [ ] **Step 6: Commit**

```bash
git add kubed/selenium_flow/recordings/collector.py kubed/selenium_flow/server.py tests/test_recording_collector.py tests/test_admin_events.py
git commit -m "Recordings: the monitor says when a session ends; the server wires the bus

The collector's /status listing moves to the monitor; the note queue
stays. The admin page is poked when a session opens or ends.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The idle timeout on the admin card

**Files:**
- Modify: `kubed/selenium_flow/http/admin/workspaces.py`, `tests/golden/admin-workspaces.json`, `ui/src/lib/types.ts`, `ui/src/lib/format.ts`, `ui/src/lib/WorkspaceSummary.svelte`, `ui/src/lib/format.test.ts`, `ui/src/lib/WorkspaceSummary.test.ts`
- Test: `tests/test_golden.py` (regenerated), the two UI test files

**Interfaces:**
- Consumes: `Workspace.grid_timeout` (Task 5).
- Produces: the admin row's `grid_timeout: int | null`; `WorkspaceRow.grid_timeout?: number | null`; `idle(seconds?: number | null): string` in `lib/format.ts`.

Boards: Penpot, Components page, `summary / live` and `summary / idle`, row `IDLE TIMEOUT` (ruling 11: shown in the workspace group as `5 min`, or `N s` when not whole minutes). If the board's text or format differs from this, the board wins; change the label string and `idle()` to match and say so in the PR.

- [ ] **Step 1: Write the failing UI tests**

`ui/src/lib/format.test.ts`: add `idle` to the import from `./format`, and inside `describe('format', …)`:

```ts
  test('idle timeout: whole minutes, else seconds, nothing when unknown (spec ruling 11)', () => {
    expect(idle(300)).toBe('5 min')
    expect(idle(60)).toBe('1 min')
    expect(idle(90)).toBe('90 s')
    expect(idle(null)).toBe('')
    expect(idle(undefined)).toBe('')
    expect(idle(0)).toBe('')
  })
```

`ui/src/lib/WorkspaceSummary.test.ts`, append:

```ts
test('the idle timeout is a workspace fact, shown live and idle, hidden when unknown (spec §6)', () => {
  const r = render(WorkspaceSummary, { data: { key: 'k', live: false, grid_timeout: 300 } })
  expect(screen.getByText('idle timeout')).toBeInTheDocument()
  expect(screen.getByText('5 min')).toBeInTheDocument()
  r.unmount()
  render(WorkspaceSummary, { data: { key: 'k', live: true, grid_timeout: null } })
  expect(screen.queryByText('idle timeout')).toBeNull()
})
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `npm --prefix ui test -- src/lib/format.test.ts src/lib/WorkspaceSummary.test.ts`
Expected: FAIL — `idle` is not exported; `idle timeout` not found.

- [ ] **Step 3: Implement**

`ui/src/lib/types.ts`, interface `WorkspaceRow`, after `window?: string | null`:

```ts
  grid_timeout?: number | null
```

`ui/src/lib/format.ts`, after `ago`:

```ts
/* How long the Grid lets a session sit idle: whole minutes read as minutes,
   anything else in seconds; nothing when the Grid did not say. */
export function idle(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return ''
  return seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`
}
```

`ui/src/lib/WorkspaceSummary.svelte`: import `idle` beside `browserMark, safeHref`, and in the `workspace` group, after the `['started', …]` entry:

```ts
      ['idle timeout', idle(s.grid_timeout)],
```

(an empty string is falsy, so the existing `.filter(([, v]) => v)` hides it when unknown).

`kubed/selenium_flow/http/admin/workspaces.py`, in `workspaces_payload`'s row dict, after `"window": record.window,`:

```python
                    # How long the Grid lets its session sit idle: kept from
                    # the last open, so an idle card shows it too.
                    "grid_timeout": record.grid_timeout,
```

- [ ] **Step 4: Run the tests and regenerate the admin golden**

Run: `npm --prefix ui test && npm --prefix ui run -s check && npm --prefix ui run -s lint`
Expected: all pass.

Run: `GOLDEN_UPDATE=1 pytest tests/test_golden.py -q -p no:randomly && git diff --stat tests/golden`
Expected: only `tests/golden/admin-workspaces.json` changes, by one `"grid_timeout": null` line per row. Then `pytest -q -n 4` and `ruff check .` pass.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/http/admin/workspaces.py tests/golden/admin-workspaces.json ui/src/lib
git commit -m "Admin: the summary card shows the Grid's idle timeout

A changed golden: each admin row gains grid_timeout.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The doctrine and the docs

**Files:**
- Modify: `AGENTS.md`, `README.md`, `skills/selenium-flow/references/WORKSPACES.md`, `CHANGELOG.md`, `wiki/` (regenerated)

- [ ] **Step 1: AGENTS.md**

Apply the spec's §11 verbatim: the BiDi sentence in *"Plain W3C WebDriver only"*; the last paragraph of *"Refresh, not cleanup"* and the new *"The Grid reaps; we let it."* paragraph; the Recordings bullet *"Nothing that watches sends the Grid a session command."*; the new section *"The session monitor and the bus"* after *"Refresh, not cleanup"*; the Gotchas sentence (appended to the bullet *"The Grid's session timeout is not this repo's setting."*); the lifetime table row (E1's section *"Two lifetimes: the session and the workspace"*, row *"How long a session's browser lives idle"*: only its middle cell changes); the *Scaling* sentence, as its own paragraph after *"The per-session lock (`workspace/locks.py`) is process memory too…"*. Copy each block from the spec rather than retyping it. Every anchor was found once in E1's AGENTS.md at `8d33c6e`.

- [ ] **Step 2: README, skill, changelog**

- `README.md`: the one line naming `SE_NODE_SESSION_TIMEOUT` (*"Browser lifetime is the Grid's (`SE_NODE_SESSION_TIMEOUT`, 300s here); … Nothing runs a cleanup loop."*) gains, at its end: *" The server reads that timeout from the Grid's `/status` at every open and shows it as `grid_timeout` in `workspace://current` and on the admin card."* Measured: 24 752 bytes after, inside `tests/test_readme.py`'s 25 000.
- `skills/selenium-flow/references/WORKSPACES.md`: the paragraph *"`live` says whether a session is open. `show(workspace://current)` draws it for the person."* gains: *"`grid_timeout` is how long a session may sit idle before the Grid ends it; any call starts the clock again."*
- `CHANGELOG.md`, `[Unreleased]`: *"`workspace://current` and the admin summary show how long the Grid lets a session sit idle (`grid_timeout`)."*

- [ ] **Step 3: Wiki**

Run: `git submodule update --init wiki && python scripts/generate_wiki.py && pytest tests/test_wiki.py -q`
Expected: only the status page (`wiki/current_workspace.md`) changes, by two lines: the description's `grid_timeout` sentence and a `grid_timeout` row in the response table (measured against the wiki at E1's pointer, `9c75f98`); `test_wiki.py` passes. Commit the submodule's change in the wiki repo the way earlier wiki changes were (see CONTRIBUTING.md), and the pointer here.

- [ ] **Step 4: The whole suite**

Run: `pytest -q -n 4 && ruff check . && npm --prefix ui test && npm --prefix ui run -s check && npm --prefix ui run -s lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md README.md skills/selenium-flow/references/WORKSPACES.md CHANGELOG.md wiki
git commit -m "Docs: the session monitor's doctrine; grid_timeout in the skill, README, wiki

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review against the spec

| Spec | Task |
|---|---|
| §1 the package, its layering, the boundary rule | 1, 2, 4 |
| §2 events, fields, causes, the bus | 1 |
| §3 the watch registry and its methods | 4 |
| §4 the socket holder | 2 |
| §5 liveness out of the collector; what it gains | 4, 6 |
| §6 the timeout read, stored, shown | 3, 5, 7 |
| §7 publishers and consumers | 5, 6 |
| §8 lifecycle | 6 |
| §9 failures | 1, 2, 4, 5 — every row has a test but "a look raises unexpectedly", which is a last-resort guard in `_run` with nothing left inside `look` that can raise |
| §10 constants | 1, 2, 4 |
| §11 AGENTS.md | 8 |
| §12 documentation | 2 (pyproject), 8 |
| §13 testing | every task |

## Reconciled with E1

On 2026-10-09 every task above was applied in order to a scratch copy of E1's tree (`issue-60-workspaces` at `8d33c6e`, wiki at `9c75f98`), outside the repo, with the code and tests exactly as written here:

| After | Result |
|---|---|
| Task 1 | `test_monitor_events.py` 10 passed; boundaries and packaging pass |
| Task 2 | `test_monitor_bidi.py` 8 passed, against a real `websockets` 17.2 server |
| Task 3 | `test_grid_listing.py` and the BiDi users (`test_grid_connections`, `test_site_data_restore`, `test_spare_tab`) 48 passed |
| Task 4 | `test_monitor.py` 19 passed; the three monitor files five times in a row under `pytest-randomly`, no flake |
| Task 5 | `test_workspace_monitor.py` 9 passed; `test_recording_open`, `test_workspaces`, `test_history`, `test_routes` pass; golden: `openapi.json` only |
| Task 6 | the rewrite script applies unchanged; the six files of Step 5 149 passed; whole suite 2 404 passed, 35 skipped; no golden moves |
| Task 7 | UI 274 tests, `check` and `lint` pass; golden: `admin-workspaces.json` only, one `"grid_timeout": null` per row (4) |
| Task 8 | wiki regenerated (`current_workspace.md`, +2 lines); whole suite 2 428 passed, 26 skipped (the wiki tests run once it is checked out); `ruff check .` clean |

The tools goldens (`tools-on.json`, `tools-off.json`), `admin-routes.json`, `errors.json` and `flow-reports.json` never moved.

What changed in this plan to get there: the boundary assert goes after `test_the_walk_finds_the_layers`'s docstring, not above it; `tests/test_workspace_monitor.py` imports `workspace.store` before `workspace.workspaces` (ruff's isort: the pre-rename order was sorted, the renamed one is not); the new sentence and schema description say *a session in this workspace* (spec ruling 18); Task 8 names E1's lifetime table and row, the README line and the skill paragraph exactly. `test_admin_events.py::test_nothing_polls_once_the_last_page_has_gone`, a timing test (0.2 s), failed once in an `-n 8` run of the whole suite and passed in the three runs after and alone; no session opens in it, so the bus never pokes it. If it fails in CI, rerun before suspecting the monitor.
