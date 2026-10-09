"""Every published shape, committed, and the two surfaces held to one answer.

What an agent reads (tool names, descriptions, schemas, annotations), what an
HTTP client reads (the OpenAPI document, error bodies, the admin listing) and
what a flow run reports are snapshotted under ``tests/golden/``. A change to
any of them fails here naming the path that moved. See ``golden_tools``.

The parity test at the end is the other half: the golden says what each surface
publishes, and it says nothing about whether the two *answer* alike.
"""

import base64
import functools
import json
import threading

import pytest
import requests
import urllib3.exceptions
from fastmcp import Client
from mcp.types import Implementation
from selenium.common.exceptions import (
    SessionNotCreatedException,
    WebDriverException,
)
from starlette.testclient import TestClient

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.core.capabilities import ENDPOINTS
from kubed.selenium_flow.flows import run as flowrun
from kubed.selenium_flow.flows.run import run
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.spec import build_spec
from kubed.selenium_flow.workspace.store import MemoryStore, Workspace

from .conftest import NAMED, TOKEN, RecordingActions, calling_as
from .fakes import FakeActions, FakeClock, FakeGrid
from .golden_tools import compare, normalised
from .test_flowrun import SECRET_STEP, SIMPLE, Vault, flow

pytestmark = pytest.mark.unit

AUTH = {"Authorization": f"Bearer {TOKEN}"}
SETTINGS = {"grid": {"url": "http://grid.invalid:4444"}, "auth": {"token": TOKEN}}


# ---- the tools, in both client modes -----------------------------------------


async def published(server, monkeypatch, resources):
    """What a client that reads resources (``on``) or does not (``off``) is
    shown: the per-request shaping in ``mcp.clients`` and ``mcp.mirror``."""
    monkeypatch.setattr(
        clients_module, "request_values", lambda: ({"resources": [resources]}, {})
    )
    info = Implementation(name="a spec-complete client", version="1")
    async with Client(server.mcp, client_info=info) as client:
        tools = await client.list_tools()
    return {
        tool.name: {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
            "annotations": (
                tool.annotations.model_dump(mode="json", exclude_none=True)
                if tool.annotations
                else None
            ),
        }
        for tool in tools
    }


@pytest.mark.parametrize("resources", ["on", "off"])
async def test_the_tools_are_what_they_were(server, monkeypatch, resources):
    compare(
        f"tools-{resources}.json", await published(server, monkeypatch, resources)
    )


# ---- the OpenAPI document ----------------------------------------------------


async def test_the_openapi_document_is_what_it_was(server):
    spec = await build_spec(server.mcp, ENDPOINTS, "", authenticated=True)
    spec["info"]["version"] = "<version>"
    compare("openapi.json", normalised(spec))


# ---- flow run reports --------------------------------------------------------


class Shoots(FakeActions):
    def screenshot(self, session_id, **kwargs):
        return {
            "url": "https://x.test/shot",
            "file": {
                "name": "shot.png",
                "url": "https://x.test/files/abc/shot.png?exp=1790000000&sig="
                + "0" * 32,
            },
        }


class Submitting(FakeActions):
    def write(self, session_id, text, **kwargs):
        return {"url": f"https://x.test/?q={text}", "title": "t"}


def reports(monkeypatch):
    """The fixture matrix of ``test_flowrun``, each run on a fresh clock."""
    def clocked(runner):
        monkeypatch.setattr(flowrun, "time", FakeClock())
        return runner()

    secret_write = [
        {"tool": "write", "args": {"selector": {"css": "#p"}, "secret": SECRET_STEP}}
    ]
    cancelled = threading.Event()
    cancelled.set()
    return {
        "simple": lambda: run(FakeActions(), flow(SIMPLE), "b"),
        "simple_verbose": lambda: run(FakeActions(), flow(SIMPLE), "b", verbose=True),
        "guarded_secret": lambda: run(
            FakeActions(),
            flow(
                [
                    {
                        "tool": "write",
                        "args": {"selector": {"css": "#p"}, "secret": SECRET_STEP},
                        "return": True,
                    }
                ]
            ),
            "b",
            catalogue=Vault(),
        ),
        "failing": lambda: run(
            FakeActions(fail_on={"write"}, error="no element matched"),
            flow(SIMPLE),
            "b",
        ),
        "continue_on_error": lambda: run(
            FakeActions(fail_on={1}),
            flow(
                [
                    {
                        "tool": "interact",
                        "args": {"action": "click", "selector": {"css": ".c"}},
                        "onError": "continue",
                        "id": "dismiss-banner",
                    },
                    {"tool": "navigate", "args": {"url": "x"}},
                ]
            ),
            "b",
        ),
        "out_of_time": lambda: run(
            FakeActions(),
            flow([{"tool": "navigate", "args": {"url": str(n)}} for n in range(5)]),
            "b",
            timeout=2,
        ),
        "cancelled": lambda: run(
            FakeActions(), flow(SIMPLE), "b", stop=cancelled
        ),
        "redacted_url": lambda: run(
            Submitting(), flow(secret_write), "b", catalogue=Vault()
        ),
        "file_producing": lambda: run(
            Shoots(), flow([{"tool": "screenshot", "args": {}}]), "b"
        ),
    }, clocked


def test_the_flow_run_reports_are_what_they_were(monkeypatch):
    matrix, clocked = reports(monkeypatch)
    got = {name: clocked(runner) for name, runner in matrix.items()}
    compare("flow-reports.json", normalised(got))


# ---- the admin session listing -----------------------------------------------

NOW = 1_790_800_000.0


class Listing(FakeGrid):
    """A Grid that lists the one browser the first record holds."""

    def sessions(self):
        return [{"session_id": sid} for sid in sorted(self.alive)]


def test_the_admin_workspace_listing_is_what_it_was(tmp_path):
    server = SeleniumMCP(
        Settings(**SETTINGS, data={"dir": str(tmp_path)}),
        store=MemoryStore(),
    )
    server.actions.grid = Listing()
    server.actions.grid.alive.add("live-1")
    site_data = {
        "cookies": [
            {"name": "sid", "value": "1", "domain": "app.example.com", "path": "/"}
        ],
        "origins": {"https://app.example.com": {"local": {"a": "1"}}},
        "session": {},
        "saved_at": NOW - 30,
    }
    store = server.workspaces.store
    store.set(
        "live",
        Workspace(session_id="live-1", opened_at=NOW - 600).visited(
            "https://app.example.com/x", now=NOW - 60
        ),
    )
    store.set(
        "detached",
        Workspace(session_id="", opened_at=NOW - 7200).visited(
            "https://mail.example.org/inbox", now=NOW - 3600
        ),
    )
    store.set(
        "sited",
        Workspace(session_id="", opened_at=NOW - 1800)
        .visited("https://app.example.com/x", now=NOW - 90)
        .with_site_data(site_data),
    )
    reopened = Workspace(
        session_id="live-1",
        opened_at=NOW - 300,
        reopened={"browser": "live-1", "report": {"restored": 1, "skipped": 0}},
    ).visited("https://app.example.com/y", now=NOW - 20)
    store.set("reopened", reopened)
    body = TestClient(server.mcp.http_app(), headers=AUTH).get("/admin/sessions")
    assert body.status_code == 200
    compare("admin-sessions.json", normalised(body.json()))


# ---- every error envelope ----------------------------------------------------


def raised():
    """One instance of every class ``errors`` classifies, and the three it
    falls back on."""
    http = requests.HTTPError("x")
    gone = requests.Response()
    gone.status_code = 404
    http_404 = requests.HTTPError("404 Client Error", response=gone)
    refused = requests.Response()
    refused.status_code = 403
    http_403 = requests.HTTPError("403 Client Error", response=refused)
    classes = [
        *errors.CALLER,
        *errors.GONE,
        *errors.UNAVAILABLE,
    ]
    out = {}
    for cls in classes:
        if cls is SessionNotCreatedException:
            out[cls.__name__] = cls("no free slot")
        elif cls is urllib3.exceptions.HTTPError:
            out["urllib3.HTTPError"] = cls("pool exhausted")
        elif cls is requests.ConnectionError:
            out["requests.ConnectionError"] = cls("refused http://u:p@grid:4444/")
        elif cls is requests.Timeout:
            out["requests.Timeout"] = cls("slow")
        else:
            out[cls.__name__] = cls("it went wrong")
    out["NotFound"] = faults.NotFound("no such thing")
    out["requests.HTTPError(no response)"] = http
    out["requests.HTTPError(404)"] = http_404
    out["requests.HTTPError(403)"] = http_403
    out["ValueError"] = ValueError("a bad value")
    out["TypeError"] = TypeError("a bad type")
    out["WebDriverException"] = WebDriverException("Message: the driver died\nStack")
    out["RuntimeError"] = RuntimeError("nobody expected this")
    return out


def test_every_error_envelope_is_what_it_was(monkeypatch):
    def fail(session_id, url):
        raise current[0]

    current = [None]
    monkeypatch.setattr(Actions, "navigate", lambda self, s, url: fail(s, url))
    server = SeleniumMCP(Settings(**SETTINGS))
    server.workspaces.store.set(NAMED, Workspace(session_id="b-1"))
    server.actions.grid = FakeGrid()
    server.actions.grid.alive.add("b-1")
    client = TestClient(server.mcp.http_app(), headers=AUTH)
    got = {}
    for name, exc in raised().items():
        current[0] = exc
        response = client.post(
            f"/browser/navigate?session={NAMED}", json={"url": "https://x.test/"}
        )
        got[name] = {"status": response.status_code, "body": response.json()}
    compare("errors.json", normalised(got))


# ---- parity: the tool and the endpoint answer alike --------------------------


class Acting(RecordingActions):
    """The six actions parity drives, each recording its call and answering
    from it, so what comes back is a function of what was asked."""

    PNG = base64.b64encode(b"\x89PNG-bytes").decode()

    def __init__(self):
        super().__init__()
        self.calls = []

    def _answer(self, tool, session_id, **kwargs):
        self.calls.append((tool, session_id, kwargs))
        return {"url": f"https://example.test/{tool}", "title": tool.title()}

    def navigate(self, session_id, url):
        return self._answer("navigate", session_id, url=url)

    def extract(self, session_id, **kwargs):
        return {**self._answer("extract", session_id, **kwargs), "text": "the text"}

    def write(self, session_id, text, **kwargs):
        return {**self._answer("write", session_id, text=text, **kwargs), "value": text}

    def interact(self, session_id, action, **kwargs):
        return self._answer("interact", session_id, action=action, **kwargs)

    def screenshot(self, session_id, **kwargs):
        return {**self._answer("screenshot", session_id, **kwargs), "image": self.PNG}

    def save_site_data(self, session_id, url=None):
        return {**self._answer("save_site_data", session_id, url=url), "saved": True}


def handed_to(double, name):
    """`Actions.<name>` answering from ``double``, keeping the real signature:
    the routes derive the fields a body may carry from it."""
    real = getattr(Actions, name)

    @functools.wraps(real)
    def call(self, *args, **kwargs):
        return getattr(double, name)(*args, **kwargs)

    return call


@pytest.fixture
def twin(monkeypatch):
    """One server whose actions are `Acting`, reachable by tool and by HTTP.

    The routes bind their action when the server is built, and the tools look
    it up per call, so the double goes onto the class: both surfaces then
    reach it through the real `Workspaces.act`.
    """
    double = Acting()
    for name in ("navigate", "extract", "write", "interact", "screenshot",
                 "save_site_data"):
        monkeypatch.setattr(Actions, name, handed_to(double, name))
    server = SeleniumMCP(Settings(**SETTINGS))
    server.actions.grid = double.grid
    calling_as(monkeypatch, NAMED)
    server.workspaces.store.set(NAMED, Workspace(session_id="b-1"))
    double.grid.alive.add("b-1")
    return server, double


PARITY = [
    ("navigate", "navigate", {"url": "https://example.test/a"}),
    ("extract", "extract", {"selector": {"css": "h1"}, "wait_timeout": 3}),
    ("write", "write", {"text": "hello", "selector": {"css": "#q"}}),
    ("interact", "interact/click", {"action": "click", "selector": {"css": "b"}}),
    ("screenshot", "screenshot", {"save": False}),
    ("save_site_data", "save-site-data", {}),
]


def plain(value):
    """A tool hands the action a `Selector`, a body hands it a dict; the action
    takes either, so they are the same request."""
    dump = getattr(value, "model_dump", None)
    return dump(exclude_none=True) if dump else value


@pytest.mark.parametrize("tool,route,args", PARITY, ids=[p[0] for p in PARITY])
async def test_the_tool_and_the_endpoint_answer_alike(twin, tool, route, args):
    server, double = twin
    async with Client(server.mcp) as client:
        called = await client.call_tool(tool, args)
    tool_calls, double.calls = list(double.calls), []
    http = TestClient(server.mcp.http_app(), headers=AUTH).post(
        f"/browser/{route}?session={NAMED}", json=args
    )
    assert http.status_code == 200, http.text
    assert [c[:2] for c in tool_calls] == [c[:2] for c in double.calls]

    by_tool = dict(called.structured_content or {})
    by_http = http.json()
    if tool == "screenshot":
        # The one sanctioned difference: an image block here, base64 there.
        image = next(b for b in called.content if b.type == "image")
        assert base64.b64decode(image.data) == base64.b64decode(by_http.pop("image"))
        # The tool says beside the image only what a caller cannot derive.
        by_http = {
            k: v for k, v in by_http.items()
            if k in ("file", "file_error", "site_data")
        }
    assert json.dumps(by_tool, sort_keys=True) == json.dumps(by_http, sort_keys=True)
    # What was asked of the action is the same too, up to the arguments a
    # surface fills in from its own defaults.
    asked_tool, asked_http = tool_calls[0][2], double.calls[0][2]
    # An argument the caller passed is never dropped by either surface.
    for key in args:
        assert key in asked_tool, f"the tool dropped {key}"
        assert key in asked_http, f"the endpoint dropped {key}"
    for key in set(asked_tool) & set(asked_http):
        assert plain(asked_tool[key]) == plain(asked_http[key]), key
