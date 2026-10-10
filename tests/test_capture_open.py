"""open_session(capture=true): the hook, the setting, and what the workspaces do
with a monitor that holds the connection."""

from types import SimpleNamespace

import pytest

from kubed.selenium_flow import config
from kubed.selenium_flow.capture.store import Capture
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.faults import BidiUnavailable
from kubed.selenium_flow.monitor.monitor import ATTACH_FAILED
from kubed.selenium_flow.workspace import settings
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .capture_events import log

pytestmark = pytest.mark.unit


# ---- Actions: a real Actions on a stub Grid ----------------------------------


class StubDriver:
    session_id = "gridstub"
    current_url = "about:blank"
    title = ""

    def __init__(self, order):
        self.order = order

    def get(self, url):
        self.order.append(f"get {url}")
        self.current_url = url

    def get_window_size(self):
        return {"width": 1280, "height": 900}


class StubGrid:
    def __init__(self):
        self.order, self.quit_ids, self.quit_fails = [], [], False
        self.last_id = StubDriver.session_id

    def open(self, name, **kwargs):
        return StubDriver(self.order)

    def quit(self, sid):
        self.quit_ids.append(sid)
        if self.quit_fails:
            raise ConnectionError("grid unreachable")


@pytest.fixture
def stub_grid():
    return StubGrid()


def test_on_ready_runs_after_the_restore_and_before_the_first_page(stub_grid):
    actions = Actions(stub_grid)
    actions.open_session(
        url="https://a.test/",
        capture=True,
        on_ready=lambda sid: stub_grid.order.append("attach"),
    )
    assert stub_grid.order == ["attach", "get https://a.test/"]


def test_a_failed_on_ready_quits_the_browser_and_raises(stub_grid):
    def refuse(_sid):
        raise BidiUnavailable("no route")

    with pytest.raises(BidiUnavailable):
        Actions(stub_grid).open_session(url="https://a.test/", on_ready=refuse)
    assert stub_grid.quit_ids == [stub_grid.last_id]
    assert "get https://a.test/" not in stub_grid.order


def test_a_quit_that_fails_does_not_hide_the_refusal(stub_grid):
    stub_grid.quit_fails = True

    def refuse(_sid):
        raise BidiUnavailable("no route")

    with pytest.raises(BidiUnavailable, match="no route"):
        Actions(stub_grid).open_session(on_ready=refuse)


def test_a_restores_stand_in_reset_happens_before_on_ready(stub_grid, monkeypatch):
    """Plan ruling 3: a restore's stand-in is never captured, so the reset to
    about:blank is done before the connection is held."""
    import contextlib

    from kubed.selenium_flow.core import actions as actions_module

    actions = Actions(stub_grid)
    actions.channels = SimpleNamespace(
        open_channel=lambda sid: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(actions_module.site_data_snapshot, "restorable", lambda d: True)
    monkeypatch.setattr(
        actions_module.site_data_transfer,
        "restore",
        lambda channel, data, now: {"restored": ["a"], "skipped": []},
    )
    # The restore left the tab on a stand-in page.
    monkeypatch.setattr(StubDriver, "current_url", "https://stand-in.test/")
    actions.open_session(
        site_data={"x": 1},
        capture=True,
        on_ready=lambda sid: stub_grid.order.append("attach"),
    )
    assert stub_grid.order == ["get about:blank", "attach"]


def test_capture_shows_in_the_applied_settings(stub_grid):
    assert Actions(stub_grid).open_session(capture=True)["settings"]["capture"] is True
    assert "capture" not in Actions(StubGrid()).open_session()["settings"]


def test_capture_is_explicit_only():
    assert settings.from_client({"capture": "true"}, {"x-capture": "true"}) == {}
    assert settings.resolve({"capture": "false"}, previous={"capture": True})[
        "capture"
    ] is False


# ---- Workspaces: a fake Actions, a fake monitor, a real Capture --------------


class Grid:
    def __init__(self):
        self.alive = set()

    def is_alive(self, sid):
        return sid in self.alive

    def session_timeout(self, sid):
        return 300

    def reconnect(self, sid):
        raise ConnectionError("not in this test")


class FakeActions:
    def __init__(self):
        self.grid = Grid()
        self.n = 0
        self.opened = []
        self.fail_after_ready = None  # the first page failing, say

    def open_session(self, *, on_created=None, on_ready=None, **kwargs):
        self.n += 1
        sid = f"grid{self.n:04d}"
        self.grid.alive.add(sid)
        self.opened.append(dict(kwargs))
        try:
            if on_ready is not None:
                on_ready(sid)
        except Exception:
            self.grid.alive.discard(sid)  # the real one quits the browser
            raise
        if self.fail_after_ready is not None:
            raise self.fail_after_ready
        return {
            "session_id": sid,
            "browser": kwargs.get("browser") or "chrome",
            "url": "about:blank",
            "title": "",
            "width": 1280,
            "height": 900,
            "settings": {},
        }

    def end_browser(self, sid):
        self.grid.alive.discard(sid)


class FakeMonitor:
    def __init__(self):
        self.attached, self.order, self.refuse = [], [], False
        self.held, self.ended_calls, self.opened_calls = set(), [], []

    def attach(self, workspace, sid, reason, **kwargs):
        self.order.append("attach")
        if self.refuse:
            raise BidiUnavailable(ATTACH_FAILED)
        self.attached.append((workspace, sid, reason))

    def opened(self, workspace, sid, browser, grid_timeout, *, reopened=False):
        self.opened_calls.append((workspace, sid))

    def ended(self, workspace, sid, cause):
        self.ended_calls.append((workspace, sid, cause))
        return True

    def capturing(self, sid):
        return sid in self.held


def caller(name="bot"):
    return Caller(name, "query")


@pytest.fixture
def ws():
    actions, monitor, capture = FakeActions(), FakeMonitor(), Capture()
    workspaces = Workspaces(actions, monitor=monitor, capture=capture)
    return SimpleNamespace(
        workspaces=workspaces, actions=actions, monitor=monitor, capture=capture
    )


def test_an_open_with_capture_attaches_and_remembers_it(ws):
    result = ws.workspaces.open_browser(caller(), url="https://a.test/", capture=True)
    assert ws.monitor.attached == [("bot", "grid0001", "capture")]
    assert ws.workspaces.store.get("bot").settings["capture"] is True
    assert result["capture"] is True and "grid0001" not in str(result)


def test_an_open_without_capture_says_so(ws):
    assert ws.workspaces.open_browser(caller())["capture"] is False


def test_a_failed_attach_fails_the_open_and_binds_nothing(ws):
    ws.monitor.refuse = True
    with pytest.raises(BidiUnavailable):
        ws.workspaces.open_browser(caller(), capture=True)
    record = ws.workspaces.store.get("bot")
    assert record is None or not record.attached


def test_a_failed_attach_tells_the_monitor_the_session_ended(ws):
    ws.monitor.refuse = True
    with pytest.raises(BidiUnavailable):
        ws.workspaces.open_browser(caller(), capture=True)
    assert [c[1] for c in ws.monitor.ended_calls] == ["grid0001"]
    assert ws.monitor.opened_calls == []


def test_a_failure_after_the_attach_ends_the_watch(ws):
    """The first page timing out must not leave the monitor holding a
    connection to a browser no workspace was handed."""
    ws.actions.fail_after_ready = TimeoutError("page load")
    with pytest.raises(TimeoutError):
        ws.workspaces.open_browser(caller(), url="https://typo.test/", capture=True)
    assert [c[1] for c in ws.monitor.ended_calls] == ["grid0001"]
    record = ws.workspaces.store.get("bot")
    assert record is None or not record.attached


def test_a_failure_after_a_reopens_attach_ends_the_watch(ws):
    ws.workspaces.open_browser(caller(), capture=True)
    ws.actions.grid.alive.clear()
    ws.monitor.ended_calls.clear()
    ws.actions.fail_after_ready = TimeoutError("page load")
    with pytest.raises(TimeoutError):
        ws.workspaces.resolve("bot")
    assert [c[1] for c in ws.monitor.ended_calls] == ["grid0001", "grid0002"]


def test_a_monitor_that_is_not_running_is_a_503_not_a_500(ws):
    def not_running(*_args, **_kwargs):
        raise RuntimeError("the monitor is not running")

    ws.monitor.attach = not_running
    with pytest.raises(BidiUnavailable) as caught:
        ws.workspaces.open_browser(caller(), capture=True)
    assert str(caught.value) == ATTACH_FAILED
    assert "grid0001" not in str(caught.value)


def test_capture_is_never_inherited_but_a_reap_replays_it(ws):
    ws.workspaces.open_browser(caller(), capture=True)
    ws.actions.grid.alive.clear()  # the Grid reaped it
    ws.workspaces.resolve("bot")
    assert len(ws.monitor.attached) == 2  # the silent reopen replayed it
    ws.workspaces.open_browser(caller())
    assert len(ws.monitor.attached) == 2  # an explicit open did not
    assert "capture" not in ws.actions.opened[-1]


def test_a_reap_without_a_monitor_any_more_drops_capture():
    actions = FakeActions()
    workspaces = Workspaces(actions, monitor=FakeMonitor(), capture=Capture())
    workspaces.open_browser(caller(), capture=True)
    workspaces.monitor = None
    actions.grid.alive.clear()
    workspaces.resolve("bot")
    assert "capture" not in actions.opened[-1]


def test_an_explicit_open_without_capture_discards_the_buffers(ws):
    ws.capture.attached("bot", "old", "chrome", "top-1", again=False)
    ws.capture.ingest("bot", "old", *log("kept"))
    assert ws.capture.console("bot") is not None
    ws.workspaces.open_browser(caller())
    assert ws.capture.console("bot") is None


def test_capture_without_a_monitor_is_refused_before_a_browser_opens():
    bare = Workspaces(FakeActions())
    with pytest.raises(ValueError, match="Capture is not set up"):
        bare.open_browser(caller(), capture=True)
    assert bare.actions.opened == []


def test_a_raising_monitor_never_fails_end_browser(ws):
    ws.workspaces.open_browser(caller())

    def boom(*_args, **_kwargs):
        raise RuntimeError("monitor broke")

    ws.monitor.ended = boom
    ws.workspaces.end_browser(caller())
    assert not ws.workspaces.store.get("bot").attached


def test_a_raising_monitor_never_fails_an_open(ws):
    def boom(*_args, **_kwargs):
        raise RuntimeError("monitor broke")

    ws.monitor.opened = boom
    assert ws.workspaces.open_browser(caller())["workspace"] == "bot"


def test_current_reports_capture_capturing_and_counts(ws):
    ws.workspaces.open_browser(caller(), capture=True)
    ws.monitor.held.add("grid0001")
    status = ws.workspaces.describe(caller())
    assert status["capture"] is True and status["capturing"] is True
    assert status["captured"] == {
        "console": 0,
        "network": 0,
        "errors": 0,
        "failed_requests": 0,
    }


def test_current_without_capture_has_neither_counts_nor_capturing(ws):
    ws.workspaces.open_browser(caller())
    status = ws.workspaces.describe(caller())
    assert status["capture"] is False and status["capturing"] is False
    assert "captured" not in status


def test_capture_off_reads_as_off_not_an_error(ws):
    ws.workspaces.open_browser(caller())
    assert ws.workspaces.captured("bot", "console") == {
        "capture": False,
        "how": "open_session(capture=true)",
    }


def test_an_empty_buffer_reads_as_an_empty_view(ws):
    ws.workspaces.open_browser(caller(), capture=True)
    ws.monitor.held.add("grid0001")
    view = ws.workspaces.captured("bot", "console")
    assert view["capture"] is True and view["capturing"] is True
    assert view["entries"] == [] and view["count"] == 0
    network = ws.workspaces.captured("bot", "network", filter="failed")
    assert network["requests"] == [] and network["capturing"] is True


def test_an_unknown_request_id_names_the_listing(ws):
    ws.workspaces.open_browser(caller(), capture=True)
    with pytest.raises(
        ValueError, match=r"no captured request 'r9': workspace://network lists"
    ):
        ws.workspaces.captured("bot", "request", id="r9")


def test_a_bad_level_is_refused_even_on_an_empty_buffer(ws):
    ws.workspaces.open_browser(caller(), capture=True)
    with pytest.raises(ValueError, match="level is one of"):
        ws.workspaces.captured("bot", "console", level="loud")


def test_the_ttl_is_a_setting_in_its_own_section():
    assert config.load([], {}).settings.capture.ttl == 10_800
    assert config.load([], {"CAPTURE_TTL": "600"}).settings.capture.ttl == 600
    assert config.load(["--capture-ttl", "900"], {}).settings.capture.ttl == 900
    with pytest.raises(config.ConfigError):
        config.load([], {"CAPTURE_TTL": "59"})
