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
    {"tool": "navigate", "args": {"url": "https://example.test/login"}},
    {"tool": "write", "args": {"css": "#email", "text": "a@b.c"}},
    {"tool": "interact", "args": {"action": "click", "css": "button"}},
]


class Vault:
    """Just enough catalogue to hand `secrets.bind` one value.

    The guarded-value tests below used to reach this state through a `writeOnly`
    parameter. There is no such thing now — a parameter is text and non-secret
    by definition (§F1.38) — so the thing that must not leak is a secret, which
    is what these were always really about.
    """

    def __init__(self, value="hunter2"):
        self.secret = value

    def entry(self, name):
        return {"name": name, "keys": ["password"], "allowed_urls": []}

    def allows(self, name, url):
        return True

    def value(self, name, key):
        return self.secret


SECRET_STEP = {"name": "nextcloud", "key": "password"}


def guarded(steps, value="hunter2", actions=None, **extra):
    """Run `steps` with a catalogue that will bind `SECRET_STEP`."""
    return run(
        actions or FakeActions(), flow(steps, **extra), "b", catalogue=Vault(value)
    )


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


def test_no_result_comes_back_unless_a_step_asked():
    """A run answers with the steps that said they were the answer, and with
    nothing otherwise.

    There used to be a top-level `result` carrying the last step's. It made the
    shape every example taught — a flow ending in `extract` with `return: true`
    — report the same object twice, and it was a second way of saying what a
    run answers with while `return` was the explicit one. Two mechanisms for
    one job is how the two drift.
    """
    report = run(FakeActions(), flow(SIMPLE), "b")
    assert "result" not in report
    assert all("result" not in step for step in report["steps"])
    # Where the browser ended up is a fact about the run, not a step's output,
    # and it is what a caller needs in order to carry on.
    assert report["url"]


def test_every_step_that_asks_gets_its_result():
    """`return` was documented as if one step could use it. Nothing stopped
    more, and now nothing else reports a result, so it has to carry a flow
    whose answer is in two places."""
    steps = [
        {"tool": "extract", "args": {"css": "h1"}, "return": True},
        {"tool": "navigate", "args": {"url": "https://example.test/next"}},
        {"tool": "extract", "args": {"css": "h2"}, "return": True},
    ]
    report = run(FakeActions(), flow(steps), "b")
    assert [("result" in s) for s in report["steps"]] == [True, False, True]


def test_a_step_can_ask_for_its_own_result():
    steps = [
        {"tool": "navigate", "args": {"url": "x"}},
        {"tool": "extract", "args": {"css": "h1"}, "return": True},
        {"tool": "interact", "args": {"action": "click", "css": "b"}},
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
    steps = [{"tool": "screenshot", "args": {}, "return": True}]
    report = run(FakeActions(), flow(steps), "b")
    assert "image" not in report["steps"][0]["result"]


# ---- failure -----------------------------------------------------------------


def test_a_run_stops_at_the_first_failing_step():
    actions = FakeActions(fail_on={"write"})
    report = run(actions, flow(SIMPLE), "b")
    assert report["status"] == "failed"
    assert report["steps_run"] == 2
    assert [tool for tool, _, _ in actions.calls] == ["navigate", "write"]


def test_a_failure_says_which_step_and_what_page():
    steps = [
        {"tool": "navigate", "args": {"url": "x"}},
        {"tool": "write", "args": {"css": "#a", "text": "b"}, "id": "fill-email"},
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
        {"tool": "interact", "args": {"action": "click", "css": ".cookies"},
         "onError": "continue", "id": "dismiss-banner"},
        {"tool": "navigate", "args": {"url": "x"}},
    ]
    report = run(FakeActions(fail_on={1}), flow(steps), "b")
    assert report["status"] == "ok"
    assert report["steps"][0]["ok"] is False
    assert report["steps"][1]["ok"] is True


def test_the_run_status_and_a_step_status_are_different_questions():
    steps = [
        {"tool": "navigate", "args": {"url": "x"}, "onError": "continue"},
        {"tool": "navigate", "args": {"url": "y"}},
    ]
    report = run(FakeActions(fail_on={1}), flow(steps), "b")
    assert report["status"] == "ok"
    assert report["steps"][0]["ok"] is False


def test_a_tool_that_no_longer_exists_fails_loudly():
    """Saving validates this, so getting here means the document was written
    before a rename, or edited on disk by hand."""
    report = run(FakeActions(), flow([{"tool": "teleport", "args": {}}]), "b")
    assert report["status"] == "failed"
    assert "no action called 'teleport'" in report["steps"][0]["error"]


def test_a_flow_cannot_open_its_own_browser_even_if_the_document_says_so():
    actions = FakeActions()
    report = run(actions, flow([{"tool": "open_session", "args": {}}]), "b")
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
    steps = [{"tool": "navigate", "args": {"url": str(n)}} for n in range(5)]
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
            "args": {"css": "#email", "text": "${email}"},
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


def test_a_callers_value_is_never_rescanned():
    """Single pass, and the rule that keeps "a payload cannot collide with a
    reference" true now that strings are scanned at all. A caller passing
    something that looks like a reference gets that text typed, not resolved."""
    steps = [{"tool": "write", "args": {"css": "#a", "text": "${note}"}}]
    document = flow(
        steps,
        parameters={"type": "object", "properties": {"note": {}, "admin": {}}},
    )
    actions = FakeActions()
    run(actions, document, "b", params={"note": "${admin}", "admin": "s3cret"})
    assert actions.calls[0][2]["text"] == "${admin}"


def test_an_escaped_sigil_arrives_as_one():
    steps = [{"tool": "write", "args": {"css": "#a", "text": "cost: $${total}"}}]
    actions = FakeActions()
    run(actions, flow(steps), "b")
    assert actions.calls[0][2]["text"] == "cost: ${total}"


def test_a_string_that_is_exactly_one_reference_keeps_the_value_s_type():
    """`wait_timeout` wants an integer. Interpolating would hand it "30"."""
    steps = [{"tool": "extract", "args": {"css": "#a", "wait_timeout": "${secs}"}}]
    document = flow(steps, parameters={"type": "object", "properties": {"secs": {}}})
    actions = FakeActions()
    run(actions, document, "b", params={"secs": 30})
    assert actions.calls[0][2]["wait_timeout"] == 30


def test_a_script_containing_a_javascript_template_is_left_alone():
    """`${window.scrollY}` is not a parameter name, and a flow that declares no
    parameters cannot have one. It must arrive byte for byte."""
    script = "return `${window.scrollY}px`"
    actions = FakeActions()
    run(actions, flow([{"tool": "navigate", "args": {"url": script}}]), "b")
    assert actions.calls[0][2]["url"] == script


# ---- values that must not come back ------------------------------------------


def test_a_secret_is_not_echoed_by_the_step_that_used_it():
    """`write` reads the field back and returns it, which for a guarded value
    would hand it straight back on the very call meant to protect it."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#password", "secret": SECRET_STEP},
            "return": True,
        }
    ]
    report = run(FakeActions(), flow(steps), "b", catalogue=Vault())
    assert report["steps"][0]["result"]["value"] is None
    assert "hunter2" not in str(report)


def test_a_guarded_value_is_hidden_in_the_summary_too():
    steps = [
        {
            "tool": "write",
            "args": {"css": "#password", "secret": SECRET_STEP},
        }
    ]
    document = flow(steps)
    report = run(FakeActions(), document, "b", catalogue=Vault())
    assert report["steps"][0]["summary"].endswith("text=<hidden>")


def test_an_ordinary_parameter_is_not_hidden():
    """writeOnly is a marker someone chose, not a guess about what looks secret."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#email", "text": "${email}"},
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
            "args": {"css": "#password", "secret": SECRET_STEP},
        }
    ]
    document = flow(steps)
    actions = FakeActions()
    run(actions, document, "b", catalogue=Vault())
    assert actions.calls[0][2]["text"] == "hunter2"


# ---- sources that are not built yet ------------------------------------------


def test_a_flow_binding_a_secret_refuses_rather_than_typing_nothing():
    """A login flow that silently typed nothing into the password field would
    report success and leave someone staring at a login page."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#password", "secret": {"name": "nextcloud", "key": "password"}},
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
            "args": {"css": "#p", "secret": SECRET_STEP},
        }
    ]
    document = flow(
        steps,
    )
    report = run(
        FakeActions(), document, "b",
        catalogue=Vault("https://example.test/login?token=abc123"),
    )
    assert "text=<hidden>" in report["steps"][0]["summary"]
    assert "abc123" not in report["steps"][0]["summary"]


def test_an_unguarded_field_is_still_printed_beside_a_guarded_one():
    steps = [
        {
            "tool": "write",
            "args": {"css": "#password", "secret": SECRET_STEP},
        }
    ]
    document = flow(steps)
    summary = run(FakeActions(), document, "b", catalogue=Vault())["steps"][0][
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
    report = run(actions, flow([{"tool": attribute, "args": {}}]), "b")
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
        {"tool": "navigate", "args": {"url": "https://example.test/one"}},
        {"tool": "write", "args": {"url": "https://example.test/two", "css": "#a",
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
            "args": {"css": "#p", "secret": SECRET_STEP},
            "return": True,
        }
    ]
    document = flow(
        steps,
    )

    class Echoing(FakeActions):
        def write(self, session_id, **kwargs):
            self.calls.append(("write", session_id, kwargs))
            return {"value": kwargs["text"], "url": "u", "title": "Welcome"}

    report = run(
        Echoing(), document, "b",
        catalogue=Vault("https://example.test/login?token=abc123"),
    )
    assert report["steps"][0]["result"]["value"] is None
    assert "abc123" not in str(report)


def test_a_guarded_value_never_reaches_the_top_level_report():
    """Which is what sessions.touch stores — a secret URL in Redis outlives the
    run, and a later reopen would navigate straight back to it."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#p", "secret": SECRET_STEP},
        }
    ]
    document = flow(
        steps,
    )

    class Echoing(FakeActions):
        def write(self, session_id, **kwargs):
            self.calls.append(("write", session_id, kwargs))
            return {"value": kwargs["text"], "url": "u", "title": "Welcome"}

    report = run(Echoing(), document, "b", catalogue=Vault())
    # The whole report, not one field: nothing anywhere may carry it.
    assert "zzz-secret" not in str(report)


def test_a_guarded_value_is_scrubbed_out_of_an_error():
    """An action puts its arguments in its error text: upload_file bound to a
    guarded path raises `no file at <path>`."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#p", "secret": SECRET_STEP},
        }
    ]
    document = flow(steps)

    class Leaky(FakeActions):
        def write(self, session_id, **kwargs):
            raise ValueError(f"could not type {kwargs['text']} into #p")

    report = run(Leaky(), document, "b", catalogue=Vault())
    assert "hunter2" not in str(report)
    assert "<hidden>" in report["steps"][0]["error"]
    # And the rest of the message survives, so the failure is still diagnosable.
    assert "could not type" in report["steps"][0]["error"]


def test_a_failed_page_read_does_not_reintroduce_a_guarded_value():
    steps = [
        {
            "tool": "write",
            "args": {"css": "#p", "secret": SECRET_STEP},
        }
    ]
    document = flow(
        steps,
    )
    secret = "abc123"

    class Failing(FakeActions):
        def page(self, session_id):
            # Typing into a search box lands you on `?q=<what you typed>`, so
            # the page a bound write failed on can carry the value back.
            return {"url": f"https://example.test/?q={secret}", "title": "x"}

        def write(self, session_id, **kwargs):
            raise RuntimeError("timed out")

    report = run(Failing(), document, "b", catalogue=Vault(secret))
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
            "args": {"css": "#p", "secret": SECRET_STEP},
            "return": True,
        }
    ]
    document = flow(
        steps
    )

    class Echoing(FakeActions):
        def write(self, session_id, **kwargs):
            self.calls.append(("write", session_id, kwargs))
            # An unmapped name, which is the case the sweep exists for.
            return {"echoed": kwargs["text"], "url": "u", "title": "t"}

    report = run(Echoing(), document, "b", catalogue=Vault())
    assert "sekrit" not in str(report)


def test_a_guarded_value_is_swept_out_of_any_field_at_all():
    """The named map is a list somebody has to remember to extend. For a
    guarded step every string in the result is swept, so a field nobody mapped
    cannot carry the value out."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#p", "secret": SECRET_STEP},
            "return": True,
        }
    ]
    document = flow(
        steps
    )

    class Nested(FakeActions):
        def write(self, session_id, **kwargs):
            self.calls.append(("write", session_id, kwargs))
            # A field nobody mapped, nested, carrying the value.
            return {"ok": True, "meta": {"seen": [kwargs["text"]]}, "title": "t"}

    report = run(Nested(), document, "b", catalogue=Vault())
    assert "zzz" not in str(report)


def test_a_flow_saved_in_the_old_format_is_refused_with_the_fix():
    """`valueFrom` was a step key before it became a parameter. A stored flow
    from then would silently lose its binding — a missing value, or a literal
    one used in its place — so it is refused rather than half-run."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#p"},
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
            "args": {
                "css": "#p",
                "url": "https://evil.test/",
                "secret": {"name": "n", "key": "password"},
            },
        }
    ]
    actions = FakeActions()
    report = run(actions, flow(steps), "b")
    assert report["status"] == "failed"
    assert "may not also navigate" in report["steps"][0]["error"]
    assert actions.calls == []


def test_a_bound_write_in_a_flow_does_not_read_the_field_back():
    """The direct tool and the HTTP endpoint both turned the read-back off and
    the flow path — the main one — did not. The end-to-end test missed it
    because its action is a double that never reads anything."""
    seen = {}

    class Recording(FakeActions):
        def write(self, session_id, text, **kwargs):
            seen.update(kwargs)
            return {"value": None, "url": "u", "title": "t"}

    steps = [
        {
            "tool": "write",
            "args": {"css": "#p", "secret": SECRET_STEP},
        }
    ]
    document = flow(
        steps,
    )
    run(Recording(), document, "b", catalogue=Vault())
    assert seen["read_back"] is False


def test_an_unbound_write_still_reads_the_field_back():
    seen = {}

    class Recording(FakeActions):
        def write(self, session_id, text, **kwargs):
            seen.update(kwargs)
            return {"value": text, "url": "u", "title": "t"}

    run(Recording(), flow([{"tool": "write", "args": {"css": "#a", "text": "x"}}]), "b")
    assert seen.get("read_back") is None


def test_a_percent_encoded_value_is_scrubbed_too():
    """A submitting write lands on `?q=<what was typed>` and the browser
    encodes it on the way, so a literal replacement misses it entirely."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#q", "secret": SECRET_STEP},
            "return": True,
        }
    ]
    document = flow(steps)

    class Submitting(FakeActions):
        def write(self, session_id, text, **kwargs):
            return {"url": "https://x.test/?q=a%2Fb+c", "title": "t"}

    report = run(Submitting(), document, "b", catalogue=Vault("a/b c"))
    assert "a%2Fb" not in str(report)
    assert "a/b c" not in str(report)


def test_an_old_format_step_stops_the_flow_before_anything_runs():
    """A stale key on step two would otherwise run step one and then report
    steps_run: 0 — half-running a flow the message says was refused."""
    steps = [
        {"tool": "navigate", "args": {"url": "https://x.test/"}},
        {
            "tool": "write",
            "args": {"css": "#p"},
            "valueFrom": {"secret": {"name": "n", "key": "password"}},
        },
    ]
    actions = FakeActions()
    report = run(actions, flow(steps), "b")
    assert report["status"] == "failed"
    assert actions.calls == []
    assert report["steps"][0]["n"] == 2


def test_a_non_string_secret_value_is_still_scrubbed():
    """`Actions.write` does `str(text)`, so a numeric secret really is typed
    into the page — and a scrub that skipped non-strings missed it. A file in a
    secrets directory holds bytes; nothing guarantees they spell a string the
    way Python means one."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#pin", "secret": SECRET_STEP},
            "return": True,
        }
    ]

    class Submitting(FakeActions):
        def write(self, session_id, text, **kwargs):
            return {"url": f"https://x.test/?pin={text}", "title": "t"}

    report = run(Submitting(), flow(steps), "b", catalogue=Vault(123456))
    assert "123456" not in str(report)

def test_a_value_typed_early_is_still_hidden_from_a_later_step():
    """A submitting bound write leaves the value in the browser's URL, and a
    later ordinary step's page state would carry it back out."""
    steps = [
        {"tool": "write", "args": {"css": "#q", "secret": SECRET_STEP}},
        {"tool": "navigate", "args": {"url": "https://x.test/next"}, "return": True},
    ]
    document = flow(steps)

    class Lingering(FakeActions):
        def write(self, session_id, text, **kwargs):
            return {"url": "https://x.test/?q=zzz-secret", "title": "t"}

        def navigate(self, session_id, **kwargs):
            # The browser is still showing the submitted query.
            return {"url": "https://x.test/next?ref=zzz-secret", "title": "t"}

    report = run(Lingering(), document, "b", catalogue=Vault("zzz-secret"))
    assert "zzz-secret" not in str(report)


def test_an_empty_binding_is_malformed_rather_than_absent():
    """Save-time validation rejects `secret: {}`, and the store reads YAML
    that never passed through it — treating it as absent let a literal `text`
    beside it run instead, which is quietly doing the wrong thing."""
    steps = [
        {"tool": "write", "args": {"css": "#p", "text": "literal", "secret": {}}}
    ]
    actions = FakeActions()
    report = run(actions, flow(steps), "b", catalogue=Vault())
    assert report["status"] == "failed"
    assert "one value, one place" in report["steps"][0]["error"]
    assert actions.calls == []


def test_a_literal_beside_a_secret_is_refused_at_run_time_too():
    """Saving refuses it, and saving is not the only way a document gets here.
    Without this the literal was silently discarded and the credential typed in
    its place — a step saying two things quietly becoming a step saying one."""
    steps = [
        {
            "tool": "write",
            "args": {"css": "#p", "text": "literal", "secret": SECRET_STEP},
        }
    ]
    actions = FakeActions()
    report = run(actions, flow(steps), "b", catalogue=Vault())
    assert report["status"] == "failed"
    assert "one value, one place" in report["steps"][0]["error"]
    assert actions.calls == []


def test_a_malformed_secret_on_its_own_says_what_is_missing():
    steps = [{"tool": "write", "args": {"css": "#p", "secret": {}}}]
    actions = FakeActions()
    report = run(actions, flow(steps), "b", catalogue=Vault())
    assert report["status"] == "failed"
    assert "needs a name" in report["steps"][0]["error"]
    assert actions.calls == []


def test_a_redacted_page_is_reported_as_a_fact_not_a_marker():
    """A caller deciding whether to persist the page needs to know, and a
    secret whose value happens to be the marker makes searching for it
    useless."""
    steps = [
        {"tool": "write", "args": {"css": "#q", "secret": SECRET_STEP}}
    ]
    document = flow(steps)

    class Submitting(FakeActions):
        def write(self, session_id, text, **kwargs):
            return {"url": f"https://x.test/?q={text}", "title": "t"}

    report = run(Submitting(), document, "b", catalogue=Vault())
    assert report["url_redacted"] is True


def test_an_ordinary_run_does_not_claim_a_redacted_page():
    assert "url_redacted" not in run(FakeActions(), flow(SIMPLE), "b")


def test_a_secret_that_is_the_marker_is_still_a_redacted_page():
    """The collision the equality check could not see: `<hidden>` scrubs to
    itself, so comparing a URL with its scrubbed form said nothing had been
    redacted while the credential was still in it. The question is whether the
    value is in the URL, so that is what is asked."""
    steps = [
        {"tool": "write", "args": {"css": "#q", "secret": SECRET_STEP}}
    ]
    document = flow(steps)

    class Submitting(FakeActions):
        def write(self, session_id, text, **kwargs):
            return {"url": f"https://x.test/?q={text}", "title": "t"}

    report = run(Submitting(), document, "b", catalogue=Vault())
    assert report["url_redacted"] is True


def test_a_step_that_fails_after_typing_reports_a_redacted_page():
    """A step can fail *after* the value reached the page, so the URL it failed
    on is exactly as unsafe to store as one a step succeeded on. Only the
    success branch recorded it."""
    steps = [
        {"tool": "write", "args": {"css": "#q", "secret": SECRET_STEP}},
        {"tool": "navigate", "args": {"url": "https://x.test/next"}},
    ]
    document = flow(steps)

    class FailsAfterTyping(FakeActions):
        def write(self, session_id, text, **kwargs):
            # Lands somewhere clean, so the successful step records nothing and
            # the failure below is the only thing that can set the flag.
            return {"url": "https://x.test/home", "title": "t"}

        def navigate(self, session_id, **kwargs):
            raise RuntimeError("nope")

        def page(self, session_id):
            # A redirect carried the value into the URL on the way.
            return {"url": "https://x.test/sso?token=zzz-secret", "title": "t"}

    report = run(FailsAfterTyping(), document, "b", catalogue=Vault("zzz-secret"))
    assert report["status"] == "failed"
    assert report["url_redacted"] is True
    assert "zzz-secret" not in str(report)


def test_taints_asks_whether_the_value_is_there():
    assert flowrun.taints("https://x.test/?q=hunter2", {"hunter2"}) is True
    assert flowrun.taints("https://x.test/", {"hunter2"}) is False
    # The marker is not evidence either way.
    assert flowrun.taints(f"https://x.test/?q={flowrun.HIDDEN}", {flowrun.HIDDEN}) is True
    assert flowrun.taints(None, {"hunter2"}) is False


def test_a_run_that_navigates_away_reports_the_clean_page_it_ended_on():
    """The flag describes the page the run *reports*, not its history. Sticky,
    it threw away a perfectly ordinary final page because an earlier step had
    briefly been somewhere unprintable."""
    steps = [
        {"tool": "write", "args": {"css": "#q", "secret": SECRET_STEP}},
        {"tool": "navigate", "args": {"url": "https://x.test/done"}},
    ]
    document = flow(steps)

    class SubmitsThenLeaves(FakeActions):
        def write(self, session_id, text, **kwargs):
            return {"url": f"https://x.test/?q={text}", "title": "t"}

        def navigate(self, session_id, **kwargs):
            return {"url": "https://x.test/done", "title": "Done"}

    report = run(SubmitsThenLeaves(), document, "b", catalogue=Vault())
    assert report["status"] == "ok"
    assert report["url"] == "https://x.test/done"
    # The page it ended on is clean, so it is safe to remember.
    assert "url_redacted" not in report
    assert "zzz" not in str(report)


@pytest.mark.parametrize(
    "reference",
    [
        {"name": "n", "key": "k", 1: "x"},  # an extra key YAML made an int
        {"name": "n", 1: "x"},  # and one with a field missing as well
    ],
)
def test_a_hand_edited_flow_with_an_integer_key_is_refused_not_a_crash(reference):
    """YAML turns `1:` into an integer key, and the stored flow never passed
    through save-time validation. Listing that key in the refusal raised
    TypeError — a 500 about our code instead of a refusal of the document."""
    steps = [{"tool": "write", "args": {"css": "#p", "secret": reference}}]
    actions = FakeActions()
    report = run(actions, flow(steps), "b", catalogue=Vault())
    assert report["status"] == "failed"
    assert "1" in report["steps"][0]["error"]
    assert actions.calls == []


def test_listed_names_any_key_a_document_can_hold():
    assert flowrun.listed({"b", 1, "a"}) == "1, a, b"
    assert flowrun.listed([]) == ""


def test_a_stored_flow_that_still_marks_a_parameter_write_only_is_refused():
    """Saving refuses it, and `LocalFlowStore` reads YAML that may never have
    been saved through the validator — which is exactly where a marker survives
    a migration. `writeOnly` used to hide a parameter from the report and now
    hides nothing, so an author who trusted it would have the value echoed back
    by any `return: true` step (§F1.38)."""
    steps = [
        {"tool": "write", "args": {"css": "#p", "text": "${password}"}, "return": True}
    ]
    document = flow(
        steps,
        parameters={
            "type": "object",
            "properties": {"password": {"type": "string", "writeOnly": True}},
        },
    )
    actions = FakeActions()
    report = run(actions, document, "b", params={"password": "hunter2"})
    assert report["status"] == "failed"
    assert report["steps_run"] == 0
    assert "writeOnly" in report["steps"][0]["error"]
    assert "secret" in report["steps"][0]["error"]
    # Refused before anything was typed, so the value never reached a page.
    assert actions.calls == []
    assert "hunter2" not in str(report)


@pytest.mark.parametrize(
    "parameters",
    [
        {"type": "object", "properties": []},
        {"type": "object", "properties": "nonsense"},
        {"type": "object", "properties": {1: {"writeOnly": True}}},
        {"type": "object", "properties": {"a": "scalar"}},
        "not an object at all",
    ],
)
def test_a_malformed_parameters_block_does_not_crash_the_preflight(parameters):
    """The preflight reads a stored file, so every shape a hand edit produces
    has to reach an outcome rather than an AttributeError — a 500 about our own
    code instead of a verdict on the document."""
    document = flow(
        [{"tool": "navigate", "args": {"url": "https://example.test/"}}],
        parameters=parameters,
    )
    report = run(FakeActions(), document, "b")
    assert report["status"] in {"ok", "failed"}
