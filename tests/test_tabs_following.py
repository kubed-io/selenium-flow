"""Every call follows the tabs before and after it acts (ACT-49 to ACT-51).

The bracket is `Workspaces._said` for a tool or a route and
`secrets.perform_write` for a bound write. The check before a call is noted
before its origin is read, so the call is scrubbed with the secrets of the tab
it acted on (SEC-40).
"""

from __future__ import annotations

import json
import logging

import pytest
from selenium.common.exceptions import NoSuchWindowException, WebDriverException

from kubed.selenium_flow import errors, faults, secrets
from kubed.selenium_flow.workspace import locks
from kubed.selenium_flow.workspace.store import MemoryStore
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .fakes import BindingCatalogue, FakeCatalogue, TabbedActions

pytestmark = pytest.mark.unit

A = "https://shop.example.test"
C = "https://c.example.test"
P_ORIGIN = "https://sso.example.test"
P = f"{P_ORIGIN}/login"
MOVE_TO_P = {"tab": "t2", "url": P, "title": "Sign in", "in_frame": False}
BACK_TO_A = {"closed": ["t2"], "tab": "t1", "url": f"{A}/", "title": "Shop",
             "in_frame": False}
TAB_KEYS = ("tab", "opened", "closed", "in_frame")

CALLER = Caller("w", "query")
SECRETS = {
    A: {("a", "password"): "alpha-11"},
    P_ORIGIN: {("p", "password"): "pi-314159"},
}


def scoped(catalogue=None, *, url=f"{A}/"):
    """A `Workspaces` over `TabbedActions`, its browser open on ``url`` and
    nothing asked of it yet."""
    ws = Workspaces(TabbedActions(), MemoryStore(), catalogue=catalogue)
    ws.open_browser(CALLER, url=url)
    ws.actions.calls.clear()
    return ws


@pytest.fixture
def ws():
    made = scoped(BindingCatalogue(SECRETS, on=P_ORIGIN))
    yield made
    made.scrubber.close()


def sid(ws):
    return ws.store.get("w").session_id


def test_a_call_after_a_popup_opened_acts_on_the_popup_and_says_so(ws):
    ws.actions.checks = [{"opened": [{"tab": "t2", "url": P}], **MOVE_TO_P}, {}]
    result = ws.act(CALLER, ws.actions.do, tool="interact")
    assert result["tab"] == "t2" and result["opened"] == [{"tab": "t2", "url": P}]
    assert ws.actions.calls == ["check_tabs", "do", "check_tabs"]


def test_the_before_check_is_noted_before_the_call_reads_its_origin(ws):
    ws.actions.checks = [MOVE_TO_P, {}]
    seen = []

    def call(session_id):
        seen.append(ws.origins.current("w", session_id))
        return {}

    ws.act(CALLER, call, tool="interact")
    assert seen == [P_ORIGIN]


def test_a_call_after_a_move_is_scrubbed_with_the_secrets_of_the_tab_it_acted_on(ws):
    """The popup's own secret, on a call that came back to `A`: the origin it
    began on is the popup's, so the popup's secret is in scope."""
    ws.actions.checks = [MOVE_TO_P, {}]
    ws.actions.result = {"url": f"{A}/", "title": "Shop", "text": "pi-314159"}
    result = ws.act(CALLER, ws.actions.do, tool="extract")
    assert result["text"] == "[secret:p/password]"


def test_a_popup_the_call_opened_moves_the_results_page(ws):
    ws.actions.result = {"url": f"{A}/", "title": "Shop"}
    ws.actions.checks = [{}, {"opened": [{"tab": "t2", "url": P}], **MOVE_TO_P}]
    result = ws.act(CALLER, ws.actions.do, tool="interact")
    assert (result["url"], result["title"]) == (P, "Sign in")
    assert result["tab"] == "t2" and result["in_frame"] is False
    assert ws.store.get("w").url == P


def test_a_result_where_nothing_moved_carries_no_tab_keys(ws):
    ws.actions.result = {"url": f"{A}/cart", "title": "Cart"}
    result = ws.act(CALLER, ws.actions.do, tool="interact")
    assert not [key for key in TAB_KEYS if key in result]
    assert ws.actions.calls == ["check_tabs", "do", "check_tabs"]


def test_a_move_clears_the_noted_frame(ws):
    ws.origins.framed("w", sid(ws), True)
    ws.actions.checks = [MOVE_TO_P, {}]
    ws.act(CALLER, ws.actions.do, tool="interact")
    assert ws.origins.framed("w", sid(ws)) is False


def test_a_call_whose_tab_closed_before_it_acted_is_a_409_naming_the_new_tab(ws):
    ws.actions.raises = NoSuchWindowException("no such window: target window already closed")
    ws.actions.checks = [{}, BACK_TO_A]
    with pytest.raises(faults.TabClosed) as raised:
        ws.act(CALLER, ws.actions.do, tool="interact")
    assert errors.status_for(raised.value) == 409
    assert "t1" in str(raised.value) and f"{A}/" in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert ws.origins.current("w", sid(ws)) == A


def test_a_tab_closed_error_carrying_a_value_is_scrubbed_with_every_secret(ws):
    ws.actions.raises = NoSuchWindowException("no such window")
    ws.actions.checks = [{}, {**BACK_TO_A, "url": f"{A}/?t=pi-314159"}]
    with pytest.raises(faults.TabClosed) as raised:
        ws.act(CALLER, ws.actions.do, tool="interact")
    assert "pi-314159" not in faults.message(raised.value)


def test_a_window_gone_with_no_move_keeps_its_own_failure(ws):
    """Nothing closed under the session: the driver's failure is not a
    closed tab's, so it is not said to be."""
    ws.actions.raises = NoSuchWindowException("no such window")
    with pytest.raises(NoSuchWindowException):
        ws.act(CALLER, ws.actions.do, tool="interact")


def refused(*args, **kwargs):
    raise faults.Controlled("a person has control of this browser")


def test_a_call_refused_after_a_popup_says_the_session_moved(ws, monkeypatch):
    monkeypatch.setattr(ws.calls, "begin", refused)
    ws.actions.checks = [MOVE_TO_P]
    with pytest.raises(faults.Controlled) as raised:
        ws.act(CALLER, ws.actions.do, tool="interact")
    said = faults.message(raised.value)
    assert f"The session moved to tab t2 ({P}) during this call." in said
    assert ws.actions.calls == ["check_tabs"]


def test_a_failed_call_after_a_popup_says_the_session_moved(ws):
    ws.actions.raises = ValueError("no element matched #buy")
    ws.actions.checks = [MOVE_TO_P, {}]
    with pytest.raises(ValueError) as raised:
        ws.act(CALLER, ws.actions.do, tool="interact")
    said = faults.message(raised.value)
    assert said.startswith("no element matched #buy")
    assert f"The session moved to tab t2 ({P}) during this call." in said


def test_a_failed_call_that_opened_a_popup_says_where_it_went(ws):
    ws.actions.raises = ValueError("no element matched #buy")
    ws.actions.checks = [{}, {"opened": [{"tab": "t2", "url": P}], **MOVE_TO_P}]
    with pytest.raises(ValueError) as raised:
        ws.act(CALLER, ws.actions.do, tool="interact")
    assert "moved to tab t2" in faults.message(raised.value)


def test_a_failed_call_where_nothing_moved_says_nothing_of_tabs(ws):
    ws.actions.raises = ValueError("no element matched #buy")
    with pytest.raises(ValueError) as raised:
        ws.act(CALLER, ws.actions.do, tool="interact")
    assert faults.message(raised.value) == "no element matched #buy"


@pytest.mark.parametrize("refuse", [False, True], ids=["failed", "refused"])
def test_a_secret_in_the_moved_to_url_is_masked_in_the_note(ws, monkeypatch, refuse):
    if refuse:
        monkeypatch.setattr(ws.calls, "begin", refused)
    ws.actions.raises = ValueError("no element matched #buy")
    ws.actions.checks = [{**MOVE_TO_P, "url": f"{A}/?k=alpha-11"}, {}]
    with pytest.raises(Exception) as raised:
        ws.act(CALLER, ws.actions.do, tool="interact")
    said = faults.message(raised.value)
    assert "alpha-11" not in said and "[secret:a/password]" in said


def test_a_bound_write_that_fails_after_a_popup_says_the_session_moved(ws):
    ws.actions.write = _failed_write
    ws.actions.checks = [MOVE_TO_P, {}]
    with pytest.raises(ValueError) as raised:
        secrets.perform_write(
            ws.scrubber._catalogue, ws.actions, ws, "w",
            {"selector": {"css": "#p"}, "secret": {"name": "p", "key": "password"}},
        )
    assert f"The session moved to tab t2 ({P}) during this call." in faults.message(
        raised.value
    )


def test_a_bound_write_refused_after_a_popup_says_the_session_moved(ws, monkeypatch):
    monkeypatch.setattr(ws.calls, "begin", refused)
    ws.actions.checks = [MOVE_TO_P]
    with pytest.raises(faults.Controlled) as raised:
        secrets.perform_write(
            ws.scrubber._catalogue, ws.actions, ws, "w",
            {"selector": {"css": "#p"}, "secret": {"name": "p", "key": "password"}},
        )
    assert "moved to tab t2" in faults.message(raised.value)
    assert ws.actions.calls == ["check_tabs"]


def test_a_call_whose_own_tab_closed_after_it_acted_succeeds(ws):  # ACT-51
    ws.actions.result = {"url": P, "title": "Sign in"}
    ws.actions.checks = [{}, BACK_TO_A]
    result = ws.act(CALLER, ws.actions.do, tool="interact")
    assert result["url"] == f"{A}/" and result["closed"] == ["t2"]
    assert result["tab"] == "t1"


def test_an_after_check_that_fails_never_fails_a_call_that_acted(ws, caplog):
    caplog.set_level(logging.DEBUG)
    ws.actions.result = {"url": f"{A}/cart", "title": "Cart"}
    ws.actions.checks = [{}, WebDriverException(P)]
    result = ws.act(CALLER, ws.actions.do, tool="interact")
    assert result["url"] == f"{A}/cart"
    assert ws.actions.calls == ["check_tabs", "do", "check_tabs"]
    assert not [r for r in caplog.records if P in r.getMessage()]
    assert any("WebDriverException" in r.getMessage() for r in caplog.records)


def test_no_check_follows_a_call_whose_browser_is_ending(ws):
    """`end_browser` cut in while the call acted: its Grid DELETE may be in
    flight, so nothing reconnects to look at the tabs."""

    def call(session_id):
        locks.hold(session_id).ending.set()
        return {"url": f"{A}/", "title": "Shop"}

    ws.actions.calls.clear()
    ws.act(CALLER, call, tool="interact")
    assert ws.actions.calls == ["check_tabs"]


def test_a_before_check_that_fails_fails_the_call_before_it_begins(ws):
    ws.actions.checks = [WebDriverException(f"lost at {P}?t=pi-314159")]
    with pytest.raises(WebDriverException) as raised:
        ws.act(CALLER, ws.actions.do, tool="interact")
    assert "do" not in ws.actions.calls
    # Scrubbed as a failed call's: every usable secret, though the session is on A.
    assert "pi-314159" not in faults.message(raised.value)


def test_an_opened_popups_url_is_scrubbed_with_every_leashed_secret():
    made = scoped(FakeCatalogue({C: {("c", "token"): "gamma-33"}, A: {}}))
    try:
        made.actions.checks = [
            {},
            {
                "opened": [
                    {"tab": "t2", "url": f"{C}/?t=gamma-33"},
                    {"tab": "t3", "url": f"{A}/help"},
                ],
                "tab": "t3",
                "url": f"{A}/help",
                "title": "Help",
                "in_frame": False,
            },
        ]
        made.actions.result = {"url": f"{A}/", "title": "Shop"}
        result = made.act(CALLER, made.actions.do, tool="interact")
        assert "gamma-33" not in json.dumps(result)
        assert result["opened"][0]["url"] == f"{C}/?t=[secret:c/token]"
    finally:
        made.scrubber.close()


def test_a_bound_write_after_a_popup_opened_types_into_the_popup(ws):
    ws.actions.checks = [MOVE_TO_P, {}]
    shown = secrets.perform_write(
        ws.scrubber._catalogue, ws.actions, ws, "w",
        {"selector": {"css": "#p"}, "secret": {"name": "p", "key": "password"}},
    )
    assert ws.actions.calls[:4] == ["check_tabs", "page", "write", "check_tabs"]
    assert shown["tab"] == "t2" and shown["url"] == P
    assert "pi-314159" not in json.dumps(shown)


def test_a_bound_write_whose_tab_closed_before_it_typed_is_a_409(ws):
    ws.actions.checks = [MOVE_TO_P, BACK_TO_A]
    ws.actions.write = _closed_window
    with pytest.raises(faults.TabClosed) as raised:
        secrets.perform_write(
            ws.scrubber._catalogue, ws.actions, ws, "w",
            {"selector": {"css": "#p"}, "secret": {"name": "p", "key": "password"}},
        )
    assert "t1" in str(raised.value) and "pi-314159" not in faults.message(raised.value)


def test_a_bound_write_whose_before_check_fails_never_reads_the_page(ws):
    ws.actions.checks = [WebDriverException(f"lost at {P}?t=pi-314159")]
    with pytest.raises(WebDriverException) as raised:
        secrets.perform_write(
            ws.scrubber._catalogue, ws.actions, ws, "w",
            {"selector": {"css": "#p"}, "secret": {"name": "p", "key": "password"}},
        )
    assert ws.actions.calls == ["check_tabs"]
    assert "pi-314159" not in faults.message(raised.value)


def test_a_bound_writes_opened_urls_are_scrubbed_with_every_leashed_secret(ws):
    ws.actions.checks = [
        MOVE_TO_P,
        {
            "opened": [
                {"tab": "t3", "url": f"{A}/?k=alpha-11"},
                {"tab": "t4", "url": f"{P}/next"},
            ],
            **MOVE_TO_P,
            "tab": "t4",
            "url": f"{P}/next",
        },
    ]
    shown = secrets.perform_write(
        ws.scrubber._catalogue, ws.actions, ws, "w",
        {"selector": {"css": "#p"}, "secret": {"name": "p", "key": "password"}},
    )
    assert shown["opened"][0] == {"tab": "t3", "url": f"{A}/?k=[secret:a/password]"}


def _closed_window(session_id, text=None, **kwargs):
    raise NoSuchWindowException("no such window")


def _failed_write(session_id, text=None, **kwargs):
    raise ValueError("no element matched #p")


def test_tab_list_is_scrubbed_with_every_leashed_secret():
    made = scoped(FakeCatalogue({C: {("c", "token"): "gamma-33"}, A: {}}))
    try:
        made.actions.tabs_listed = [
            {"tab": "t1", "url": f"{A}/", "title": "Shop", "current": True},
            {"tab": "t2", "url": f"{C}/", "title": "token gamma-33", "current": False},
        ]
        result = made.act(CALLER, made.actions.tab, tool="tab")
        assert "gamma-33" not in json.dumps(result)
        assert result["tabs"][1]["title"] == "token [secret:c/token]"
    finally:
        made.scrubber.close()
