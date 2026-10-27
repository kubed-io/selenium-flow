"""The hosts ask before they drive: while a person holds Control, a call that
would change the browser is refused, after its turn and before it is begun
(spec 2026-10-27-live-control, rulings 3, 11, 14, 32 and 33).

`live.control.Controls` decides what is refused (`test_live_control.py`); here
a stand-in with the same rule says it, so what is pinned is that every host —
`Workspaces.act`, `paste`, the bound write, a flow step, `open_session` and
`end_browser` — asks, and asks at the right moment.
"""

from __future__ import annotations

import threading
import time

import pytest

from kubed.selenium_flow import faults
from kubed.selenium_flow.core.capabilities import UNDER_CONTROL
from kubed.selenium_flow.flows import api as flowapi
from kubed.selenium_flow.flows import store as flow_store
from kubed.selenium_flow.secrets import perform_write
from kubed.selenium_flow.workspace import locks
from kubed.selenium_flow.workspace.store import Workspace
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .test_capture_hint import Anywhere, FakeMonitor
from .test_workspace_monitor import Actions as MonitorActions

pytestmark = pytest.mark.unit

REFUSED = (
    "a person has control of this browser since 09:41 UTC. Call await_control "
    "(POST /browser/await-control), which returns when they hand back."
)
CALLER = Caller("ws", "query")


class Gate:
    """`Controls.refusal`'s rule, over Grid ids a test shuts: reads and the
    controller's own paste answer, everything else is refused."""

    def __init__(self):
        self.shut: set[str] = set()
        self.asked: list[tuple[str, str]] = []

    def refusal(self, session_id, tool):
        self.asked.append((session_id, tool))
        if session_id not in self.shut or tool == "paste":
            return None
        if UNDER_CONTROL.get(tool) == "answers":
            return None
        return faults.Controlled(REFUSED)


class Acting(MonitorActions):
    """What the hosts drive, recording every action that reached the browser."""

    def __init__(self):
        super().__init__()
        self.reached: list[str] = []

    def page(self, session_id):
        self.reached.append("page")
        return {"url": "https://a.test/", "title": "A"}

    def write(self, session_id, text, **kwargs):
        self.reached.append("write")
        return {"url": "https://a.test/", "value": None}

    def interact(self, session_id, **kwargs):
        self.reached.append("interact")
        return {"url": "https://a.test/", "title": "A"}

    def paste(self, session_id, text):
        self.reached.append("paste")
        return {"typed": len(text)}


def controlled(gate=None):
    """A workspace ``ws`` holding a browser, its monitor, its gate (open)."""
    gate = gate if gate is not None else Gate()
    monitor = FakeMonitor()
    workspaces = Workspaces(Acting(), monitor=monitor, control=gate)
    workspaces.open_browser(CALLER)
    monitor.touched.clear()
    monitor.published.clear()
    return workspaces, monitor, gate


def grid_id(workspaces):
    return workspaces.store.get("ws").session_id


def test_an_acting_call_is_refused_and_never_begun():
    """Ruling 32: no in-flight count, no `call.finished`, and the action never ran."""
    workspaces, monitor, gate = controlled()
    gate.shut.add(grid_id(workspaces))
    ran = []
    with pytest.raises(faults.Controlled, match="has control of this browser"):
        workspaces.act(
            CALLER, lambda s: ran.append(s) or {"url": "u"}, tool="navigate",
            surface="http",
        )
    assert ran == []
    assert monitor.touched == [] and monitor.published == []
    assert gate.asked == [(grid_id(workspaces), "navigate")]


def test_a_read_answers_while_controlled():
    workspaces, monitor, gate = controlled()
    gate.shut.add(grid_id(workspaces))
    result = workspaces.act(CALLER, lambda s: {"url": "u", "text": "hi"}, tool="extract")
    assert result["text"] == "hi"
    [event] = monitor.published
    assert (event.tool, event.outcome) == ("extract", "ok")


def test_a_call_queued_before_the_request_is_refused_when_its_turn_comes():
    """Ruling 14: the check runs once a call has its turn, so a call that was
    already waiting on the lock when the person asked is refused, not let in."""
    workspaces, monitor, gate = controlled()
    sid = "queued before control"
    workspaces.store.set("ws", Workspace(session_id=sid))
    workspaces.actions.grid.alive.add(sid)
    held, release = threading.Event(), threading.Event()

    def holder():
        with locks.driving(sid):
            held.set()
            release.wait(5)

    a = threading.Thread(target=holder)
    a.start()
    assert held.wait(5), "the holder never took its turn"
    ran, outcome = [], []

    def queued():
        try:
            outcome.append(
                workspaces.act(
                    CALLER, lambda s: ran.append(s) or {"url": "u"}, tool="interact"
                )
            )
        except Exception as exc:  # noqa: BLE001 - asserted on below
            outcome.append(exc)

    b = threading.Thread(target=queued)
    b.start()
    # Until B holds a ticket behind A's (internals: the one deterministic sign
    # that it is waiting on the lock), and only then does the gate shut.
    turns = locks._holds[sid].lock
    deadline = time.monotonic() + 5
    while turns._next - turns._serving < 2:
        assert time.monotonic() < deadline, "the call never queued"
        time.sleep(0.001)
    assert gate.asked == [], "asked before its turn"
    gate.shut.add(sid)

    release.set()
    a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive()
    (error,) = outcome
    assert isinstance(error, faults.Controlled), error
    assert ran == []
    assert monitor.touched == [] and monitor.published == []


def test_open_session_and_end_browser_are_refused_while_controlled():
    """They take no lock, so they are checked directly; neither touches the
    browser a person holds."""
    workspaces, _, gate = controlled()
    sid = grid_id(workspaces)
    gate.shut.add(sid)
    with pytest.raises(faults.Controlled):
        workspaces.open_browser(CALLER)
    with pytest.raises(faults.Controlled):
        workspaces.end_browser(CALLER)
    assert workspaces.actions.n == 1, "a second browser was opened"
    assert sid in workspaces.actions.grid.alive, "the held browser was quit"
    assert workspaces.store.get("ws").session_id == sid
    assert [tool for _, tool in gate.asked] == ["open_session", "end_browser"]


def test_with_no_session_open_there_is_nothing_to_refuse():
    gate = Gate()
    workspaces = Workspaces(Acting(), monitor=FakeMonitor(), control=gate)
    workspaces.open_browser(CALLER)
    assert gate.asked == []


def test_the_admins_end_is_never_refused():
    """Ruling 11: the admin's End still works, and releases control by ending
    the session."""
    workspaces, _, gate = controlled()
    sid = grid_id(workspaces)
    gate.shut.add(sid)
    assert workspaces.end_browser(Caller("ws", "admin"), admin=True) == sid
    assert sid not in workspaces.actions.grid.alive
    assert not workspaces.store.get("ws").attached
    assert gate.asked == []


def test_only_the_admin_route_says_admin():
    """The exemption is the route's flag, never anything a caller carries."""
    workspaces, _, gate = controlled()
    gate.shut.add(grid_id(workspaces))
    with pytest.raises(faults.Controlled):
        workspaces.end_browser(Caller("ws", "admin"))
    assert workspaces.store.get("ws").attached


def test_a_bound_write_is_refused_before_the_page_is_read():
    workspaces, monitor, gate = controlled()
    gate.shut.add(grid_id(workspaces))
    with pytest.raises(faults.Controlled):
        perform_write(
            Anywhere(), workspaces.actions, workspaces, "ws",
            {"secret": {"name": "demo", "key": "password"}, "selector": {"css": "#p"}},
        )
    assert workspaces.actions.reached == []
    assert monitor.touched == [] and monitor.published == []
    assert [tool for _, tool in gate.asked] == ["write"]


def test_a_flow_stops_at_a_refused_step_even_with_on_error_continue(tmp_path):
    """Ruling 3: a flow stops at its next step with the refusal, whatever that
    step's onError says, and the step after is never dispatched."""
    workspaces, monitor, gate = controlled()
    actions = workspaces.actions
    sid = grid_id(workspaces)
    first = actions.interact

    def interact_then_shut(session_id, **kwargs):
        gate.shut.add(sid)  # the person asks while step one runs
        return first(session_id, **kwargs)

    actions.interact = interact_then_shut
    store = flow_store.LocalFlowStore(tmp_path)
    click = {"action": "click", "selector": {"css": "button"}}
    store.save("ws", "trip", {"steps": [
        {"tool": "interact", "args": click},
        {"tool": "interact", "args": click, "onError": "continue"},
        {"tool": "interact", "args": click},
    ]})
    report = flowapi.run_for(store, actions, workspaces, "ws", "trip")

    assert report["status"] == "failed"
    assert len(report["steps"]) == 2
    refused = report["steps"][1]
    assert refused["ok"] is False and refused["error"] == REFUSED
    # The failure report reads the page; nothing after step one drove it.
    assert actions.reached.count("interact") == 1
    # Step one was said; the refused step was never begun.
    assert [e.tool for e in monitor.published] == ["interact"]
    assert [kind for kind, _ in monitor.touched] == ["calling", "called"]


def test_paste_is_never_refused():
    """The person in control types through it (ruling 3)."""
    workspaces, _, gate = controlled()
    gate.shut.add(grid_id(workspaces))
    assert workspaces.paste("ws", "hello") == {"typed": 5}
    assert workspaces.actions.reached == ["paste"]
    assert [tool for _, tool in gate.asked] == ["paste"]
