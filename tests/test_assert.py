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


def test_false_without_a_message_says_what_was_false_and_where(actions, driving):
    driving(False)
    with pytest.raises(actions_module.AssertionFailed) as failed:
        actions.assert_("abc", "return 1 > 2", wait_timeout=0)
    message = str(failed.value)
    assert "return 1 > 2" in message
    assert "https://example.test/dashboard" in message


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
        {"tool": "write", "args": {"css": "#login-username", "text": "someone"}},
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
    monkeypatch.setattr(actions_module.time, "monotonic", lambda: now["t"])

    def fake_sleep(seconds):
        slept.append(seconds)
        now["t"] += seconds

    monkeypatch.setattr(actions_module.time, "sleep", fake_sleep)

    with pytest.raises(actions_module.AssertionFailed):
        actions.assert_("abc", "return false", wait_timeout=1)

    assert sum(slept) == pytest.approx(1), "it waited past its own deadline"
    assert all(nap <= actions_module.ASSERT_POLL for nap in slept)
    # Six evaluations: at 0.0 through 1.0, and none after the deadline.
    assert driver.calls == 6
