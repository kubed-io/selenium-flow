"""A node that stopped answering says so, and never in the hub's words.

When a Grid node dies mid-call the hub answers 500 and Selenium raises a bare
`WebDriverException` quoting the node's address and the Grid's session id. That
was a 500 with a traceback, and the text reached the caller and the log. It is
now one fixed sentence and a 404: the fix is `open_session`, as for a reaped
browser.
"""

from __future__ import annotations

import logging

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from selenium.common.exceptions import WebDriverException

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import calling_as
from .fakes import ScriptedDriver

pytestmark = pytest.mark.unit

HUB = (
    "Unable to execute request for an existing session: Failed to execute "
    "request | Connection error "
    "(POST http://10.42.8.168:5555/session/5cf1d0a2b3c4/execute/sync)"
)
MARKERS = {
    "hub": HUB,
    "page crash": "unknown error: session deleted because of page crash",
    "tab crash": "unknown error: cannot determine loading status\nfrom tab crashed",
}
SAID = (
    "the browser's Grid node stopped answering during this call (it may have "
    "crashed or run out of memory): call open_session for a new browser, which "
    "goes back to the page this one was on"
)
LEAKS = ("http://", "10.42.8.168", "5555", "5cf1d0a2b3c4", "Unable to execute")


class Dying(ScriptedDriver):
    def execute_script(self, script, *args):
        self.log.append(("execute_script", script))
        raise WebDriverException(HUB)

    def find_element(self, by=None, value=None):
        self.log.append(("find_element", by, value))
        raise WebDriverException(HUB)


@pytest.mark.parametrize("name", sorted(MARKERS))
def test_each_marker_is_read_as_a_lost_node(name):
    assert faults.lost(WebDriverException(MARKERS[name]))


def test_only_a_driver_failure_is_read_as_a_lost_node():
    assert not faults.lost(ValueError(MARKERS["hub"]))
    assert not faults.lost(WebDriverException("unknown error: something else"))


def test_the_sentence_is_fixed_and_a_404():
    caught = faults.NodeLost()
    assert faults.message(caught) == SAID
    assert errors.status_for(caught) == 404


def lost_call(actions, monkeypatch, call):
    monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: Dying())
    with pytest.raises(faults.NodeLost) as caught:
        call()
    return caught.value


def check(exc):
    assert errors.status_for(exc) == 404
    said = faults.message(exc)
    assert said == SAID
    for leaked in LEAKS:
        assert leaked not in said
    assert exc.__cause__ is None and exc.__suppress_context__


def test_execute_script_says_it(actions, monkeypatch):
    check(lost_call(actions, monkeypatch, lambda: actions.execute_script("abc", "return 1")))


def test_every_action_says_it_because_the_recipe_does(actions, monkeypatch):
    check(
        lost_call(
            actions,
            monkeypatch,
            lambda: actions.extract("abc", selector={"css": "#x"}, wait_timeout=0),
        )
    )


def test_the_page_read_says_it_too_though_it_skips_the_recipe(actions, monkeypatch):
    """The leash check and a flow's page state read the page directly."""

    class Gone(ScriptedDriver):
        @property
        def current_url(self):
            raise WebDriverException(HUB)

    monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: Gone())
    with pytest.raises(faults.NodeLost) as caught:
        actions.page("abc")
    check(caught.value)


def test_an_ordinary_driver_failure_is_raised_as_it_was(actions, monkeypatch):
    class Odd(ScriptedDriver):
        def execute_script(self, script, *args):
            raise WebDriverException("unknown error: something else")

    monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: Odd())
    with pytest.raises(WebDriverException) as caught:
        actions.execute_script("abc", "return 1")
    assert not isinstance(caught.value, faults.NodeLost)


# ---- over MCP --------------------------------------------------------------------


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[tuple[int, str, bool]] = []

    def emit(self, record):
        self.records.append(
            (record.levelno, self.format(record), record.exc_info is not None)
        )


@pytest.fixture
def mcp_log():
    from kubed.selenium_flow.mcp.failures import FASTMCP_LOGGER

    handler = Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(FASTMCP_LOGGER)
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)


async def test_over_mcp_the_caller_and_the_log_read_the_sentence_only(
    tmp_path, monkeypatch, mcp_log
):
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, data={"dir": str(tmp_path)})
    )
    driver = Dying()
    monkeypatch.setattr(server.actions.grid, "open", lambda *a, **k: driver)
    monkeypatch.setattr(server.actions.grid, "reconnect", lambda session_id: driver)
    monkeypatch.setattr(server.actions.grid, "is_alive", lambda session_id: True)
    calling_as(monkeypatch, "lost")
    async with Client(server.mcp) as client:
        await client.call_tool("open_session", {})
        with pytest.raises(ToolError) as caught:
            await client.call_tool("execute_script", {"script": "return 1"})
    assert str(caught.value) == f"Error calling tool 'execute_script': {SAID}"
    calls = [r for r in mcp_log if "Error calling tool" in r[1]]
    assert len(calls) == 1, calls
    level, text, has_traceback = calls[0]
    assert level == logging.WARNING and not has_traceback
    assert "Traceback" not in text and "\n" not in text
    assert text.endswith(SAID)
    for leaked in LEAKS:
        assert leaked not in text
