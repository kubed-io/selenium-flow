"""No spelling of a secret allowed where a call ran survives any surface.

The regression the secret-scope notes ask for ("Coverage", spec
2026-11-14-secret-scope, plan input 6): every action answers with a value
allowed on its page in every spelling, in every string field, and raises with it
too; every resource an agent can read holds it; and what comes back - over MCP,
over HTTP, from a flow, through `read_resource` and `show`, and in the log - is
checked for any spelling of it outside a marker. A new tool or resource that
bypasses the choke points fails here, naming itself.

Exempt: a screenshot's pixels (ruling 12), and the resources whose text this
server wrote (`mcp.scrubbed.EXEMPT`, ruling 16), which are not read.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import re
from dataclasses import replace
from urllib.parse import quote

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from kubed.selenium_flow import redaction
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.core.capabilities import CAPABILITIES
from kubed.selenium_flow.http import files as files_module
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.mcp import mirror
from kubed.selenium_flow.mcp.scrubbed import EXEMPT
from kubed.selenium_flow.server import SeleniumMCP

from .capture_events import done, log, nav_started, req
from .conftest import NAMED, TOKEN, http
from .fakes import FakeGrid
from .test_redaction import golden

pytestmark = pytest.mark.unit

A = "https://a.test"
VALUE = "Alpha-Secret-11"
MARKED = "[secret:a/password]"
SPELLINGS = sorted({spelt for _, spelt in golden(VALUE)})
# Every spelling in one string, so each string field carries all of them.
ALL = " ".join(SPELLINGS)
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# A screenshot's pixels, with the value in them: exempt (ruling 12).
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + ALL.encode()).decode()
FLOW = "steps"
# A download the site named after the value: its name is the page's words.
DOWNLOAD = f"report-{VALUE}.txt"
# A flow whose first step's argument holds the value, so its summary does too.
FLOW_DOCUMENT = {
    "steps": [
        {"tool": "navigate", "args": {"url": f"{A}/?q={VALUE}"}},
        {"tool": "navigate", "args": {"url": f"{A}/"}},
        {"tool": "extract", "args": {"selector": {"css": "h1"}}, "return": True},
    ],
}

# What each capability is called with: the least that is a valid call.
ARGS = {
    "open_session": {"url": f"{A}/"},
    "navigate": {"url": f"{A}/"},
    "interact": {"action": "click", "selector": {"css": "b"}},
    "drag": {"selector": {"css": "a"}, "to": {"css": "b"}},
    "write": {"text": "x", "selector": {"css": "#q"}},
    "press_key": {"key": "Enter"},
    "extract": {},
    "execute_script": {"script": "return 1"},
    "assert": {"script": "return true"},
    "outline": {},
    "screenshot": {"save": False},
    "frame": {"action": "default"},
    "resize": {"width": 800, "height": 600},
    "dialog": {"action": "read"},
    "upload_file": {"selector": {"css": "input"}, "text": "x", "filename": "a.txt"},
    "print": {},
    "save_site_data": {},
    "network_body": {"id": "1.1"},
    "await_control": {},
    "end_browser": {},
}
# A bound write takes its own road (`secrets.perform_write`): one more call.
BOUND = {"selector": {"css": "#p"}, "secret": {"name": "a", "key": "password"}}


class Leaky:
    """Every action, answering with the value in every string field, or raising
    with it. ``opened`` counts the browsers it made."""

    def __init__(self):
        self.raises = False
        self.opened = 0
        self.grid = FakeGrid(
            entries=[{"name": "dl.txt", "size": 9}, {"name": DOWNLOAD, "size": 9}],
            data=ALL.encode(),
        )

    def _fail(self, name):
        raise ValueError(f"{name} failed on {A}/?q={ALL}: {ALL}")

    def answer(self, name, *_args, **_kwargs):
        if self.raises:
            self._fail(name)
        result = {
            "url": f"{A}/{name}?q={ALL}",
            "title": ALL,
            "text": ALL,
            "value": ALL,
            "result": ALL,
            "nested": {"deep": [ALL, {ALL: ALL}]},
        }
        if name == "screenshot":
            result.update(image=PNG, file_error=ALL)
        if name == "resize":
            result.update(width=800, height=600)
        return result

    def page(self, *_args, **_kwargs):
        return {"url": f"{A}/login?q={ALL}", "title": ALL}

    def open_session(self, url=None, **_kwargs):
        if self.raises:
            self._fail("open_session")
        self.opened += 1
        session_id = f"g-{self.opened}"
        self.grid.alive.add(session_id)
        return {
            "session_id": session_id,
            "url": f"{A}/?q={ALL}",
            "title": ALL,
            "sent": [],
            "ran": {"version": "1", "node": "n", "live_view": True},
        }

    def end_browser(self, session_id):
        self.grid.alive.discard(session_id)
        return {"success": True}


# The `Actions` methods the double answers for, by name: every capability's,
# and what a bound write and a failed flow step read the page with.
METHODS = sorted(
    {row.method for row in CAPABILITIES if not row.waits}
    | {"page", "outline", "open_session", "end_browser"}
)


def handed_to(double, name):
    """`Actions.<name>` answering from ``double``, keeping the real signature:
    the routes derive the fields a body may carry from it."""
    real = getattr(Actions, name)
    own = getattr(double, name, None)

    @functools.wraps(real)
    def call(self, *args, **kwargs):
        if own is not None:
            return own(*args, **kwargs)
        return double.answer(name, *args, **kwargs)

    return call


@pytest.fixture
def leaky(monkeypatch):
    double = Leaky()
    for name in METHODS:
        monkeypatch.setattr(Actions, name, handed_to(double, name))
    return double


@pytest.fixture
def server(tmp_path, leaky, monkeypatch):
    """A server with a secret allowed on ``A``, its actions `Leaky`, and every
    resource seeded with the value: a kept text file, a download, a site-data
    snapshot and a captured request."""
    secret = tmp_path / "secrets" / "a"
    secret.mkdir(parents=True)
    (secret / "password").write_text(VALUE)
    (secret / "_allowed_urls").write_text(f"{A}\n")
    made = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path / "data")},
        secrets={"dirs": str(tmp_path / "secrets")},
    ))
    made.actions.grid = leaky.grid
    monkeypatch.setattr(made.actions.grid, "session_timeout", lambda _s: None, raising=False)
    yield made
    made.workspaces.scrubber.close()


def seed(server):
    """The value everywhere a resource reads from, for the browser now open."""
    store = server.workspaces.store
    record = store.get(NAMED)

    def capturing(r):
        settings = {**(r.settings or {}), "capture": True}
        set_by = {**(r.set_by or {}), "capture": "caller"}
        return replace(r, settings=settings, set_by=set_by, site_data={
            "cookies": [{"name": "sid", "value": ALL, "domain": "a.test"}],
            "origins": {A: {"local": {"token": ALL}}},
            "session": {"origin": A, "items": {"k": ALL}},
            "saved_at": 1000.0,
        })

    store.update(NAMED, capturing)
    server.flows.write_file(NAMED, "notes.txt", ALL.encode(), files_module.FILES)
    # A plain image: pixels pass unscrubbed (ruling 16), so it holds no value.
    server.flows.write_file(
        NAMED, "shot.png", b"\x89PNG\r\n\x1a\n" + bytes(8), files_module.SCREENSHOTS
    )
    capture = server.capture
    capture.attached(NAMED, record.session_id, "chrome", "top-1", again=False)
    basic = base64.b64encode(f"user:{VALUE}".encode()).decode()
    for method, params in (
        nav_started(f"{A}/"),
        log(ALL, level="error"),
        req(
            "r1", f"{A}/api?q={ALL}", method="POST",
            headers=[("authorization", f"Basic {basic}"), ("x-echo", ALL)],
        ),
        done("r1", 200, headers=[("x-echo", ALL), ("content-type", "application/json")]),
    ):
        capture.ingest(NAMED, record.session_id, method, params)


# ---- what comes back -------------------------------------------------------


def _text_of(result, tool: str = "") -> str:
    """Everything a tool result says; a screenshot's pixels only for `screenshot`
    (ruling 12)."""
    parts = []
    for block in result.content:
        if block.type == "text":
            parts.append(block.text)
        elif not (block.type == "image" and tool == "screenshot"):
            parts.append(json.dumps(block.model_dump(mode="json")))
    if result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False))
    return "\n".join(parts)


def _contents(read) -> str:
    """A resource read's contents as text: a blob that is text, decoded."""
    parts = []
    for item in read:
        text = getattr(item, "text", None)
        if text is None:
            data = base64.b64decode(item.blob)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:  # binary: passes unscrubbed (ruling 16)
                continue
        parts.append(text)
    return "\n".join(parts)


def _http_text(response, route: str = "") -> str:
    body = response.json()
    if isinstance(body, dict) and route == "screenshot":
        body.pop("image", None)  # a screenshot's pixels (ruling 12)
    return json.dumps(body, ensure_ascii=False)


def _filled(template: str, ids: dict) -> str:
    """One URI a template answers, filled with something seeded where it can be."""
    known = {
        "workspace://site-data/{site}": "workspace://site-data/a.test",
        "workspace://files/{name}": "workspace://files/notes.txt",
        "workspace://files/screenshots/{name}": "workspace://files/screenshots/shot.png",
        "session://downloads/{name}": "session://downloads/dl.txt",
        "flow://flows/{name}": f"flow://flows/{FLOW}",
        "workspace://network/{id}": f"workspace://network/{ids.get('request', 'x')}",
    }
    if template in known:
        return known[template]
    return re.sub(r"\{[^}]+\}", "sample", re.sub(r"\{\?[^}]+\}", "", template))


def _named(monkeypatch, **flags):
    monkeypatch.setattr(
        clients_module, "request_values",
        lambda: http({"workspace": NAMED, **flags}),
    )


class Answers(list):
    """(where, text) for every answer, and which tools and routes gave one."""

    def __init__(self):
        super().__init__()
        self.tools: set[str] = set()
        self.routes: set[tuple[str, str]] = set()

    async def call(self, client, tool, args, where):
        result = await client.call_tool(tool, args, raise_on_error=False)
        self.tools.add(tool)
        self.append((where, _text_of(result, tool)))
        return result

    def http(self, client, method, template, url, where, route="", **kwargs):
        """``url``, the route at ``template``, asked with ``method``."""
        response = client.request(method, url, **kwargs)
        self.routes.add((method, template))
        self.append((where, _http_text(response, route)))
        return response


async def _uris(client) -> list[str]:
    resources = [str(r.uri) for r in await client.list_resources()]
    templates = [
        getattr(t, "uri_template", None) or t.uriTemplate
        for t in await client.list_resource_templates()
    ]
    return resources + templates


async def _listed_tools(server, monkeypatch) -> set[str]:
    """Every tool a client is shown, in either resources mode."""
    names = set()
    for flags in ({}, {"resources": "off"}):
        _named(monkeypatch, **flags)
        async with Client(server.mcp) as c:
            names |= {t.name for t in await c.list_tools()}
    _named(monkeypatch)
    return names


def _served_routes(server) -> set[tuple[str, str]]:
    """Every (method, path) the HTTP surface serves; HEAD is GET's."""
    return {
        (method, route.path)
        for route in server.mcp.http_app().routes
        if hasattr(route, "methods") and route.methods
        for method in route.methods
        if method != "HEAD"
    }


# Tools no answer is taken from. Empty: every tool a client is shown is called
# below. A new tool fails `test_every_tool_and_route_is_covered` until it is
# called here or named here with why.
TOOL_EXEMPT: dict[str, str] = {}

# Routes no answer is taken from, and why. Anything else - a new GET included -
# fails `test_every_tool_and_route_is_covered` until it is called or named here.
ROUTE_EXEMPT = {
    "/mcp": "the MCP transport: every tool and resource above went through it",
    "/": "the admin UI (ruling 17)",
    "/admin": "the admin UI's old address (ruling 17)",
    "/live.js": "the admin live view's script (ruling 17)",
    "/health": "ops: liveness, says nothing of a workspace",
    "/started": "ops: startup",
    "/ready": "ops: readiness",
    "/info": "ops: what the server is",
    "/openapi.yaml": "the published contract, written by the server",
    "/openapi.json": "the published contract, written by the server",
    "/schemas/flow": "flow://schema's twin: text the server wrote (ruling 16)",
    "/secrets": "secret://secrets' twin: names secrets, never holds one (ruling 16)",
    # Signed links: the bytes of one file, which an agent is handed as a `url`
    # in a listing, a resource or a screenshot's result. Binary contents pass
    # unscrubbed (ruling 16): images, recordings, downloads, and kept files,
    # which are kept downloads or screenshots. A value in a file's name is
    # masked in the listing, signed `url` included.
    "/files/{session_id}/{name}": "a download's bytes: binary passes (ruling 16)",
    "/kept/{workspace}/{name}": (
        "a kept file's bytes, a kept download or screenshot: binary passes "
        "(ruling 16)"
    ),
    "/screenshots/{workspace}/{name}": "a screenshot's pixels (rulings 12, 16)",
    "/recordings/{workspace}/{name}": "a video's bytes: binary passes (ruling 16)",
}
ADMIN = "/admin/"  # the admin API, by prefix (ruling 17)


def _exempt(path: str) -> bool:
    return path in ROUTE_EXEMPT or path.startswith(ADMIN)


def _recorded_progress(monkeypatch, answers):
    """Every progress line a run's watcher is handed, as an answer."""
    from kubed.selenium_flow.mcp import progress

    real = progress.Watch.step

    def step(self, entry, total):
        answers.append(("mcp run_flow progress", json.dumps(entry, ensure_ascii=False)))
        return real(self, entry, total)

    monkeypatch.setattr(progress.Watch, "step", step)


async def every_answer(server, leaky, monkeypatch) -> Answers:
    """(where, text) for every answer every surface gives."""
    answers = Answers()
    _named(monkeypatch)
    _recorded_progress(monkeypatch, answers)
    ids: dict = {}
    async with Client(server.mcp) as c:
        await answers.call(c, "open_session", ARGS["open_session"], "mcp open_session ok")
        seed(server)
        network = json.loads((await c.read_resource("workspace://network"))[0].text)
        ids["request"] = network["requests"][0]["id"]
        saved = await answers.call(
            c, "save_flow", {"name": FLOW, **FLOW_DOCUMENT}, "mcp save_flow ok"
        )
        assert not saved.is_error, _text_of(saved)
        kept = await answers.call(
            c, "keep_file", {"uri": f"session://downloads/{DOWNLOAD}"}, "mcp keep_file ok"
        )
        assert not kept.is_error, _text_of(kept)

        # Every capability, over MCP: success, then failure.
        for raises in (False, True):
            leaky.raises = raises
            said = "error" if raises else "ok"
            for row in CAPABILITIES:
                if row.name in ("open_session", "end_browser"):
                    continue
                await answers.call(c, row.name, ARGS[row.name], f"mcp {row.name} {said}")
            await answers.call(c, "write", BOUND, f"mcp write(secret) {said}")
            await answers.call(
                c, "run_flow", {"name": FLOW, "verbose": True}, f"mcp run_flow {said}"
            )
        leaky.raises = False

        # Every resource and template, as a client reads it and through `show`.
        uris = [
            _filled(uri, ids) for uri in await _uris(c) if not uri.startswith(EXEMPT)
        ]
        assert len(uris) > 15, uris
        for uri in uris:
            try:
                answers.append((f"resource {uri}", _contents(await c.read_resource(uri))))
            except Exception as exc:  # noqa: BLE001 - a refusal is an answer too
                answers.append((f"resource {uri} (error)", str(exc)))
            await answers.call(c, "show", {"uri": uri}, f"show {uri}")

    # The same, through the mirror tools a client that cannot read resources gets.
    _named(monkeypatch, resources="off")
    async with Client(server.mcp) as c:
        listed = await answers.call(c, mirror.LIST_TOOL, {}, "mcp list_resources")
        rows = json.loads(_text_of(listed).split("\n")[-1])["result"]
        mirrored = [
            _filled(row.get("uri") or row["uri_template"], ids) for row in rows
        ]
        assert set(mirrored) >= set(uris) - {u for u in uris if u.startswith(EXEMPT)}
        for uri in mirrored:
            if uri.startswith(EXEMPT):
                continue
            await answers.call(c, mirror.READ_TOOL, {"uri": uri}, f"read_resource {uri}")
    _named(monkeypatch)

    # Deleting, ending and opening over MCP, last there: ending ends the
    # browser the rest were called on.
    async with Client(server.mcp) as c:
        await answers.call(c, "delete_flow", {"name": FLOW}, "mcp delete_flow ok")
        await answers.call(c, "end_browser", {}, "mcp end_browser ok")
        leaky.raises = True
        await answers.call(
            c, "open_session", ARGS["open_session"], "mcp open_session error"
        )
        leaky.raises = False

    # Every route, over HTTP. With the server's lifespan running, as it does
    # deployed: `await_control` waits on it.
    q = f"?workspace={NAMED}"
    with TestClient(server.mcp.http_app(), headers=AUTH) as client:
        http_ = functools.partial(answers.http, client)
        http_("POST", "/browser", f"/browser{q}", "http open_session ok",
              json=ARGS["open_session"])
        http_("PUT", "/flows/{name}", f"/flows/{FLOW}{q}", "http save_flow ok",
              json=FLOW_DOCUMENT)
        http_("PUT", "/files/{folder}/{name}/kept",
              f"/files/downloads/{quote(DOWNLOAD)}/kept{q}", "http keep_file ok")
        for template, url in (
            ("/browser", "/browser"),
            ("/site-data", "/site-data"),
            ("/site-data/{site}", "/site-data/a.test"),
            ("/files", "/files"),
            ("/files/screenshots", "/files/screenshots"),
            ("/files/recordings", "/files/recordings"),
            ("/browser/downloads", "/browser/downloads"),
            ("/flows", "/flows"),
            ("/flows/{name}", f"/flows/{FLOW}"),
        ):
            http_("GET", template, f"{url}{q}", f"http GET {url}")
        for raises in (False, True):
            leaky.raises = raises
            said = "error" if raises else "ok"
            for row in CAPABILITIES:
                if row.name in ("open_session", "end_browser"):
                    continue
                body = dict(ARGS[row.name])
                template = url = f"/browser/{row.route}"
                if row.in_path:
                    template += "/{action}"
                    url += f"/{body.pop('action')}"
                http_(row.http_method, template, url + q, f"http {row.name} {said}",
                      route=row.name, json=body)
            http_("POST", "/browser/write", f"/browser/write{q}",
                  f"http write(secret) {said}", json=BOUND)
            http_("POST", "/flows/{name}/runs", f"/flows/{FLOW}/runs{q}",
                  f"http run_flow {said}", json={"verbose": True})
        leaky.raises = False
        http_("DELETE", "/flows/{name}", f"/flows/{FLOW}{q}", "http delete_flow ok")
        http_("DELETE", "/browser", f"/browser{q}", "http end_browser ok")
        leaky.raises = True
        http_("POST", "/browser", f"/browser{q}", "http open_session error",
              json=ARGS["open_session"])
        leaky.raises = False
    return answers


def leaks(text: str) -> list[str]:
    """The spellings in ``text`` outside a marker."""
    bare = redaction.MARKER.sub("", text)
    return [spelt for spelt in SPELLINGS if spelt in bare]


async def test_no_spelling_survives_any_surface(server, leaky, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    answers = await every_answer(server, leaky, monkeypatch)
    leaked = {where: found for where, text in answers if (found := leaks(text))}
    assert leaked == {}, "leaked:\n" + "\n".join(f"  {w}: {f}" for w, f in leaked.items())
    # `httpx` is the test's own client, saying the URL it asked for.
    logged = [
        r.getMessage() for r in caplog.records
        if not r.name.startswith("httpx") and leaks(r.getMessage())
    ]
    assert logged == [], "logged:\n" + "\n".join(logged)


async def test_every_tool_and_route_is_covered(server, leaky, monkeypatch):
    """Every tool a client is shown, and every route the HTTP surface serves,
    gave an answer above or is exempt with a reason: a new one fails here."""
    answers = await every_answer(server, leaky, monkeypatch)
    tools = await _listed_tools(server, monkeypatch)
    assert tools - answers.tools - set(TOOL_EXEMPT) == set()
    assert set(TOOL_EXEMPT) <= tools, "an exemption for a tool that is gone"
    served = _served_routes(server)
    uncovered = {r for r in served - answers.routes if not _exempt(r[1])}
    assert uncovered == set(), f"no answer taken from {sorted(uncovered)}"
    assert set(ROUTE_EXEMPT) <= {p for _, p in served}, (
        "an exemption for a route that is gone"
    )


async def test_the_value_reached_every_surface_it_was_checked_on(server, leaky, monkeypatch):
    """Not vacuous: each capability's answer, and each seeded resource, carried
    the value - shown as its marker - so the check above looked at something."""
    answers = dict(await every_answer(server, leaky, monkeypatch))
    for row in CAPABILITIES:
        if row.name in ("end_browser",):
            continue
        for where in (f"mcp {row.name} ok", f"http {row.name} ok"):
            assert MARKED in answers[where], where
    for where in (
        "mcp run_flow ok", "mcp run_flow error", "http run_flow ok",
        "mcp run_flow progress", "mcp save_flow ok", "http save_flow ok",
        "mcp keep_file ok", "http keep_file ok",
        "mcp write(secret) ok", "resource workspace://site-data/a.test",
        "resource workspace://files/notes.txt", "resource session://downloads/dl.txt",
        "resource session://downloads", "read_resource workspace://files/notes.txt",
        "show workspace://site-data/a.test",
        "http GET /site-data/a.test", "http GET /flows/" + FLOW,
        "http GET /browser/downloads", "http GET /files",
    ):
        assert MARKED in answers[where], where
    request = next(w for w in answers if w.startswith("resource workspace://network/"))
    assert MARKED in answers[request], request


def test_capture_hears_the_origin_while_secrets_are_on(server):
    """Source 2 (ruling 8): the server hands capture its ask, so a captured
    page's events wait for the secrets in scope there."""
    assert server.capture.heard == server.workspaces.capture_heard


# ---- the middleware on its own ------------------------------------------------


def _scrubbed_server(monkeypatch, scopes, *, name=NAMED):
    """`Scrubbed` over a `Workspaces` whose catalogue holds ``scopes``, and the
    caller naming ``name`` (or nothing)."""
    from kubed.selenium_flow.mcp.scrubbed import Scrubbed
    from kubed.selenium_flow.workspace.store import MemoryStore
    from kubed.selenium_flow.workspace.workspaces import Workspaces

    from .conftest import RecordingActions, calling_as
    from .test_secret_scope import FakeCatalogue

    catalogue = FakeCatalogue(scopes) if scopes is not None else None
    workspaces = Workspaces(RecordingActions(), MemoryStore(), catalogue=catalogue)
    calling_as(monkeypatch, name)
    return Scrubbed(workspaces), workspaces, catalogue


async def _through(middleware, uri, contents):
    """``contents`` read at ``uri`` through ``middleware``."""
    from fastmcp.resources import ResourceResult
    from fastmcp.server.middleware import MiddlewareContext
    from mcp.types import ReadResourceRequestParams

    served = ResourceResult(contents)

    async def call_next(_context):
        return served

    context = MiddlewareContext(message=ReadResourceRequestParams(uri=uri))
    return served, await middleware.on_read_resource(context, call_next)


SCOPES = {A: {("a", "password"): VALUE}}


async def test_a_read_naming_no_workspace_is_scrubbed_with_every_leashed_secret(monkeypatch):
    from fastmcp.resources import ResourceContent

    middleware, workspaces, catalogue = _scrubbed_server(monkeypatch, SCOPES, name=None)
    try:
        _, read = await _through(
            middleware, "flow://flows", [ResourceContent(f"go to {VALUE}")]
        )
        assert read.contents[0].content == f"go to {MARKED}"
        assert catalogue.calls["leashed"] >= 1
    finally:
        workspaces.scrubber.close()


async def test_a_value_json_escapes_again_is_found_value_by_value(monkeypatch):
    """Reversed, a value with a quote in it is escaped once more by the
    serialiser, where the text scan cannot see it."""
    from fastmcp.resources import ResourceContent

    quoted = 'ab"cd-99é'
    middleware, workspaces, _ = _scrubbed_server(monkeypatch, {A: {("a", "password"): quoted}})
    try:
        document = json.dumps({"rows": [{"text": quoted[::-1]}]})
        assert quoted[::-1] not in document  # escaped: `\"` and `é`
        _, read = await _through(
            middleware, "workspace://network", [ResourceContent(document, "application/json")]
        )
        assert json.loads(read.contents[0].content) == {"rows": [{"text": MARKED}]}
    finally:
        workspaces.scrubber.close()


async def test_text_served_as_bytes_is_scrubbed_and_an_image_passes(monkeypatch):
    from fastmcp.resources import ResourceContent

    middleware, workspaces, _ = _scrubbed_server(monkeypatch, SCOPES)
    image = b"\x89PNG\r\n\x1a\n" + VALUE.encode()
    try:
        _, read = await _through(middleware, "workspace://files/notes.txt", [
            ResourceContent(f"a {VALUE}".encode(), "application/octet-stream"),
        ])
        assert read.contents[0].content == f"a {MARKED}".encode()
        _, read = await _through(middleware, "workspace://files/screenshots/x.png", [
            ResourceContent(image, "image/png"),
        ])
        assert read.contents[0].content == image  # ruling 12: pixels pass
    finally:
        workspaces.scrubber.close()


@pytest.mark.parametrize("uri", ["skill://selenium-flow/SKILL.md", "flow://schema",
                                 "secret://secrets", "ui://selenium-flow/component"])
async def test_what_the_server_wrote_is_not_scanned(monkeypatch, uri):  # ruling 16
    from fastmcp.resources import ResourceContent

    middleware, workspaces, catalogue = _scrubbed_server(monkeypatch, SCOPES)
    try:
        served, read = await _through(middleware, uri, [ResourceContent(VALUE)])
        assert read is served and catalogue.calls == {"forms_for": [], "leashed": 0}
    finally:
        workspaces.scrubber.close()


async def test_with_secrets_off_a_read_is_the_very_result(monkeypatch):  # Review Focus 1
    from fastmcp.resources import ResourceContent

    middleware, workspaces, _ = _scrubbed_server(monkeypatch, None)
    try:
        served, read = await _through(
            middleware, "workspace://files/notes.txt", [ResourceContent(VALUE)]
        )
        assert read is served
    finally:
        workspaces.scrubber.close()


async def test_a_read_whose_scrub_fails_answers_the_fixed_sentence(monkeypatch, caplog):
    from fastmcp.exceptions import ResourceError
    from fastmcp.resources import ResourceContent

    from kubed.selenium_flow import faults

    middleware, workspaces, catalogue = _scrubbed_server(monkeypatch, SCOPES)
    catalogue.fail_leashed = True
    try:
        with pytest.raises(ResourceError) as raised:
            await _through(
                middleware, "workspace://files/notes.txt", [ResourceContent(VALUE)]
            )
        assert str(raised.value) == faults.SCRUB_FAILED
        assert raised.value.__cause__ is None and raised.value.__context__ is None
        assert VALUE not in caplog.text
    finally:
        workspaces.scrubber.close()


async def test_a_read_whose_scrub_fails_reaches_the_client_as_the_sentence(server, monkeypatch):
    """Through the whole server: `Explained` passes the sentence on, and the
    mirror tool says it too."""
    from kubed.selenium_flow import faults

    _named(monkeypatch)
    server.flows.write_file(NAMED, "notes.txt", ALL.encode(), files_module.FILES)

    def broken(*_args, **_kwargs):
        raise faults.ScrubFailed()

    monkeypatch.setattr(server.workspaces.scrubber, "resource", broken)
    async with Client(server.mcp) as c:
        with pytest.raises(Exception) as raised:
            await c.read_resource("workspace://files/notes.txt")
        assert faults.SCRUB_FAILED in str(raised.value)
        mirrored = await c.call_tool(
            mirror.READ_TOOL, {"uri": "workspace://files/notes.txt"}, raise_on_error=False
        )
    assert mirrored.is_error and faults.SCRUB_FAILED in _text_of(mirrored)
    assert not leaks(_text_of(mirrored))


async def test_text_bytes_that_are_not_utf8_are_scrubbed_all_the_same(monkeypatch):
    """A `.txt` in Latin-1 is still text a reader decodes somehow: its bytes
    that are not UTF-8 are carried through, and the value is found."""
    from fastmcp.resources import ResourceContent

    middleware, workspaces, _ = _scrubbed_server(monkeypatch, SCOPES)
    data = "caf\xe9 ".encode("latin-1") + VALUE.encode()
    try:
        _, read = await _through(
            middleware, "workspace://files/notes.txt", [ResourceContent(data, "text/plain")]
        )
        assert read.contents[0].content == "caf\xe9 ".encode("latin-1") + MARKED.encode()
    finally:
        workspaces.scrubber.close()


async def test_a_read_that_fails_is_scrubbed_too(monkeypatch):
    """The choke point is unconditional: a refusal is scrubbed as an answer is,
    its type kept, so `Explained` still says what it means."""
    from fastmcp.exceptions import ResourceError
    from fastmcp.server.middleware import MiddlewareContext
    from mcp.types import ReadResourceRequestParams

    middleware, workspaces, _ = _scrubbed_server(monkeypatch, SCOPES)

    async def call_next(_context):
        try:
            raise ValueError(f"no saved data for {VALUE!r}")
        except ValueError as exc:
            raise ResourceError(f"Error reading resource: {exc}") from exc

    context = MiddlewareContext(message=ReadResourceRequestParams(uri="workspace://x"))
    try:
        with pytest.raises(ResourceError) as raised:
            await middleware.on_read_resource(context, call_next)
        assert VALUE not in str(raised.value) and MARKED in str(raised.value)
        assert isinstance(raised.value.__cause__, ValueError)
        assert VALUE not in str(raised.value.__cause__)
    finally:
        workspaces.scrubber.close()
