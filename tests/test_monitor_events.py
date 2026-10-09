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
