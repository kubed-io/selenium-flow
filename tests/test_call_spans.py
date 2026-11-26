"""Every call is a span from `Calls.begin` to `Calls.end`, naming its workspace
and never its sub-session or its Grid id."""

import pytest
from opentelemetry.trace import StatusCode

from kubed.selenium_flow import faults
from kubed.selenium_flow.workspace.calls import Calls, outcome

from .fakes import FakeMonitor, recorded

pytestmark = pytest.mark.unit


def refuse(*args, **kwargs):
    raise RuntimeError("no tracer")


def calls_with_spans(**kwargs):
    rec = recorded()
    return Calls(telemetry=rec.telemetry, **kwargs), rec


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
