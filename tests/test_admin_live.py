"""The live view's way in: the mint, the signed socket, and what each refuses.

The hub is played by a scripted x11vnc on a `FakePeer` that runs on the
server's own event loop (Starlette's TestClient runs the app on a loop of its
own, in a thread), so the socket's whole road is walked without a Grid.
"""

import asyncio
import logging
import time
from urllib.parse import parse_qsl, urlsplit

import pytest
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.http import links
from kubed.selenium_flow.http.admin import live as live_module
from kubed.selenium_flow.live.rfb import RFB_38
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace.store import Workspace

from .conftest import TOKEN
from .fakes import FakePeer
from .test_live_tunnel import GRID_ID, vnc_node

pytestmark = pytest.mark.unit

AUTH = {"Authorization": f"Bearer {TOKEN}"}
SOCKET = "/admin/workspaces/w1/live/socket"


def build(prefix="", token=TOKEN, **live):
    settings = Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": token} if token else {},
        route_prefix=prefix,
        live=live,
    )
    server = SeleniumMCP(settings)
    server.actions.grid.vnc_url = lambda sid: f"ws://hub.invalid/{sid}"
    store = server.workspaces.store
    store.set(
        "w1",
        Workspace(
            session_id=GRID_ID,
            opened_at=1_700_000_000.0,
            ran={"live_view": True},
        ),
    )
    store.set("idle", Workspace(session_id=""))
    store.set(
        "novnc",
        Workspace(session_id="g-other", ran={"live_view": False}),
    )
    return server


@pytest.fixture
def hub(monkeypatch):
    """`open_socket`, replaced by a hub that answers like x11vnc."""
    dialled = []

    async def dial(url, **_kw):
        dialled.append(url)
        peer = FakePeer()
        peer.node = asyncio.create_task(vnc_node(peer))
        return peer

    monkeypatch.setattr(live_module, "open_socket", dial)
    return dialled


@pytest.fixture
def server(hub):
    return build()


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app())


def mint(client):
    return client.get("/admin/workspaces/w1/live", headers=AUTH).json()["url"]


def test_the_mint_answers_a_signed_url_bound_to_the_admin(client):
    body = client.get("/admin/workspaces/w1/live", headers=AUTH).json()
    assert body["heartbeat"] == 10 and body["grace"] == 30
    assert body["url"].startswith("/admin/workspaces/w1/live/socket?exp=")
    assert "by=admin" in body["url"]


def test_the_mint_is_behind_the_admin_door(client):
    assert client.get("/admin/workspaces/w1/live").status_code == 401


@pytest.mark.parametrize(
    "name, enabled, reason",
    [
        ("w1", False, "the live view is switched off"),
        ("idle", True, "this workspace has no browser open"),
        ("novnc", True, "this browser has no live view"),
    ],
)
def test_the_mint_refuses_with_404_and_a_reason(hub, name, enabled, reason):
    client = TestClient(build(enabled=enabled).mcp.http_app())
    answered = client.get(f"/admin/workspaces/{name}/live", headers=AUTH)
    assert answered.status_code == 404
    assert answered.json() == {"error": reason}


def test_the_socket_is_routed_and_mounted_with_the_prefix(hub):
    routes = build(prefix="/flow").mcp.http_app().routes
    found = [r for r in routes if r.name == "admin_live_socket"]
    assert len(found) == 1 and isinstance(found[0], WebSocketRoute)
    assert found[0].path == "/flow/admin/workspaces/{key}/live/socket"


def test_a_minted_link_opens_the_view_in_watch(client, hub):
    with client.websocket_connect(mint(client)) as ws:
        assert ws.receive_json() == {"type": "mode", "mode": "watch"}
    assert hub == [f"ws://hub.invalid/{GRID_ID}"]


def closed_with(client, url, **kw):
    with (
        pytest.raises(WebSocketDisconnect) as gone,
        client.websocket_connect(url, **kw) as ws,
    ):
        ws.receive_json()
    return gone.value


@pytest.mark.parametrize("change", ["by=drk", "sig=00", "no query"])
def test_an_altered_or_missing_link_closes_4401(client, change):
    url = mint(client)
    if change == "no query":
        url = url.split("?")[0]
    else:
        name, value = change.split("=")
        pairs = [(k, value if k == name else v) for k, v in parse_qsl(urlsplit(url).query)]
        url = url.split("?")[0] + "?" + "&".join(f"{k}={v}" for k, v in pairs)
    gone = closed_with(client, url)
    assert (gone.code, gone.reason) == (4401, "unauthorized")


def test_an_expired_link_closes_4401_saying_so(client):
    url = links.sign(SOCKET, TOKEN, 60, now=time.time() - 3600, by="admin")
    gone = closed_with(client, url)
    assert (gone.code, gone.reason) == (4401, live_module.EXPIRED)


def test_an_admin_header_opens_the_socket_without_a_link(client):
    with client.websocket_connect(SOCKET, headers=AUTH) as ws:
        assert ws.receive_json() == {"type": "mode", "mode": "watch"}


def test_the_socket_refuses_4004_once_the_browser_has_gone(client, server):
    url = mint(client)
    server.workspaces.store.set("w1", Workspace(session_id=""))
    gone = closed_with(client, url)
    assert (gone.code, gone.reason) == (4004, live_module.NO_BROWSER)


def test_an_unreadable_password_file_closes_4003(hub, tmp_path):
    server = build(password_file=str(tmp_path / "missing"))
    client = TestClient(server.mcp.http_app())
    gone = closed_with(client, mint(client))
    assert gone.code == 4003


def test_a_refusal_is_logged_without_the_link(client, caplog):
    with caplog.at_level("INFO"):
        closed_with(client, SOCKET + "?exp=1&sig=00")
    text = caplog.text
    assert "refused" in text and "sig=00" not in text and GRID_ID not in text


def test_the_payload_says_whether_live_is_on(client):
    assert client.get("/admin/workspaces", headers=AUTH).json()["live_enabled"] is True


def test_the_payload_says_when_live_is_off(hub):
    client = TestClient(build(enabled=False).mcp.http_app())
    assert client.get("/admin/workspaces", headers=AUTH).json()["live_enabled"] is False


def test_an_unbound_link_never_opens_the_socket(client):
    gone = closed_with(client, links.sign(SOCKET, TOKEN, 60))
    assert (gone.code, gone.reason) == (4401, "unauthorized")


def test_a_link_for_one_workspace_opens_no_other(client, server):
    server.workspaces.store.set(
        "w2", Workspace(session_id="g-2", ran={"live_view": True})
    )
    url = mint(client).replace("/w1/", "/w2/")
    gone = closed_with(client, url)
    assert (gone.code, gone.reason) == (4401, "unauthorized")


def test_without_a_token_the_url_is_unsigned_and_the_viewer_is_open(
    hub, caplog, tmp_path
):
    password = tmp_path / "vnc"
    password.write_text("secret\n")
    client = TestClient(build(token=None, password_file=str(password)).mcp.http_app())
    url = client.get("/admin/workspaces/w1/live").json()["url"]
    assert url == SOCKET
    with (
        caplog.at_level("INFO", logger="kubed.selenium_flow.live"),
        client.websocket_connect(url) as ws,
    ):
        assert ws.receive_json() == {"type": "mode", "mode": "watch"}
        ws.receive_bytes()  # the greeting; noVNC answers it
        ws.send_bytes(RFB_38)
        assert ws.receive_bytes() == bytes([1, 1])
        ws.send_bytes(b"\x01")
        assert ws.receive_bytes() == bytes(4)
        ws.send_bytes(b"\x01")
        ws.receive_bytes()  # ServerInit: the line is written
    assert any("live: open opened" in r.getMessage() for r in caplog.records)


def test_a_forged_workspace_name_cannot_forge_a_log_line(client, caplog):
    path = "/admin/workspaces/x%0Alive:%20admin%20opened/live/socket"
    with caplog.at_level("INFO", logger="kubed.selenium_flow.live"):
        closed_with(client, path)
    records = [r for r in caplog.records if r.name == "kubed.selenium_flow.live"]
    assert len(records) == 1 and "\n" not in records[0].getMessage()


class Wire:
    """An ASGI send that fails as a dead transport does."""

    def __init__(self):
        self.sent = []

    async def receive(self):
        return {"type": "websocket.connect"}

    async def send(self, message):
        if message["type"] == "websocket.accept":
            return
        raise OSError("transport gone")


def dead_viewer():
    scope = {"type": "websocket", "path": "/", "headers": [], "query_string": b""}
    socket = WebSocket(scope, Wire().receive, Wire().send)
    socket.application_state = socket.client_state = (
        __import__("starlette.websockets", fromlist=["x"]).WebSocketState.CONNECTED
    )
    return live_module._Viewer(socket)


async def test_a_viewer_whose_transport_is_gone_raises_socket_closed_on_send():
    from kubed.selenium_flow.faults import SocketClosed

    with pytest.raises(SocketClosed):
        await dead_viewer().send("x")
    with pytest.raises(SocketClosed):
        await dead_viewer().send(b"x")


async def test_closing_a_viewer_whose_transport_is_gone_is_quiet():
    await dead_viewer().close(4000, "bye")


@pytest.mark.parametrize("failing", ["store", "door"])
def test_a_failure_after_accept_closes_1011_and_logs_its_type(
    hub, failing, caplog, monkeypatch
):
    server = build()
    client = TestClient(server.mcp.http_app())
    url = mint(client)
    if failing == "store":
        def boom(_key):
            raise RuntimeError("redis://user:pw@secret-host blew up")

        monkeypatch.setattr(server.workspaces.store, "get", boom)
        headers = {}
    else:
        async def admit(_request):
            raise RuntimeError("https://secret-host/jwks blew up")

        monkeypatch.setattr(server.doors.admin, "admit", admit)
        url, headers = SOCKET, AUTH
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.live"):
        gone = closed_with(client, url, headers=headers)
    assert (gone.code, gone.reason) == (1011, "the live view failed")
    assert "RuntimeError" in caplog.text and "secret-host" not in caplog.text
