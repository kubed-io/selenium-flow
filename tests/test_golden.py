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
import hashlib
import json
import threading
from importlib.metadata import version
from pathlib import Path

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
from kubed.selenium_flow.mcp import skill
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.spec import build_spec
from kubed.selenium_flow.workspace.store import MemoryStore, Workspace

from .conftest import NAMED, TOKEN, RecordingActions, calling_as, over_http
from .fakes import FakeActions, FakeClock, FakeGrid, Vault, header, request, untabbed
from .golden_tools import GOLDEN, compare, normalised, render
from .test_flowrun import SECRET_STEP, SIMPLE, flow

pytestmark = pytest.mark.unit

AUTH = {"Authorization": f"Bearer {TOKEN}"}
SETTINGS = {"grid": {"url": "http://grid.invalid:4444"}, "auth": {"token": TOKEN}}


# ---- the tools, in both client modes -----------------------------------------


async def published(server, resources):
    """What a client that reads resources (``on``) or does not (``off``) is
    shown: the per-request shaping in kubed-mcp's delivery and ``mcp.apps``."""
    info = Implementation(name="a spec-complete client", version="1")
    async with over_http(server, {"X-MCP-Resources": resources}, client_info=info) as client:
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
async def test_the_tools_are_what_they_were(server, resources):
    compare(f"tools-{resources}.json", await published(server, resources))


# ---- the plugin surface ------------------------------------------------------

ROOTS = {
    "skill://selenium-flow/": "plugin/skills/",
    "prompts://selenium-flow/": "plugin/commands/",
    "agents://selenium-flow/": "plugin/agents/",
}
SERVER_INFO = "io.modelcontextprotocol/serverInfo"
DIRECTORY = "inode/directory"


def _root(uri: str) -> str | None:
    """The served root ``uri`` is under, if any."""
    return next((root for root in ROOTS if uri.startswith(root)), None)


def relative(uri: str) -> str:
    """A plugin file's path from the repo root."""
    root = _root(uri)
    return ROOTS[root] + uri.removeprefix(root)


def on_disk(uri: str) -> Path | None:
    """The plugin file served at ``uri``, or None for anything else: another
    scheme, a folder, and the ``_manifest`` and ``_index.*`` the plugin layer
    generates."""
    root = _root(uri)
    if root is None:
        return None
    rest = uri.removeprefix(root)
    name = rest.rsplit("/", 1)[-1]
    if name == "_manifest" or name.startswith("_index."):
        return None
    # A skill's root is a folder: `skill://selenium-flow/<skill>`.
    if root.startswith("skill://") and "/" not in rest:
        return None
    path = skill.plugin_path() / ROOTS[root].removeprefix("plugin/") / rest
    return path if path.is_file() else None


def stood_in(uri: str, text: str) -> str:
    """``text``, or the file's name when it is the file's bytes exactly: a
    served text that differs from disk by a byte stays whole, so the diff
    shows it."""
    path = on_disk(uri)
    if path is not None and text.encode("utf-8") == path.read_bytes():
        return f"<{relative(uri)}>"
    return text


def _measured(row: dict, uri: str) -> dict:
    """A row's size and digest (a manifest calls it ``hash``) as stand-ins,
    when both are the file's on disk."""
    path = on_disk(uri)
    key = "digest" if "digest" in row else "hash"
    if path is None or "size" not in row or key not in row:
        return row
    data = path.read_bytes()
    if row["size"] != len(data) or row[key] != f"sha256:{hashlib.sha256(data).hexdigest()}":
        return row
    name = relative(uri)
    return {**row, "size": f"<size of {name}>", key: f"<digest of {name}>"}


def _headed(row: dict, uri: str) -> dict:
    """A skill entry's front matter and a row's description as stand-ins,
    when they are the front matter of the SKILL.md on disk."""
    path = on_disk(uri)
    if path is None or path.name != "SKILL.md":
        return row
    front = header(path.read_text(encoding="utf-8"))
    name = relative(uri)
    row = dict(row)
    if row.get("frontmatter") == front:
        row["frontmatter"] = f"<frontmatter of {name}>"
    if "description" in row and row["description"] == front.get("description"):
        row["description"] = f"<description of {name}>"
    return row


def _stand_ins(data):
    """Every row naming a skill file, with what is the file's own stood in."""
    if isinstance(data, list):
        return [_stand_ins(v) for v in data]
    if not isinstance(data, dict):
        return data
    out = {k: _stand_ins(v) for k, v in data.items()}
    uri = out.get("uri")
    if isinstance(uri, str):
        out = _headed(_measured(out, uri), uri)
    return out


def _without_server_info(data):
    """``data`` without FastMCP's serverInfo, which names its version, and
    without the ``_meta`` it leaves empty."""
    if isinstance(data, list):
        return [_without_server_info(v) for v in data]
    if not isinstance(data, dict):
        return data
    out = {k: _without_server_info(v) for k, v in data.items()}
    meta = out.get("_meta")
    if isinstance(meta, dict) and SERVER_INFO in meta:
        meta.pop(SERVER_INFO)
        if not meta:
            out.pop("_meta")
    return out


def _text(uri: str, text: str):
    """A read's text: a manifest parsed, its files' sizes and digests stood
    in; an ``_index.json`` parsed, for `_stand_ins` to reach its rows; any
    other file stood in when it is the file on disk."""
    if uri.endswith("/_manifest"):
        manifest = json.loads(text)
        root = uri.removesuffix("_manifest")
        manifest["files"] = [
            _measured(f, root + f["path"]) for f in manifest.get("files", [])
        ]
        return manifest
    if uri.endswith("/_index.json"):
        return json.loads(text)
    return stood_in(uri, text)


def _parsed(result: dict) -> dict:
    """A tool result whose text blocks are JSON, parsed, for `_stand_ins` to
    reach the rows inside them."""
    return {
        **result,
        "content": [
            {**c, "text": json.loads(c["text"])} if "text" in c else c
            for c in result["content"]
        ],
    }


async def plugin_surface(server, mode: str, resources: str) -> dict:
    """Everything kubed-mcp's plugin layer serves one client, as it comes over
    the wire: ``request`` sends even the methods a client knows, so a result is
    the server's dict and not a client model's reading of it.

    It reads only the plugin's URIs (``skill://``, ``prompts://``,
    ``agents://``): the others ask the Grid.
    """
    info = Implementation(name="a spec-complete client", version="1")
    cell: dict = {}
    async with over_http(
        server, {"X-MCP-Resources": resources}, mode=mode, client_info=info
    ) as client:
        cell["initialize"] = {
            "capabilities": {"extensions": client.server_capabilities.extensions},
            "instructions": client.instructions,
        }
        cell["resources/list"] = (await request(client, "resources/list"))["resources"]
        cell["resources/templates/list"] = (
            await request(client, "resources/templates/list")
        )["resourceTemplates"]
        listing = await request(client, "skills/list")
        cell["skills/list"] = listing
        cell["skills/get"] = {
            entry["uri"]: await request(client, "skills/get", uri=entry["uri"])
            for entry in listing["skills"]
        }

        folders = []
        directories = {}
        files = []

        async def walk(start: str) -> None:
            pending = [start]
            while pending:
                folder = pending.pop(0)
                folders.append(folder)
                result = await request(client, "resources/directory/read", uri=folder)
                directories[folder] = result
                for row in result["resources"]:
                    if row.get("mimeType") == DIRECTORY:
                        pending.append(row["uri"])
                    elif not folder.startswith("skill://"):
                        files.append(row["uri"])

        for entry in listing["skills"]:
            await walk(entry["uri"].removesuffix("/SKILL.md"))
        for start in ("prompts://selenium-flow", "agents://selenium-flow"):
            await walk(start)
        cell["resources/directory/read"] = directories

        uris = []
        for entry in listing["skills"]:
            uris += [entry["uri"], *(row["uri"] for row in entry["resources"])]
            uris.append(entry["uri"].removesuffix("SKILL.md") + "_manifest")
        uris += files
        uris += [f"{folder}/_index.{kind}" for folder in folders for kind in ("md", "json")]
        uris = list(dict.fromkeys(uris))
        reads = {}
        for uri in uris:
            result = await request(client, "resources/read", uri=uri)
            reads[uri] = [
                {"text": _text(uri, c["text"]), "mimeType": c.get("mimeType")}
                for c in result["contents"]
            ]
        cell["resources/read"] = reads

        prompts = await request(client, "prompts/list")
        cell["prompts/list"] = prompts
        cell["prompts/get"] = {}
        for prompt in prompts["prompts"]:
            given = {
                a["name"]: f"<{a['name']}>"
                for a in prompt.get("arguments", [])
                if a.get("required")
            }
            cell["prompts/get"][prompt["name"]] = await request(
                client, "prompts/get", name=prompt["name"], arguments=given
            )

        agents = await request(client, "agents/list")
        cell["agents/list"] = agents
        cell["agents/get"] = {
            agent["name"]: await request(client, "agents/get", name=agent["name"])
            for agent in agents["agents"]
        }

        if resources == "off":
            cell["tools/list_resources"] = _parsed(
                await request(client, "tools/call", name="list_resources", arguments={})
            )
            mirrored = {}
            # A folder is a miss naming its index (`FolderNotFound.miss`): an
            # error result, recorded as served.
            for uri in [*uris, *folders]:
                result = await request(
                    client, "tools/call", name="read_resource", arguments={"uri": uri}
                )
                result["content"] = [
                    {**c, "text": _text(uri, c["text"])} if "text" in c else c
                    for c in result["content"]
                ]
                mirrored[uri] = result
            cell["tools/read_resource"] = mirrored

    versions = {
        version("kubed-selenium-flow"): "<version>",
        version("kubed-mcp"): "<kubed-mcp version>",
    }
    return normalised(_stand_ins(_without_server_info(cell)), replace=versions)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("resources", ["on", "off"])
async def test_the_plugin_surface_is_what_it_was(server, mode, resources):
    first = await plugin_surface(server, mode, resources)
    again = await plugin_surface(server, mode, resources)
    assert render(first) == render(again), "the surface moved between two runs"
    compare(f"plugin-{mode}-{resources}.json", first)


def test_the_plugin_golden_names_no_version():
    for path in GOLDEN.glob("plugin-*.json"):
        text = path.read_text()
        assert version("kubed-mcp") not in text, path.name
        assert "serverInfo" not in text, path.name


def test_a_file_unlike_its_disk_copy_keeps_its_text():
    uri = "skill://selenium-flow/driving-a-browser/SKILL.md"
    text = on_disk(uri).read_text(encoding="utf-8")
    assert stood_in(uri, text) == f"<{relative(uri)}>"
    assert stood_in(uri, text.replace("\n", "\r\n")) == text.replace("\n", "\r\n")
    assert stood_in(uri, text + "\n") == text + "\n"
    uri = "prompts://selenium-flow/build_flow.md"
    text = on_disk(uri).read_text(encoding="utf-8")
    assert stood_in(uri, text) == "<plugin/commands/build_flow.md>"
    assert stood_in(uri, text.replace("\n", "\r\n")) == text.replace("\n", "\r\n")


def test_on_disk_maps_only_plugin_files():
    assert on_disk("skill://selenium-flow/signing-in/references/SECRETS.md") == (
        skill.plugin_path() / "skills/signing-in/references/SECRETS.md"
    )
    assert on_disk("skill://selenium-flow/signing-in/_manifest") is None
    assert on_disk("skill://selenium-flow/signing-in/_index.md") is None
    assert on_disk("workspace://current") is None
    assert on_disk("prompts://selenium-flow/build_flow.md") == (
        skill.plugin_path() / "commands/build_flow.md"
    )
    assert on_disk("agents://selenium-flow/explorer.md") == (
        skill.plugin_path() / "agents/explorer.md"
    )
    assert on_disk("prompts://selenium-flow/_index.json") is None
    assert on_disk("agents://selenium-flow") is None


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


# ---- the admin workspace listing -----------------------------------------------

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
        Workspace(
            session_id="",
            opened_at=NOW - 7200,
            settings={"browser": "firefox", "version": "142"},
            sent=[
                {"key": "browserName", "value": "firefox", "sources": ["option"]},
                {"key": "kubed:pool", "value": "default", "sources": ["config"]},
                {
                    "key": "moz:firefoxOptions",
                    "value": {"prefs": {"pdfjs.disabled": True}},
                    "sources": ["flow", "raw"],
                },
            ],
            ran={"version": "142.0", "node": "node-firefox-1", "live_view": True},
            ended={"at": NOW - 3500, "by": "admin"},
        ).visited("https://mail.example.org/inbox", now=NOW - 3600),
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
    body = TestClient(server.mcp.server.http_app(), headers=AUTH).get("/admin/workspaces")
    assert body.status_code == 200
    compare("admin-workspaces.json", normalised(body.json()))


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
        elif cls is faults.Unresolved:
            out[cls.__name__] = cls("nonexistent.invalid")
        elif cls is faults.Untrusted:
            out[cls.__name__] = cls()
        elif cls is faults.ScriptTimedOut:
            out[cls.__name__] = cls(30)
        elif cls is faults.NodeLost:
            out[cls.__name__] = cls()
        else:
            out[cls.__name__] = cls("it went wrong")
    out["NodeRefused(blamed)"] = faults.NodeRefused("it went wrong", blamed=True)
    out["NodeRefused(not blamed)"] = faults.NodeRefused("it went wrong", blamed=False)
    out["NotFound"] = faults.NotFound("no such thing")
    out["Controlled"] = faults.Controlled("alex has control")
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
    server = untabbed(SeleniumMCP(Settings(**SETTINGS)))
    server.workspaces.store.set(NAMED, Workspace(session_id="b-1"))
    server.actions.grid = FakeGrid()
    server.actions.grid.alive.add("b-1")
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    got = {}
    for name, exc in raised().items():
        current[0] = exc
        response = client.post(
            f"/browser/navigate?workspace={NAMED}", json={"url": "https://x.test/"}
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
    server = untabbed(SeleniumMCP(Settings(**SETTINGS)))
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
    async with Client(server.mcp.server) as client:
        called = await client.call_tool(tool, args)
    tool_calls, double.calls = list(double.calls), []
    http = TestClient(server.mcp.server.http_app(), headers=AUTH).post(
        f"/browser/{route}?workspace={NAMED}", json=args
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
