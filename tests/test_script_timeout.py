"""A script that runs past the session's script timeout says so.

Selenium raises W3C `script timeout` as a `TimeoutException`, which is a 400
like every wait here, and the caller read just `script timeout` (Chrome) or
`Timed out after 30000 ms` (Firefox): no timeout named and no fix. The
exception is keyed on the command that raised it, not on the browser's words.
"""

from __future__ import annotations

import pytest
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from kubed.selenium_flow import errors, faults

from .fakes import ScriptedDriver

pytestmark = pytest.mark.unit

WORDS = {"chrome": "script timeout", "firefox": "Timed out after 30000 ms"}


class _Timeouts:
    def __init__(self, script):
        self.script = script


class Slow(ScriptedDriver):
    """A driver whose scripts time out as ``words`` say; its script timeout is
    ``seconds``, or unreadable when that is None."""

    def __init__(self, words: str, seconds=30, **settings):
        super().__init__(**settings)
        self.__dict__["words"] = words
        self.__dict__["_seconds"] = seconds

    @property
    def timeouts(self):
        if self._seconds is None:
            raise RuntimeError("cannot read the timeouts")
        return _Timeouts(self._seconds)

    def execute_script(self, script, *args):
        self.log.append(("execute_script", script))
        raise TimeoutException(self.words)


def driven_by(actions, monkeypatch, driver):
    monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: driver)


@pytest.mark.parametrize("name", sorted(WORDS))
def test_execute_script_names_the_timeout_and_the_fix(actions, monkeypatch, name):
    driven_by(actions, monkeypatch, Slow(WORDS[name]))
    with pytest.raises(faults.ScriptTimedOut) as caught:
        actions.execute_script("abc", "while(true){}")
    said = faults.message(caught.value)
    assert "30s" in said and "open_session(script_timeout=" in said
    assert "30.0" not in said
    assert errors.status_for(caught.value) == 400
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@pytest.mark.parametrize("name", sorted(WORDS))
def test_assert_says_it_too_not_an_assertion_failure(actions, monkeypatch, name):
    driven_by(actions, monkeypatch, Slow(WORDS[name]))
    with pytest.raises(faults.ScriptTimedOut) as caught:
        actions.assert_("abc", "return true", wait_timeout=0)
    assert "30s" in faults.message(caught.value)
    assert errors.status_for(caught.value) == 400


def test_a_fractional_timeout_is_said_as_it_is():
    assert "2.5s" in faults.message(faults.ScriptTimedOut(2.5))


def test_a_timeout_that_cannot_be_read_says_the_rest(actions, monkeypatch):
    driven_by(actions, monkeypatch, Slow(WORDS["chrome"], seconds=None))
    with pytest.raises(faults.ScriptTimedOut) as caught:
        actions.execute_script("abc", "while(true){}")
    said = faults.message(caught.value)
    assert said == (
        "the script ran past this session's script timeout: make it shorter, "
        "or raise it with open_session(script_timeout=<seconds>)"
    )
    assert errors.status_for(caught.value) == 400


def test_a_timeout_from_anything_else_stays_as_it_was(actions, monkeypatch):
    """A selector wait that runs out is the caller's element, not their script."""
    class Empty(ScriptedDriver):
        def find_element(self, by=None, value=None):
            raise NoSuchElementException("no such element")

        def find_elements(self, by=None, value=None):
            return []

    driven_by(actions, monkeypatch, Empty())
    with pytest.raises(TimeoutException) as caught:
        actions.extract("abc", selector={"css": "#nope"}, wait_timeout=0)
    assert not isinstance(caught.value, faults.ScriptTimedOut)
