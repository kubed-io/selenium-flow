"""selenium-flow's own signals: the call histogram, the Grid gauge, the call spans."""

import dataclasses
import importlib.metadata
import json
import logging
import subprocess
import sys
import textwrap

from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.trace import SpanKind, StatusCode

from kubed.selenium_flow.monitor.events import CallFinished
from kubed.selenium_flow.telemetry import (
    BUCKETS,
    CALL_DURATION,
    GRID_SLOTS,
    SCOPE,
    Telemetry,
    grid_counts,
)

from .fakes import points, recorded

STATUS = {"value": {"ready": True, "nodes": [
    {"availability": "UP", "maxSessions": 2, "slots": [
        {"stereotype": {"browserName": "chrome"}, "session": {"sessionId": "g1"}},
        {"stereotype": {"browserName": "chrome"}, "session": None}]},
    {"availability": "UP", "maxSessions": 1, "slots": [
        {"stereotype": {"browserName": "firefox"}, "session": {"sessionId": "g2"}}]},
    {"availability": "DRAINING", "maxSessions": 1, "slots": [
        {"stereotype": {"browserName": "chrome"}, "session": None}]},
    {"availability": "UP", "maxSessions": 1, "slots": [
        {"stereotype": {"browserName": "chrome"}, "session": {"sessionId": "g3"}},
        {"stereotype": {"browserName": "firefox"}, "session": None}]},
    {"slots": [{"stereotype": {"browserName": "chrome"}, "session": None}]},
]}}
FINISHED = CallFinished("ws/alice", "grid-1", "chrome", "navigate", "flow", "4xx", 0.0, 0.25)


def test_a_finished_call_is_one_point_with_exactly_four_attributes():
    rec = recorded()
    rec.telemetry.call_finished(FINISHED)
    [(attributes, point)] = points(rec.metrics, CALL_DURATION)
    assert attributes == {"selenium_flow.tool": "navigate", "selenium_flow.surface": "flow",
                          "selenium_flow.outcome": "4xx", "selenium_flow.browser": "chrome"}
    assert (point.count, point.sum) == (1, 0.25)


def test_the_buckets_are_mcp_server_operation_durations():
    rec = recorded()
    rec.telemetry.call_finished(FINISHED)
    [(_, point)] = points(rec.metrics, CALL_DURATION)
    assert tuple(point.explicit_bounds) == BUCKETS == (
        0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300)


def test_an_empty_browser_is_recorded_as_given():
    rec = recorded()
    rec.telemetry.call_finished(dataclasses.replace(FINISHED, browser=""))
    [(attributes, _)] = points(rec.metrics, CALL_DURATION)
    assert attributes["selenium_flow.browser"] == ""


def test_the_grid_counts_only_slots_that_can_take_a_session():
    assert grid_counts(STATUS) == {("chrome", "busy"): 2, ("chrome", "free"): 2, ("firefox", "busy"): 1, ("firefox", "free"): 0}


def test_an_over_full_node_has_no_free_slot():
    node = {"availability": "UP", "maxSessions": 1, "slots": [
        {"stereotype": {"browserName": "chrome"}, "session": {"sessionId": "g1"}},
        {"stereotype": {"browserName": "chrome"}, "session": {"sessionId": "g2"}},
        {"stereotype": {"browserName": "chrome"}, "session": None}]}
    assert grid_counts({"value": {"nodes": [node]}}) == {
        ("chrome", "busy"): 2, ("chrome", "free"): 0, ("firefox", "busy"): 0, ("firefox", "free"): 0}


def test_the_gauge_reports_the_grid_by_browser_and_state():
    rec = recorded()
    rec.telemetry.grid_gauge(lambda: STATUS)
    slots = {(a["selenium_flow.browser"], a["selenium_flow.slot.state"]): p.value
             for a, p in points(rec.metrics, GRID_SLOTS)}
    assert slots == {("chrome", "busy"): 2, ("chrome", "free"): 2, ("firefox", "busy"): 1, ("firefox", "free"): 0}


def test_a_full_grid_reports_no_free_slot_as_zero():
    slots = [{"stereotype": {"browserName": "chrome"}, "session": {"sessionId": "g1"}}]
    full = {"value": {"nodes": [{"availability": "UP", "maxSessions": 1, "slots": slots}]}}
    assert grid_counts(full) == {
        ("chrome", "busy"): 1, ("chrome", "free"): 0, ("firefox", "busy"): 0, ("firefox", "free"): 0}


def test_an_empty_grid_reports_no_busy_slot_as_zero():
    slots = [{"stereotype": {"browserName": "chrome"}, "session": None},
             {"stereotype": {"browserName": "firefox"}, "session": None}]
    idle = {"value": {"nodes": [{"availability": "UP", "maxSessions": 2, "slots": slots}]}}
    assert grid_counts(idle) == {
        ("chrome", "busy"): 0, ("chrome", "free"): 1, ("firefox", "busy"): 0, ("firefox", "free"): 1}


def test_a_grid_with_no_nodes_reports_every_state_as_zero():
    none = {"value": {"ready": False, "nodes": []}}
    assert grid_counts(none) == {
        ("chrome", "busy"): 0, ("chrome", "free"): 0, ("firefox", "busy"): 0, ("firefox", "free"): 0}
    rec = recorded()
    rec.telemetry.grid_gauge(lambda: none)
    assert [p.value for _, p in points(rec.metrics, GRID_SLOTS)] == [0, 0, 0, 0]


def test_a_browser_the_server_does_not_open_gets_both_states():
    draining = {"availability": "DRAINING", "maxSessions": 1, "slots": [
        {"stereotype": {"browserName": "MicrosoftEdge"}, "session": None},
        {"session": None}]}
    counts = grid_counts({"value": {"nodes": [draining]}})
    assert counts[("MicrosoftEdge", "busy")] == counts[("MicrosoftEdge", "free")] == 0
    assert counts[("", "busy")] == counts[("", "free")] == 0


def test_one_collection_reads_the_grid_once():
    reads = []
    rec = recorded()
    rec.telemetry.grid_gauge(lambda: reads.append(1) or STATUS)
    rec.metrics.get_metrics_data()
    assert len(reads) == 1


def test_a_failed_read_observes_nothing_and_logs_its_type_once_a_streak(caplog):
    rec = recorded()
    answers = [ConnectionError("http://u:hunter2@grid.example.com:4444"),
               ConnectionError("again"), STATUS, ConnectionError("later")]

    def read():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    rec.telemetry.grid_gauge(read)
    rounds = []
    with caplog.at_level(logging.WARNING, logger="kubed.selenium_flow.telemetry"):
        for _ in range(4):  # each `points` is one collection, one read
            rounds.append(points(rec.metrics, GRID_SLOTS))
    assert [bool(s) for s in rounds] == [False, False, True, False]
    assert [rounds[0], rounds[1], rounds[3]] == [[], [], []]
    assert caplog.text.count("ConnectionError") == 2
    assert "hunter2" not in caplog.text and "grid.example.com" not in caplog.text


async def test_a_collection_on_the_event_loop_reads_nothing():
    reads = []
    rec = recorded()
    rec.telemetry.grid_gauge(lambda: reads.append(1) or STATUS)
    assert points(rec.metrics, GRID_SLOTS) == []  # collected on this test's running loop
    assert reads == []


def test_after_stop_a_collection_reads_nothing():
    reads = []
    rec = recorded()
    rec.telemetry.grid_gauge(lambda: reads.append(1) or STATUS)
    assert points(rec.metrics, GRID_SLOTS) != [] and reads == [1]  # off the loop
    rec.telemetry.stop()
    assert points(rec.metrics, GRID_SLOTS) == []
    assert reads == [1]


def test_a_null_maxsessions_is_no_cap():
    node = {"availability": "UP", "maxSessions": None, "slots": [
        {"stereotype": {"browserName": "chrome"}, "session": None},
        {"stereotype": {"browserName": "chrome"}, "session": None}]}
    assert grid_counts({"value": {"nodes": [node]}}) == {
        ("chrome", "busy"): 0, ("chrome", "free"): 2, ("firefox", "busy"): 0, ("firefox", "free"): 0}


def test_the_histogram_describes_the_calls_own_time():
    rec = recorded()
    rec.telemetry.call_finished(FINISHED)
    [metric] = [m for rm in rec.metrics.get_metrics_data().resource_metrics
                for sm in rm.scope_metrics for m in sm.metrics if m.name == CALL_DURATION]
    assert metric.description == (
        "A call's own time: inside its session's turn, not counting the wait "
        "for it, for a call that takes one; from its start to its end for one "
        "that takes none."
    )


def test_a_span_starts_under_the_current_context_and_is_not_made_current():
    rec = recorded()
    with rec.telemetry.tracer.start_as_current_span("parent") as parent:
        span = rec.telemetry.start_call("ws", sub_session=True, tool="navigate", browser="chrome")
        assert trace.get_current_span() is parent
    rec.telemetry.end_call(span, surface="mcp", outcome="ok")
    call = next(s for s in rec.spans.get_finished_spans() if s.name == "call navigate")
    assert call.parent.span_id == parent.get_span_context().span_id
    assert call.kind is SpanKind.INTERNAL
    assert dict(call.attributes) == {
        "selenium_flow.workspace": "ws", "selenium_flow.sub_session": True,
        "selenium_flow.tool": "navigate", "selenium_flow.browser": "chrome",
        "selenium_flow.surface": "mcp", "selenium_flow.outcome": "ok"}
    assert call.status.status_code is StatusCode.UNSET


def test_a_failed_call_is_an_error_with_its_type_and_none_of_its_text():
    rec = recorded()
    span = rec.telemetry.start_call("ws", sub_session=False, tool="write", browser="chrome")
    error = ValueError("typed hunter2 on https://example.com/?q=1")
    rec.telemetry.end_call(span, surface="http", outcome="4xx", error=error)
    [call] = rec.spans.get_finished_spans()
    assert call.status.status_code is StatusCode.ERROR and call.status.description is None
    assert call.attributes["error.type"] == "ValueError"
    assert len(call.events) == 0
    assert "hunter2" not in repr((call.name, dict(call.attributes), call.status, call.events))


class Raising:
    """A tracer provider that is its own tracer, and whose spans cannot start."""

    def get_tracer(self, *_, **__):
        return self

    def start_span(self, *_, **__):
        raise RuntimeError("no span for hunter2")


class BrokenSpan:
    ended = False

    def set_attribute(self, *_):
        raise RuntimeError("no attribute for hunter2")

    def set_attributes(self, *_):
        raise RuntimeError("no attribute for hunter2")

    def set_status(self, *_):
        raise RuntimeError("no status for hunter2")

    def end(self, *_, **__):
        self.ended = True


def test_a_span_is_ended_though_its_attributes_fail():
    span = BrokenSpan()
    Telemetry(tracer_provider=Raising(), meter_provider=MeterProvider()).end_call(
        span, surface="mcp", outcome="5xx", error=ValueError("x"))
    assert span.ended


def test_the_span_helpers_never_raise(caplog):
    telemetry = Telemetry(tracer_provider=Raising(), meter_provider=MeterProvider())
    with caplog.at_level(logging.WARNING, logger="kubed.selenium_flow.telemetry"):
        assert telemetry.start_call("ws", sub_session=False, tool="t", browser="") is None
        telemetry.end_call(None, surface="mcp", outcome="ok")
        telemetry.end_call(BrokenSpan(), surface="mcp", outcome="ok")
    assert caplog.text.count("RuntimeError") == 2
    assert "hunter2" not in caplog.text


def test_the_scope_is_kubed_selenium_flow_with_the_package_version():
    rec = recorded()
    rec.telemetry.call_finished(FINISHED)
    [scope] = [sm.scope for rm in rec.metrics.get_metrics_data().resource_metrics
               for sm in rm.scope_metrics]
    assert (scope.name, scope.version) == (SCOPE, importlib.metadata.version("kubed-selenium-flow"))


SCRIPT = textwrap.dedent(
    """
    import json
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from kubed.selenium_flow.monitor.events import CallFinished
    from kubed.selenium_flow.telemetry import Telemetry

    STATUS = {status!r}
    telemetry = Telemetry()
    telemetry.grid_gauge(lambda: STATUS)

    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    telemetry.call_finished(CallFinished(
        "ws/alice", "grid-1", "chrome", "navigate", "flow", "ok", 0.0, 0.25))
    span = telemetry.start_call("ws", sub_session=False, tool="navigate", browser="chrome")
    telemetry.end_call(span, surface="mcp", outcome="ok")
    data = reader.get_metrics_data()
    names = [m.name for rm in data.resource_metrics for sm in rm.scope_metrics
             for m in sm.metrics]
    print(json.dumps({{"metrics": names, "spans": [s.name for s in exporter.get_finished_spans()]}}))
    """
)


def test_instruments_made_before_the_sdk_is_set_still_record():
    # A fresh interpreter: the global providers can be set once per process, and this
    # suite's other tests must not have set them.
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT.format(status=STATUS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout.strip().splitlines()[-1])
    assert {CALL_DURATION, GRID_SLOTS} <= set(found["metrics"])
    assert found["spans"] == ["call navigate"]
