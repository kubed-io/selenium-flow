"""The Captured tab's API: console, network, a request, its body, the live tail,
and the capture fields of a workspace row."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.http.admin import capture as admin_capture
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace.store import Workspace

from .capture_events import done, log, nav_started, req
from .conftest import TOKEN

pytestmark = pytest.mark.unit

AUTH = {"Authorization": f"Bearer {TOKEN}"}


class SilentGrid:
    """A Grid that fails the test if anything is put to it."""

    def __getattr__(self, name):
        def ask(*args, **kwargs):
            raise AssertionError(f"the Grid was asked: {name}")

        return ask


def feed(capture, *events, workspace="ws"):
    for method, params in events:
        capture.ingest(workspace, "s1", method, params)


@pytest.fixture
def server(tmp_path):
    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path)},
    ))
    server.workspaces.actions.grid = SilentGrid()
    server.actions.grid = server.workspaces.actions.grid
    return server


@pytest.fixture
def fed(server):
    """Workspace `ws` captures, holds no live browser, and has seen a failure."""
    server.workspaces.store.set("ws", Workspace(settings={"capture": True}))
    server.capture.attached("ws", "s1", "chrome", "top-1", again=False)
    feed(
        server.capture,
        nav_started("https://a.test/"),
        log("Uncaught TypeError", level="error", type="javascript"),
        req("r1", "https://a.test/api", initiator="fetch"),
        done("r1", 500),
    )
    return server


@pytest.fixture
def plain(server):
    server.workspaces.store.set("plain", Workspace(settings={}))


@pytest.fixture
def admin_app(server):
    return server.mcp.http_app()


@pytest.fixture
def admin_client(admin_app):
    return TestClient(admin_app)


def test_captured_console_reads_the_buffer_and_mints_a_signed_tail(admin_client, fed):
    r = admin_client.get("/admin/workspaces/ws/console?level=error", headers=AUTH)
    assert r.status_code == 200 and r.json()["entries"][0]["text"] == "Uncaught TypeError"
    tail = r.json()["tail_url"]
    assert tail.startswith("/admin/workspaces/ws/tail?exp=") and "&sig=" in tail


def test_network_lists_one_request_and_opens_it(admin_client, fed):
    listing = admin_client.get("/admin/workspaces/ws/network?filter=failed", headers=AUTH).json()
    assert [r["id"] for r in listing["requests"]] == ["1.1"] and "tail_url" in listing
    one = admin_client.get("/admin/workspaces/ws/network/1.1", headers=AUTH)
    assert one.status_code == 200 and one.json()["id"] == "1.1"


def test_a_bad_query_or_unknown_id_is_400(admin_client, fed):
    assert admin_client.get("/admin/workspaces/ws/console?pages=9", headers=AUTH).status_code == 400
    assert admin_client.get("/admin/workspaces/ws/network/9.9", headers=AUTH).status_code == 400


def test_every_captured_route_is_behind_the_admin_door(admin_client, fed):
    for path in ("/admin/workspaces/ws/console", "/admin/workspaces/ws/network",
                 "/admin/workspaces/ws/network/1.1"):
        assert admin_client.get(path).status_code == 401
    assert admin_client.post("/admin/workspaces/ws/network/1.1/body").status_code == 401
    assert admin_client.get("/admin/workspaces/ws/tail?kind=console").status_code == 401


def test_a_tail_link_for_one_workspace_does_not_open_another(admin_client, fed):
    signed = admin_client.get("/admin/workspaces/ws/console", headers=AUTH).json()["tail_url"]
    other = signed.replace("/workspaces/ws/", "/workspaces/them/")
    assert admin_client.get(other).status_code == 401


def test_a_tail_path_quotes_the_key():
    assert admin_capture.tail_path("a b/c") == "/admin/workspaces/a%20b%2Fc/tail"


def test_a_bad_kind_is_400(admin_client):
    assert admin_client.get("/admin/workspaces/ws/tail?kind=all", headers=AUTH).status_code == 400


def test_a_body_with_no_live_browser_is_404_and_opens_nothing(admin_client, fed):
    r = admin_client.post("/admin/workspaces/ws/network/1.1/body", headers=AUTH)
    assert r.status_code == 404 and "no session is open" in r.json()["error"]


def test_a_body_is_asked_of_the_live_browser(admin_client, fed, monkeypatch):
    asked = []
    monkeypatch.setattr(fed.workspaces, "browser", lambda key: "s1")
    monkeypatch.setattr(
        fed.actions, "network_body", lambda sid, id: asked.append((sid, id)) or {"body": "{}"}
    )
    r = admin_client.post("/admin/workspaces/ws/network/1.1/body", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"body": "{}"} and asked == [("s1", "1.1")]


def test_an_unknown_body_id_is_400(admin_client, fed, monkeypatch):
    monkeypatch.setattr(fed.workspaces, "browser", lambda key: "s1")
    r = admin_client.post("/admin/workspaces/ws/network/9.9/body", headers=AUTH)
    assert r.status_code == 400 and "s1" not in r.text


def test_capture_off_reads_as_off(admin_client, plain):
    assert admin_client.get("/admin/workspaces/plain/network", headers=AUTH).json() == {
        "capture": False, "how": "open_session(capture=true)"}


def test_the_row_carries_capture_capturing_counts_and_ttl(admin_client, fed):
    [row] = admin_client.get("/admin/workspaces", headers=AUTH).json()["workspaces"]
    assert (row["capture"], row["capture_ttl"]) == (True, 10_800)
    assert row["capturing"] is False
    assert set(row["captured"]) == {"console", "network", "errors", "failed_requests"}


def test_a_row_without_capture_says_so(admin_client, plain):
    [row] = admin_client.get("/admin/workspaces", headers=AUTH).json()["workspaces"]
    assert (row["capture"], row["capturing"], row["captured"]) == (False, False, None)


class Events:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.lines: list[str] = []
        self.status = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        return json.loads(await asyncio.wait_for(self.queue.get(), 5))


@asynccontextmanager
async def stream(app, target, headers=None):
    """One raw-ASGI SSE client: each `data:` line is parsed and queued."""
    path, _, query = target.partition("?")
    events, stop = Events(), asyncio.Event()

    async def receive():
        await stop.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            events.status = message["status"]
        elif message["type"] == "http.response.body":
            for line in message.get("body", b"").decode().splitlines():
                if line.startswith("data: "):
                    events.lines.append(line)
                    events.queue.put_nowait(line[len("data: "):])

    task = asyncio.ensure_future(app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }, receive, send))
    try:
        yield events
    finally:
        stop.set()
        await asyncio.wait_for(task, 5)


async def until(condition, seconds=5.0):
    for _ in range(int(seconds / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


async def test_the_tail_streams_rows(admin_app, fed):
    async with stream(admin_app, "/admin/workspaces/ws/tail?kind=network", AUTH) as events:
        await until(lambda: fed.capture._tails)
        feed(fed.capture, req("r2", "https://a.test/api2", initiator="fetch"), done("r2", 500))
        first = await anext(events)
        assert first["url"] == "https://a.test/api2" and first["kept"] is True


async def test_a_signed_link_opens_the_tail_without_a_header(admin_app, fed):
    signed = TestClient(admin_app).get(
        "/admin/workspaces/ws/console", headers=AUTH).json()["tail_url"]
    async with stream(admin_app, signed + "&kind=console") as events:
        await until(lambda: fed.capture._tails)
        assert events.status == 200


async def test_the_tail_reports_drops(admin_app, fed):
    async with stream(admin_app, "/admin/workspaces/ws/tail?kind=console", AUTH) as events:
        await until(lambda: fed.capture._tails)
        [sub] = fed.capture._tails["ws"]
        sub.dropped = 3
        feed(fed.capture, log("later", level="error"))
        assert (await anext(events))["text"] == "later"
        assert await anext(events) == {"dropped": 3}


async def test_a_value_cannot_forge_a_frame(admin_app, fed):
    text = "x\n\ndata: forged"
    async with stream(admin_app, "/admin/workspaces/ws/tail?kind=console", AUTH) as events:
        await until(lambda: fed.capture._tails)
        feed(fed.capture, log(text, level="error"))
        row = await anext(events)
        assert row["text"] == text
        await asyncio.sleep(0.05)
        assert len(events.lines) == 1


async def test_a_client_that_goes_leaves_no_subscription(admin_app, fed):
    async with stream(admin_app, "/admin/workspaces/ws/tail?kind=console", AUTH):
        await until(lambda: fed.capture._tails)
    assert fed.capture._tails == {}
    assert not [t for t in asyncio.all_tasks() if "stream" in repr(t.get_coro())]
