"""The session's side of site data: merge a save, restore on open, announce."""

from kubed.selenium_flow.core import site_data
from tests.conftest import NAMED, RecordingActions, manager

URL = "https://app.example.com/x"


class SiteActions(RecordingActions):
    def __init__(self):
        super().__init__()
        self.site_data_seen = []
        self.retired = []
        self.landing_only = False

    def open_session(self, url=None, site_data=None, **kw):
        self.site_data_seen.append(site_data)
        opened = super().open_session(url=url, **kw)
        if site_data:
            opened["site_data"] = {
                "restored": ["x"], "waiting": ["https://w.test"], "skipped": [],
            }
            opened["_site_data_pending"] = {
                "origins": [] if self.landing_only else ["https://w.test"],
                "script": "p1",
            }
        return opened

    def save_site_data(self, session_id, url=None):
        return {
            "url": URL,
            "title": "t",
            site_data.CAPTURED: {
                "cookies": [{"name": "a", "value": "1", "domain": "app.example.com"}],
                "origin": "https://app.example.com",
                "local": {"k": "v"},
                "session": {},
            },
        }

    def retire_site_data(self, session_id, script):
        self.retired.append(script)


def opened_with_save():
    m = manager(SiteActions())
    m.open_browser(NAMED)
    m.act(NAMED, lambda s: m.actions.save_site_data(s))
    return m


def reopened(m, **kw):
    m.end_browser(NAMED)
    return m.open_browser(NAMED, **kw)


def test_a_save_is_merged_into_the_record_and_never_returned_raw(named_caller):
    m = manager(SiteActions())
    m.open_browser(NAMED)
    result = m.act(NAMED, lambda s: m.actions.save_site_data(s))
    assert site_data.CAPTURED not in result
    assert result["saved"] and result["uri"] == "session://site-data"
    assert "https://app.example.com" in m.store.get(NAMED).site_data["origins"]


def test_open_restores_what_was_saved_and_says_so(named_caller):
    m = opened_with_save()
    told = reopened(m)
    assert m.actions.site_data_seen[-1]
    assert told["site_data"]["waiting"] == ["https://w.test"]
    assert "_site_data_pending" not in told
    record = m.store.get(NAMED)
    assert record.site_data["pending"]["browser"] == record.session_id


def test_restore_off_deletes_and_says_how_many(named_caller):
    m = opened_with_save()
    told = reopened(m, restore_site_data=False)
    assert m.actions.site_data_seen[-1] is None
    assert told["site_data"] == {"forgotten": 1}
    assert m.store.get(NAMED).site_data == {}


def test_nothing_saved_means_no_hint(named_caller):
    m = manager(SiteActions())
    assert "site_data" not in m.open_browser(NAMED)


def test_arriving_on_a_waiting_origin_is_announced_once_and_retires_the_script(
    named_caller,
):
    m = opened_with_save()
    reopened(m)
    arrive = {"url": "https://w.test/page"}
    first = m.act(NAMED, lambda s: dict(arrive))
    assert first["site_data"] == {
        "restored": ["https://w.test"], "uri": "session://site-data/w.test",
    }
    assert m.actions.retired == ["p1"]
    assert "site_data" not in m.act(NAMED, lambda s: dict(arrive))
    assert m.actions.retired == ["p1"]


def test_a_quiet_call_carries_no_hint(named_caller):
    m = opened_with_save()
    reopened(m)
    quiet = m.act(NAMED, lambda s: {"url": "https://elsewhere.test"})
    assert "site_data" not in quiet


def test_a_silent_reopen_restores_and_the_next_result_says_so(named_caller):
    m = opened_with_save()
    m.actions.grid.alive.clear()
    first = m.act(NAMED, lambda s: {"url": "https://elsewhere.test"})
    assert m.actions.site_data_seen[-1]
    assert first["site_data"]["waiting"] == ["https://w.test"]
    assert "site_data" not in m.act(NAMED, lambda s: {"url": "https://elsewhere.test"})


def test_remember_keeps_site_data(named_caller):
    m = opened_with_save()
    m.open_browser(NAMED)
    m.open_browser(NAMED)
    assert "https://app.example.com" in m.store.get(NAMED).site_data["origins"]


def test_describe_summarises_site_data(named_caller):
    m = opened_with_save()
    assert m.describe(NAMED)["site_data"] == {
        "sites": 1, "uri": "session://site-data",
    }


def test_restore_site_data_accepts_a_string_false(named_caller):
    m = opened_with_save()
    reopened(m, restore_site_data="false")
    assert m.store.get(NAMED).site_data == {}


def test_a_landing_only_restore_retires_the_script_at_open(named_caller):
    m = opened_with_save()
    m.actions.landing_only = True
    reopened(m)
    assert m.actions.retired == ["p1"]
    assert "pending" not in m.store.get(NAMED).site_data


def test_a_landing_only_silent_reopen_retires_and_still_announces(named_caller):
    m = opened_with_save()
    m.actions.landing_only = True
    m.actions.grid.alive.clear()
    first = m.act(NAMED, lambda s: {"url": "https://elsewhere.test"})
    assert m.actions.retired == ["p1"]
    assert first["site_data"]["restored"] == ["x"]
    assert "pending" not in m.store.get(NAMED).site_data
    assert "site_data" not in m.act(NAMED, lambda s: {"url": "https://e.test"})


def test_the_pending_note_never_reaches_the_browser(named_caller):
    m = opened_with_save()
    reopened(m)
    reopened(m)
    assert m.actions.site_data_seen[-1]
    assert "pending" not in m.actions.site_data_seen[-1]
