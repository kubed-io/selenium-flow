"""open_session(record=true): the capability, the gate, and the hook."""

import pytest

from kubed.selenium_flow import config
from kubed.selenium_flow.session import sessions as sessions_module
from kubed.selenium_flow.session.sessions import Caller, SessionManager

pytestmark = pytest.mark.unit


class Recorder:
    """The hook SessionManager calls; records what it was told."""

    def __init__(self):
        self.expected, self.finished, self.discarded = [], [], []

    def expect(self, session, grid_id, browser, *, discard=False):
        (self.discarded if discard else self.expected).append(
            (session, grid_id, browser)
        )

    def ended(self, grid_id):
        self.finished.append(grid_id)


class Grid:
    def __init__(self):
        self.alive, self.opened, self.quit = set(), [], []

    def is_alive(self, sid):
        return sid in self.alive


class Actions:
    """Answers the two calls SessionManager makes when opening and ending."""

    def __init__(self):
        self.grid = Grid()
        self.calls = []
        self.n = 0

    def open_session(self, **kwargs):
        self.n += 1
        sid = f"grid{self.n:04d}"
        self.grid.alive.add(sid)
        self.calls.append(kwargs)
        settings = {"browser": "chrome", "width": 1280, "height": 900}
        if kwargs.get("record"):
            settings["record"] = True
        return {"session_id": sid, "browser": "chrome", "url": "about:blank",
                "title": "", "width": 1280, "height": 900, "settings": settings}

    def end_browser(self, sid):
        self.grid.alive.discard(sid)
        return {"success": True}


def manager(recorder=None):
    return SessionManager(Actions(), recordings=recorder)


def caller(name="bot"):
    return Caller(name, "query")


def test_record_without_recording_set_up_is_a_400_and_opens_nothing():
    m = manager(None)
    with pytest.raises(ValueError) as exc:
        m.open_browser(caller(), record=True)
    assert str(exc.value) == sessions_module.RECORDING_OFF
    assert m.actions.calls == []


def test_record_sends_the_capability_and_tells_the_collector():
    rec = Recorder()
    m = manager(rec)
    result = m.open_browser(caller(), record=True)
    assert m.actions.calls[-1]["record"] is True
    assert m.actions.calls[-1]["video_name"] == "bot"
    assert rec.expected == [("bot", "grid0001", "chrome")]
    assert result["recording"] is True
    assert "grid0001" not in str(result)


def test_an_explicit_open_after_end_does_not_inherit_record():
    rec = Recorder()
    m = manager(rec)
    m.open_browser(caller(), record=True)
    m.end_browser(caller())
    result = m.open_browser(caller())
    assert not m.actions.calls[-1].get("record")
    assert result["recording"] is False
    assert rec.finished == ["grid0001"]


def test_a_reap_replays_record_and_expects_the_new_browser():
    rec = Recorder()
    m = manager(rec)
    m.open_browser(caller(), record=True)
    m.actions.grid.alive.clear()  # the Grid reaped it
    m.resolve("bot")
    assert m.actions.calls[-1]["record"] is True
    assert m.actions.calls[-1]["video_name"] == "bot"
    assert rec.expected[-1] == ("bot", "grid0002", "chrome")


def test_a_note_that_cannot_be_written_keeps_the_browser_and_says_why():
    class Broken(Recorder):
        def expect(self, session, grid_id, browser):
            raise PermissionError(13, "denied", "/data/sessions/bot/recordings")

    m = manager(Broken())
    result = m.open_browser(caller(), record=True)
    assert result["recording"] is True
    assert result["recording_error"] == "this recording cannot be filed: PermissionError"
    assert "/data" not in result["recording_error"]
    assert m.actions.grid.alive == {"grid0001"}


def test_describe_says_whether_the_held_browser_is_recorded():
    rec = Recorder()
    m = manager(rec)
    assert m.describe(caller())["recording"] is False
    m.open_browser(caller(), record=True)
    assert m.describe(caller())["recording"] is True
    m.end_browser(caller())
    assert m.describe(caller())["recording"] is False


def test_recording_enabled_without_a_data_dir_does_not_boot():
    with pytest.raises(config.ConfigError, match=r"recording\.enabled needs data\.dir"):
        config.load([], {"RECORDING_ENABLED": "true"})


def test_the_inbox_defaults_inside_the_data_dir_and_can_be_set_apart():
    s = config.Settings(data={"dir": "/d"})
    assert config.recording_dir(s) == "/d/recordings"
    s = config.Settings(data={"dir": "/d"}, recording={"dir": "/elsewhere"})
    assert config.recording_dir(s) == "/elsewhere"
    assert config.recording_dir(config.Settings()) is None


def recording_at(data_dir, inbox=None):
    return config.recording_problem(config.Settings(
        data={"dir": str(data_dir)},
        recording={"enabled": True, "dir": inbox and str(inbox)},
    ))


@pytest.mark.parametrize("inbox", [
    "sessions/bot/recordings",  # inside: half-written videos listed as files
    "sessions",
    ".",  # above: the collector would sweep every session folder
    "/",
])
def test_an_inbox_overlapping_the_sessions_does_not_boot(tmp_path, inbox):
    problem = recording_at(tmp_path, tmp_path / inbox)
    assert problem and "must not overlap" in problem
    assert str((tmp_path / "sessions").resolve()) in problem


def test_a_relative_inbox_is_compared_where_the_server_opens_it(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    assert "must not overlap" in recording_at(tmp_path, "sessions/bot/inbox")
    assert recording_at(tmp_path, "inbox") is None


def test_the_default_inbox_and_a_sibling_are_apart_from_the_sessions(tmp_path):
    assert recording_at(tmp_path) is None
    assert recording_at(tmp_path, tmp_path / "videos") is None
    assert recording_at(tmp_path, tmp_path / "sessions-inbox") is None


def test_a_disabled_recorder_may_name_any_inbox(tmp_path):
    assert config.recording_problem(config.Settings(
        data={"dir": str(tmp_path)},
        recording={"dir": str(tmp_path / "sessions")},
    )) is None


def test_a_reap_survives_a_note_that_cannot_be_written():
    class Broken(Recorder):
        def expect(self, session, grid_id, browser):
            raise PermissionError(13, "denied", "/data/sessions/bot/recordings")

    m = manager(Recorder())
    m.open_browser(caller(), record=True)
    m.recordings = Broken()
    m.actions.grid.alive.clear()
    assert m.resolve("bot") == "grid0002"
    assert m.actions.grid.alive == {"grid0002"}


def test_a_reap_with_recording_off_does_not_replay_record():
    m = manager(Recorder())
    m.open_browser(caller(), record=True)
    m.recordings = None
    m.actions.grid.alive.clear()
    m.resolve("bot")
    assert not m.actions.calls[-1].get("record")
    assert "video_name" not in m.actions.calls[-1]


def test_a_reap_with_recording_off_remembers_the_browser_as_unrecorded():
    m = manager(Recorder())
    m.open_browser(caller(), record=True)
    m.recordings = None
    m.actions.grid.alive.clear()
    m.resolve("bot")
    assert m.describe(caller())["recording"] is False
    assert "record" not in m.store.get("bot").settings


def test_a_reap_that_loses_the_race_notes_its_video_for_discard():
    rec = Recorder()
    m = manager(rec)
    m.open_browser(caller(), record=True)
    m.actions.grid.alive.clear()
    real = m.actions.open_session

    def open_while_another_binds(**kwargs):
        # A reopen alongside this one binds its browser first.
        m.actions.grid.alive.add("grid9999")
        m.remember("bot", "grid9999", "", {"record": True}, replacing="grid0001")
        return real(**kwargs)

    m.actions.open_session = open_while_another_binds
    assert m.resolve("bot") == "grid9999"
    assert [e[1] for e in rec.expected] == ["grid0001"]
    assert rec.discarded == [("bot", "grid0002", "chrome")]


def test_an_open_that_loses_the_race_notes_its_video_for_discard():
    rec = Recorder()
    m = manager(rec)
    real = m.actions.open_session

    def open_while_another_binds(**kwargs):
        m.actions.grid.alive.add("grid9999")
        m.remember("bot", "grid9999", "", {"record": True})
        return real(**kwargs)

    m.actions.open_session = open_while_another_binds
    m.open_browser(caller(), record=True)
    assert rec.expected == []
    assert rec.discarded == [("bot", "grid0001", "chrome")]


def test_a_discard_that_cannot_be_noted_never_fails_the_open(caplog):
    class Broken(Recorder):
        def expect(self, session, grid_id, browser, *, discard=False):
            raise PermissionError(13, "denied", "/data/sessions/bot/recordings")

    m = manager(Broken())
    real = m.actions.open_session

    def open_while_another_binds(**kwargs):
        m.actions.grid.alive.add("grid9999")
        m.remember("bot", "grid9999", "", {"record": True})
        return real(**kwargs)

    m.actions.open_session = open_while_another_binds
    assert m.open_browser(caller(), record=True)["session"] == "bot"
    assert "cannot be noted" in caplog.text and "/data" not in caplog.text


@pytest.mark.parametrize("where", ["open", "reap"])
def test_a_note_path_that_is_refused_never_fails_the_open(where):
    from kubed.selenium_flow.names import InvalidName

    class Refused(Recorder):
        def expect(self, session, grid_id, browser):
            raise InvalidName("'recordings' is a link")

    if where == "open":
        m = manager(Refused())
        result = m.open_browser(caller(), record=True)
        assert result["recording_error"] == "this recording cannot be filed: InvalidName"
        assert m.actions.grid.alive == {"grid0001"}
    else:
        m = manager(Recorder())
        m.open_browser(caller(), record=True)
        m.recordings = Refused()
        m.actions.grid.alive.clear()
        assert m.resolve("bot") == "grid0002"


def test_a_quit_that_failed_does_not_start_the_collectors_clock():
    rec = Recorder()
    m = manager(rec)
    m.open_browser(caller(), record=True)

    def down(sid):
        raise ConnectionError("grid unreachable")

    m.actions.end_browser = down
    m.end_browser(caller())
    assert rec.finished == []
    assert not m.store.get("bot").attached
