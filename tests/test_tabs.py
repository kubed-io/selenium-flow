"""The tab state, its stores and the rules that follow a browser's tabs."""

import pytest
from selenium.common.exceptions import NoSuchWindowException, WebDriverException

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.core import browser as browser_module
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


def test_a_results_own_tab_wins_over_a_move_before_it():
    """`tab switch` answers the tab it switched to, after a popup moved the
    session first: its own word stands."""
    before = {"tab": "t2", "url": P, "title": "Sign in", "in_frame": False}
    out = merged({"url": A, "tab": "t1", "in_frame": False}, before, {})
    assert out["tab"] == "t1"


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


def _linked(actions, driver, **kwargs):
    """A BiDi channel whose ``getTree`` lists ``driver``'s windows, as a real
    browser's does: a window handle is its context id."""
    channel = FakeChannel(**kwargs)
    channel.windows = driver.pages
    actions.channels = site_data_fakes.channels_for(channel)
    return channel


def _activated(channel):
    return [p["context"] for m, p in channel.log if m == "browsingContext.activate"]


def _trees(channel):
    return [p for m, p in channel.log if m == "browsingContext.getTree"]


def test_the_moved_to_tab_is_brought_to_front_by_activate(actions, scripted):
    driver = scripted()
    channel = _linked(actions, driver)
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    assert _activated(channel) == ["w2"]
    assert _switches(driver) == [("switch_to", "window", "w2")]


def test_with_several_tabs_every_check_brings_the_current_one_to_front(
    actions, scripted
):
    """A person activated another tab between two calls: WebDriver's window is
    unchanged, so only BiDi brings the session's tab back to the front."""
    driver = scripted()
    channel = _linked(actions, driver)
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    switched = len(_switches(driver))
    assert actions.check_tabs("abc") == {}
    assert _activated(channel)[-1] == "w2"
    assert len(_switches(driver)) == switched


def test_a_check_reads_the_tree_once_and_activates_from_it(actions, scripted):
    driver = scripted()
    channel = _linked(actions, driver)
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    channel.log.clear()
    actions.check_tabs("abc")
    assert _trees(channel) == [{"maxDepth": 0}]
    assert _activated(channel) == ["w2"]


def test_a_session_on_its_tab_is_never_switched_to_it(actions, scripted):
    """A switch to the window the session is on puts it back at the top page,
    out of any frame (measured on Chrome and Firefox)."""
    driver, _ = _two(actions, scripted)
    for _ in range(3):
        assert actions.check_tabs("abc") == {}
    assert not _switches(driver)


def test_a_frame_switch_survives_a_call_with_two_tabs_open(actions, scripted):
    driver, _ = _two(actions, scripted)
    actions.check_tabs("abc")  # the call's check before
    assert actions.frame("abc", index=0)["in_frame"] is True
    assert actions.check_tabs("abc") == {}  # its check after
    assert actions.check_tabs("abc") == {}  # the next call's check before
    assert browser_module.in_frame(driver) is True
    assert not _switches(driver)


def test_without_bidi_no_tab_is_brought_to_front(actions, scripted):
    driver, _ = _two(actions, scripted)
    actions.channels = None
    assert actions.check_tabs("abc") == {}
    assert not _switches(driver)


def test_a_driver_on_another_window_is_moved_back_to_the_current_tab(
    actions, scripted
):
    driver, _ = _two(actions, scripted)
    driver.__dict__["current"] = "w2"
    assert actions.check_tabs("abc") == {}
    assert _switches(driver) == [("switch_to", "window", "w1")]
    assert driver.current == "w1"


def test_no_tab_is_brought_to_front_while_a_person_holds_the_browser(
    actions, scripted
):
    driver = scripted()
    channel = _linked(actions, driver)
    actions.check_tabs("abc")
    actions.control = _Held()
    _popup(driver)
    found = actions.check_tabs("abc")
    assert found["tab"] == "t2"
    assert ("switch_to", "window", "w2") in driver.log
    assert _activated(channel) == []
    assert actions.check_tabs("abc") == {}  # still holds: no front, no activate
    assert _activated(channel) == []


def test_under_control_a_closed_tab_moves_the_driver_without_fronting(
    actions, scripted
):
    """ACT-52: the driver needs a live window, so it switches to the opener;
    the session itself brings nothing to the front."""
    driver = scripted()
    channel = _linked(actions, driver)
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    actions.control = _Held()
    channel.log.clear()
    del driver.pages["w2"]
    found = actions.check_tabs("abc")
    assert (found["tab"], found["closed"]) == ("t1", ["t2"])
    assert _switches(driver)[-1] == ("switch_to", "window", "w1")
    assert _activated(channel) == []


def test_the_tabs_are_listed_over_bidi_not_window_handles(actions, scripted):
    """Chrome holds `window_handles` while a popup loads a host that never
    answers; `getTree` answers at once."""
    driver = scripted()
    _linked(actions, driver)
    actions.check_tabs("abc")
    _popup(driver)
    assert actions.check_tabs("abc")["tab"] == "t2"
    assert "window_handles" not in driver.names()


def test_tabs_first_seen_together_are_numbered_as_bidi_lists_them(actions, scripted):
    driver = scripted()
    actions.check_tabs("abc")
    _popup(driver, "w2", ("https://one.example.test/", "One"))
    _popup(driver, "w3", (LOGIN, "Sign in"))
    channel = FakeChannel(tree=[
        {"context": "w1", "url": ""},
        {"context": "w3", "url": LOGIN},
        {"context": "w2", "url": "https://one.example.test/"},
    ])
    actions.channels = site_data_fakes.channels_for(channel)
    found = actions.check_tabs("abc")
    assert [o["tab"] for o in found["opened"]] == ["t2", "t3"]
    assert actions.tabs.get("abc").by_id("t2").handle == "w3"
    assert found["tab"] == "t3" and found["url"] == "https://one.example.test/"


def test_without_bidi_the_tabs_are_listed_by_window_handles(actions, scripted):
    driver = scripted()
    _linked(actions, driver, down={"browsingContext.getTree"})
    actions.check_tabs("abc")
    _popup(driver)
    assert actions.check_tabs("abc")["tab"] == "t2"
    assert "window_handles" in driver.names()


def test_a_page_that_cannot_be_read_after_a_move_keeps_the_move(
    actions, scripted, monkeypatch
):
    driver = scripted()
    actions.check_tabs("abc")
    _popup(driver)

    def unreadable(_driver):
        raise WebDriverException("timeout: " + LOGIN)

    with monkeypatch.context() as patched:
        patched.setattr(browser_module, "page_state", unreadable)
        assert actions.check_tabs("abc") == {
            "opened": [{"tab": "t2", "url": None}],
            "tab": "t2",
            "url": None,
            "title": None,
            "in_frame": False,
        }
    assert actions.tabs.get("abc").current == "t2"
    assert actions.check_tabs("abc") == {}  # never followed again


def test_a_tab_opened_and_closed_inside_one_call_is_never_reported(actions, scripted):
    driver = scripted()
    channel = FakeChannel()
    channel.windows = driver.pages
    appeared = []
    create = channel._browsingContext_create

    def watched(params):
        reply = create(params)
        appeared.append(set(driver.pages))
        return reply

    channel._browsingContext_create = watched
    actions.channels = site_data_fakes.channels_for(channel)
    actions.visited = lambda: ["https://other.example.test"]
    actions.check_tabs("abc")
    actions.save_site_data("abc")  # its spare tab appears and vanishes inside
    assert appeared and len(appeared[0]) == 2 and set(driver.pages) == {"w1"}
    assert actions.check_tabs("abc") == {}
    assert actions.tabs.get("abc").next == 2


def _vanishing(driver, handle):
    """Make the window ``handle`` close itself as the driver switches to it."""

    def switch(target):
        if target == handle and handle in driver.pages:
            del driver.pages[handle]
            raise NoSuchWindowException("window closed")
        driver.log.append(("switch_to", "window", target))
        driver.__dict__["current"] = target

    driver.switch_to.window = switch


def test_a_popup_that_closes_before_the_switch_never_fails_the_check(actions, scripted):
    driver = scripted()
    actions.check_tabs("abc")
    _popup(driver)
    _vanishing(driver, "w2")
    assert actions.check_tabs("abc") == {}
    state = actions.tabs.get("abc")
    assert (state.current, state.next) == ("t1", 2)


def test_a_current_tab_that_closes_after_it_was_listed_is_reported_closed(
    actions, scripted, monkeypatch
):
    driver = scripted()
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    stored = actions.tabs.get

    def closing(session_id):  # read right after the tabs were listed
        driver.pages.pop("w2", None)
        return stored(session_id)

    monkeypatch.setattr(actions.tabs, "get", closing)
    found = actions.check_tabs("abc")
    assert found["closed"] == ["t2"] and found["tab"] == "t1"
    assert actions.tabs.get("abc").current == "t1"


def test_a_bidi_failure_never_fails_a_check(actions, scripted):
    driver = scripted()
    _linked(actions, driver, down={"browsingContext.getTree", "browsingContext.activate"})
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


def test_an_activate_bidi_refuses_never_fails_a_check(actions, scripted):
    driver = scripted()
    _linked(actions, driver, refuse={"browsingContext.activate"})
    actions.check_tabs("abc")
    _popup(driver)
    assert actions.check_tabs("abc")["tab"] == "t2"


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


def test_a_screenshot_after_a_popup_is_the_popup(actions, scripted):  # ACT-53
    driver = scripted()
    actions.check_tabs("abc")
    _popup(driver)
    actions.check_tabs("abc")
    assert actions.screenshot("abc", save=False)["url"] == LOGIN


# ---- Actions.tab (ACT-48) ------------------------------------------------------

B = "https://b.example.test/"
H = "https://h.example.test/"


def _two(actions, scripted, on="t1", **channel):
    """A browser on t1 (A, "Example") and t2 (B, "Bee", opened from t1), the
    session on ``on``. Returns ``(driver, channel)``."""
    driver = scripted(url=A, title="Example")
    driver.pages["w2"] = (B, "Bee")
    driver.__dict__["current"] = "w1" if on == "t1" else "w2"
    chan = FakeChannel(
        tree=channel.pop("tree", None),
        titles=channel.pop("titles", {"w2": "Bee"}),
        **channel,
    )
    chan.windows = driver.pages  # what `getTree` lists without a ``tree``
    actions.channels = site_data_fakes.channels_for(chan)
    actions.tabs.set(
        "abc", state(on, ("t1", "w1", None), ("t2", "w2", "t1"))
    )
    return driver, chan


def test_list_names_each_tab_in_opening_order_without_switching(actions, scripted):
    driver, _ = _two(actions, scripted)
    result = actions.tab("abc")
    assert result["tabs"] == [
        {"tab": "t1", "url": A, "title": "Example", "current": True},
        {"tab": "t2", "url": B, "title": "Bee", "current": False},
    ]
    assert result["tab"] == "t1" and "in_frame" not in result
    assert result["url"] == A and result["title"] == "Example"
    assert not [e for e in driver.log if e[:2] == ("switch_to", "window")]


def test_reading_the_tabs_never_follows_a_popup(actions, scripted):
    driver, _ = _two(actions, scripted)
    _popup(driver, "w3")
    seen = actions.tabs_listed("abc")
    assert [t["tab"] for t in seen] == ["t1", "t2"]
    assert seen[0]["current"] is True
    assert not _switches(driver)
    assert actions.tabs.get("abc").next == 3
    assert actions.check_tabs("abc")["opened"] == [{"tab": "t3", "url": LOGIN}]


def test_a_closed_current_window_lists_the_opener_as_current(actions, scripted):
    driver, _ = _two(actions, scripted, titles={"w1": "Example", "w2": "Bee"})
    driver.pages.pop("w1")
    seen = actions.tabs_listed("abc")
    assert seen == [{"tab": "t2", "url": B, "title": "Bee", "current": True}]
    assert not _switches(driver)


def test_a_driver_on_another_tab_does_not_mislabel_the_current_one(actions, scripted):
    driver, _ = _two(actions, scripted, titles={"w1": "Example", "w2": "Bee"})
    driver.__dict__["current"] = "w2"
    actions.tabs.set("abc", state("t1", ("t1", "w1", None), ("t2", "w2", "t1")))
    seen = actions.tabs_listed("abc")
    assert seen == [
        {"tab": "t1", "url": A, "title": "Example", "current": True},
        {"tab": "t2", "url": B, "title": "Bee", "current": False},
    ]
    assert not _switches(driver)


def _never_set(actions, monkeypatch):
    def refused(*args):
        raise AssertionError("a read wrote the tab state")

    monkeypatch.setattr(actions.tabs, "set", refused)


def test_reading_the_tabs_of_a_browser_nothing_has_seen_stores_nothing(
    actions, scripted, monkeypatch
):
    scripted(url=A, title="Example")
    _never_set(actions, monkeypatch)
    assert [t["tab"] for t in actions.tabs_listed("abc")] == ["t1"]
    assert actions.tabs.get("abc") is None


def test_reading_the_tabs_never_writes_the_state(actions, scripted, monkeypatch):
    driver, _ = _two(actions, scripted)
    _popup(driver, "w3")
    del driver.pages["w2"]
    _never_set(actions, monkeypatch)
    assert [t["tab"] for t in actions.tabs_listed("abc")] == ["t1"]


def test_open_session_numbers_its_one_tab(actions, scripted):
    scripted()
    actions.open_session()
    assert actions.tabs.get("abc") == TabState(2, "t1", (Known("t1", "w1"),))


def test_a_popup_there_at_the_first_check_after_open_is_followed(actions, scripted):
    driver = scripted()
    actions.open_session()
    _popup(driver)  # the first page opened it, before any call looked
    assert actions.check_tabs("abc") == {
        "opened": [{"tab": "t2", "url": LOGIN}],
        "tab": "t2",
        "url": LOGIN,
        "title": "Sign in",
        "in_frame": False,
    }


def test_a_reopen_starts_the_tabs_again_at_t1(actions, scripted):
    driver = scripted()
    actions.open_session()
    _popup(driver)
    actions.check_tabs("abc")
    fresh = scripted(session_id="abc")
    actions.open_session()
    assert actions.tabs.get("abc") == TabState(2, "t1", (Known("t1", "w1"),))
    assert fresh.current == "w1"


def test_a_tab_bidi_cannot_read_has_null_url_and_title(actions, scripted):
    _two(actions, scripted, down={"browsingContext.getTree"})
    tabs = actions.tab("abc")["tabs"]
    assert tabs[1] == {"tab": "t2", "url": None, "title": None, "current": False}
    assert tabs[0]["url"] == A


def test_a_handle_that_is_not_a_context_id_reads_as_null(actions, scripted):
    driver, _ = _two(actions, scripted, tree=[{"context": "other", "url": B}])
    listed = actions._listed("abc", driver, actions.tabs.get("abc"))
    assert listed[1] == {"tab": "t2", "url": None, "title": None, "current": False}


def test_a_title_that_cannot_be_read_is_null_with_its_url(actions, scripted):
    _two(actions, scripted, down={"script.evaluate"})
    assert actions.tab("abc")["tabs"][1] == {
        "tab": "t2", "url": None, "title": None, "current": False,
    }


def test_with_a_dialog_open_the_current_tab_reads_null(actions, scripted):
    driver, _ = _two(actions, scripted)
    driver.__dict__["dialog"] = "sure?"
    assert actions.tab("abc")["tabs"][0]["url"] is None


def test_open_makes_a_new_tab_current_and_loads_url(actions, scripted):
    driver, _ = _two(actions, scripted)
    result = actions.tab("abc", "open", url=H)
    assert result["tab"] == "t3" and result["url"] == H and result["in_frame"] is False
    assert ("switch_to", "new_window", "tab") in driver.log and ("get", H) in driver.log
    state_now = actions.tabs.get("abc")
    assert state_now.current == "t3" and state_now.by_id("t3").from_ == "t1"
    assert state_now.next == 4


def test_open_with_no_url_leaves_the_tab_blank(actions, scripted):
    driver, _ = _two(actions, scripted)
    assert actions.tab("abc", "open")["tab"] == "t3"
    assert not [e for e in driver.log if e[0] == "get"]


def test_switch_by_id(actions, scripted):
    driver, _ = _two(actions, scripted)
    result = actions.tab("abc", "switch", id="t2")
    assert (result["tab"], result["url"], result["in_frame"]) == ("t2", B, False)
    assert ("switch_to", "window", "w2") in driver.log
    assert actions.tabs.get("abc").current == "t2"


def test_switch_by_url_takes_the_newest_match(actions, scripted):
    driver, _ = _two(actions, scripted)
    driver.pages["w3"] = (A + "again", "Again")
    actions.tabs.set(
        "abc", state("t1", ("t1", "w1", None), ("t2", "w2", "t1"), ("t3", "w3", "t1"))
    )
    actions.channels = site_data_fakes.channels_for(FakeChannel(tree=[
        {"context": "w1", "url": A}, {"context": "w2", "url": B},
        {"context": "w3", "url": A + "again"},
    ]))
    assert actions.tab("abc", "switch", url="a.example.test")["tab"] == "t3"


def test_switch_with_neither_takes_the_newest(actions, scripted):
    _two(actions, scripted)
    assert actions.tab("abc", "switch")["tab"] == "t2"


def test_close_with_no_id_closes_the_current_and_returns_to_its_opener(actions, scripted):
    driver, _ = _two(actions, scripted, on="t2")
    result = actions.tab("abc", "close")
    assert result["tab"] == "t1" and result["closed"] == ["t2"]
    assert result["in_frame"] is False and result["url"] == A
    assert "w2" not in driver.pages and actions.tabs.get("abc").current == "t1"


def test_closing_another_tab_keeps_the_current_one(actions, scripted):
    driver, _ = _two(actions, scripted, on="t2")
    result = actions.tab("abc", "close", id="t1")
    assert (result["tab"], result["closed"]) == ("t2", ["t1"])
    switches = [e for e in driver.log if e[0] in ("close",) or e[:2] == ("switch_to", "window")]
    assert switches[:3] == [
        ("switch_to", "window", "w1"), ("close",), ("switch_to", "window", "w2"),
    ]
    assert driver.current == "w2" and list(driver.pages) == ["w2"]


def test_a_switch_to_a_tab_that_closed_is_a_400_listing_the_tabs_left(
    actions, scripted
):
    driver, _ = _two(actions, scripted)
    _vanishing(driver, "w2")
    with pytest.raises(ValueError) as caught:
        actions.tab("abc", "switch", id="t2")
    assert errors.status_for(caught.value) == 400
    assert str(caught.value).startswith("no tab t2; the tabs are t1 (")
    assert driver.current == "w1"


def test_closing_another_tab_that_closed_is_a_400_and_stays_on_the_current(
    actions, scripted
):
    driver, _ = _two(actions, scripted, on="t2")
    _vanishing(driver, "w1")
    with pytest.raises(ValueError) as caught:
        actions.tab("abc", "close", id="t1")
    assert errors.status_for(caught.value) == 400
    assert str(caught.value).startswith("no tab t1; the tabs are t2 (")
    assert driver.current == "w2"


def test_a_close_that_fails_still_leaves_the_driver_on_the_current_tab(
    actions, scripted
):
    driver, _ = _two(actions, scripted, on="t2")

    def refused():
        driver.log.append(("close",))
        raise WebDriverException("unknown error")

    driver.__dict__["close"] = refused
    with pytest.raises(WebDriverException):
        actions.tab("abc", "close", id="t1")
    assert driver.current == "w2"


def test_closing_the_last_tab_is_refused_naming_end_browser(actions, scripted):
    driver = scripted()
    actions.tabs.set("abc", state("t1", ("t1", "w1", None)))
    with pytest.raises(ValueError, match="end_browser") as caught:
        actions.tab("abc", "close")
    assert errors.status_for(caught.value) == 400
    assert ("close",) not in driver.log


def _listing(caught):
    text = str(caught.value)
    for part in ("t1", A, "t2", B):
        assert part in text
    assert errors.status_for(caught.value) == 400


def test_an_unknown_id_is_a_400_listing_every_tab(actions, scripted):
    _two(actions, scripted)
    with pytest.raises(ValueError) as caught:
        actions.tab("abc", "switch", id="t9")
    _listing(caught)


def test_no_tab_matching_url_is_a_400_listing_every_tab(actions, scripted):
    _two(actions, scripted)
    with pytest.raises(ValueError) as caught:
        actions.tab("abc", "switch", url="nowhere.example.test")
    _listing(caught)


def test_a_blank_id_or_url_is_unset(actions, scripted):
    _two(actions, scripted)
    assert actions.tab("abc", "list", id="", url="  ")["tab"] == "t1"
    assert actions.tab("abc", "SWITCH", id="", url="")["tab"] == "t2"


def test_a_tab_urls_text_is_never_a_navigation(actions, scripted):
    driver, _ = _two(actions, scripted)
    actions.tab("abc", "switch", url="a.example.test")
    assert not [e for e in driver.log if e[0] == "get"]


def test_tab_is_refused_under_control_so_no_tab_is_brought_forward(
    actions, scripted
):
    from kubed.selenium_flow.core.capabilities import UNDER_CONTROL

    assert UNDER_CONTROL["tab"] == "refused"
    seen = []

    class Held:
        def refusal(self, session_id, tool):
            seen.append(tool)
            return faults.Controlled("a person has control of this browser")

    driver, chan = _two(actions, scripted)
    actions.control = Held()
    driver.log.clear()
    actions._front("abc", "w2", {"w1": A, "w2": B})
    assert seen == ["tab"]
    assert driver.log == []
    assert _activated(chan) == []
