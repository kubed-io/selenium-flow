"""`open_session` on a live session the caller holds joins it: it ends,
opens and restores nothing, and answers the session as it is (WS-85). A
`fresh` open, or one passing a setting the live browser does not have,
replaces it as WS-13 says."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from kubed.selenium_flow import faults
from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace import workspaces as workspaces_module
from kubed.selenium_flow.workspace.store import MemoryStore
from kubed.selenium_flow.workspace.workspaces import Workspaces

from .fakes import Clock, RecordingActions, record_of, shared_of
from .test_control_hosts import Gate
from .test_session_holder import FIRST, as_, built

pytestmark = pytest.mark.unit

KEY = sessions.key_of("shop", "first")
SAVED = {"cookies": [{"name": "a", "value": "1", "domain": "app.example.com"}]}


def live(**settings):
    """A manager whose FIRST holds a live browser opened with ``settings``."""
    workspaces, store, actions = built()
    workspaces.open_browser(as_(FIRST), **settings)
    return workspaces, store, actions


def test_a_live_session_you_hold_joins(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(workspaces_module, "time", SimpleNamespace(time=clock))
    workspaces, store, actions = live()
    before = record_of(store, "shop", "first")
    clock.now += 10
    joined = workspaces.open_browser(as_(FIRST))
    after = record_of(store, "shop", "first")
    assert joined["joined"] is True and joined["session"] == "first"
    assert "note" not in joined
    assert actions.opened == 1 and actions.closed == []
    assert (after.opened_at, after.opened_by) == (before.opened_at, before.opened_by)
    assert after.called_at == clock.now != before.called_at
    assert after.session_id == before.session_id


def test_fresh_replaces():
    workspaces, _, actions = live()
    told = workspaces.open_browser(as_(FIRST), fresh=True)
    assert "joined" not in told
    assert actions.opened == 2 and actions.closed == ["generated-1"]


@pytest.mark.parametrize(
    "setting", [{"browser": "firefox"}, {"width": 1024}], ids=["browser", "width"]
)
def test_a_changed_browser_or_setting_replaces(setting):
    workspaces, _, actions = live(width=1400)
    told = workspaces.open_browser(as_(FIRST), **setting)
    assert "joined" not in told
    assert actions.opened == 2 and actions.closed == ["generated-1"]


def test_the_same_values_passed_explicitly_join():
    workspaces, _, actions = live(browser="firefox", width=1400)
    told = workspaces.open_browser(as_(FIRST), browser="Firefox", width="1400")
    assert told["joined"] is True
    assert actions.opened == 1 and actions.closed == []


def test_header_defaults_alone_join():
    workspaces, _, actions = live(browser="chrome", width=1400)
    caller = replace(
        as_(FIRST), said=({}, {"sf-browser": "firefox", "sf-window-width": "800"})
    )
    assert caller.defaults == {"browser": "firefox", "width": 800}
    told = workspaces.open_browser(caller)
    assert told["joined"] is True
    assert actions.opened == 1 and actions.closed == []


def test_a_join_with_a_url_navigates_and_ends_nothing():
    workspaces, _, actions = live()
    page = "https://example.com/b"
    told = workspaces.open_browser(as_(FIRST), url=page)
    assert told["joined"] is True and told["url"] == page
    assert actions.calls == [("navigate", "generated-1", {"url": page})]
    assert actions.opened == 1 and actions.closed == []
    assert workspaces.history(KEY)[0]["url"] == page


def test_a_dead_browser_reopens_as_today():
    workspaces, _, actions = live()
    actions.grid.alive.clear()
    told = workspaces.open_browser(as_(FIRST))
    assert "joined" not in told
    assert actions.opened == 2


def controlled():
    """A manager with a gate, whose FIRST holds a live browser a person controls."""
    gate = Gate()
    actions = RecordingActions()
    workspaces = Workspaces(actions, MemoryStore(), control=gate)
    workspaces.open_browser(as_(FIRST))
    gate.shut.add(record_of(workspaces.store, "shop", "first").session_id)
    return workspaces, actions


def test_a_join_under_person_control_is_not_refused_and_a_replace_is():
    workspaces, actions = controlled()
    assert workspaces.open_browser(as_(FIRST))["joined"] is True
    with pytest.raises(faults.Controlled):
        workspaces.open_browser(as_(FIRST), fresh=True)
    assert actions.opened == 1 and actions.closed == []


def test_a_join_with_a_url_under_person_control_is_refused():
    workspaces, actions = controlled()
    with pytest.raises(faults.Controlled):
        workspaces.open_browser(as_(FIRST), url="https://example.com/b")
    assert actions.calls == []
    assert actions.opened == 1 and actions.closed == []


def test_restore_site_data_does_not_apply_to_a_join():
    workspaces, store, actions = live()
    store.update(KEY, lambda r: replace(r, site_data=dict(SAVED)))
    told = workspaces.open_browser(as_(FIRST), restore_site_data=False)
    assert told["joined"] is True and "site_data" not in told
    assert record_of(store, "shop", "first").site_data == SAVED
    assert actions.opened == 1


def test_a_new_name_is_listed_at_the_end():
    workspaces, store, _ = live()
    assert shared_of(store, "shop").sessions == ["first"]
    told = workspaces.open_browser(as_(FIRST), session="tester")
    assert told["session"] == "tester" and "joined" not in told
    assert shared_of(store, "shop").sessions == ["first", "tester"]


@pytest.mark.parametrize(
    "passed", [{"fresh": True}, {"width": 1024}], ids=["fresh", "changed"]
)
def test_a_replace_never_asks_the_grid(passed):
    workspaces, _, actions = live(width=1400)
    actions.grid.checked.clear()
    told = workspaces.open_browser(as_(FIRST), **passed)
    assert "joined" not in told
    assert actions.grid.checked == []


def test_a_join_keeps_a_held_reopen_notice_for_the_next_action():
    workspaces, store, _ = live()
    store.update(
        KEY,
        lambda r: replace(
            r, reopened={"browser": r.session_id, "cause": "reaped", "report": None}
        ),
    )
    told = workspaces.open_browser(as_(FIRST))
    assert told["joined"] is True and "reopened" not in told
    acted = workspaces.act(as_(FIRST), lambda s: {"url": "https://example.com/"})
    assert acted["reopened"]["cause"] == "reaped"


LOGIN = "https://example.com/login"
HINT = {"name": "login", "description": "signs in"}


def hinting():
    workspaces, _, actions = built()
    workspaces.hints = lambda workspace, url: [HINT] if "/login" in url else []
    workspaces.open_browser(as_(FIRST), url=LOGIN)
    return workspaces, actions


def test_a_join_hints_for_the_page_it_is_on():
    workspaces, actions = hinting()
    told = workspaces.open_browser(as_(FIRST))
    assert told["joined"] is True and told["url"] == LOGIN
    assert told["flows_here"] == [HINT]
    assert actions.opened == 1
