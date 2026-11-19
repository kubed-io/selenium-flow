"""A flow runs in one session, and a person's control covers one (FLOW-50,
FLOW-51, LIVE-42, LIVE-15)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from kubed.selenium_flow import faults
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.flows import api as flowapi
from kubed.selenium_flow.flows import store as flow_store
from kubed.selenium_flow.live.control import Controls
from kubed.selenium_flow.live.tunnels import Tunnels
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.site_data import snapshot as site_data
from kubed.selenium_flow.workspace import locks
from kubed.selenium_flow.workspace.store import MemoryStore, Workspace
from kubed.selenium_flow.workspace.workspaces import Workspaces, acting

from .conftest import NAMED, calling_as
from .fakes import BindingCatalogue, RecordingActions, TabbedActions, held, untabbed
from .test_admin_live import hub  # noqa: F401 - a fixture, used below
from .test_await_control import (  # noqa: F401 - fixtures, used below
    GRID,
    OUTLINE,
    entered,
    hand_back,
    hold,
    legacy,
    named,
    server,
)
from .test_golden import AUTH, SETTINGS, Acting, handed_to
from .test_sub_sessions import A_SITE, B_SITE, as_, ids, two_open

pytestmark = pytest.mark.unit

STEPS = [
    {"tool": "navigate", "args": {"url": "https://example.test/a"}},
    {"tool": "extract", "args": {"selector": {"css": "h1"}}},
]


@pytest.fixture
def flows(monkeypatch, tmp_path):
    """`twin`, with a data directory so flows are on, and a sub-session `tester`
    on its own browser beside the default's."""
    double = Acting()
    for name in ("navigate", "extract"):
        monkeypatch.setattr(Actions, name, handed_to(double, name))
    app = untabbed(
        SeleniumMCP(Settings(**SETTINGS, data={"dir": str(tmp_path)}))
    )
    app.actions.grid = double.grid
    calling_as(monkeypatch, NAMED)
    store = app.workspaces.store
    store.set(NAMED, Workspace(session_id="b-1", subs=["tester"]))
    store.set(f"{NAMED}/tester", Workspace(session_id="b-2", label="tester"))
    double.grid.alive.update({"b-1", "b-2"})
    app.flows.save(NAMED, "trip", {"steps": STEPS})
    return app, double


def browsers(double):
    return [session_id for _tool, session_id, _kwargs in double.calls]


async def test_run_flow_runs_every_step_in_its_session(flows):
    app, double = flows
    async with Client(app.mcp) as client:
        await client.call_tool("run_flow", {"name": "trip", "session": "tester"})
        assert browsers(double) == ["b-2", "b-2"]
        await client.call_tool("run_flow", {"name": "trip"})
    assert browsers(double) == ["b-2", "b-2", "b-1", "b-1"]
    http = TestClient(app.mcp.http_app(), headers=AUTH)
    answered = http.post(
        f"/flows/trip/runs?workspace={NAMED}", json={"session": "tester"}
    )
    assert answered.status_code == 200, answered.text
    assert browsers(double)[4:] == ["b-2", "b-2"]
    # The pin is the session: another argument than the pin is refused, and
    # nothing runs.
    refused = http.post(
        f"/flows/trip/runs?workspace={NAMED}&session=tester",
        json={"session": "default"},
    )
    assert refused.status_code == 400, refused.text
    typo = http.post(f"/flows/trip/runs?workspace={NAMED}", json={"session": "typo"})
    assert typo.status_code == 400 and "open_session" in typo.json()["error"]
    assert len(double.calls) == 6


async def test_the_run_is_scrubbed_and_checked_as_its_session(flows):
    """The run's origins and history are the session's, not the default's."""
    app, _double = flows
    async with Client(app.mcp) as client:
        await client.call_tool("run_flow", {"name": "trip", "session": "tester"})
    store = app.workspaces.store
    workspaces = app.workspaces
    assert workspaces.context(f"{NAMED}/tester")["url"] == "https://example.test/extract"
    assert workspaces.context(NAMED).get("url", "") == ""
    assert {v["session"] for v in store.get(NAMED).history} == {"tester"}


async def test_save_flow_refuses_a_step_naming_a_session(flows):
    app, _ = flows
    steps = [
        {"tool": "navigate", "args": {"url": "https://example.test/", "session": "x"}}
    ]
    async with Client(app.mcp) as client:
        with pytest.raises(ToolError) as refused:
            await client.call_tool("save_flow", {"name": "bad", "steps": steps})
    assert "session" in str(refused.value)
    assert "run_flow" in str(refused.value)
    assert "has no parameter" not in str(refused.value)
    assert app.flows.get(NAMED, "bad") is None


class Saving(RecordingActions):
    """A save that names the site its browser is on, and what acted."""

    def __init__(self):
        super().__init__()
        self.acted_in = []

    def save_site_data(self, session_id, url=None):
        self.acted_in.append(acting())
        return {
            "url": B_SITE,
            "title": "t",
            site_data.CAPTURED: {
                "cookies": [{"name": "a", "value": "1", "domain": "b.example.test"}],
                "origin": B_SITE.rstrip("/"),
                "local": {"k": "v"},
                "session": {},
            },
        }


def test_a_flow_steps_save_site_data_saves_into_its_sessions_record(tmp_path):
    actions = Saving()
    workspaces, store = two_open(actions)
    flows = flow_store.LocalFlowStore(tmp_path)
    flows.save("shop", "keep", {"steps": [{"tool": "save_site_data", "args": {}}]})
    report = flowapi.run_for(
        flows, actions, workspaces, "shop", "keep", key="shop/tester"
    )
    assert report["status"] == "ok", report
    assert actions.acted_in == ["shop/tester"], "the step reads its session's sites"
    assert list(store.get("shop/tester").site_data["origins"]) == [
        B_SITE.rstrip("/")
    ]
    assert not store.get("shop").site_data
    assert acting() == "", "and only while the run does"


# ---- live control covers one session (LIVE-42) --------------------------------


def controlled():
    actions = RecordingActions()
    controls = Controls(behind=locks.behind, poll=0.01)
    store = MemoryStore()
    workspaces = Workspaces(actions, store, control=controls)
    workspaces.open_browser(as_(), url=A_SITE)
    workspaces.open_browser(as_(), url=B_SITE, session="tester")
    return workspaces, Tunnels(controls), store


def test_control_of_one_session_leaves_another_acting():
    workspaces, tunnels, store = controlled()
    default, tester = ids(store)
    tunnel = held(tunnels, "shop/tester", "drk", "control", grid_id=tester)
    assert tunnels.controls.controlling("shop/tester", "drk")
    assert not tunnels.controls.controlling("shop", "drk")
    with pytest.raises(faults.Controlled, match="has control of this browser"):
        workspaces.act(as_(), lambda s: {"url": B_SITE}, session="tester", tool="navigate")
    ran = []
    workspaces.act(as_(), lambda s: ran.append(s) or {"url": A_SITE}, tool="navigate")
    assert ran == [default]
    # A read answers in the controlled one (LIVE-15), and a hand-back frees it.
    workspaces.act(as_(), lambda s: {"url": B_SITE}, session="tester", tool="extract")
    tunnel.mode = "watch"
    tunnel.on_mode()
    workspaces.act(as_(), lambda s: {"url": B_SITE}, session="tester", tool="navigate")


async def test_await_control_waits_for_its_own_session(server, named, monkeypatch):  # noqa: F811
    store = server.store
    store.set("w1", replace(store.get("w1"), subs=["tester"]))
    store.set("w1/tester", Workspace(session_id="g2", label="tester"))
    server.browser.alive.add("g2")
    tunnel = await asyncio.to_thread(
        held, server.tunnels, "w1/tester", "alex", "control", "g2"
    )
    began = entered(server, monkeypatch)
    async with asyncio.timeout(5), legacy(server) as client:
        # The default is nobody's: it answers at once.
        quiet = await client.call_tool("await_control")
        assert quiet.structured_content["handed_back"] is False
        assert quiet.structured_content["control"] is None
        call = asyncio.create_task(
            client.call_tool("await_control", {"session": "tester"})
        )
        await began.wait()
        await asyncio.sleep(0.05)
        assert not call.done()
        hand_back(tunnel)
        result = await call
    summary = result.structured_content
    assert summary["handed_back"] is True
    assert summary["control"]["who"] == ["alex"]
    assert summary["page"]["url"] == OUTLINE["url"]
    # The default's answer read its own page; the hand-back read the session's.
    assert server.browser.read == [GRID, "g2"]


def test_a_notice_names_the_session_it_is_about():
    from kubed.selenium_flow.mcp.control import Notices

    workspaces, tunnels, store = controlled()
    held(tunnels, "shop/tester", "drk", "control", grid_id=ids(store)[1])
    from .test_sub_sessions import pinned

    notices = Notices(tunnels.controls, workspaces)
    assert notices.notice_for(as_(), None) is None
    assert notices.notice_for(as_(), "tester")["state"] == "held"
    assert notices.notice_for(pinned("tester"), None)["state"] == "held"


# ---- the admin's live view, one session at a time ------------------------------


def live_build():
    from .test_admin_live import build

    app = build()
    app.workspaces.store.update("w1", lambda r: replace(r, subs=["tester"]))
    app.workspaces.store.set(
        "w1/tester",
        Workspace(session_id="g-tester", label="tester", ran={"live_view": True}),
    )
    return app


def test_the_live_view_and_its_control_are_one_sessions(hub, monkeypatch):  # noqa: F811
    from starlette.websockets import WebSocketDisconnect

    from .test_admin_live import AUTH as ADMIN
    from .test_admin_live import GRID_ID
    from .test_live_tunnel import vnc_node

    app = live_build()
    client = TestClient(app.mcp.http_app())
    minted = client.get("/admin/workspaces/w1/live?session=tester", headers=ADMIN)
    assert minted.status_code == 200, minted.text
    url = minted.json()["url"]
    assert url.startswith("/admin/workspaces/w1/live/socket?session=tester&exp=")
    bad = client.get("/admin/workspaces/w1/live?session=console", headers=ADMIN)
    assert bad.status_code == 400
    # The link is for that session: without its `session` it opens nothing.
    for forged in (url.replace("session=tester&", ""), url.replace("tester", "other")):
        with (
            pytest.raises(WebSocketDisconnect) as gone,
            client.websocket_connect(forged) as ws,
        ):
            ws.receive_json()
        assert gone.value.code == 4401

    from .fakes import FakePeer

    async def dial(vnc, **_kw):
        peer = FakePeer()
        peer.node = asyncio.create_task(vnc_node(peer, password_asked=False))
        return peer

    from kubed.selenium_flow.http.admin import live as live_module

    monkeypatch.setattr(live_module, "open_socket", dial)
    with client.websocket_connect(url) as ws:
        assert ws.receive_json() == {"type": "mode", "mode": "watch"}
        ws.receive_bytes()
        ws.send_bytes(b"RFB 003.008\n")
        ws.receive_bytes()
        ws.send_bytes(b"\x01")
        ws.receive_bytes()
        ws.send_bytes(b"\x01")
        ws.receive_bytes()
        ws.send_json({"type": "mode", "mode": "control"})
        assert ws.receive_json() == {"type": "mode", "mode": "requested"}
        assert ws.receive_json() == {"type": "mode", "mode": "control"}
        assert app.controls.controlling("w1/tester", "admin")
        assert not app.controls.controlling("w1", "admin")
        assert app.controls.refusal("g-tester", "navigate") is not None
        assert app.controls.refusal(GRID_ID, "navigate") is None


class Echoing(TabbedActions):
    """Every page read says the secret's value, so the scrub is what hides it."""

    def extract(self, session_id, **kwargs):
        return {"url": B_SITE, "text": "beta-22"}


def test_a_flow_types_and_scrubs_with_its_own_sessions_origin(tmp_path):
    b = B_SITE.rstrip("/")
    catalogue = BindingCatalogue({b: {("b", "password"): "beta-22"}}, on=b)
    workspaces = Workspaces(Echoing(), MemoryStore(), catalogue=catalogue)
    try:
        workspaces.open_browser(as_(), url=A_SITE)
        workspaces.open_browser(as_(), url=B_SITE, session="tester")
        flows = flow_store.LocalFlowStore(tmp_path)
        flows.save("shop", "login", {"steps": [
            {"tool": "write", "args": {
                "selector": {"css": "#p"}, "secret": {"name": "b", "key": "password"},
            }},
            {"tool": "extract", "args": {"selector": {"css": "h1"}}, "return": True},
        ]})

        def run(key):
            return flowapi.run_for(
                flows, workspaces.actions, workspaces, "shop", "login",
                verbose=True, secrets_catalogue=catalogue, key=key,
            )

        # The secret is leashed to the sub's origin: typed there, never from
        # the default's page.
        report = run("shop/tester")
        assert report["status"] == "ok", report
        assert "beta-22" not in json.dumps(report)
        assert "[secret:b/password]" in json.dumps(report)
        refused = run("shop")
        assert refused["status"] == "failed", refused
        assert "beta-22" not in json.dumps(refused)
    finally:
        workspaces.scrubber.close()


def test_a_paste_goes_to_the_controlled_session_only(hub, monkeypatch):  # noqa: F811
    from .fakes import FocusedDriver, element
    from .test_admin_live import AUTH as ADMIN

    app = live_build()
    grid = app.actions.grid
    drivers = {}

    def reconnect(session_id):
        if session_id not in drivers:
            driver = FocusedDriver()
            driver.answers[:] = [element(driver.log)]
            drivers[session_id] = driver
        return drivers[session_id]

    monkeypatch.setattr(grid, "is_alive", lambda session_id: True)
    monkeypatch.setattr(grid, "reconnect", reconnect)
    client = TestClient(app.mcp.http_app())
    held(app.tunnels, "w1/tester", "admin", "control", "g-tester")
    paste = "/admin/workspaces/w1/live/paste"
    typed = client.post(f"{paste}?session=tester", json={"text": "hi"}, headers=ADMIN)
    assert typed.status_code == 200, typed.text
    assert typed.json() == {"typed": 2}
    assert set(drivers) == {"g-tester"}, "the default's browser received nothing"
    other = client.post(paste, json={"text": "hi"}, headers=ADMIN)
    assert other.status_code == 409
    assert set(drivers) == {"g-tester"}
