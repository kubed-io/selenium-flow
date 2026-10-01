"""Site data on both surfaces: the tool, the endpoint, and the two resources."""

import json

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from kubed.selenium_flow import routes
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core import site_data
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.session.sessions import Caller, SessionManager
from kubed.selenium_flow.session.store import SessionRecord

from .conftest import NAMED, TOKEN

pytestmark = pytest.mark.unit

SITE = "app.example.com"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def saved_record():
    data, _ = site_data.merge(
        {},
        {
            "cookies": [
                {"name": "sid", "value": "s3cret", "domain": SITE, "http_only": True},
                {"name": "theme", "value": "dark", "domain": SITE},
            ],
            "origin": f"https://{SITE}",
            "local": {"k": "v"},
            "session": {},
        },
        1000.0,
    )
    return SessionRecord(url=f"https://{SITE}/x", site_data=data)


@pytest.fixture
def saved(server):
    server.sessions.name = lambda: NAMED
    server.sessions.caller = lambda: Caller(NAMED, "header")
    server.sessions.store.set(NAMED, saved_record())
    return server


async def read(server, uri):
    async with Client(server.mcp) as c:
        return json.loads((await c.read_resource(uri))[0].text)


async def test_save_site_data_is_a_tool_and_an_endpoint(server):
    async with Client(server.mcp) as c:
        names = {t.name for t in await c.list_tools()}
    assert "save_site_data" in names
    assert routes.ENDPOINTS["save-site-data"] == "save_site_data"


async def test_open_session_takes_restore_site_data(server):
    async with Client(server.mcp) as c:
        tool = next(t for t in await c.list_tools() if t.name == "open_session")
    assert tool.input_schema["properties"]["restore_site_data"]["default"] is True


async def test_the_resources_list_and_show_one_site_masking_httponly(saved):
    listing = await read(saved, "session://site-data")
    assert [s["site"] for s in listing["sites"]] == [SITE]
    assert listing["sites"][0]["cookies"] == 2
    one = await read(saved, f"session://site-data/{SITE}")
    values = {c["name"]: c["value"] for c in one["cookies"]}
    assert values == {"sid": "•••", "theme": "dark"}
    assert one["storage"] == [
        {"origin": f"https://{SITE}", "local_storage": {"k": "v"}, "session_storage": {}}
    ]
    assert "pending" not in json.dumps([listing, one])
    with pytest.raises(Exception, match="session://site-data"):
        await read(saved, "session://site-data/nowhere.test")


async def test_the_http_routes_answer_the_same(saved):
    client = TestClient(saved.mcp.http_app())
    params = {"session": NAMED}
    listed = client.get("/site-data", headers=AUTH, params=params)
    one = client.get(f"/site-data/{SITE}", headers=AUTH, params=params)
    assert listed.json() == await read(saved, "session://site-data")
    assert one.json() == await read(saved, f"session://site-data/{SITE}")
    assert client.get("/site-data", params=params).status_code == 401
    missing = client.get("/site-data/nowhere.test", headers=AUTH, params=params)
    assert missing.status_code == 400
    assert "session://site-data" in missing.json()["error"]


def test_the_capture_never_leaves_the_server(monkeypatch):
    captured = {
        "cookies": [{"name": "a", "value": "1", "domain": SITE}],
        "origin": f"https://{SITE}",
        "local": {"k": "v"},
        "session": {},
    }
    monkeypatch.setattr(
        Actions,
        "save_site_data",
        lambda self, session_id, url=None: {
            "url": f"https://{SITE}/",
            "title": "t",
            site_data.CAPTURED: captured,
        },
    )
    monkeypatch.setattr(SessionManager, "resolve", lambda self, name, **kw: "live-id")
    # After the patches: a route binds its action when the server is built.
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN})
    )
    # The record names the browser `resolve` hands back, as it does for real:
    # a save is kept only by the browser that captured it.
    server.sessions.store.set(
        NAMED, SessionRecord(session_id="live-id", url=f"https://{SITE}/")
    )
    client = TestClient(server.mcp.http_app())
    response = client.post(
        "/browser/save-site-data", headers=AUTH, params={"session": NAMED}, json={}
    )
    body = response.json()
    assert response.status_code == 200, body
    assert site_data.CAPTURED not in body
    assert body["saved"] == {"cookies": 1, "sites": [f"https://{SITE}"], "skipped": []}
    assert body["uri"] == "session://site-data"


async def test_current_session_names_site_data(saved):
    current = await read(saved, "session://current")
    assert current["site_data"] == {"sites": 1, "uri": "session://site-data"}


def test_the_published_kept_shared_is_the_shape_returned():
    """Generated clients read the spec: `kept_shared` is a cookie's identity,
    not a bare name, so the published shape has to say so (Copilot, #49)."""
    from kubed.selenium_flow.spec.schemas import SITE_DATA_SCHEMAS

    items = SITE_DATA_SCHEMAS["SiteData"]["properties"]["kept_shared"]["items"]
    assert items["type"] == "object"
    assert set(items["required"]) == {"name", "domain", "path"}
    data, _ = site_data.merge(
        {},
        {"cookies": [{"name": "ab", "value": "1", "domain": ".example.com",
                      "path": "/", "http_only": False, "secure": True,
                      "same_site": "lax", "expiry": None}],
         "origin": "https://app.example.com", "local": {}, "session": {}},
        0.0,
    )
    shared = site_data.site_view(data, "app.example.com")["kept_shared"]
    assert shared and set(shared[0]) == set(items["required"])


def test_save_with_bidi_unreachable_is_a_scrubbed_503(monkeypatch):
    """Selenium surfaces a dead BiDi route as a closed websocket: a 500 saying
    "socket is already closed." told nobody what to do."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from websocket import WebSocketConnectionClosedException

    from kubed.selenium_flow.core.browser import Grid

    page = SimpleNamespace(current_url=f"https://{SITE}/", title="t")

    class Dead:
        def get_cookies(self, *a, **kw):
            raise WebSocketConnectionClosedException(
                "ws://user:pw@grid.invalid/session/x/se/bidi is closed"
            )

    @contextmanager
    def bidi(self, session_id):
        yield SimpleNamespace(storage=Dead())

    monkeypatch.setattr(Grid, "reconnect", lambda self, sid: page)
    monkeypatch.setattr(Grid, "bidi", bidi)
    monkeypatch.setattr(SessionManager, "resolve", lambda self, name, **kw: "live-id")
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN})
    )
    server.sessions.store.set(NAMED, SessionRecord(url=f"https://{SITE}/"))
    client = TestClient(server.mcp.http_app())
    response = client.post(
        "/browser/save-site-data", headers=AUTH, params={"session": NAMED}, json={}
    )
    assert response.status_code == 503, response.json()
    error = response.json()["error"]
    assert error.startswith("the browser's BiDi channel is unavailable")
    assert "pw" not in error
    assert server.sessions.store.get(NAMED).site_data == {}


def test_an_unexpected_cookie_read_failure_stays_a_500(monkeypatch):
    """Only a BiDi channel that did not answer is a retryable 503; a bug or an
    unexpected return shape stays a 500, as errors.py rules (Copilot, #50)."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from kubed.selenium_flow.core.browser import Grid

    page = SimpleNamespace(current_url=f"https://{SITE}/", title="t")

    class Broken:
        def get_cookies(self, *a, **kw):
            raise AttributeError("'dict' object has no attribute 'cookies'")

    @contextmanager
    def bidi(self, session_id):
        yield SimpleNamespace(storage=Broken())

    monkeypatch.setattr(Grid, "reconnect", lambda self, sid: page)
    monkeypatch.setattr(Grid, "bidi", bidi)
    monkeypatch.setattr(SessionManager, "resolve", lambda self, name, **kw: "live-id")
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN})
    )
    server.sessions.store.set(NAMED, SessionRecord(url=f"https://{SITE}/"))
    client = TestClient(server.mcp.http_app())
    response = client.post(
        "/browser/save-site-data", headers=AUTH, params={"session": NAMED}, json={}
    )
    assert response.status_code == 500, response.json()

