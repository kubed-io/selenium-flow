"""Which session a call reaches: the default, a named sub-session, or the one a
token's subject gets when the default is another subject's (WS-48 to WS-50, WS-78)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp_tasks import call_tool_task
from starlette.testclient import TestClient

from kubed.selenium_flow import faults, secrets
from kubed.selenium_flow.core.capabilities import CAPABILITIES
from kubed.selenium_flow.flows.document import step_schemas
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.mcp import waiting
from kubed.selenium_flow.principal import ADMIN, Principal
from kubed.selenium_flow.workspace import locks
from kubed.selenium_flow.workspace.store import (
    MemoryStore,
    RedisStore,
    StoreConflict,
    Workspace,
)
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .conftest import NAMED, manager
from .fakes import BindingCatalogue, FakeRedis, RecordingActions, TabbedActions
from .test_await_control import over_http
from .test_golden import AUTH, twin  # noqa: F401 - a fixture, used below
from .test_locks import in_thread
from .test_recording_open import Recorder
from .test_site_data_workspaces import SiteActions

pytestmark = pytest.mark.unit

FIRST = Principal("oidc", subject="sub-a", username="first")
SECOND = Principal("oidc", subject="sub-b", username="second")


def as_(principal=None, workspace="shop"):
    return Caller(workspace, "query", principal=principal)


def pinned(session, workspace="shop", principal=None):
    caller = Caller.from_request({"workspace": [workspace], "session": [session]}, {})
    return replace(caller, principal=principal)


def built(record=None, actions=None):
    store = MemoryStore()
    if record is not None:
        store.set("shop", record)
    return manager(actions or RecordingActions(), store), store


def test_no_name_reaches_the_default():
    workspaces, _ = built(Workspace())
    assert workspaces.session_of(as_()) == "shop"


def test_a_name_reaches_its_sub_session():
    workspaces, store = built(Workspace(subs=["tester"]))
    store.set("shop/tester", Workspace(label="tester"))
    assert workspaces.session_of(as_(), "tester") == "shop/tester"


def test_default_by_name_is_the_default():
    workspaces, store = built(Workspace())
    store.set("shop/tester", Workspace())
    assert workspaces.session_of(as_(), "default") == "shop"
    assert workspaces.session_of(pinned("default")) == "shop"


def test_a_pin_wins_and_a_different_argument_is_refused():
    workspaces, store = built(Workspace(subs=["tester"]))
    store.set("shop/tester", Workspace(label="tester"))
    caller = pinned("tester")
    assert caller.pinned == "tester"
    assert workspaces.session_of(caller) == "shop/tester"
    assert workspaces.session_of(caller, "tester") == "shop/tester"
    with pytest.raises(ValueError, match="pinned to a session and the call names another"):
        workspaces.session_of(caller, "default")


def test_the_pin_is_read_from_the_header_too():
    caller = Caller.from_request({"workspace": ["shop"]}, {"sf-session": ["tester"]})
    assert caller.pinned == "tester"
    assert Caller.from_request({"workspace": ["shop"]}, {}).pinned == ""


@pytest.mark.parametrize(
    ("params", "headers", "says"),
    [
        ({"session": ["a"]}, {"sf-session": ["a"]}, "pin your session once"),
        ({"session": ["a", "b"]}, {}, "two sessions: a, b"),
        ({}, {"sf-session": ["a", "b"]}, "two sessions: a, b"),
        ({"session": ["current"]}, {}, "cannot name a session"),
        ({"session": ["a/b"]}, {}, "session name"),
    ],
)
def test_a_bad_pin_is_refused_when_the_session_is_asked_for(params, headers, says):
    caller = Caller.from_request({"workspace": ["shop"], **params}, headers)
    assert caller.name == "shop", "the workspace is still named"
    with pytest.raises(ValueError, match=says):
        _ = caller.pinned
    workspaces, _ = built(Workspace())
    with pytest.raises(ValueError, match=says):
        workspaces.session_of(caller)


def test_a_call_naming_an_unknown_session_is_refused_and_opens_nothing():
    actions = RecordingActions()
    workspaces, store = built(Workspace(), actions)
    with pytest.raises(faults.NoSession, match="open_session"):
        workspaces.session_of(as_(), "tester")
    with pytest.raises(faults.NoSession, match="open_session"):
        workspaces.session_of(pinned("tester"))
    assert actions.opened == 0
    assert store.get("shop/tester") is None
    assert store.get("shop").subs == []


def test_open_session_makes_a_named_sub_session():
    actions = RecordingActions()
    workspaces, store = built(Workspace(), actions)
    assert workspaces.session_of(as_(), "tester", opening=True) == "shop/tester"
    assert store.get("shop/tester").label == "tester"
    assert store.get("shop").subs == ["tester"]
    # Again: the same record, listed once.
    assert workspaces.session_of(as_(), "tester", opening=True) == "shop/tester"
    assert store.get("shop").subs == ["tester"]
    assert actions.opened == 0, "the record is made; the browser is open_session's"


def test_opening_a_sub_session_in_a_workspace_with_no_record_lists_it():
    workspaces, store = built()
    assert workspaces.session_of(as_(), "tester", opening=True) == "shop/tester"
    assert store.get("shop").subs == ["tester"]
    assert store.get("shop").owner is None


def test_a_dead_sub_session_name_is_pruned_on_the_next_listing():  # WS-60, WS-77
    workspaces, store = built(Workspace(subs=["gone", "tester"]))
    store.set("shop/tester", Workspace(label="tester"))
    assert [s["name"] for s in workspaces.listed_sessions("shop")] == [
        "default", "tester",
    ]
    assert store.get("shop").subs == ["tester"]


def test_a_listing_reads_every_sub_session_in_one_round_trip():
    fake = FakeRedis()
    store = RedisStore(fake, prefix="p:")
    store.set("shop", Workspace(subs=["a", "b", "c"]))
    for name in "abc":
        store.set(f"shop/{name}", Workspace(label=name))
    workspaces = manager(RecordingActions(), store)
    fake.calls.clear()
    listed = workspaces.listed_sessions("shop")
    assert [s["name"] for s in listed] == ["default", "a", "b", "c"]
    assert fake.calls == ["get", "mget"], "the workspace's record, then its subs at once"


class HidingStore(MemoryStore):
    """A store whose first batch read misses ``hidden``: a sub-session made
    between a listing's read and its prune."""

    hidden = ""

    def get_many(self, keys):
        found = super().get_many(keys)
        if self.hidden:
            found = [None if k == self.hidden else r for k, r in zip(keys, found, strict=True)]
            self.hidden = ""
        return found


def test_a_name_whose_record_was_made_since_the_read_is_not_pruned():
    store = HidingStore()
    store.set("shop", Workspace(subs=["tester"]))
    store.set("shop/tester", Workspace(label="tester"))
    store.hidden = "shop/tester"
    manager(RecordingActions(), store).listed_sessions("shop")
    assert store.get("shop").subs == ["tester"]


class FailingOnce(MemoryStore):
    """A store whose next write to ``failing`` loses to other writers."""

    failing = ""

    def upsert(self, key, fn):
        if key == self.failing:
            self.failing = ""
            raise StoreConflict("kept changing")
        return super().upsert(key, fn)


@pytest.mark.parametrize("failing", ["shop", "shop/tester"])
def test_a_failed_open_leaves_no_sub_record_the_listing_misses(failing):
    store = FailingOnce()
    store.set("shop", Workspace())
    store.failing = failing
    workspaces = manager(RecordingActions(), store)
    with pytest.raises(StoreConflict):
        workspaces.session_of(as_(), "tester", opening=True)
    workspaces.listed_sessions("shop")
    # Either nothing, or a name the listing pruned: never a record no listing shows.
    assert store.get("shop/tester") is None
    assert store.get("shop").subs == []


@pytest.mark.parametrize(
    "wanted",
    [{"browser": "netscape"}, {"url": "file:///etc/passwd"}, {"capabilities": "{nope"}],
)
def test_a_refused_open_makes_and_lists_no_sub_session(wanted):  # WS-78
    actions = RecordingActions()
    workspaces, store = built(Workspace(), actions)
    with pytest.raises(ValueError):
        workspaces.open_browser(as_(), session="tester", **wanted)
    assert store.get("shop/tester") is None
    assert store.get("shop").subs == []
    assert actions.opened == 0


def test_an_unowned_default_is_claimed_by_the_first_subject():
    # claudecode's record today: written before sub-sessions, no owner.
    workspaces, store = built(Workspace(session_id="g1"))
    assert workspaces.session_of(as_(FIRST)) == "shop"
    assert store.get("shop").owner == "sub-a"
    assert store.get("shop").session_id == "g1"
    assert workspaces.session_of(as_(FIRST)) == "shop", "the owner stays on it"


def test_a_call_makes_no_workspace_record_to_claim():
    workspaces, store = built()
    assert workspaces.session_of(as_(FIRST)) == "shop"
    assert store.get("shop") is None


def test_a_second_subject_gets_its_own_sub_session():
    workspaces, store = built(Workspace(owner="sub-a"))
    assert workspaces.session_of(as_(SECOND)) == "shop/sub-b"
    assert store.get("shop/sub-b").label == "second"
    assert store.get("shop").subs == ["sub-b"]
    assert store.get("shop").owner == "sub-a"
    assert workspaces.session_of(as_(SECOND)) == "shop/sub-b"
    assert store.get("shop").subs == ["sub-b"]


def test_a_subject_with_no_username_is_labelled_by_its_subject():
    workspaces, store = built(Workspace(owner="sub-a"))
    assert workspaces.session_of(as_(Principal("oidc", subject="sub-c"))) == "shop/sub-c"
    assert store.get("shop/sub-c").label == "sub-c"


@pytest.mark.parametrize("principal", [ADMIN, Principal("oidc"), None])
def test_a_caller_with_no_subject_never_claims_the_default(principal):
    workspaces, store = built(Workspace())
    assert workspaces.session_of(as_(principal)) == "shop"
    assert store.get("shop").owner is None
    workspaces.claim("shop", principal)
    assert store.get("shop").owner is None
    # Nor is it sent away from one a subject owns.
    store.set("shop", Workspace(owner="sub-a"))
    assert workspaces.session_of(as_(principal)) == "shop"


def test_the_stdio_caller_reaches_the_default():
    workspaces, store = built()
    stdio = Caller.stdio()
    store.set(stdio.name, Workspace(owner="sub-a"))
    assert workspaces.session_of(stdio) == stdio.name


def test_the_owner_survives_end_browser_and_a_reopen_naming_default():
    actions = RecordingActions()
    workspaces, store = built(Workspace(), actions)
    assert workspaces.session_of(as_(FIRST)) == "shop"
    workspaces.open_browser(as_(FIRST))
    workspaces.end_browser(as_(FIRST))
    assert store.get("shop").owner == "sub-a"
    assert workspaces.session_of(as_(SECOND), "default", opening=True) == "shop"
    workspaces.open_browser(as_(SECOND), session="default")
    workspaces.claim("shop", SECOND)
    assert store.get("shop").owner == "sub-a"
    assert actions.opened == 2
    assert store.get("shop/sub-b") is None


def test_the_first_subject_to_open_a_new_workspace_owns_its_default():
    workspaces, store = built()
    workspaces.open_browser(as_(FIRST))
    assert store.get("shop").owner == "sub-a"
    assert workspaces.session_of(as_(SECOND)) == "shop/sub-b"


def test_a_second_subject_naming_default_shares_it():
    workspaces, store = built(Workspace(owner="sub-a"))
    assert workspaces.session_of(as_(SECOND), "default") == "shop"
    assert workspaces.session_of(pinned("default", principal=SECOND)) == "shop"
    assert store.get("shop").owner == "sub-a"
    assert store.get("shop/sub-b") is None


def test_a_claim_is_written_once():
    workspaces, store = built(Workspace())
    workspaces.claim("shop", FIRST)
    workspaces.claim("shop", SECOND)
    assert store.get("shop").owner == "sub-a"
    # A sub-session's record holds no owner.
    store.set("shop/tester", Workspace())
    workspaces.claim("shop/tester", SECOND)
    assert store.get("shop/tester").owner is None


def test_a_subject_that_cannot_name_a_session_is_refused():
    workspaces, store = built(Workspace(owner="sub-a"))
    with pytest.raises(ValueError, match="session"):
        workspaces.session_of(as_(Principal("oidc", subject="idp|42")))
    assert store.get("shop").subs == []


def test_a_tasks_worker_reaches_the_pinned_session(monkeypatch):
    """Off the request object only the carried pin is read, as only the carried
    workspace is: never an Sf-Session the snapshot kept."""
    import kubed.mcp.request as dependencies

    def no_request():
        raise RuntimeError("no active HTTP request")

    def snapshot(headers):
        return lambda include_all=False: dict(headers)

    monkeypatch.setattr(dependencies, "get_http_request", no_request)
    monkeypatch.setattr(dependencies, "get_http_headers", snapshot({
        "host": "test",
        "Sf-Session": "spoofed",
        clients_module.CARRIED: "shop",
        clients_module.CARRIED_SESSION: "tester",
    }))
    assert clients_module.caller().pinned == "tester"
    monkeypatch.setattr(dependencies, "get_http_headers", snapshot({
        "host": "test", "Sf-Session": "spoofed", clients_module.CARRIED: "shop",
    }))
    assert clients_module.caller().pinned == ""


async def test_the_pin_from_the_query_reaches_a_tasks_worker(open_server):
    """Carried at submission as the workspace is, overwriting one the client
    sent; a bad pin is refused there, as the tool would refuse it."""

    @open_server.mcp.tool(task=waiting.TASK)
    async def pinned_to() -> dict:
        return {"session": clients_module.caller().pinned}

    async with over_http(
        open_server,
        "http://test/mcp?workspace=w1&session=tester",
        headers={clients_module.CARRIED_SESSION: "spoofed"},
    ) as client:
        tasked = await (await call_tool_task(client, "pinned_to")).result()
    assert tasked.structured_content == {"session": "tester"}

    async with over_http(
        open_server,
        "http://test/mcp?workspace=w1&session=tester",
        headers={"Sf-Session": "other"},
    ) as client:
        with pytest.raises(ToolError, match="did not run as a task"):
            await call_tool_task(client, "pinned_to")


# ---- a call acts in its session (WS-51 to WS-53, WS-59, WS-74, SURF-46) -----

A_SITE = "https://a.example.test/"
B_SITE = "https://b.example.test/"


def two_open(actions=None):
    """`built`, the default open on `A_SITE` and `tester` open on `B_SITE`."""
    workspaces, store = built(Workspace(), actions)
    workspaces.open_browser(as_(), url=A_SITE)
    workspaces.open_browser(as_(), url=B_SITE, session="tester")
    return workspaces, store


def ids(store):
    return store.get("shop").session_id, store.get("shop/tester").session_id


def test_a_sub_session_opens_its_own_browser_and_leaves_the_default_open():
    actions = RecordingActions()
    workspaces, store = built(Workspace(), actions)
    workspaces.open_browser(as_())
    default = store.get("shop").session_id
    workspaces.open_browser(as_(), session="tester")
    assert actions.opened == 2 and actions.closed == []
    assert store.get("shop").session_id == default
    tester = store.get("shop/tester").session_id
    assert tester not in ("", default)
    # Opening it again ends its own browser, never the default's (WS-13, 4).
    workspaces.open_browser(as_(), session="tester")
    assert actions.closed == [tester]
    assert store.get("shop").session_id == default


def test_two_sessions_act_at_once():
    workspaces, store = two_open()
    both = threading.Barrier(2, timeout=5)  # broken unless both are inside at once

    def meet(session_id):
        both.wait()
        return {"acted_on": session_id}

    runs = [
        in_thread(lambda name=name: workspaces.act(as_(), meet, session=name))
        for name in ("default", "tester")
    ]
    for thread, _ in runs:
        thread.join(10)
    assert [outcome[0]["acted_on"] for _, outcome in runs] == list(ids(store))


def test_two_callers_naming_one_session_take_turns():  # WS-53
    workspaces, store = two_open()
    order, entered, release = [], threading.Event(), threading.Event()

    def first(_):
        order.append("first")
        entered.set()
        release.wait(5)
        order.append("first done")
        return {}

    def second(_):
        order.append("second")
        return {}

    one = in_thread(lambda: workspaces.act(as_(FIRST), first, session="tester"))
    assert entered.wait(5)
    two = in_thread(lambda: workspaces.act(as_(SECOND), second, session="tester"))
    # Until the second holds a ticket behind the first's (internals: the one
    # deterministic sign that it waits on the session's turn).
    turns = locks._holds[ids(store)[1]].lock
    deadline = time.monotonic() + 5
    while turns._next - turns._serving < 2:
        assert time.monotonic() < deadline, "the second call never queued"
        time.sleep(0.001)
    assert order == ["first"]
    release.set()
    for thread, _ in (one, two):
        thread.join(5)
    assert order == ["first", "first done", "second"]


def test_a_sub_session_opens_from_config_not_from_the_defaults_settings():
    actions = RecordingActions()
    workspaces, store = built(Workspace(), actions)
    workspaces.open_browser(as_(), browser="firefox", width=1400, height=900)
    workspaces.open_browser(as_(), session="tester")
    assert actions.opened_settings[0]["browser"] == "firefox"
    assert actions.opened_settings[1].get("browser", "chrome") == "chrome"
    assert "width" not in actions.opened_settings[1]
    assert store.get("shop/tester").settings.get("browser", "chrome") == "chrome"
    assert store.get("shop").settings["browser"] == "firefox"


def test_save_site_data_saves_into_the_calling_sessions_record():
    actions = SiteActions()
    workspaces, store = two_open(actions)
    result = workspaces.act(
        as_(), actions.save_site_data, session="tester", tool="save_site_data"
    )
    assert result["saved"]["cookies"] == 1
    assert list(store.get("shop/tester").site_data["origins"]) == [
        "https://app.example.com"
    ]
    assert store.get("shop").site_data == {}


def test_a_reaped_sub_session_reopens_signed_in():  # WS-59, SITE-37
    actions = SiteActions()
    workspaces, store = two_open(actions)
    workspaces.act(as_(), actions.save_site_data, session="tester")
    default, reaped = ids(store)
    actions.grid.alive.discard(reaped)
    result = workspaces.act(as_(), lambda s: {"url": B_SITE}, session="tester")
    assert result["reopened"]["cause"] in ("reaped", "lost")
    assert store.get("shop/tester").session_id not in ("", reaped)
    assert actions.site_data_seen[-1]["cookies"], "its own save came back"
    assert store.get("shop").session_id == default


def test_end_browser_ends_only_the_callers_session():  # WS-74
    actions = RecordingActions()
    workspaces, store = two_open(actions)
    default, tester = ids(store)
    workspaces.end_browser(pinned("tester"))
    assert actions.closed == [tester]
    assert store.get("shop").session_id == default
    assert store.get("shop/tester") is not None, "its record is kept"
    assert not store.get("shop/tester").attached
    workspaces.end_browser(as_())
    assert actions.closed == [tester, default]


def test_a_secret_is_scoped_by_its_own_sessions_origin():
    a, b = A_SITE.rstrip("/"), B_SITE.rstrip("/")
    catalogue = BindingCatalogue(
        {a: {("a", "password"): "alpha-11"}, b: {("b", "password"): "beta-22"}},
        on=b,
    )
    workspaces = Workspaces(TabbedActions(), MemoryStore(), catalogue=catalogue)
    try:
        workspaces.open_browser(as_(), url=A_SITE)
        workspaces.open_browser(as_(), url=B_SITE, session="tester")
        default, tester = ids(workspaces.store)
        key = workspaces.session_of(as_(), "tester")
        assert workspaces.origins.current(key, tester) == b
        assert workspaces.origins.current("shop", default) == a
        typed = {"selector": {"css": "#p"}, "secret": {"name": "b", "key": "password"}}
        shown = secrets.perform_write(
            catalogue, workspaces.actions, workspaces, key, dict(typed)
        )
        assert shown["url"] == B_SITE and "beta-22" not in json.dumps(shown)
        with pytest.raises(secrets.Refused):
            secrets.perform_write(
                catalogue, workspaces.actions, workspaces, "shop", dict(typed)
            )
        # Each session's answers are scrubbed with its own page's secrets.
        workspaces.actions.result = {"url": B_SITE, "text": "beta-22"}
        read = workspaces.act(
            as_(), workspaces.actions.do, session="tester", tool="extract"
        )
        assert read["text"] == "[secret:b/password]"
        workspaces.actions.result = {"url": A_SITE, "text": "alpha-11"}
        read = workspaces.act(as_(), workspaces.actions.do, tool="extract")
        assert read["text"] == "[secret:a/password]"
    finally:
        workspaces.scrubber.close()


def test_a_sub_sessions_recording_is_the_workspaces_and_known_by_its_key():
    recorder, actions = Recorder(), RecordingActions()
    workspaces = Workspaces(actions, MemoryStore(), recordings=recorder)
    workspaces.open_browser(as_(), session="tester", record=True)
    gid = workspaces.store.get("shop/tester").session_id
    assert recorder.expected == [("shop", gid, "chrome")]
    assert recorder.sessions == {gid: "shop/tester"}
    assert actions.opened_settings[0]["video_name"] == "shop"


def test_another_sessions_page_is_listed_without_its_query():  # WS-72
    secret_page = "https://b.example.test/cb?code=SECRET#state=TOKEN"
    workspaces, _ = built(Workspace())
    workspaces.open_browser(as_(), url=A_SITE + "?mine=1")
    workspaces.open_browser(as_(), url=secret_page, session="tester")

    def urls(listed):
        return {s["name"]: s["url"] for s in listed}

    told = workspaces.open_browser(as_())
    assert urls(told["others"]) == {"tester": "https://b.example.test/cb"}
    assert urls(workspaces.describe(as_())["sessions"]) == {
        "default": A_SITE + "?mine=1", "tester": "https://b.example.test/cb",
    }
    # Its own context has it whole: only a reopen needs it, and it is its own.
    own = workspaces.describe(pinned("tester"))
    assert own["url"] == secret_page
    assert urls(own["sessions"]) == {
        "default": A_SITE, "tester": secret_page,
    }
    told = workspaces.open_browser(as_(), session="tester")
    assert urls(told["others"]) == {"default": A_SITE}


def test_open_session_answers_session_default_and_others():  # WS-48, WS-72
    workspaces, _ = built(Workspace())
    first = workspaces.open_browser(as_(), url=A_SITE)
    assert (first["workspace"], first["session"], first["default"]) == (
        "shop", "default", True
    )
    assert "others" not in first, "a workspace with one session is as it was"
    second = workspaces.open_browser(as_(), url=B_SITE, session="tester")
    assert (second["workspace"], second["session"], second["default"]) == (
        "shop", "tester", False
    )
    assert second["others"] == [
        {"name": "default", "label": "default", "default": True, "live": True,
         "url": A_SITE, "uri": "session://default"},
    ]
    workspaces.end_browser(as_(), session="tester")
    again = workspaces.open_browser(as_())
    assert again["session"] == "default"
    assert again["others"] == [
        {"name": "tester", "label": "tester", "default": False, "live": False,
         "url": B_SITE, "uri": "session://tester"},
    ]


def test_a_subjects_own_sub_session_is_labelled_by_its_username():
    workspaces, _ = built(Workspace(owner="sub-a"))
    workspaces.open_browser(as_(FIRST), url=A_SITE)
    told = workspaces.open_browser(as_(SECOND), url=B_SITE)
    assert (told["session"], told["default"]) == ("sub-b", False)
    assert [o["name"] for o in told["others"]] == ["default"]
    assert workspaces.open_browser(as_(FIRST))["others"][0]["label"] == "second"


def test_listing_sessions_sends_no_browser_a_command():  # WS-53, WS-72
    actions = RecordingActions()
    workspaces, store = two_open(actions)
    default, tester = ids(store)
    grid = actions.grid
    grid.checked.clear()
    grid.listed = 0
    listed = workspaces.listed_sessions("shop")
    assert [(s["name"], s["live"]) for s in listed] == [
        ("default", True), ("tester", True),
    ]
    assert grid.checked == [], "a command to a browser is activity that keeps it"
    assert grid.listed == 1, "one Grid listing for every session"
    # A context read asks after its own browser only.
    grid.checked.clear()
    workspaces.describe(as_())
    assert tester not in grid.checked
    grid.checked.clear()
    told = workspaces.open_browser(as_(), session="tester")
    assert default not in grid.checked
    assert told["others"][0]["live"] is True
    # Ended: the listing no longer has it.
    workspaces.end_browser(as_(), session="tester")
    assert [s["live"] for s in workspaces.listed_sessions("shop")] == [True, False]


def test_a_grid_that_cannot_be_listed_lists_attached_sessions_as_live():
    actions = RecordingActions()
    workspaces, _ = two_open(actions)
    actions.grid.list_fails = True
    actions.grid.checked.clear()
    assert [s["live"] for s in workspaces.listed_sessions("shop")] == [True, True]
    assert actions.grid.checked == []


def test_an_acting_call_naming_an_unknown_session_opens_nothing():  # WS-78
    actions = RecordingActions()
    workspaces, store = built(Workspace(), actions)
    workspaces.open_browser(as_())
    ran = []
    with pytest.raises(faults.NoSession, match="open_session"):
        workspaces.act(as_(), ran.append, session="tester")
    with pytest.raises(faults.NoSession, match="open_session"):
        workspaces.end_browser(as_(), session="tester")
    assert ran == [] and actions.opened == 1 and actions.closed == []
    assert store.get("shop/tester") is None


def test_an_acting_call_on_a_session_with_no_browser_opens_nothing():  # WS-78
    actions = RecordingActions()
    workspaces, _ = two_open(actions)
    workspaces.end_browser(as_(), session="tester")
    ran = []
    with pytest.raises(faults.NoSession, match="open_session"):
        workspaces.act(as_(), ran.append, session="tester")
    assert ran == [] and actions.opened == 2


# ---- the surfaces ------------------------------------------------------------


async def test_every_acting_tool_takes_a_session_and_no_flow_step_does(server):
    tools = {
        row.name: (await server.mcp.get_tool(row.name)).parameters
        for row in CAPABILITIES
    }
    lacking = [
        name for name, schema in tools.items()
        if name != "await_control" and "session" not in schema["properties"]
    ]
    assert lacking == []
    assert not [
        name for name, schema in step_schemas(tools).items()
        if "session" in schema["properties"]
    ], "a step runs in its run's session"


async def test_a_call_that_never_names_a_session_is_unchanged(twin):  # noqa: F811
    server, double = twin
    async with Client(server.mcp.server) as client:
        called = await client.call_tool("navigate", {"url": "https://example.test/a"})
    assert json.dumps(called.structured_content) == (
        '{"url": "https://example.test/navigate", "title": "Navigate"}'
    )
    assert double.calls == [("navigate", "b-1", {"url": "https://example.test/a"})]


async def test_a_tool_and_a_route_naming_a_session_act_in_it(twin):  # noqa: F811
    server, double = twin
    store = server.workspaces.store
    store.update(NAMED, lambda r: replace(r, subs=["tester"]))
    store.set(f"{NAMED}/tester", Workspace(session_id="b-2", label="tester"))
    double.grid.alive.add("b-2")
    args = {"url": "https://example.test/a", "session": "tester"}
    async with Client(server.mcp.server) as client:
        await client.call_tool("navigate", args)
    http = TestClient(server.mcp.server.http_app(), headers=AUTH)
    answered = http.post(f"/browser/navigate?workspace={NAMED}", json=args)
    assert answered.status_code == 200, answered.text
    assert double.calls == [
        ("navigate", "b-2", {"url": "https://example.test/a"}),
        ("navigate", "b-2", {"url": "https://example.test/a"}),
    ], "the call reaches the sub-session's browser and never sees `session`"
    typo = http.post(
        f"/browser/navigate?workspace={NAMED}", json={**args, "session": "typo"}
    )
    assert typo.status_code == 400 and "open_session" in typo.json()["error"]


# ---- what is logged ----------------------------------------------------------


def test_a_log_line_names_a_sub_session_by_its_workspace_alone():
    from kubed.selenium_flow.workspace import sessions

    assert sessions.logged("shop") == "shop"
    assert sessions.logged("shop/opaque-subject-123") == "shop (a sub-session)"


def test_a_subjects_own_sub_session_is_never_named_in_a_log_line(caplog):
    """Its key is `<workspace>/<subject>`, and no log line carries a subject:
    not as it opens, acts, is reopened after a reap and ends, nor in the
    refusal an HTTP call without a browser is logged with."""
    import logging

    from kubed.selenium_flow.http import answer

    caplog.set_level(logging.DEBUG, logger="kubed.selenium_flow")
    subject = "opaque-subject-123"
    caller = as_(Principal("oidc", subject=subject, username="tester"))
    actions = RecordingActions()
    workspaces, store = built(Workspace(owner="sub-a"), actions)
    workspaces.open_browser(caller, url=A_SITE)
    key = f"shop/{subject}"
    workspaces.act(caller, lambda s: {"url": A_SITE}, tool="navigate")
    actions.grid.alive.discard(store.get(key).session_id)
    assert "reopened" in workspaces.act(caller, lambda s: {"url": A_SITE})
    workspaces.end_browser(caller)
    with pytest.raises(faults.NoSession) as refused:
        workspaces.act(caller, lambda s: {})
    response = answer.as_response(
        refused.value, "navigate", logging.getLogger("kubed.selenium_flow.routes")
    )
    assert response.status_code == 400
    assert subject not in response.body.decode()
    said = [r.getMessage() for r in caplog.records]
    assert any("ended the browser for workspace shop (a sub-session)" in m for m in said)
    assert any("reopening for workspace shop (a sub-session)" in m for m in said)
    assert any("refused (400)" in m for m in said)
    assert not [m for m in said if subject in m]
