"""Running a flow: what happens, what is reported, and what is never reported.

The report is as much of the feature as the running is. A run that fails at step
nine and says only "failed" sends the agent straight back to twelve individual
calls to find out why, which loses more than the flow ever saved.
"""

import pytest

from kubed.selenium_flow import flowrun
from kubed.selenium_flow.flowrun import FlowError, run

pytestmark = pytest.mark.unit


class FakeActions:
    """Records what it was called with, and can be told to fail on a step."""

    def __init__(self, fail_on=None, error="boom"):
        self.calls = []
        self.fail_on = fail_on or set()
        self.error = error

    def _record(self, tool, session_id, **kwargs):
        self.calls.append((tool, session_id, kwargs))
        if tool in self.fail_on or len(self.calls) in self.fail_on:
            raise RuntimeError(self.error)
        return {"url": f"https://example.test/{tool}", "title": tool.title()}

    def navigate(self, session_id, **kwargs):
        return self._record("navigate", session_id, **kwargs)

    def write(self, session_id, **kwargs):
        # The real one reads the field back and returns it, which is the leak
        # a guarded step has to close.
        return {
            **self._record("write", session_id, **kwargs),
            "value": kwargs.get("text"),
        }

    def interact(self, session_id, **kwargs):
        return self._record("interact", session_id, **kwargs)

    def extract(self, session_id, **kwargs):
        return {**self._record("extract", session_id, **kwargs), "text": "the heading"}

    def screenshot(self, session_id, **kwargs):
        return {
            **self._record("screenshot", session_id, **kwargs),
            "image": "A" * 5000,
        }

    def open_session(self, session_id, **kwargs):  # pragma: no cover - refused
        return self._record("open_session", session_id, **kwargs)


def flow(steps, **extra):
    return {"name": "login", "steps": steps, **extra}


SIMPLE = [
    {"tool": "navigate", "params": {"url": "https://example.test/login"}},
    {"tool": "write", "params": {"css": "#email", "text": "a@b.c"}},
    {"tool": "interact", "params": {"action": "click", "css": "button"}},
]


# ---- the ordinary run --------------------------------------------------------


def test_every_step_runs_in_order_against_one_browser():
    actions = FakeActions()
    report = run(actions, flow(SIMPLE), "browser-1")
    assert report["status"] == "ok"
    assert report["steps_run"] == 3
    assert [tool for tool, _, _ in actions.calls] == ["navigate", "write", "interact"]
    assert {session for _, session, _ in actions.calls} == {"browser-1"}


def test_the_report_says_where_the_browser_ended_up():
    report = run(FakeActions(), flow(SIMPLE), "b")
    assert report["url"] == "https://example.test/interact"
    assert report["title"] == "Interact"


def test_a_step_is_summarised_without_its_content():
    report = run(FakeActions(), flow(SIMPLE), "b")
    summaries = [s["summary"] for s in report["steps"]]
    assert "css='#email'" in summaries[1]
    # The length, not the text — the summary is built from what is safe to say.
    assert "5 chars" in summaries[1]
    assert "a@b.c" not in summaries[1]


# ---- what comes back ---------------------------------------------------------


def test_only_the_last_result_comes_back_by_default():
    report = run(FakeActions(), flow(SIMPLE), "b")
    assert "result" in report
    assert all("result" not in step for step in report["steps"])


def test_a_step_can_ask_for_its_own_result():
    steps = [
        {"tool": "navigate", "params": {"url": "x"}},
        {"tool": "extract", "params": {"css": "h1"}, "return": True},
        {"tool": "interact", "params": {"action": "click", "css": "b"}},
    ]
    report = run(FakeActions(), flow(steps), "b")
    assert report["steps"][1]["result"]["text"] == "the heading"
    assert "result" not in report["steps"][0]


def test_verbose_returns_every_step():
    report = run(FakeActions(), flow(SIMPLE), "b", verbose=True)
    assert all("result" in step for step in report["steps"])


def test_a_screenshot_does_not_put_a_megabyte_in_the_report():
    """A run returning three full-page images costs more than the twelve calls
    it replaced."""
    steps = [{"tool": "screenshot", "params": {}, "return": True}]
    report = run(FakeActions(), flow(steps), "b")
    assert "image" not in report["steps"][0]["result"]
    assert "image" not in report["result"]


# ---- failure -----------------------------------------------------------------


def test_a_run_stops_at_the_first_failing_step():
    actions = FakeActions(fail_on={"write"})
    report = run(actions, flow(SIMPLE), "b")
    assert report["status"] == "failed"
    assert report["steps_run"] == 2
    assert [tool for tool, _, _ in actions.calls] == ["navigate", "write"]


def test_a_failure_says_which_step_and_what_page():
    steps = [
        {"tool": "navigate", "params": {"url": "x"}},
        {"tool": "write", "params": {"css": "#a", "text": "b"}, "id": "fill-email"},
    ]
    report = run(FakeActions(fail_on={"write"}, error="no element matched"), flow(steps), "b")
    failed = report["steps"][-1]
    assert failed["ok"] is False
    assert failed["n"] == 2
    assert failed["id"] == "fill-email"
    assert "no element matched" in failed["error"]
    # The page it failed ON, not one asked for afterwards.
    assert report["url"] == "https://example.test/navigate"


def test_a_step_may_be_allowed_to_fail():
    """The cookie banner that is only sometimes there — a real and common case."""
    steps = [
        {"tool": "interact", "params": {"action": "click", "css": ".cookies"},
         "onError": "continue", "id": "dismiss-banner"},
        {"tool": "navigate", "params": {"url": "x"}},
    ]
    report = run(FakeActions(fail_on={1}), flow(steps), "b")
    assert report["status"] == "ok"
    assert report["steps"][0]["ok"] is False
    assert report["steps"][1]["ok"] is True


def test_the_run_status_and_a_step_status_are_different_questions():
    steps = [
        {"tool": "navigate", "params": {"url": "x"}, "onError": "continue"},
        {"tool": "navigate", "params": {"url": "y"}},
    ]
    report = run(FakeActions(fail_on={1}), flow(steps), "b")
    assert report["status"] == "ok"
    assert report["steps"][0]["ok"] is False


def test_a_tool_that_no_longer_exists_fails_loudly():
    """Saving validates this, so getting here means the document was written
    before a rename, or edited on disk by hand."""
    report = run(FakeActions(), flow([{"tool": "teleport", "params": {}}]), "b")
    assert report["status"] == "failed"
    assert "no action called 'teleport'" in report["steps"][0]["error"]


def test_a_flow_cannot_open_its_own_browser_even_if_the_document_says_so():
    actions = FakeActions()
    report = run(actions, flow([{"tool": "open_session", "params": {}}]), "b")
    assert report["status"] == "failed"
    assert actions.calls == []


class FakeClock:
    """A clock that advances a second every time it is read."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 1.0
        return self.now


def test_a_run_stops_when_it_is_out_of_time(monkeypatch):
    """Checked between steps: a Selenium call blocks, so the honest bound is
    "we will not start another step"."""
    monkeypatch.setattr(flowrun, "time", FakeClock())
    steps = [{"tool": "navigate", "params": {"url": str(n)}} for n in range(5)]
    actions = FakeActions()
    report = run(actions, flow(steps), "b", timeout=2)
    assert report["status"] == "failed"
    assert "budget" in report["steps"][-1]["error"]
    # It stopped rather than running the rest.
    assert len(actions.calls) < 5


def test_an_explicit_zero_budget_is_not_read_as_unset(monkeypatch):
    """`timeout or DEFAULT` would hand a caller asking for no time the full five
    minutes, which is the coercion bug in reverse."""
    monkeypatch.setattr(flowrun, "time", FakeClock())
    actions = FakeActions()
    report = run(actions, flow(SIMPLE), "b", timeout=0)
    assert report["status"] == "failed"
    assert actions.calls == []


# ---- parameters, structurally ------------------------------------------------


def test_a_parameter_reaches_the_step_that_names_it():
    steps = [
        {
            "tool": "write",
            "params": {"css": "#email"},
            "valueFrom": {"text": {"param": "email"}},
        }
    ]
    document = flow(
        steps,
        parameters={"type": "object", "properties": {"email": {"type": "string"}}},
    )
    actions = FakeActions()
    run(actions, document, "b", params={"email": "someone@example.test"})
    assert actions.calls[0][2]["text"] == "someone@example.test"


def test_a_missing_required_parameter_is_refused_before_step_one():
    document = flow(
        SIMPLE,
        parameters={
            "type": "object",
            "properties": {"email": {}},
            "required": ["email"],
        },
    )
    actions = FakeActions()
    with pytest.raises(FlowError, match="needs email"):
        run(actions, document, "b")
    assert actions.calls == []


def test_a_parameter_the_flow_does_not_take_is_refused():
    document = flow(SIMPLE, parameters={"type": "object", "properties": {"email": {}}})
    with pytest.raises(FlowError, match="does not take nonsense"):
        run(FakeActions(), document, "b", params={"nonsense": 1})


def test_nothing_scans_a_payload_for_placeholders():
    """The whole reason references are structural. Both of these are ordinary
    strings and must arrive at the action byte for byte."""
    script = "return `${window.scrollY}px`"
    steps = [
        {"tool": "write", "params": {"css": "#a", "text": "{{not a reference}}"}},
        {"tool": "navigate", "params": {"url": script}},
    ]
    actions = FakeActions()
    run(actions, flow(steps), "b")
    assert actions.calls[0][2]["text"] == "{{not a reference}}"
    assert actions.calls[1][2]["url"] == script


# ---- values that must not come back ------------------------------------------


def test_a_write_only_parameter_is_not_echoed_by_the_step_that_used_it():
    """`write` reads the field back and returns it, which for a guarded value
    would hand it straight back on the very call meant to protect it."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#password"},
            "valueFrom": {"text": {"param": "password"}},
            "return": True,
        }
    ]
    document = flow(
        steps,
        parameters={
            "type": "object",
            "properties": {"password": {"type": "string", "writeOnly": True}},
        },
    )
    report = run(FakeActions(), document, "b", params={"password": "hunter2"})
    assert report["steps"][0]["result"]["value"] is None
    assert "hunter2" not in str(report)


def test_a_guarded_value_is_hidden_in_the_summary_too():
    steps = [
        {
            "tool": "write",
            "params": {"css": "#password"},
            "valueFrom": {"text": {"param": "password"}},
        }
    ]
    document = flow(
        steps,
        parameters={
            "type": "object",
            "properties": {"password": {"writeOnly": True}},
        },
    )
    report = run(FakeActions(), document, "b", params={"password": "hunter2"})
    assert report["steps"][0]["summary"].endswith("text=<hidden>")


def test_an_ordinary_parameter_is_not_hidden():
    """writeOnly is a marker someone chose, not a guess about what looks secret."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#email"},
            "valueFrom": {"text": {"param": "email"}},
            "return": True,
        }
    ]
    document = flow(steps, parameters={"type": "object", "properties": {"email": {}}})
    report = run(FakeActions(), document, "b", params={"email": "a@b.c"})
    assert report["steps"][0]["result"]["value"] == "a@b.c"


def test_the_value_still_reaches_the_browser():
    """Hiding it from the report must not hide it from the page."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#password"},
            "valueFrom": {"text": {"param": "password"}},
        }
    ]
    document = flow(
        steps, parameters={"type": "object", "properties": {"password": {"writeOnly": True}}}
    )
    actions = FakeActions()
    run(actions, document, "b", params={"password": "hunter2"})
    assert actions.calls[0][2]["text"] == "hunter2"


# ---- sources that are not built yet ------------------------------------------


def test_a_flow_binding_a_secret_refuses_rather_than_typing_nothing():
    """A login flow that silently typed nothing into the password field would
    report success and leave someone staring at a login page."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#password"},
            "valueFrom": {"text": {"secret": {"name": "nextcloud", "key": "password"}}},
            "id": "fill-password",
        }
    ]
    actions = FakeActions()
    report = run(actions, flow(steps), "b")
    assert report["status"] == "failed"
    assert "secrets are not available" in report["steps"][0]["error"]
    assert actions.calls == []


def test_the_run_timeout_has_a_default():
    assert flowrun.RUN_TIMEOUT > 0
