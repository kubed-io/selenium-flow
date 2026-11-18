"""The tab state, its stores and the rules that follow a browser's tabs."""

import pytest

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.core import tabs as tabs_module
from kubed.selenium_flow.core.tabs import (
    Known,
    MemoryTabs,
    RedisTabs,
    TabState,
    matching,
    merged,
    reconcile,
)
from kubed.selenium_flow.workspace.store import MemoryStore, RedisStore, Workspace

from . import site_data_fakes
from .fakes import Clock, FakeChannel, FakeRedis

A = "https://a.example.test/"
P = "https://pay.example.test/"

ONE = TabState(2, "t1", (Known("t1", "h1"),))


def state(current, *known, next=None):
    """A state from ``(tab, handle, from)`` triples."""
    ks = tuple(Known(t, h, f) for t, h, f in known)
    return TabState(next or len(ks) + 1, current, ks)


def test_the_first_sight_of_a_browser_numbers_its_tabs_and_reports_nothing():
    r = reconcile(None, ["h1", "h2"], "h2")
    assert [k.tab for k in r.state.known] == ["t1", "t2"] and r.state.next == 3
    assert r.state.current == "t2" and (r.opened, r.closed, r.moved) == ([], [], False)


def test_a_seed_whose_current_window_is_gone_moves_to_the_newest():
    r = reconcile(None, ["h1", "h2"], "gone")
    assert (r.state.current, r.moved, r.handle) == ("t2", True, "h2")


def test_a_new_handle_gets_the_next_id_and_becomes_current():
    r = reconcile(ONE, ["h1", "h9"])
    assert r.opened == ["t2"] and r.state.by_id("t2").from_ == "t1"
    assert (r.state.current, r.moved, r.handle) == ("t2", True, "h9")


def test_handles_first_seen_together_are_numbered_in_listed_order():
    r = reconcile(ONE, ["h1", "hB", "hA"])
    assert r.opened == ["t2", "t3"]
    assert r.state.by_id("t2").handle == "hB" and r.state.by_id("t3").handle == "hA"
    assert (r.state.current, r.handle) == ("t3", "hA")


def test_ids_are_never_reused():
    two = state("t2", ("t1", "h1", None), ("t2", "h2", "t1"))
    gone = reconcile(two, ["h1"]).state
    assert gone.next == 3
    r = reconcile(gone, ["h1", "h3"])
    assert r.opened == ["t3"]


def test_when_the_current_tab_closes_the_session_goes_back_to_where_it_came_from():
    three = state(
        "t3", ("t1", "h1", None), ("t2", "h2", "t1"), ("t3", "h3", "t2")
    )
    r = reconcile(three, ["h1", "h2"])
    assert (r.state.current, r.closed, r.moved, r.handle) == ("t2", ["t3"], True, "h2")


def test_when_that_tab_is_gone_too_the_newest_left_is_current():
    four = state(
        "t3",
        ("t1", "h1", None),
        ("t2", "h2", "t1"),
        ("t3", "h3", "t2"),
        ("t4", "h4", "t1"),
    )
    r = reconcile(four, ["h1", "h4"])
    assert (r.state.current, r.closed) == ("t4", ["t2", "t3"])


def test_a_new_tab_wins_over_a_closed_current():
    r = reconcile(ONE, ["h9"])
    assert (r.closed, r.opened) == (["t1"], ["t2"])
    assert (r.state.current, r.handle) == ("t2", "h9")
    # It came from the tab that just closed.
    assert r.state.by_id("t2").from_ == "t1"


def test_nothing_changed_is_no_move():
    r = reconcile(ONE, ["h1"])
    assert r.state == ONE and (r.opened, r.closed, r.moved) == ([], [], False)
    assert r.handle == "h1"


def test_a_state_reads_back_what_it_wrote_and_nothing_else():
    s = state("t2", ("t1", "h1", None), ("t2", "h2", "t1"))
    assert TabState.from_json(s.to_json()) == s
    assert TabState.from_json(s.to_json().encode()) == s
    assert '"from": "t1"' in s.to_json()
    for raw in ("not json", "[]", '{"next": 1}', '{"next": "x", "current": "t1", "known": []}'):
        assert TabState.from_json(raw) is None


def test_newest_is_the_highest_id_not_the_last_listed():
    s = state("t1", ("t10", "h10", None), ("t9", "h9", None))
    assert s.newest().tab == "t10"


def test_merged_adds_nothing_when_nothing_changed():
    assert merged({"url": A}, {}, {}) == {"url": A}


def test_a_move_after_the_call_replaces_url_and_title_and_clears_the_frame():
    after = {
        "opened": [{"tab": "t2", "url": P}],
        "tab": "t2",
        "url": P,
        "title": "Sign in",
        "in_frame": False,
    }
    assert merged({"url": A, "title": "Shop", "in_frame": True}, {}, after) == {
        "url": P,
        "title": "Sign in",
        "in_frame": False,
        "tab": "t2",
        "opened": [{"tab": "t2", "url": P}],
    }


def test_a_frame_results_own_in_frame_wins_over_a_move_before_it():
    before = {"tab": "t2", "url": P, "title": "Sign in", "in_frame": False}
    out = merged({"url": P, "in_frame": True}, before, {})
    assert out["in_frame"] is True and out["tab"] == "t2"


def test_a_move_before_the_call_says_in_frame_false_when_the_call_did_not():
    before = {"tab": "t2", "url": P, "title": "Sign in", "in_frame": False}
    out = merged({"url": A}, before, {})
    assert out == {"url": A, "tab": "t2", "in_frame": False}


def test_opened_and_closed_join_both_checks_in_order():
    before = {"opened": [{"tab": "t2"}], "closed": ["t1"]}
    after = {"opened": [{"tab": "t3"}], "closed": ["t2"]}
    out = merged({"url": A}, before, after)
    assert out["opened"] == [{"tab": "t2"}, {"tab": "t3"}]
    assert out["closed"] == ["t1", "t2"]
    assert "tab" not in out


def test_a_result_that_is_not_a_dict_is_left_alone():
    after = {"tab": "t2", "url": P}
    assert merged("done", {}, after) == "done"
    assert merged(None, {}, after) is None


STATE = state("t2", ("t1", "h1", None), ("t2", "h2", "t1"))


def make(kind):
    if kind == "memory":
        return MemoryTabs()
    return RedisTabs(FakeRedis(), prefix="sf:tabs:", ttl=60)


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_tab_state_round_trips(kind):
    tabs = make(kind)
    assert tabs.get("abc") is None
    tabs.set("abc", STATE)
    assert tabs.get("abc") == STATE
    tabs.forget("abc")
    assert tabs.get("abc") is None


def test_memory_tab_state_expires_with_the_ttl():
    clock = Clock()
    tabs = MemoryTabs(ttl=60, clock=clock)
    tabs.set("abc", STATE)
    clock.now += 59
    assert tabs.get("abc") == STATE
    clock.now += 2
    assert tabs.get("abc") is None


def test_redis_tab_state_is_kept_under_the_workspace_prefix():
    fake = FakeRedis()
    tabs = matching(RedisStore(fake))
    tabs.set("abc", STATE)
    assert fake.expiries["selenium-flow:workspace:tabs:abc"] == 86400


def test_matching_follows_the_workspace_store_backend_and_ttl():
    assert matching(MemoryStore(ttl=60))._ttl == 60
    assert matching(MemoryStore()).kind == "memory"
    assert matching(RedisStore(FakeRedis(), ttl=60))._ttl == 60


def test_an_unreadable_stored_state_is_absent():
    fake = FakeRedis()
    fake.set("sf:tabs:abc", "garbage")
    assert RedisTabs(fake, prefix="sf:tabs:", ttl=60).get("abc") is None


def test_redis_failures_raise():
    class Down:
        def get(self, key, *args, **kwargs):
            raise ConnectionError("nope")

        set = delete = get

    tabs = RedisTabs(Down(), prefix="p:", ttl=60)
    with pytest.raises(ConnectionError):
        tabs.get("abc")
    with pytest.raises(ConnectionError):
        tabs.set("abc", STATE)
    with pytest.raises(ConnectionError):
        tabs.forget("abc")


def test_records_skip_tab_state():
    fake = FakeRedis()
    store = RedisStore(fake)
    store.set("caller", Workspace())
    matching(store).set("abc", STATE)
    assert "tabs:abc" not in store.records()
    assert list(store.records()) == ["caller"]


# ---- Actions.check_tabs --------------------------------------------------------

LOGIN = "https://sso.example.test/login"


def _switches(driver):
    return [e for e in driver.log if e[:2] == ("switch_to", "window")]


def _popup(driver, handle="w2", page=(LOGIN, "Sign in")):
    driver.pages[handle] = page


class _Held:
    """A control that refuses every tab move on the one browser it holds."""

    def refusal(self, session_id, tool):
        return faults.Controlled("a person has control of this browser")


def test_a_check_on_one_tab_asks_the_browser_once(actions, scripted):
    driver = scripted()
    assert actions.check_tabs("abc") == {} and actions.check_tabs("abc") == {}
    assert driver.names().count("window_handles") == 2
    assert ("switch_to", "window", "w1") not in driver.log


def test_a_popup_is_followed_and_reported(actions, scripted):
    driver = scripted()
    actions.check_tabs("abc")
    _popup(driver)
    assert actions.check_tabs("abc") == {
        "opened": [{"tab": "t2", "url": LOGIN}],
        "tab": "t2",
        "url": LOGIN,
        "title": "Sign in",
        "in_frame": False,
    }
    assert ("switch_to", "window", "w2") in driver.log
    assert actions.tabs.get("abc").current == "t2"


def test_a_closed_current_tab_moves_back_to_its_opener(actions, scripted):
    driver = scripted()
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    del driver.pages["w2"]
    assert actions.check_tabs("abc") == {
        "closed": ["t2"],
        "tab": "t1",
        "url": "https://example.test/",
        "title": "Example",
        "in_frame": False,
    }
    assert _switches(driver)[-1] == ("switch_to", "window", "w1")


@pytest.mark.parametrize("by_activate", [True, False])
def test_the_moved_to_tab_is_brought_to_front(
    actions, scripted, monkeypatch, by_activate
):
    monkeypatch.setattr(tabs_module, "FRONT_BY_ACTIVATE", by_activate)
    driver = scripted()
    channel = FakeChannel(tree=[{"context": "w1", "url": ""}, {"context": "w2", "url": LOGIN}])
    actions.channels = site_data_fakes.channels_for(channel)
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    activated = [p for m, p in channel.log if m == "browsingContext.activate"]
    if by_activate:
        assert activated == [{"context": "w2"}]
    else:
        assert activated == []
        assert _switches(driver) == [("switch_to", "window", "w2")] * 2


def test_with_several_tabs_every_check_brings_the_current_one_back_to_front(
    actions, scripted
):
    """A person activated another tab between two calls."""
    driver = scripted()
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    driver.__dict__["current"] = "w1"
    before = len(_switches(driver))
    assert actions.check_tabs("abc") == {}
    assert _switches(driver)[before:] == [("switch_to", "window", "w2")]
    assert driver.current == "w2"


def test_no_tab_is_brought_to_front_while_a_person_holds_the_browser(
    actions, scripted, monkeypatch
):
    monkeypatch.setattr(tabs_module, "FRONT_BY_ACTIVATE", True)
    driver = scripted()
    channel = FakeChannel(tree=[{"context": "w1", "url": ""}, {"context": "w2", "url": LOGIN}])
    actions.channels = site_data_fakes.channels_for(channel)
    actions.check_tabs("abc")
    actions.control = _Held()
    _popup(driver)
    found = actions.check_tabs("abc")
    assert found["tab"] == "t2"
    assert ("switch_to", "window", "w2") in driver.log
    assert [m for m, _ in channel.log if m == "browsingContext.activate"] == []
    assert actions.check_tabs("abc") == {}  # still holds: no front, no activate
    assert [m for m, _ in channel.log if m == "browsingContext.activate"] == []


def test_a_tab_opened_and_closed_inside_one_call_is_never_reported(actions, scripted):
    scripted()
    channel = FakeChannel()
    actions.channels = site_data_fakes.channels_for(channel)
    actions.visited = lambda: ["https://other.example.test"]
    actions.check_tabs("abc")
    actions.save_site_data("abc")  # its spare context opens and closes inside
    assert [m for m, _ in channel.log if m == "browsingContext.create"]
    assert actions.check_tabs("abc") == {}


def test_a_bidi_failure_never_fails_a_check(actions, scripted, monkeypatch):
    monkeypatch.setattr(tabs_module, "FRONT_BY_ACTIVATE", True)
    driver = scripted()
    channel = FakeChannel(down={"browsingContext.getTree", "browsingContext.activate"})
    actions.channels = site_data_fakes.channels_for(channel)
    actions.check_tabs("abc")
    _popup(driver, "w2", ("https://one.example.test/", "One"))
    _popup(driver, "w3", (LOGIN, "Sign in"))
    assert actions.check_tabs("abc") == {
        "opened": [{"tab": "t2", "url": None}, {"tab": "t3", "url": LOGIN}],
        "tab": "t3",
        "url": LOGIN,
        "title": "Sign in",
        "in_frame": False,
    }
    assert ("switch_to", "window", "w3") in driver.log


def test_ending_a_browser_forgets_its_tabs(actions, scripted, monkeypatch):
    scripted()
    monkeypatch.setattr(actions.grid, "quit", lambda session_id: None)
    actions.check_tabs("abc")
    assert actions.tabs.get("abc") is not None
    actions.end_browser("abc")
    assert actions.tabs.get("abc") is None


def test_a_browser_with_no_tab_left_is_a_404(actions, scripted):
    driver = scripted()
    driver.pages.clear()
    with pytest.raises(faults.NotFound) as caught:
        actions.check_tabs("abc")
    assert errors.status_for(caught.value) == 404
    assert "end_browser" in str(caught.value)
