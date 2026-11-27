"""Every call is a span from its start to its end (`Calls.begin` to `Calls.end`,
or `Calls.timed` for a call that takes no turn), naming its workspace and never
its sub-session or its Grid id."""

import pytest
from opentelemetry.trace import StatusCode

from kubed.selenium_flow import faults
from kubed.selenium_flow.workspace.calls import Calls, outcome

from .fakes import FakeMonitor, recorded

pytestmark = pytest.mark.unit


def refuse(*args, **kwargs):
    raise RuntimeError("no tracer")


def headed_by_default(key):
    """Where a key stands, as `Workspaces.is_head` answers it: here `ws`'s list
    is headed by `default`, and a workspace's own key counts as its head."""
    return key.partition("/")[2] in ("", "default")


def calls_with_spans(**kwargs):
    rec = recorded()
    calls = Calls(telemetry=rec.telemetry, **kwargs)
    calls.is_head = headed_by_default
    return calls, rec


def test_a_call_has_a_span_named_by_its_tool():
    calls, rec = calls_with_spans()
    begun = calls.begin("ws", "grid-abc123", browser="chrome", tool="navigate")
    calls.end(begun, tool="navigate", surface="http", browser="chrome")
    [span] = rec.spans.get_finished_spans()
    assert span.name == "call navigate"
    assert span.attributes["selenium_flow.workspace"] == "ws"
    assert span.attributes["selenium_flow.sub_session"] is False
    assert (span.attributes["selenium_flow.surface"], span.attributes["selenium_flow.outcome"]) == ("http", "ok")
    assert "grid-abc123" not in repr((span.name, dict(span.attributes)))


def test_a_sub_session_span_names_its_workspace_never_the_session():
    calls, rec = calls_with_spans()
    begun = calls.begin("ws/alice@example.com", "grid-1", browser="firefox", tool="extract")
    calls.end(begun, tool="extract", surface="mcp", browser="firefox")
    [span] = rec.spans.get_finished_spans()
    assert span.attributes["selenium_flow.workspace"] == "ws"
    assert span.attributes["selenium_flow.sub_session"] is True
    assert "alice" not in repr((span.name, dict(span.attributes)))


def test_a_span_says_whether_its_session_heads_its_list():
    calls, rec = calls_with_spans()
    for key in ("ws/default", "ws/alice"):
        calls.end(calls.begin(key, "g", browser="chrome", tool="extract"),
                  tool="extract", surface="mcp", browser="chrome")
    head, other = rec.spans.get_finished_spans()
    assert head.attributes["selenium_flow.sub_session"] is False
    assert other.attributes["selenium_flow.sub_session"] is True


def test_a_span_whose_place_cannot_be_read_is_the_heads():
    calls, rec = calls_with_spans()

    def down(key):
        raise ConnectionError("redis down")

    calls.is_head = down
    calls.end(calls.begin("ws/alice", "g", browser="chrome", tool="extract"),
              tool="extract", surface="mcp", browser="chrome")
    [span] = rec.spans.get_finished_spans()
    assert span.attributes["selenium_flow.sub_session"] is False


def test_a_failed_call_carries_its_outcome_and_type():
    calls, rec = calls_with_spans()
    begun = calls.begin("ws", "grid-1", browser="chrome", tool="interact")
    error = ValueError("no element at https://example.com/?token=hunter2")
    calls.end(begun, tool="interact", surface="flow", browser="chrome", error=error)
    [span] = rec.spans.get_finished_spans()
    assert span.attributes["selenium_flow.outcome"] == outcome(error)
    assert span.attributes["error.type"] == "ValueError"
    assert span.status.status_code is StatusCode.ERROR
    assert "hunter2" not in repr((dict(span.attributes), span.status, span.events))


def test_a_refused_call_has_no_span():
    class Shut:
        def refusal(self, session_id, tool):
            return faults.Controlled("a person has control of this browser")

    calls, rec = calls_with_spans(control=Shut())
    with pytest.raises(faults.Controlled):
        calls.begin("ws", "grid-1", browser="chrome", tool="navigate")
    assert rec.spans.get_finished_spans() == ()


def test_without_telemetry_a_call_has_no_span():
    begun = Calls().begin("ws", "grid-1", browser="chrome", tool="navigate")
    assert begun.span is None


def test_a_tracer_that_raises_never_fails_the_call(monkeypatch):
    monitor = FakeMonitor()
    calls, rec = calls_with_spans(monitor=monitor)
    monkeypatch.setattr(rec.telemetry.tracer, "start_span", refuse)
    begun = calls.begin("ws", "grid-1", browser="chrome", tool="navigate")
    assert begun.span is None
    calls.end(begun, tool="navigate", surface="mcp", browser="chrome")
    assert [e.kind for e in monitor.published] == ["call.finished"]


# ---- `Calls.timed`: a call that takes no turn ----------------------------------


class Watched(FakeMonitor):
    """A `FakeMonitor` that notes every method asked of it, the bus apart."""

    def __init__(self):
        super().__init__()
        self.asked = []

    def __getattribute__(self, name):
        found = super().__getattribute__(name)
        if callable(found) and not name.startswith("_"):
            super().__getattribute__("asked").append(name)
        return found


class Clock:
    """A clock that says what it is told: entry, then exit."""

    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


def test_a_timed_call_has_one_span_and_one_finished_call():
    monitor = Watched()
    rec = recorded()
    calls = Calls(monitor=monitor, telemetry=rec.telemetry, clock=Clock(100.0, 102.5))
    calls.is_head = headed_by_default
    with calls.timed("ws/alice", tool="open_session", surface="mcp") as timing:
        timing.browser = "firefox"
    [span] = rec.spans.get_finished_spans()
    assert span.name == "call open_session"
    assert span.attributes["selenium_flow.workspace"] == "ws"
    assert span.attributes["selenium_flow.sub_session"] is True
    assert span.attributes["selenium_flow.browser"] == "firefox"
    assert (span.attributes["selenium_flow.surface"], span.attributes["selenium_flow.outcome"]) == ("mcp", "ok")
    assert span.start_time == 100_000_000_000
    [event] = monitor.published
    assert event.kind == "call.finished"
    assert (event.workspace, event.session_id, event.browser, event.tool) == ("ws/alice", "", "firefox", "open_session")
    assert (event.surface, event.outcome, event.started, event.duration) == ("mcp", "ok", 100.0, 2.5)
    # Never a keep-alive, a touch, a resume or a mark: the bus alone.
    assert monitor.asked == []


def test_a_timed_call_takes_the_key_it_learns():
    monitor = FakeMonitor()
    calls, rec = calls_with_spans(monitor=monitor)
    with calls.timed("ws", tool="open_session", surface="http") as timing:
        timing.workspace = "ws/tester"
    [span] = rec.spans.get_finished_spans()
    assert span.attributes["selenium_flow.sub_session"] is True
    assert monitor.published[0].workspace == "ws/tester"


def test_a_failed_timed_call_carries_its_outcome_and_type_and_still_raises():
    monitor = FakeMonitor()
    calls, rec = calls_with_spans(monitor=monitor)
    error = ValueError("no such browser at https://example.com/?token=hunter2")
    with pytest.raises(ValueError) as raised, calls.timed("ws", tool="open_session", surface="mcp"):
        raise error
    assert raised.value is error
    [span] = rec.spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "ValueError"
    assert span.attributes["selenium_flow.outcome"] == outcome(error) == "4xx"
    assert monitor.published[0].outcome == "4xx"
    assert "hunter2" not in repr((dict(span.attributes), span.status, span.events))


def test_a_refused_timed_call_records_nothing():
    monitor = FakeMonitor()
    calls, rec = calls_with_spans(monitor=monitor)
    with pytest.raises(faults.Controlled), calls.timed("ws", tool="end_browser", surface="mcp"):
        raise faults.Controlled("a person has control of this browser")
    assert rec.spans.get_finished_spans() == ()
    assert monitor.published == []


def test_a_timed_call_never_fails_on_its_own_bookkeeping(monkeypatch):
    monitor = FakeMonitor()
    monkeypatch.setattr(monitor.bus, "publish", refuse)
    calls, rec = calls_with_spans(monitor=monitor)
    monkeypatch.setattr(rec.telemetry.tracer, "start_span", refuse)
    with calls.timed("ws", tool="show", surface="mcp"):
        pass
    assert rec.spans.get_finished_spans() == ()
