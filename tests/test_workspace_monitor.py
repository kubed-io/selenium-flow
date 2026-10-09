"""What the workspaces tell the monitor, and the Grid's idle timeout they keep."""

import pytest

from kubed.selenium_flow.workspace.store import Workspace
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

pytestmark = pytest.mark.unit


class Announced:
    """Stands in for the monitor: what the manager announced, in order."""

    def __init__(self):
        self.calls = []

    def opened(self, workspace, session_id, browser, grid_timeout, *, reopened=False):
        self.calls.append(
            ("opened", workspace, session_id, browser, grid_timeout, reopened)
        )

    def ended(self, workspace, session_id, cause):
        self.calls.append(("ended", workspace, session_id, cause))
        return True


class Grid:
    def __init__(self):
        self.alive = set()
        self.timeouts = {}
        self.timeout_fails = False

    def is_alive(self, sid):
        return sid in self.alive

    def session_timeout(self, sid):
        if self.timeout_fails:
            raise ConnectionError("status unavailable")
        return self.timeouts.get(sid)

    def reconnect(self, sid):
        raise ConnectionError("not in this test")


class Actions:
    def __init__(self):
        self.grid = Grid()
        self.n = 0
        self.quit_fails = False

    def open_session(self, *, on_created=None, **kwargs):
        self.n += 1
        sid = f"grid{self.n:04d}"
        self.grid.alive.add(sid)
        self.grid.timeouts.setdefault(sid, 300)
        return {
            "session_id": sid,
            "browser": kwargs.get("browser") or "chrome",
            "url": "about:blank",
            "title": "",
            "width": 1280,
            "height": 900,
        }

    def end_browser(self, sid):
        if self.quit_fails:
            raise ConnectionError("grid unreachable")
        self.grid.alive.discard(sid)


def manager(monitor=None):
    return Workspaces(Actions(), monitor=monitor)


def caller(name="bot"):
    return Caller(name, "query")


def test_an_open_keeps_the_grids_timeout_and_announces_the_browser():
    announced = Announced()
    m = manager(announced)
    m.open_browser(caller(), browser="firefox")
    assert m.store.get("bot").grid_timeout == 300
    assert announced.calls == [("opened", "bot", "grid0001", "firefox", 300, False)]


def test_a_timeout_the_grid_will_not_give_is_none_and_the_open_goes_on():
    m = manager()
    m.actions.grid.timeout_fails = True
    result = m.open_browser(caller())
    assert result["workspace"] == "bot" and m.store.get("bot").grid_timeout is None


def test_the_timeout_survives_the_browser_and_is_described():
    m = manager()
    m.open_browser(caller())
    m.end_browser(caller())
    record = m.store.get("bot")
    assert not record.attached and record.grid_timeout == 300
    assert m.describe(caller())["grid_timeout"] == 300


def test_a_status_with_no_record_has_no_timeout():
    assert manager().describe(caller())["grid_timeout"] is None


def test_end_announces_ended_and_a_failed_quit_announces_nothing():
    announced = Announced()
    m = manager(announced)
    m.open_browser(caller())
    m.end_browser(caller())
    assert announced.calls[-1] == ("ended", "bot", "grid0001", "ended")
    m.open_browser(caller())
    m.actions.quit_fails = True
    m.end_browser(caller())
    assert [c for c in announced.calls if c[0] == "ended"] == [
        ("ended", "bot", "grid0001", "ended")
    ]


def test_a_reap_found_by_a_call_is_lost_and_the_reopen_is_announced():
    announced = Announced()
    m = manager(announced)
    m.open_browser(caller())
    m.actions.grid.alive.clear()  # the Grid reaped it
    m.actions.grid.timeouts["grid0002"] = 600  # a node with another timeout
    assert m.resolve("bot") == "grid0002"
    assert announced.calls[1:] == [
        ("ended", "bot", "grid0001", "lost"),
        ("opened", "bot", "grid0002", "chrome", 600, True),
    ]
    assert m.store.get("bot").grid_timeout == 600


def test_a_racing_opens_loser_is_announced_ended_and_never_opened():
    announced = Announced()
    m = manager(announced)
    m.store.set("bot", Workspace(session_id="winner"))
    kept = m.remember("bot", "loser", replacing=None)
    assert kept == "winner"
    assert announced.calls == [("ended", "bot", "loser", "ended")]


def test_without_a_monitor_nothing_is_announced_and_nothing_fails():
    m = manager()
    m.open_browser(caller())
    m.end_browser(caller())
    assert m.store.get("bot").grid_timeout == 300


def test_a_stored_timeout_is_read_back_only_when_it_is_a_positive_int():
    assert Workspace.from_json('{"grid_timeout": 300}').grid_timeout == 300
    for raw in ('"300"', "0", "-5", "true", "1.5", "null"):
        record = Workspace.from_json(f'{{"grid_timeout": {raw}}}')
        assert record.grid_timeout is None, raw
