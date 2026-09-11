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
            "params": {"css": "#email", "value_from": {"param": "email"}},
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
            "params": {"css": "#password", "value_from": {"param": "password"}},
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
            "params": {"css": "#password", "value_from": {"param": "password"}},
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
            "params": {"css": "#email", "value_from": {"param": "email"}},
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
            "params": {"css": "#password", "value_from": {"param": "password"}},
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
            "params": {"css": "#password", "value_from": {"secret": {"name": "nextcloud", "key": "password"}}},
            "id": "fill-password",
        }
    ]
    actions = FakeActions()
    report = run(actions, flow(steps), "b")
    assert report["status"] == "failed"
    assert "secrets are not enabled" in report["steps"][0]["error"]
    assert actions.calls == []


def test_the_run_timeout_has_a_default():
    assert flowrun.RUN_TIMEOUT > 0


# ---- what the review caught -------------------------------------------------


def test_the_guard_keys_off_the_argument_not_a_flag():
    """The whitelist decides which fields could ever be printed; the guard
    decides per argument. Only `write.text` is bindable today, and the guard
    stays a set so a second bindable argument needs no rewrite of the
    redaction."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#p", "value_from": {"param": "magic_link"}},
        }
    ]
    document = flow(
        steps,
        parameters={"type": "object", "properties": {"magic_link": {"writeOnly": True}}},
    )
    secret = "https://example.test/login?token=abc123"
    report = run(FakeActions(), document, "b", params={"magic_link": secret})
    assert "text=<hidden>" in report["steps"][0]["summary"]
    assert "abc123" not in report["steps"][0]["summary"]


def test_an_unguarded_field_is_still_printed_beside_a_guarded_one():
    steps = [
        {
            "tool": "write",
            "params": {"css": "#password", "value_from": {"param": "password"}},
        }
    ]
    document = flow(
        steps, parameters={"type": "object", "properties": {"password": {"writeOnly": True}}}
    )
    summary = run(FakeActions(), document, "b", params={"password": "x"})["steps"][0][
        "summary"
    ]
    assert "css='#password'" in summary
    assert "text=<hidden>" in summary


@pytest.mark.parametrize("attribute", ["clear_files", "_at", "files", "__init__"])
def test_a_step_cannot_dispatch_to_an_attribute_that_is_not_an_action(attribute):
    """`getattr` accepts any callable. Saving validates the name, but a file
    edited on disk never passed through saving — and `clear_files` would wipe
    the session's downloads."""
    actions = FakeActions()
    called = []
    setattr(actions, attribute, lambda *a, **k: called.append(attribute))
    report = run(actions, flow([{"tool": attribute, "params": {}}]), "b")
    assert report["status"] == "failed"
    assert "no action called" in report["steps"][0]["error"]
    assert called == []


def test_a_run_error_reads_as_the_caller_s_mistake():
    """Every one of these is fixable by whoever called. As a RuntimeError they
    reached errors.status_for as a 500, telling a retrying client to replay a
    request that was never going to work."""
    from kubed.selenium_flow import errors

    document = flow(SIMPLE, parameters={"type": "object", "properties": {},
                                        "required": ["email"]})
    try:
        run(FakeActions(), document, "b")
    except FlowError as exc:
        assert errors.status_for(exc) == 400
    else:  # pragma: no cover
        pytest.fail("expected a FlowError")


def test_a_failure_reports_the_page_the_browser_is_actually_on():
    """A step carrying `url` navigates before it waits, so a failed wait leaves
    the browser on the new page while the last success holds the newest URL."""

    class Navigating(FakeActions):
        def page(self, session_id):
            return {"url": "https://example.test/two", "title": "Two"}

        def write(self, session_id, **kwargs):
            raise RuntimeError("no element matched")

    steps = [
        {"tool": "navigate", "params": {"url": "https://example.test/one"}},
        {"tool": "write", "params": {"url": "https://example.test/two", "css": "#a",
                                     "text": "x"}},
    ]
    report = run(Navigating(), flow(steps), "b")

    assert report["status"] == "failed"
    assert report["steps"][-1]["url"] == "https://example.test/two"
    # And the run-level URL, which is what sessions.touch stores.
    assert report["url"] == "https://example.test/two"


def test_reading_the_failed_page_can_never_make_a_failure_worse():
    class Exploding(FakeActions):
        def page(self, session_id):
            raise RuntimeError("the grid is gone too")

        def write(self, session_id, **kwargs):
            raise RuntimeError("no element matched")

    report = run(Exploding(), flow(SIMPLE), "b")
    assert report["status"] == "failed"
    assert "no element matched" in report["steps"][-1]["error"]


# ---- what the third review round caught --------------------------------------


def test_a_guarded_value_does_not_come_back_in_the_result():
    """`write` reads the field back and answers with it, so redacting only the
    summary would hand the value straight back."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#p", "value_from": {"param": "magic_link"}},
            "return": True,
        }
    ]
    document = flow(
        steps,
        parameters={"type": "object", "properties": {"magic_link": {"writeOnly": True}}},
    )

    class Echoing(FakeActions):
        def write(self, session_id, **kwargs):
            self.calls.append(("write", session_id, kwargs))
            return {"value": kwargs["text"], "url": "u", "title": "Welcome"}

    secret = "https://example.test/login?token=abc123"
    report = run(Echoing(), document, "b", params={"magic_link": secret})
    assert report["steps"][0]["result"]["value"] is None
    assert "abc123" not in str(report)


def test_a_guarded_value_never_reaches_the_top_level_report():
    """Which is what sessions.touch stores — a secret URL in Redis outlives the
    run, and a later reopen would navigate straight back to it."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#p", "value_from": {"param": "magic_link"}},
        }
    ]
    document = flow(
        steps,
        parameters={"type": "object", "properties": {"magic_link": {"writeOnly": True}}},
    )

    class Echoing(FakeActions):
        def write(self, session_id, **kwargs):
            self.calls.append(("write", session_id, kwargs))
            return {"value": kwargs["text"], "url": "u", "title": "Welcome"}

    report = run(Echoing(), document, "b", params={"magic_link": "zzz-secret"})
    # The whole report, not one field: nothing anywhere may carry it.
    assert "zzz-secret" not in str(report)


def test_a_guarded_value_is_scrubbed_out_of_an_error():
    """An action puts its arguments in its error text: upload_file bound to a
    guarded path raises `no file at <path>`."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#p", "value_from": {"param": "password"}},
        }
    ]
    document = flow(
        steps, parameters={"type": "object", "properties": {"password": {"writeOnly": True}}}
    )

    class Leaky(FakeActions):
        def write(self, session_id, **kwargs):
            raise ValueError(f"could not type {kwargs['text']} into #p")

    report = run(Leaky(), document, "b", params={"password": "hunter2"})
    assert "hunter2" not in str(report)
    assert "<hidden>" in report["steps"][0]["error"]
    # And the rest of the message survives, so the failure is still diagnosable.
    assert "could not type" in report["steps"][0]["error"]


def test_a_failed_page_read_does_not_reintroduce_a_guarded_value():
    steps = [
        {
            "tool": "write",
            "params": {"css": "#p", "value_from": {"param": "magic_link"}},
        }
    ]
    document = flow(
        steps,
        parameters={"type": "object", "properties": {"magic_link": {"writeOnly": True}}},
    )
    secret = "abc123"

    class Failing(FakeActions):
        def page(self, session_id):
            # Typing into a search box lands you on `?q=<what you typed>`, so
            # the page a bound write failed on can carry the value back.
            return {"url": f"https://example.test/?q={secret}", "title": "x"}

        def write(self, session_id, **kwargs):
            raise RuntimeError("timed out")

    report = run(Failing(), document, "b", params={"magic_link": secret})
    assert secret not in str(report)
    # And the rest of the page is still reported, so the failure is diagnosable.
    assert "example.test" in str(report)


def test_an_after_step_callback_sees_every_successful_step():
    seen = []
    run(FakeActions(), flow(SIMPLE), "b", after_step=lambda tool, r: seen.append(tool))
    assert seen == ["navigate", "write", "interact"]


def test_a_failing_step_does_not_fire_the_callback():
    seen = []
    run(
        FakeActions(fail_on={"write"}),
        flow(SIMPLE),
        "b",
        after_step=lambda tool, r: seen.append(tool),
    )
    assert seen == ["navigate"]


def test_a_guarded_value_does_not_come_back_under_an_unmapped_name():
    """An action can answer under a name nobody mapped. The named map handles
    the fields we know; the sweep handles the ones we do not."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#p", "value_from": {"param": "snippet"}},
            "return": True,
        }
    ]
    document = flow(
        steps, parameters={"type": "object", "properties": {"snippet": {"writeOnly": True}}}
    )

    class Echoing(FakeActions):
        def write(self, session_id, **kwargs):
            self.calls.append(("write", session_id, kwargs))
            # An unmapped name, which is the case the sweep exists for.
            return {"echoed": kwargs["text"], "url": "u", "title": "t"}

    report = run(Echoing(), document, "b", params={"snippet": "return 'sekrit'"})
    assert "sekrit" not in str(report)


def test_a_guarded_value_is_swept_out_of_any_field_at_all():
    """The named map is a list somebody has to remember to extend. For a
    guarded step every string in the result is swept, so a field nobody mapped
    cannot carry the value out."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#p", "value_from": {"param": "magic"}},
            "return": True,
        }
    ]
    document = flow(
        steps, parameters={"type": "object", "properties": {"magic": {"writeOnly": True}}}
    )

    class Nested(FakeActions):
        def write(self, session_id, **kwargs):
            self.calls.append(("write", session_id, kwargs))
            # A field nobody mapped, nested, carrying the value.
            return {"ok": True, "meta": {"seen": [kwargs["text"]]}, "title": "t"}

    report = run(Nested(), document, "b", params={"magic": "https://x.test/?t=zzz"})
    assert "zzz" not in str(report)


def test_a_flow_saved_in_the_old_format_is_refused_with_the_fix():
    """`valueFrom` was a step key before it became a parameter. A stored flow
    from then would silently lose its binding — a missing value, or a literal
    one used in its place — so it is refused rather than half-run."""
    steps = [
        {
            "tool": "write",
            "params": {"css": "#p"},
            "valueFrom": {"secret": {"name": "n", "key": "password"}},
        }
    ]
    actions = FakeActions()
    report = run(actions, flow(steps), "b")
    assert report["status"] == "failed"
    assert "older format" in report["steps"][0]["error"]
    assert "value_from" in report["steps"][0]["error"]
    assert actions.calls == []


def test_a_stored_step_binding_a_secret_and_a_url_is_refused_at_run_time():
    """Saving refuses this, and saving is not the only way a document gets
    here: the store reads YAML somebody may have written by hand."""
    steps = [
        {
            "tool": "write",
            "params": {
                "css": "#p",
                "url": "https://evil.test/",
                "value_from": {"secret": {"name": "n", "key": "password"}},
            },
        }
    ]
    actions = FakeActions()
    report = run(actions, flow(steps), "b")
    assert report["status"] == "failed"
    assert "may not also navigate" in report["steps"][0]["error"]
    assert actions.calls == []
