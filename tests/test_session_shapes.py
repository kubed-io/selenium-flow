"""Results and contexts say `main`, and both session lists, `others` and
`sessions`, use one entry shape (WS-89)."""

from __future__ import annotations

import pytest

from kubed.selenium_flow.urls import page_of
from kubed.selenium_flow.workspace.store import Session

from .fakes import seated
from .test_session_holder import FIRST, SECOND, as_, built, pinned

pytestmark = pytest.mark.unit

ENTRY_KEYS = {"name", "main", "live", "last_used", "url", "uri", "yours"}
SECRET_PAGE = "https://example.com/a?code=1#x"


def test_open_session_says_main_and_lists_others_in_one_shape():
    workspaces, _, _ = built()
    workspaces.open_browser(as_(FIRST), url="https://example.com/first")
    second = workspaces.open_browser(as_(SECOND))
    assert second["main"] is False
    assert "default" not in second and "label" not in second
    [other] = second["others"]
    assert set(other) == ENTRY_KEYS
    assert isinstance(other["last_used"], int) and other["last_used"] > 0
    assert {k: v for k, v in other.items() if k != "last_used"} == {
        "name": "first",
        "main": True,
        "live": True,
        "url": "https://example.com/first",
        "uri": "session://first",
        "yours": False,
    }


def test_the_context_lists_every_session_in_list_order():
    workspaces, _, _ = built()
    workspaces.open_browser(as_(FIRST))
    workspaces.open_browser(as_(SECOND))
    workspaces.open_browser(as_(FIRST), session="tester")
    context = workspaces.describe(as_(FIRST))
    assert context["session"] == "first" and context["main"] is True
    listed = context["sessions"]
    assert [s["name"] for s in listed] == ["first", "second", "tester"]
    assert [s["yours"] for s in listed] == [True, False, True]
    assert [s["main"] for s in listed] == [True, False, False]
    assert all(set(s) == ENTRY_KEYS for s in listed)


def test_another_sessions_url_is_cut():
    workspaces, _, _ = built()
    workspaces.open_browser(as_(SECOND), url=SECRET_PAGE)
    first = workspaces.open_browser(as_(FIRST))
    [other] = first["others"]
    assert other["name"] == "second"
    assert other["url"] == page_of(SECRET_PAGE) != SECRET_PAGE
    own = workspaces.describe(as_(SECOND))
    [entry] = [s for s in own["sessions"] if s["name"] == "second"]
    assert entry["url"] == SECRET_PAGE


def test_last_used_is_null_for_a_session_that_never_bound():
    workspaces, store, _ = built()
    seated(store, "shop", "first", record=Session(holder="sub-a"))
    [entry] = workspaces.describe(as_(FIRST))["sessions"]
    assert entry["name"] == "first" and entry["last_used"] is None


def test_a_workspace_with_no_record_lists_no_sessions():
    workspaces, _, _ = built()
    context = workspaces.describe(as_(FIRST))
    assert context["sessions"] == []
    assert context["session"] == "first" and context["main"] is False


def test_the_main_part_is_served_and_default_is_not():
    workspaces, _, _ = built()
    workspaces.open_browser(as_(FIRST))
    assert workspaces.describe_part(as_(FIRST), "main") == {
        "workspace": "shop", "main": True,
    }
    with pytest.raises(ValueError, match=r"the parts are .*main"):
        workspaces.describe_part(as_(FIRST), "default")


def _keys(value) -> set:
    """Every key of every dict in ``value``, however deep."""
    if isinstance(value, dict):
        return set(value).union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value))
    return set()


def test_no_default_or_label_key_survives():
    workspaces, _, _ = built()
    workspaces.open_browser(as_(SECOND))
    opened = workspaces.open_browser(as_(FIRST))
    context = workspaces.describe(as_(FIRST))
    named = workspaces.describe(pinned("first", FIRST), at="session://first")
    for answer in (opened, context, named):
        assert {"default", "label"}.isdisjoint(_keys(answer))
