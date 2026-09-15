"""`assert`: JavaScript that must come back true, or the run fails saying why.

A flow had no way to state what must be true, so one came back 8/8 green while
sitting on the wrong page — the worst thing this server can do, because a loud
failure costs a turn and a confident green costs ten. See saga §F2.5.
"""

import pytest

from kubed.selenium_flow import actions as actions_module
from kubed.selenium_flow.errors import status_for
from kubed.selenium_flow.routes import ENDPOINTS, method_for

pytestmark = pytest.mark.unit


class _Driver:
    """A driver whose script returns each answer in turn, then the last."""

    current_url = "https://example.test/dashboard"
    title = "Dashboard"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def execute_script(self, script, *args):
        self.calls += 1
        if len(self.answers) > 1:
            return self.answers.pop(0)
        return self.answers[0]


@pytest.fixture
def driving(actions, monkeypatch):
    """Point the actions at a scripted driver."""

    def use(*answers):
        driver = _Driver(*answers)
        monkeypatch.setattr(actions, "_at", lambda *a, **k: driver)
        return driver

    return use


# ---- the surface ------------------------------------------------------------


def test_assert_is_a_browser_action_on_both_surfaces():
    """A step is a tool is an endpoint — the promise test_surfaces.py holds."""
    assert ENDPOINTS["assert"] == "assert"


def test_the_keyword_is_aliased_in_exactly_one_place(actions):
    """`assert` cannot name a Python method, so the tool and the route say
    `assert` and the method is `assert_`. One alias, or the two surfaces drift."""
    assert method_for("assert") == "assert_"
    assert method_for("extract") == "extract"
    assert callable(getattr(actions, method_for("assert")))


async def test_the_tool_publishes_script_message_and_a_wait(server):
    schema = (await server.mcp.get_tool("assert")).parameters
    assert {"script", "message", "wait_timeout"} <= set(schema["properties"])
    assert schema["required"] == ["script"]


# ---- what it does -----------------------------------------------------------


def test_true_passes_and_says_where_it_was_true(actions, driving):
    driver = driving(True)
    result = actions.assert_("abc", "return true")
    assert result["asserted"] is True
    assert result["url"] == driver.current_url
    assert driver.calls == 1


def test_false_fails_with_the_authors_message(actions, driving):
    driving(False)
    with pytest.raises(actions_module.AssertionFailed) as failed:
        actions.assert_("abc", "return false", message="Already signed in.", wait_timeout=0)
    assert "Already signed in." in str(failed.value)


def test_false_without_a_message_names_the_page_and_asks_for_one(actions, driving):
    driving(False)
    with pytest.raises(actions_module.AssertionFailed) as failed:
        actions.assert_("abc", "return 1 > 2", wait_timeout=0)
    message = str(failed.value)
    assert "https://example.test/dashboard" in message
    assert "message" in message, "it should ask the author to write one"


def test_the_failure_never_echoes_the_script(actions, driving):
    """The script is the author's text, but it can carry a literal a report must
    not: a token compared inline, a serialised body. `SAFE_IN_SUMMARY` already
    keeps `script` out of run summaries, and a failure is read in more places
    than a summary — the HTTP error, the flow report, the log (Copilot, #26)."""
    driving(False)
    script = "return document.cookie.includes('s3cret-token')"
    with pytest.raises(actions_module.AssertionFailed) as failed:
        actions.assert_("abc", script, wait_timeout=0)
    assert "s3cret-token" not in str(failed.value)
    assert script not in str(failed.value)


def test_it_waits_for_the_page_to_catch_up(actions, driving):
    """The wrong-page green again: a route change lands a few frames after the
    click that caused it, so an assertion that only looked once would be the
    same race one step later. Polled, like every element wait here — no sleep."""
    driver = driving(False, False, True)
    assert actions.assert_("abc", "return ready", wait_timeout=5)["asserted"] is True
    assert driver.calls == 3


@pytest.mark.parametrize(
    "answer", [None, 0, 1, "true", "", [], {}, ["x"]]
)
def test_anything_but_a_boolean_is_refused(actions, driving, answer):
    """Truthiness is how an assertion passes by accident: an element, a string,
    an empty list. The expression has to answer the question it was asked."""
    driving(answer)
    with pytest.raises(ValueError, match="true or false"):
        actions.assert_("abc", "return document.querySelector('#x')", wait_timeout=0)


def test_a_failed_assertion_is_the_callers_to_fix(actions, driving):
    """400, beside a bad selector: retrying the identical request fails again."""
    driving(False)
    with pytest.raises(actions_module.AssertionFailed) as failed:
        actions.assert_("abc", "return false", wait_timeout=0)
    assert status_for(failed.value) == 400


def test_a_broken_expression_is_an_error_not_a_false(actions, monkeypatch):
    """Found while flying it: asking a `data:` page for `document.cookie` throws.
    A script that cannot run has not answered the question, and swallowing that
    as "false" would report the page is wrong when the assertion is."""
    from selenium.common.exceptions import JavascriptException

    class _Broken:
        current_url = "https://example.test/"
        title = "t"

        def execute_script(self, *_):
            raise JavascriptException("Cookies are disabled inside 'data:' URLs")

    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Broken())
    with pytest.raises(JavascriptException):
        actions.assert_("abc", "return !!document.cookie", wait_timeout=0)


# ---- through a real run, not the action alone -------------------------------


LOGIN_GUARD = {
    "description": "Sign in",
    "steps": [
        {"tool": "navigate", "args": {"url": "https://app.example.test/login"}},
        {
            "tool": "assert",
            "args": {
                "script": "return !!document.querySelector('#login-username')",
                "message": "Already signed in - you don't need to run this flow.",
                "wait_timeout": 5,
            },
        },
        {"tool": "write", "args": {"selector": {"css": "#login-username"}, "text": "someone"}},
    ],
}


class _AlreadySignedIn:
    """A browser that redirected to the dashboard, so the form never appears."""

    def navigate(self, session_id, **kwargs):
        return {"url": kwargs.get("url"), "title": "Login"}

    def assert_(self, session_id, script, message=None, **kwargs):
        raise actions_module.AssertionFailed(message)

    def write(self, session_id, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the flow should have stopped at the assertion")

    def page(self, session_id):
        return {"url": "https://app.example.test/dashboard", "title": "Dashboard"}


def test_a_failing_assert_stops_the_run_and_carries_the_message():
    """The whole point, through `run()`: no new status, and the step's error is
    the sentence its author wrote (saga §F2.5)."""
    from kubed.selenium_flow.flowrun import run

    report = run(_AlreadySignedIn(), LOGIN_GUARD, "b")

    assert report["status"] == "failed"
    assert report["steps_run"] == 2, "the steps after the assertion must not run"
    failed = report["steps"][-1]
    assert failed["tool"] == "assert"
    assert failed["error"] == "Already signed in - you don't need to run this flow."


async def test_an_assert_cannot_be_continued_past(server):
    """`onError: continue` on an assertion is the confident green again: the run
    would carry on and report `ok` (Copilot, #26)."""
    from kubed.selenium_flow.flowdoc import InvalidFlow, step_schemas, validate
    from kubed.selenium_flow.routes import ENDPOINTS as ROUTES

    tools = {}
    for name in sorted(set(ROUTES.values())):
        tools[name] = (await server.mcp.get_tool(name)).parameters or {}

    document = {
        "description": "Sign in",
        "steps": [
            {
                "tool": "assert",
                "onError": "continue",
                "args": {"script": "return true"},
            }
        ],
    }
    with pytest.raises(InvalidFlow, match="cannot be continued past"):
        validate(document, step_schemas(tools))


def test_a_hand_edited_flow_cannot_continue_past_one_either():
    """Saving refuses the pairing, and a document edited on disk never passed
    through saving — so the runner refuses it too, rather than trusting it."""
    from kubed.selenium_flow.flowrun import run

    class _Acting:
        def __init__(self):
            self.wrote = False

        def assert_(self, session_id, script, message=None, **kwargs):
            raise actions_module.AssertionFailed(message or "false")

        def write(self, session_id, **kwargs):
            self.wrote = True
            return {"url": "https://example.test/", "title": "t"}

        def page(self, session_id):
            return {"url": "https://example.test/", "title": "t"}

    acting = _Acting()
    document = {
        "steps": [
            {
                "tool": "assert",
                "onError": "continue",
                "args": {"script": "return false"},
            },
            {"tool": "write", "args": {"selector": {"css": "#a"}, "text": "x"}},
        ]
    }
    report = run(acting, document, "b")
    assert report["status"] == "failed"
    assert acting.wrote is False, "the run carried on past a false assertion"


async def test_a_flow_with_an_assert_step_saves(server):
    """Save-time validation knows the tool, so an author finds a typo now."""
    from kubed.selenium_flow.flowdoc import step_schemas, validate
    from kubed.selenium_flow.routes import ENDPOINTS as ROUTES

    tools = {}
    for name in sorted(set(ROUTES.values())):
        tools[name] = (await server.mcp.get_tool(name)).parameters or {}
    assert validate(LOGIN_GUARD, step_schemas(tools))


# ---- what the refusal is allowed to say (Copilot, #26) ----------------------


def test_the_refusal_describes_the_shape_and_never_the_value(actions, driving):
    """An assertion can return anything the page holds — a cookie, an innerHTML,
    a token in a data attribute — and this text goes into a 400 and the log."""
    driving("session=s3cret-token; csrf=abc123")
    with pytest.raises(ValueError) as refused:
        actions.assert_("abc", "return document.cookie", wait_timeout=0)
    message = str(refused.value)
    assert "s3cret-token" not in message
    assert "a string of 33 characters" in message


@pytest.mark.parametrize(
    "answer,described",
    [
        (None, "null"),
        (["a", "b"], "an array of 2 items"),
        ({"a": 1}, "an object with 1 keys"),
        (3, "a number (int)"),
    ],
)
def test_every_refusal_says_what_kind_of_answer_it_got(
    actions, driving, answer, described
):
    driving(answer)
    with pytest.raises(ValueError, match=described.replace("(", r"\(").replace(")", r"\)")):
        actions.assert_("abc", "return whatever", wait_timeout=0)


def test_it_never_waits_longer_than_it_was_told(actions, monkeypatch):
    """The deadline was checked before the sleep but not before the next
    evaluation, so a fixed pause could carry the call past its timeout
    (Copilot, #26). On a fake clock, so the assertion is about the arithmetic
    rather than about how busy the machine running the test is."""
    now = {"t": 1000.0}
    slept = []
    driver = _Driver(False)
    monkeypatch.setattr(actions, "_at", lambda *a, **k: driver)
    # A poll that does not divide the timeout evenly, which is the only way the
    # overshoot shows: at 0.2 into 1s the old loop landed exactly on the
    # deadline and looked correct.
    monkeypatch.setattr(actions_module, "ASSERT_POLL", 0.3)
    monkeypatch.setattr(actions_module.time, "monotonic", lambda: now["t"])

    def fake_sleep(seconds):
        slept.append(seconds)
        now["t"] += seconds

    monkeypatch.setattr(actions_module.time, "sleep", fake_sleep)

    with pytest.raises(actions_module.AssertionFailed):
        actions.assert_("abc", "return false", wait_timeout=1)

    assert sum(slept) == pytest.approx(1), "it waited past its own deadline"
    assert all(nap <= 0.3 for nap in slept)
    # Evaluated at 0.0, 0.3, 0.6, 0.9 and 1.0 — the last one exactly on the
    # deadline, and none after it.
    assert driver.calls == 5


def test_an_answer_that_arrives_after_the_deadline_is_not_accepted(
    actions, monkeypatch
):
    """A sleep can wake late, and the loop evaluated again without re-checking.
    A page that turns true a moment after the caller stopped waiting must not
    pass (Copilot, late review on #26)."""
    now = {"t": 1000.0}
    driver = _Driver(False, True)  # false first, then true — but too late
    monkeypatch.setattr(actions, "_at", lambda *a, **k: driver)
    monkeypatch.setattr(actions_module.time, "monotonic", lambda: now["t"])
    # Oversleeps, the way a loaded machine does.
    monkeypatch.setattr(
        actions_module.time, "sleep", lambda seconds: now.update(t=now["t"] + 5)
    )

    with pytest.raises(actions_module.AssertionFailed):
        actions.assert_("abc", "return ready", wait_timeout=1)
    assert driver.calls == 1, "it evaluated again after the deadline had passed"


def test_no_wait_still_means_exactly_one_look(actions, driving):
    """The guard above must not cost the single evaluation wait_timeout=0
    promises."""
    driver = driving(True)
    assert actions.assert_("abc", "return true", wait_timeout=0)["asserted"] is True
    assert driver.calls == 1


# ---- stable_for: a guard needs an answer that HOLDS --------------------------


class _Clock:
    """A clock the test moves, so a hold is measured rather than waited out."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(actions_module.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(actions_module.time, "sleep", fake.sleep)
    return fake


async def test_the_tool_publishes_stable_for(server):
    schema = (await server.mcp.get_tool("assert")).parameters
    assert "stable_for" in schema["properties"]


def test_a_transient_true_does_not_satisfy_a_hold(actions, driving, clock):
    """The fault §F2.10 opened with. Navigating to `/`, a real app painted its
    authenticated shell for a moment before the auth guard redirected, and a
    guard asking until true caught that moment — so it passed while signed out.
    True, then false, has not held true for anything."""
    driving(True, False, False, False, False, False, False, False, False, False)
    with pytest.raises(actions_module.AssertionFailed) as failed:
        actions.assert_("abc", "return signedIn", wait_timeout=1, stable_for=0.5)
    assert "did not hold" in str(failed.value)


def test_an_answer_that_stays_true_passes_and_says_how_long(actions, driving, clock):
    driving(True)
    result = actions.assert_("abc", "return signedIn", wait_timeout=5, stable_for=0.5)
    assert result["asserted"] is True
    assert result["stable_for"] == 0.5


def test_the_hold_restarts_rather_than_accumulating(actions, driving, clock):
    """True for 0.2s, false, then true again must need another full 0.5s — a
    hold that added up would let a flicker satisfy it in pieces."""
    driver = driving(True, False, True, True, True, True, True, True)
    actions.assert_("abc", "return ready", wait_timeout=5, stable_for=0.5)
    # 1 true, 1 false, then the four polls (0.2s each) that make up the new hold.
    assert driver.calls >= 6


def test_without_a_hold_nothing_about_the_old_behaviour_changes(actions, driving):
    """The default is 0, and a bare assert still returns on the first true."""
    driver = driving(True)
    result = actions.assert_("abc", "return true")
    assert result["asserted"] is True
    assert "stable_for" not in result
    assert driver.calls == 1


def test_an_impossible_hold_is_refused_before_the_browser_is_touched(actions, monkeypatch):
    """`_at` reconnects and may NAVIGATE, so validating after it means a request
    that can never succeed moves the caller's browser and then answers 400. A
    rejected argument must cost nothing (Copilot, #31)."""
    touched = []
    monkeypatch.setattr(
        actions, "_at", lambda *a, **k: touched.append(1) or _Driver(True)
    )
    with pytest.raises(ValueError, match="seconds"):
        actions.assert_("abc", "return true", wait_timeout=5, stable_for=500,
                        url="https://elsewhere.test/")
    assert touched == [], "the browser must not have been reconnected or moved"


def test_a_hold_longer_than_the_wait_is_refused_naming_the_unit(actions, driving):
    """The footgun this argument brings: `stable_for: 500` read as milliseconds
    is 500 seconds, and an assertion that can never pass. Refused before the
    page is asked, with the unit in the message."""
    driving(True)
    with pytest.raises(ValueError, match="seconds"):
        actions.assert_("abc", "return true", wait_timeout=30, stable_for=500)


def test_a_failure_tells_never_true_apart_from_would_not_hold(actions, driving, clock):
    """Opposite fixes: one is a wrong assertion, the other a page still
    settling. A single message for both would send an author the wrong way."""
    driving(False)
    with pytest.raises(actions_module.AssertionFailed) as never:
        actions.assert_("abc", "return false", wait_timeout=1, stable_for=0.5)
    assert "did not hold" not in str(never.value)
    assert "assertion failed after" in str(never.value)


def test_an_answer_that_arrives_after_the_deadline_is_not_an_answer(actions, monkeypatch):
    """The script itself can run past `wait_timeout` — a blocking expression, a
    page that stops responding. The sleep-wake case was already guarded; this is
    the symmetric one, and without it a slow page passes an assertion it had
    already failed (Copilot, #31)."""

    class _Slow:
        current_url = "https://example.test/dashboard"
        title = "Dashboard"

        def __init__(self, clock):
            self.clock = clock
            self.calls = 0

        def execute_script(self, *_):
            self.calls += 1
            # First look is quick and false; the second takes 60s and is true.
            if self.calls > 1:
                self.clock.now += 60
            return self.calls > 1

    fake = _Clock()
    monkeypatch.setattr(actions_module.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(actions_module.time, "sleep", fake.sleep)
    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Slow(fake))

    with pytest.raises(actions_module.AssertionFailed):
        actions.assert_("abc", "return slow()", wait_timeout=5)


def test_the_single_look_a_zero_wait_promises_is_still_honoured(actions, monkeypatch):
    """`wait_timeout=0` asks exactly once, and any script takes longer than
    nothing — so the first evaluation cannot be subject to the deadline."""

    class _Slow:
        current_url = "https://example.test/"
        title = "t"

        def __init__(self, clock):
            self.clock = clock
            self.calls = 0

        def execute_script(self, *_):
            self.calls += 1
            self.clock.now += 3
            return True

    fake = _Clock()
    monkeypatch.setattr(actions_module.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(actions_module.time, "sleep", fake.sleep)
    driver = _Slow(fake)
    monkeypatch.setattr(actions, "_at", lambda *a, **k: driver)

    assert actions.assert_("abc", "return true", wait_timeout=0)["asserted"] is True
    assert driver.calls == 1


@pytest.mark.parametrize("bad", ["abc", -1, float("inf"), float("nan")])
def test_a_stability_window_that_cannot_be_read_is_refused(actions, driving, bad):
    """Everywhere else a value that cannot be read falls back to the default,
    and that is right because the default still does the work. Here the default
    is zero, and zero means the check does not happen — a typo would silently
    take away the guard this argument exists to add (Copilot, #31)."""
    driving(True)
    with pytest.raises(ValueError, match="stable_for"):
        actions.assert_("abc", "return true", wait_timeout=5, stable_for=bad)


def test_a_hold_that_fills_the_whole_wait_is_refused(actions, driving):
    """Verifying a hold needs a poll AFTER the answer first came back true, and
    the deadline stops that poll at `wait_timeout` — so `stable_for` equal to
    `wait_timeout` could never be confirmed, and every such assertion failed
    with "did not hold" whatever the page did (Copilot, #31)."""
    driving(True)
    with pytest.raises(ValueError, match="no room"):
        actions.assert_("abc", "return true", wait_timeout=5, stable_for=5)


def test_a_hold_with_room_left_still_passes(actions, driving, clock):
    driving(True)
    assert actions.assert_(
        "abc", "return true", wait_timeout=5, stable_for=4.9
    )["asserted"] is True
