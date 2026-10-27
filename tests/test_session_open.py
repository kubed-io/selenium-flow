"""`Actions.open_session` and what it sends: the built set, from the named
options, the operator's `session.capabilities` and the caller's raw object
(spec 2026-10-26-session-capabilities)."""

import pytest
from selenium.common.exceptions import SessionNotCreatedException

from kubed.selenium_flow import errors
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.faults import NodeRefused, NoNodeTook
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

pytestmark = pytest.mark.unit


class Driver:
    session_id = "opened"
    current_url = "about:blank"
    title = ""

    def __init__(self, caps=None):
        self.caps = caps or {}

    def get(self, url):
        self.current_url = url

    def get_window_size(self):
        return {"width": 1280, "height": 900}

    def set_window_size(self, width, height):
        pass


class Grid:
    """Records what each open was asked to send."""

    def __init__(self, caps=None):
        self.built = None
        self.caps = caps
        self.fails = None
        self.quit_ids = []

    def open(self, built):
        self.built = built
        if self.fails is not None:
            raise self.fails
        return Driver(self.caps)

    def quit(self, session_id):
        self.quit_ids.append(session_id)

    def is_alive(self, session_id):
        return True

    def session_timeout(self, session_id):
        return None


def test_the_open_sends_the_built_set_with_the_config_default():
    grid = Grid()
    actions = Actions(grid)
    actions.config_capabilities = {"kubed:pool": "default"}
    actions.open_session(browser="chrome", version="152", capabilities={"x:y": 1})
    assert grid.built.send["kubed:pool"] == "default"
    assert grid.built.send["x:y"] == 1
    assert grid.built.send["browserVersion"] == "152"
    assert grid.built.raw_keys == ("x:y",)


def test_a_client_version_and_a_config_version_reach_the_open():
    """Both settings are in the cascade, so `open_browser` hands them to the
    open as keywords: the open has to take them."""
    grid = Grid()
    workspaces = Workspaces(Actions(grid), defaults={"version": "150"})
    caller = Caller.from_request({"workspace": ["w"]}, {"x-version": ["152"]})
    workspaces.open_browser(caller)
    assert grid.built.send["browserVersion"] == "152"
    workspaces.open_browser(Caller.from_request({"workspace": ["v"]}, {}))
    assert grid.built.send["browserVersion"] == "150"


RAN = {"browserVersion": "152.0.1", "se:containerName": "node-a", "se:vncEnabled": True}


def test_the_result_says_what_ran_and_keeps_sent_and_ran_for_the_workspace():
    result = Actions(Grid(RAN)).open_session(version="152")
    assert result["version"] == "152.0.1"
    assert result["settings"]["version"] == "152"
    assert result["ran"] == {"version": "152.0.1", "node": "node-a", "live_view": True}
    assert [e["key"] for e in result["sent"]][:2] == ["browserName", "browserVersion"]
    assert "ignored" not in result
    assert "capabilities" not in result["settings"]


def test_latest_is_reported_as_asked_and_nothing_is_sent():
    grid = Grid(RAN)
    result = Actions(grid).open_session(version="latest")
    assert result["settings"]["version"] == "latest"
    assert "browserVersion" not in grid.built.send


def test_a_driver_that_says_nothing_ran_reads_as_unknown():
    result = Actions(Grid({"browserVersion": 5, "se:vncEnabled": "yes"})).open_session()
    assert result["ran"] == {"version": None, "node": None, "live_view": None}
    assert result["version"] is None


def test_the_raw_object_and_what_lost_are_reported():
    result = Actions(Grid(RAN)).open_session(
        capabilities={
            "kubed:pool": "a",
            "goog:chromeOptions": {"prefs": {"credentials_enable_service": True}},
        }
    )
    assert result["settings"]["capabilities"] == {
        "kubed:pool": "a",
        "goog:chromeOptions": {"prefs": {"credentials_enable_service": True}},
    }
    assert result["ignored"] == ["goog:chromeOptions.prefs.credentials_enable_service"]


def test_an_owned_key_opens_nothing():
    grid = Grid()
    with pytest.raises(ValueError, match="browserName cannot be sent"):
        Actions(grid).open_session(capabilities={"browserName": "x"})
    assert grid.built is None


@pytest.mark.parametrize(
    "said", ["New session request timed out", "Timed out creating session"]
)
def test_a_queue_timeout_raises_no_node_took(said):
    grid = Grid()
    grid.fails = SessionNotCreatedException(f"Could not start a new session. {said}")
    actions = Actions(grid)
    actions.config_capabilities = {"kubed:pool": "headless"}
    with pytest.raises(NoNodeTook) as raised:
        actions.open_session(version="152")
    assert "it asked for chrome 152, kubed:pool=headless" in str(raised.value)
    assert errors.status_for(raised.value) == 503
    assert isinstance(raised.value.__cause__, SessionNotCreatedException)
    assert grid.quit_ids == []


def test_a_grid_side_failure_is_raised_as_it_was():
    grid = Grid()
    grid.fails = SessionNotCreatedException("Unable to find owning node for slot")
    with pytest.raises(SessionNotCreatedException) as raised:
        Actions(grid).open_session()
    assert raised.value is grid.fails
    assert errors.status_for(raised.value) == 503


def test_a_node_refusal_is_the_callers_only_when_it_asked_for_something():
    grid = Grid()
    grid.fails = SessionNotCreatedException("session not created: bad flag")
    with pytest.raises(NodeRefused) as blamed:
        Actions(grid).open_session(capabilities={"x:y": 1})
    assert errors.status_for(blamed.value) == 400
    assert "capabilities x:y" in str(blamed.value)
    with pytest.raises(NodeRefused) as not_blamed:
        Actions(grid).open_session()
    assert errors.status_for(not_blamed.value) == 503


def test_the_workspace_result_never_carries_sent_or_ran():
    grid = Grid(RAN)
    workspaces = Workspaces(Actions(grid))
    told = workspaces.open_browser(Caller.from_request({"workspace": ["w"]}, {}))
    assert "sent" not in told and "ran" not in told and "session_id" not in told
    assert told["version"] == "152.0.1"
