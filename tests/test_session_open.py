"""`Actions.open_session` and what it sends: the built set, from the named
options, the operator's `session.capabilities` and the caller's raw object
(spec 2026-10-26-session-capabilities)."""

import pytest

from kubed.selenium_flow.core.actions import Actions
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

    def open(self, built):
        self.built = built
        return Driver(self.caps)

    def quit(self, session_id):
        pass

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
