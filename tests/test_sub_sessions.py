"""Which session a call reaches: the default, a named sub-session, or the one a
token's subject gets when the default is another subject's (WS-48 to WS-50, WS-78)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastmcp.exceptions import ToolError
from fastmcp_tasks import call_tool_task

from kubed.selenium_flow import faults
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.mcp import waiting
from kubed.selenium_flow.principal import ADMIN, Principal
from kubed.selenium_flow.workspace.store import MemoryStore, Workspace
from kubed.selenium_flow.workspace.workspaces import Caller

from .conftest import manager
from .fakes import RecordingActions
from .test_await_control import over_http

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
    with pytest.raises(ValueError, match="pinned to session 'tester' and the call names 'default'"):
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
    workspaces.open_browser(as_(SECOND))
    workspaces.claim("shop", SECOND)
    assert store.get("shop").owner == "sub-a"
    assert actions.opened == 2


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
    import fastmcp.server.dependencies as dependencies

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
