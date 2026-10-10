"""Every call: the monitor is touched, `call.finished` is said, and with capture
on the result carries what the page reported while the call ran (spec §6,
ruling 12; plan rulings 4-8)."""

import itertools
import logging
import threading

import pytest
from selenium.common.exceptions import TimeoutException

from kubed.selenium_flow import faults
from kubed.selenium_flow.capture.store import Capture
from kubed.selenium_flow.flows import store as flow_store
from kubed.selenium_flow.monitor.events import CallFinished
from kubed.selenium_flow.secrets import perform_write
from kubed.selenium_flow.workspace.calls import Calls, hint, total
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .capture_events import done, fail, log, req
from .test_workspace_monitor import Actions as MonitorActions

pytestmark = pytest.mark.unit


class Bus:
    def __init__(self, published):
        self.published = published

    def publish(self, event):
        self.published.append(event)


class FakeMonitor:
    """Records `calling` / `called` and what the bus was handed; holds no connection unless
    a test gives it one."""

    def __init__(self):
        self.touched, self.published = [], []
        self.bus = Bus(self.published)
        self.held = None
        self.loop = None
        self.owed: set[str] = set()  # sessions that owe capture
        self.attached: list = []

    def calling(self, session_id):
        self.touched.append(("calling", session_id))

    def called(self, session_id):
        self.touched.append(("called", session_id))

    def backing_off(self, session_id):
        return False

    def connection(self, session_id):
        return self.held

    def owes(self, session_id, reason):
        return reason == "capture" and session_id in self.owed

    def capturing(self, session_id):
        return self.held is not None

    def attach(self, workspace, session_id, reason, **kwargs):
        self.attached.append((workspace, session_id, reason, kwargs.get("browser")))

    def opened(self, *args, **kwargs):
        pass

    def ended(self, *args, **kwargs):
        return True


class PageActions(MonitorActions):
    def page(self, session_id):
        return {"url": "https://a.test/", "title": "A"}


class Anywhere:
    """A catalogue holding ``demo/password``, usable on any page."""

    def entry(self, name):
        return {"name": name, "keys": ["password"], "allowed_urls": []}

    def allows(self, name, url):
        return True

    def value(self, name, key):
        return "hunter2"


def build(capture, monitor, actions=None):
    workspaces = Workspaces(actions or PageActions(), monitor=monitor, capture=capture)
    workspaces.open_browser(Caller("ws", "query"))
    if capture is not None:
        sid = workspaces.store.get("ws").session_id
        capture.attached("ws", sid, "chrome", "top-1", again=False)
    monitor.touched.clear()
    monitor.published.clear()
    return workspaces


@pytest.fixture
def captured_ws():
    capture, monitor = Capture(), FakeMonitor()
    return build(capture, monitor), capture, monitor


@pytest.fixture
def plain_ws():
    return build(None, FakeMonitor())


@pytest.fixture
def catalogue():
    return Anywhere()


# ---- the hint ----------------------------------------------------------------


def test_a_call_that_caused_errors_says_so_on_its_result(captured_ws):
    workspaces, capture, _ = captured_ws

    def click(session_id):
        capture.ingest("ws", session_id, *log("Uncaught TypeError", type="javascript", level="error"))
        capture.ingest("ws", session_id, *req("r1", "https://a.test/api/orders", method="POST"))
        capture.ingest("ws", session_id, *done("r1", 500))
        return {"url": "https://a.test/", "title": "A"}

    result = workspaces.act(Caller("ws", "query"), click, tool="interact", surface="mcp")
    assert result["page"] == {"errors": 1, "failed_requests": 1, "see": "workspace://console?level=error"}


def test_no_hint_when_nothing_went_wrong_or_capture_is_off(captured_ws, plain_ws):
    workspaces, _, _ = captured_ws
    ok = workspaces.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="extract")
    assert "page" not in ok
    assert "page" not in plain_ws.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="extract")


def test_warnings_and_failed_images_stay_silent(captured_ws):
    workspaces, capture, _ = captured_ws

    def noisy(sid):
        capture.ingest("ws", sid, *log("careful", level="warn"))
        capture.ingest("ws", sid, *req("r2", "https://a.test/x.png", dest="image"))
        capture.ingest("ws", sid, *fail("r2"))
        return {"url": "u"}

    assert "page" not in workspaces.act(Caller("ws", "query"), noisy, tool="interact")


def test_a_failed_call_says_what_the_page_reported(captured_ws):
    workspaces, capture, _ = captured_ws

    def click_then_time_out(sid):
        capture.ingest("ws", sid, *req("r1", "https://a.test/api", initiator="fetch"))
        capture.ingest("ws", sid, *done("r1", 500))
        raise TimeoutException("waited 30s for //div[@id='done']")

    with pytest.raises(TimeoutException) as raised:
        workspaces.act(Caller("ws", "query"), click_then_time_out, tool="interact")
    assert faults.message(raised.value).endswith(
        "This call caused 0 page error(s) and 1 failed request(s): read workspace://network?filter=failed.")


def test_a_failed_call_without_a_hint_says_only_its_own_words(captured_ws):
    workspaces, _, _ = captured_ws

    def time_out(sid):
        raise TimeoutException("waited 30s for //div[@id='done']")

    with pytest.raises(TimeoutException) as raised:
        workspaces.act(Caller("ws", "query"), time_out, tool="interact")
    assert faults.message(raised.value) == "waited 30s for //div[@id='done']"


def test_each_call_counts_only_its_own_window(captured_ws):
    """The quick call arrives while the slow one runs, and causes an error of
    its own. Holding the session's lock, it runs after the slow one ends, and
    each counts exactly its own; without it, the slow call's window would take
    in the quick one's error too."""
    workspaces, capture, _ = captured_ws
    entered, release = threading.Event(), threading.Event()

    def slow(sid):
        entered.set()
        release.wait(2)
        capture.ingest("ws", sid, *log("mine", level="error"))
        return {"url": "u"}

    def quick(sid):
        capture.ingest("ws", sid, *log("theirs", level="error"))
        return {"url": "u"}

    results = {}
    first = threading.Thread(target=lambda: results.setdefault(
        "slow", workspaces.act(Caller("ws", "query"), slow, tool="interact")))
    first.start()
    assert entered.wait(2)
    second = threading.Thread(target=lambda: results.setdefault(
        "quick", workspaces.act(Caller("ws", "query"), quick, tool="extract")))
    second.start()
    second.join(0.3)  # in act before the slow call lets go: waiting, or (unlocked) done
    release.set()
    first.join()
    second.join()
    assert results["slow"]["page"]["errors"] == 1
    assert results["quick"]["page"]["errors"] == 1


# ---- the first call after a pause (final review I1) ---------------------------


def test_the_first_call_after_a_pause_holds_capture_again_before_it_acts(captured_ws):
    """The deadline closed the connection while the agent thought; the call
    that comes back is the one most worth capturing."""
    workspaces, _, monitor = captured_ws
    sid = workspaces.store.get("ws").session_id
    monitor.owed.add(sid)
    order = []
    monitor.attach = lambda *a, **k: order.append(("attach", *a, k.get("browser")))
    workspaces.act(Caller("ws", "query"), lambda s: order.append("act") or {"url": "u"},
                   tool="interact")
    assert order == [("attach", "ws", sid, "capture", "chrome"), "act"]


def test_a_held_or_unowed_capture_is_not_attached_again(captured_ws):
    workspaces, _, monitor = captured_ws
    sid = workspaces.store.get("ws").session_id
    workspaces.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="extract")
    monitor.owed.add(sid)
    monitor.held = object()
    monitor.loop = None
    workspaces.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="extract")
    assert monitor.attached == []


def test_capture_that_cannot_resume_never_fails_the_call(captured_ws, caplog):
    workspaces, _, monitor = captured_ws
    sid = workspaces.store.get("ws").session_id
    monitor.owed.add(sid)

    def refuse(*_a, **_k):
        raise faults.BidiUnavailable(f"no route to {sid}")

    monitor.attach = refuse
    assert workspaces.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="extract") == {"url": "u"}
    assert "BidiUnavailable" in caplog.text and sid not in caplog.text


def test_a_bound_write_and_a_flow_step_resume_capture_too(tmp_path, catalogue):
    run = FlowRun(tmp_path)
    sid = run.workspaces.store.get("ws").session_id
    run.monitor.owed.add(sid)
    run(steps=[("interact", 0, 0)])
    run.actions.write = lambda sid, text, **kw: {"url": "u", "value": None}
    perform_write(catalogue, run.actions, run.workspaces, "ws",
                  {"secret": {"name": "demo", "key": "password"}, "selector": {"css": "#p"}})
    assert run.monitor.attached == [("ws", sid, "capture", "chrome")] * 2


# ---- every call is said ------------------------------------------------------


def test_every_call_touches_and_publishes_call_finished(captured_ws):
    workspaces, _, monitor = captured_ws
    with pytest.raises(ValueError):
        workspaces.act(Caller("ws", "query"), lambda s: (_ for _ in ()).throw(ValueError("bad")),
                       tool="write", surface="http")
    [event] = monitor.published
    assert (event.tool, event.surface, event.outcome, event.browser) == ("write", "http", "4xx", "chrome")
    assert monitor.touched == [("calling", event.session_id), ("called", event.session_id)]


def test_a_call_without_capture_is_said_too(plain_ws):
    plain_ws.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="extract")
    [event] = plain_ws.monitor.published
    assert isinstance(event, CallFinished)
    assert (event.workspace, event.tool, event.surface, event.outcome) == ("ws", "extract", "mcp", "ok")
    assert event.duration >= 0


def test_a_call_says_which_browser_it_ran_on_after_a_reopen_too():
    monitor = FakeMonitor()
    workspaces = Workspaces(PageActions(), monitor=monitor)
    workspaces.open_browser(Caller("ws", "query"), browser="firefox")
    workspaces.actions.grid.alive.clear()  # the Grid reaped it
    workspaces.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="extract")
    [event] = [e for e in monitor.published if isinstance(e, CallFinished)]
    assert event.browser == "firefox"
    assert event.session_id == workspaces.store.get("ws").session_id


def test_a_failure_of_ours_is_a_5xx():
    monitor = FakeMonitor()
    calls = Calls(monitor=monitor)
    calls.end(calls.begin("ws", "g1"), tool="extract", surface="mcp", browser="chrome",
              error=RuntimeError("boom"))
    assert monitor.published[0].outcome == "5xx"


def test_a_monitor_that_raises_never_fails_the_call(captured_ws, caplog):
    workspaces, _, monitor = captured_ws

    def broken(*args, **kwargs):
        raise RuntimeError("the monitor fell over")

    monitor.calling = monitor.called = broken
    monitor.bus.publish = broken
    result = workspaces.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="extract")
    assert result == {"url": "u"}


def test_a_flush_that_fails_is_logged_by_type_and_the_hint_still_comes(captured_ws, caplog):
    workspaces, capture, monitor = captured_ws
    monitor.held, monitor.loop = object(), object()  # a Channel over these cannot flush

    def click(sid):
        capture.ingest("ws", sid, *log("boom", level="error"))
        return {"url": "u"}

    with caplog.at_level(logging.DEBUG, logger="kubed.selenium_flow.workspace.calls"):
        result = workspaces.act(Caller("ws", "query"), click, tool="interact")
    assert result["page"]["errors"] == 1
    sid = workspaces.store.get("ws").session_id
    assert "flush" in caplog.text and sid not in caplog.text


def test_with_a_held_connection_the_call_flushes_it_before_counting(captured_ws, monkeypatch):
    workspaces, capture, monitor = captured_ws
    monitor.held, monitor.loop = "conn", "loop"
    flushed = []

    class Channel:
        def __init__(self, conn, loop):
            self.pair = (conn, loop)

        def flush(self, timeout):
            # What the browser sent before the call ended lands now.
            capture.ingest("ws", workspaces.store.get("ws").session_id, *log("late", level="error"))
            flushed.append(self.pair)

    from kubed.selenium_flow.workspace import calls as calls_module

    monkeypatch.setattr(calls_module, "Channel", Channel)
    result = workspaces.act(Caller("ws", "query"), lambda s: {"url": "u"}, tool="interact")
    assert flushed == [("conn", "loop")] and result["page"]["errors"] == 1


# ---- the hint's shape ---------------------------------------------------------


def test_the_hint_points_at_the_console_first_then_the_network():
    assert hint({"errors": 0, "failed_requests": 0}) is None
    assert hint({"errors": 0, "failed_requests": 3})["see"] == "workspace://network?filter=failed"
    assert hint({"errors": 2, "failed_requests": 3})["see"] == "workspace://console?level=error"


def test_a_total_sums_the_hints_and_is_none_when_there_were_none():
    assert total([None, None]) is None
    assert total([None, hint({"errors": 0, "failed_requests": 1}),
                  hint({"errors": 0, "failed_requests": 2})]) == {
        "errors": 0, "failed_requests": 3, "see": "workspace://network?filter=failed"}


def test_only_our_note_reaches_a_caller():
    exc = ValueError("bad")
    exc.add_note("an internal note")
    exc.add_note(f"{faults.PAGE_NOTE}1 page error(s) and 0 failed request(s): read x.")
    assert faults.message(exc) == "bad This call caused 1 page error(s) and 0 failed request(s): read x."


# ---- the bound write ----------------------------------------------------------


def test_a_bound_write_registers_its_value_before_typing(captured_ws, catalogue):
    workspaces, _, _ = captured_ws
    order = []
    workspaces.calls.bound = lambda ws, forms: order.append(("bound", sorted(forms)[0]))
    actions = workspaces.actions
    actions.write = lambda sid, text, **kw: order.append(("typed", text)) or {"url": "u", "value": None}
    perform_write(catalogue, actions, workspaces, "ws",
                  {"secret": {"name": "demo", "key": "password"}, "selector": {"css": "#p"}})
    assert [step for step, _ in order] == ["bound", "typed"]


def test_a_bound_write_masks_its_value_and_carries_the_hint(captured_ws, catalogue):
    workspaces, capture, monitor = captured_ws

    def typed(sid, text, **kw):
        capture.ingest("ws", sid, *log(f"saw {text}", level="error"))
        return {"url": "u", "value": None}

    workspaces.actions.write = typed
    shown = perform_write(catalogue, workspaces.actions, workspaces, "ws",
                          {"secret": {"name": "demo", "key": "password"}, "selector": {"css": "#p"}},
                          surface="http")
    assert shown["page"]["errors"] == 1
    assert "hunter2" not in str(capture.console("ws"))
    [event] = monitor.published
    assert (event.tool, event.surface, event.outcome) == ("write", "http", "ok")


# ---- a flow run ---------------------------------------------------------------


class FlowActions(PageActions):
    """A flow's actions: each ``interact`` makes the page report what the next
    planned step says, as (errors, failed fetches)."""

    def __init__(self, capture):
        super().__init__()
        self.capture, self.plan, self.ids = capture, [], itertools.count(1)

    def interact(self, session_id, **kwargs):
        errors, failed = self.plan.pop(0)
        for _ in range(errors):
            self.capture.ingest("ws", session_id, *log("broken", level="error"))
        for _ in range(failed):
            rid = f"f{next(self.ids)}"
            self.capture.ingest("ws", session_id, *req(rid, "https://a.test/api"))
            self.capture.ingest("ws", session_id, *done(rid, 503))
        return {"url": "https://a.test/", "title": "A"}


class FlowRun:
    def __init__(self, tmp_path):
        self.capture, self.monitor = Capture(), FakeMonitor()
        self.actions = FlowActions(self.capture)
        self.workspaces = build(self.capture, self.monitor, self.actions)
        self.store = flow_store.LocalFlowStore(tmp_path)

    def __call__(self, steps):
        from kubed.selenium_flow.flows import api as flowapi

        self.actions.plan = [(errors, failed) for _, errors, failed in steps]
        self.store.save("ws", "trip", {"steps": [
            {"tool": tool, "args": {"action": "click", "selector": {"css": "button"}}}
            for tool, _, _ in steps
        ]})
        return flowapi.run_for(self.store, self.actions, self.workspaces, "ws", "trip")


@pytest.fixture
def flow_run_with_capture(tmp_path):
    return FlowRun(tmp_path)


def test_a_flow_run_sums_its_steps_hints(flow_run_with_capture):
    report = flow_run_with_capture(steps=[("interact", 1, 0), ("interact", 0, 2)])  # (tool, errors, failed) per step
    assert report["page"] == {"errors": 1, "failed_requests": 2, "see": "workspace://console?level=error"}
    assert [e.surface for e in flow_run_with_capture.monitor.published] == ["flow", "flow"]


def test_a_quiet_flow_run_has_no_hint(flow_run_with_capture):
    report = flow_run_with_capture(steps=[("interact", 0, 0)])
    assert report["status"] == "ok" and "page" not in report


class SecretFlowActions(FlowActions):
    """A flow's `write` whose page echoes what was typed into its console."""

    def write(self, session_id, text=None, **kwargs):
        self.capture.ingest("ws", session_id, *log(f"saw {text}", level="error"))
        return {"url": "https://a.test/", "title": "A", "value": text}


def test_a_flow_steps_bound_value_is_masked_in_what_capture_keeps(tmp_path):
    from kubed.selenium_flow.flows import api as flowapi

    capture, monitor = Capture(), FakeMonitor()
    actions = SecretFlowActions(capture)
    workspaces = build(capture, monitor, actions)
    store = flow_store.LocalFlowStore(tmp_path)
    store.save("ws", "login", {"steps": [{"tool": "write", "args": {
        "selector": {"css": "#p"}, "secret": {"name": "demo", "key": "password"},
    }}]})
    report = flowapi.run_for(
        store, actions, workspaces, "ws", "login", secrets_catalogue=Anywhere()
    )
    assert report["status"] == "ok" and report["page"]["errors"] == 1
    console = str(capture.console("ws"))
    assert "saw" in console and "hunter2" not in console


# ---- the published contract ---------------------------------------------------


def test_every_call_that_can_carry_the_hint_declares_it():
    from kubed.selenium_flow.core.capabilities import CAPABILITIES, PAGE_HINT
    from kubed.selenium_flow.spec.schemas import FLOW_SCHEMAS

    # Opening and ending a browser are served by `Workspaces`, not `act`.
    for row in CAPABILITIES:
        declared = row.response["properties"]
        if row.name in ("open_session", "end_browser"):
            assert "page" not in declared, row.name
        else:
            assert declared["page"] == PAGE_HINT, row.name
    assert FLOW_SCHEMAS["FlowRun"]["properties"]["page"] == PAGE_HINT
    assert set(PAGE_HINT["properties"]) == {"errors", "failed_requests", "see"}


def test_a_step_that_fails_before_it_acts_still_ends_its_call(flow_run_with_capture, monkeypatch):
    """A save step's pages are written before it acts; when that write fails
    the step was still begun, and a call left in flight would keep its
    connection held past every deadline (PR review I1)."""
    from kubed.selenium_flow.flows import api as flowapi

    run = flow_run_with_capture
    settle, failed = run.workspaces.settle, []

    def settle_once_failing(*args, **kwargs):
        if not failed:
            failed.append(1)
            raise RuntimeError("the store blinked")
        return settle(*args, **kwargs)

    monkeypatch.setattr(run.workspaces, "settle", settle_once_failing)
    run.actions.save_site_data = lambda session_id, **kwargs: {"url": "https://a.test/"}
    run.store.save("ws", "keep", {"steps": [{"tool": "save_site_data", "args": {}}]})
    report = flowapi.run_for(run.store, run.actions, run.workspaces, "ws", "keep")
    assert report["status"] != "ok"
    said = [kind for kind, _ in run.monitor.touched]
    assert said == ["calling", "called"]
