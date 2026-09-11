"""Saved flows, offered every way a client might reach them.

The same split `files.py` makes, for the same reason: a listing is *state to
read*, so it is a resource, and it is mirrored as a tool for the clients — n8n
among them — with no notion of resources. Writes are only ever tools, because a
resource cannot write.

| Verb | Surface | Visible to a client that reads resources? |
|---|---|---|
| list | `flow://flows` + `list_flows` | resource only |
| get one | `flow://flows/{name}` + `get_flow` | resource only |
| the step schema | `flow://schema` + `flow_schema` | resource only |
| save | `save_flow` | yes |
| delete | `delete_flow` | yes |

So a client with resources sees **two** new tools and reads the library for
free; one without sees five and loses nothing. That is saga §F1.5, and it is the
answer to "how do we avoid five CRUD tools" — not by overloading one verb, but
by putting reads where reads belong.

**`/flows` is a layer above `/browser`, not more of it.** The browser endpoints
are single actions; these are about documents that *contain* them. They get
their own route table, so the one-to-one promise `test_surfaces.py` guards over
browser actions is untouched (§F1.7, question #7).

Two rules from the chapter show up here as code:

- **Reads merge your session with `global`, writes never do** (§F1.2). Your own
  flow wins a name collision, so a session can shadow a shared one without
  disturbing it, and nothing an agent does can publish to the shared library.
- **A flow is validated when it is saved** (§F1.6), against the real tool
  schemas, so a broken one is refused while its author is still looking at it.
"""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from . import auth, errors, flowdoc, flowrun, flows
from .browser import as_bool
from .hints import hints, reads
from .routes import ENDPOINTS

log = logging.getLogger(__name__)

LIST_URI = "flow://flows"
FLOW_URI = "flow://flows/{name}"
SCHEMA_URI = "flow://schema"

LIST_TOOL = "list_flows"
GET_TOOL = "get_flow"
SCHEMA_TOOL = "flow_schema"
RUN_TOOL = "run_flow"
SAVE_TOOL = "save_flow"
DELETE_TOOL = "delete_flow"

# Path -> the function behind it. Separate from routes.ENDPOINTS on purpose:
# these are not browser actions and must not be counted as though they were.
FLOW_ENDPOINTS = ("list", "get", "save", "delete", "schema", "run")

OFF = (
    "saved flows are not enabled on this server: it was started with no "
    "FLOW_DATA_DIR, so there is nowhere to keep them"
)

LIST_DESCRIPTION = (
    "The flows this session can run: saved sequences of tool calls that run "
    "server-side in one call.\n\n"
    "Each entry has a name, a description, the parameters it takes and how many "
    "steps it has — never the steps themselves, which get_flow returns.\n\n"
    "Flows named by this session come first; anything in the shared 'global' "
    "library is also listed, and a flow of your own with the same name wins."
)


def _require(store):
    if store is None:
        raise ValueError(OFF)
    return store


def session_of(sessions, explicit: str | None = None) -> str:
    """Whose library this call is about.

    An explicit name is how the HTTP surface says it, because that surface is
    always explicit — the same contract `/browser/*` already has. Over MCP it
    comes from the caller's key, and anything unnamed is `global` (§F1.2).
    """
    if explicit:
        return flows.valid_name(explicit, "session name")
    return flows.session_for(sessions.key())


def catalogue(store, session: str) -> dict:
    """Every flow this session can run: its own, plus the shared library.

    A name defined in both resolves to this session's, and the entry says which
    library it came from — "why am I running the wrong login" is otherwise
    unanswerable.
    """
    store = _require(store)
    entries = {}
    if session != flows.GLOBAL_SESSION:
        for summary in store.summaries(flows.GLOBAL_SESSION):
            entries[summary["name"]] = {**summary, "shared": True}
    for summary in store.summaries(session):
        entries[summary["name"]] = {**summary, "shared": False}
    return {
        "session": session,
        "count": len(entries),
        "flows": [entries[name] for name in sorted(entries)],
    }


def read_one(store, session: str, name: str) -> dict:
    """One flow: this session's if it has one, else the shared library's."""
    store = _require(store)
    flow = store.get(session, name)
    shared = False
    if flow is None and session != flows.GLOBAL_SESSION:
        flow = store.get(flows.GLOBAL_SESSION, name)
        shared = flow is not None
    if flow is None:
        raise ValueError(
            f"no flow called {name!r} in {session} or the shared library. "
            "list_flows shows what there is."
        )
    return {
        **flow,
        "session": flows.GLOBAL_SESSION if shared else session,
        "shared": shared,
    }


def save_one(store, session: str, name: str, document: dict, schemas: dict) -> dict:
    """Create or replace one of *this session's* flows.

    Never the shared library, even when a flow of that name was read from it:
    a save is a copy into your own, which is the copy-on-write half of §F1.2.
    Promotion to `global` is an admin action, deliberately not a tool.
    """
    store = _require(store)
    document = dict(document or {})
    document.pop("session", None)
    document.pop("shared", None)
    flowdoc.validate(document, schemas)
    stored = store.save(session, name, document)
    steps = len(stored.get("steps") or [])
    log.info("flow %s/%s saved (%s steps)", session, name, steps)
    return {"saved": True, "session": session, **stored}


def delete_one(store, session: str, name: str) -> dict:
    store = _require(store)
    removed = store.delete(session, name)
    return {"deleted": removed, "session": session, "name": name}


class Schemas:
    """The step schemas, built once from the registered tools.

    Built lazily because `get_tool` is async and registration is not, and from
    `ENDPOINTS` rather than from a listing: a listing is rewritten per request
    by `resources.ShapeSessionId` and filtered by `HideMirrorTools`, so what it
    contains depends on who is asking. What a flow step may say must not.
    """

    def __init__(self, mcp):
        self.mcp = mcp
        self._cache: dict | None = None

    async def get(self) -> dict:
        if self._cache is None:
            tools = {}
            for name in sorted(set(ENDPOINTS.values())):
                tool = await self.mcp.get_tool(name)
                tools[name] = tool.parameters or {}
            self._cache = flowdoc.step_schemas(tools)
        return self._cache


def run_one(
    store,
    actions,
    session: str,
    name: str,
    params: dict | None = None,
    verbose: bool = False,
    session_id: str = "",
) -> dict:
    """Run one flow against an already-resolved browser."""
    document = read_one(store, session, name)
    report = flowrun.run(
        actions, document, session_id, params=params, verbose=verbose
    )
    return {"session": document["session"], **report}


def register(
    mcp, store, sessions, actions, token: str | None, prefix: str = "/flows"
) -> set[str]:
    """Register the flow resources, tools and endpoints. Returns mirror names."""
    schemas = Schemas(mcp)

    # ---- resources ---------------------------------------------------------

    @mcp.resource(LIST_URI, description=LIST_DESCRIPTION, mime_type="application/json")
    def flows_resource() -> dict:
        return catalogue(store, session_of(sessions))

    @mcp.resource(
        FLOW_URI,
        description=(
            "One saved flow, with its steps. The name comes from the "
            f"{LIST_URI} listing."
        ),
        mime_type="application/json",
    )
    def flow_resource(name: str) -> dict:
        return read_one(store, session_of(sessions), name)

    @mcp.resource(
        SCHEMA_URI,
        description=(
            "The shape of a flow document: every tool that may be a step and "
            "the parameters it takes. Read this before writing a flow."
        ),
        mime_type="application/json",
    )
    async def schema_resource() -> dict:
        return await _document_schema(schemas)

    # ---- tools -------------------------------------------------------------

    @mcp.tool(
        name=LIST_TOOL,
        description=LIST_DESCRIPTION,
        annotations=reads("Flows this session can run"),
    )
    def list_flows() -> dict:
        return catalogue(store, session_of(sessions))

    @mcp.tool(
        name=GET_TOOL,
        description=(
            "One saved flow, with its steps — what it does, what it takes, and "
            "what it would run. Use list_flows to see what there is."
        ),
        annotations=reads("Read one saved flow"),
    )
    def get_flow(name: str) -> dict:
        return read_one(store, session_of(sessions), name)

    @mcp.tool(
        name=SCHEMA_TOOL,
        description=(
            "The shape of a flow document: every tool that may be a step and "
            "the parameters each takes.\n\nRead this before writing a flow — it "
            "is derived from the live tools, so it cannot describe a step that "
            "would not run."
        ),
        annotations=reads("The shape of a flow document", open_world=False),
    )
    async def flow_schema() -> dict:
        return await _document_schema(schemas)

    @mcp.tool(
        name=SAVE_TOOL,
        description=(
            "Save a flow under a name, creating it or replacing it.\n\n"
            "steps is a list of {tool, params} objects — one tool call each, in "
            "order. A step may also carry id, note, onError ('abort' or "
            "'continue'), return (include its full result in the run report), "
            "and valueFrom. To bound one step, set wait_timeout in its params — "
            "the actions that can wait all take it.\n\n"
            "valueFrom maps a parameter name to a source instead of a literal: "
            "{'text': {'param': 'email'}} takes it from this flow's parameters, "
            "and {'text': {'secret': {'name': 'x', 'key': 'password'}}} takes it "
            "from a secret you never see. There is no {{templating}}.\n\n"
            "open_session and end_browser are not steps: a flow runs in the "
            "browser you already have, which is what lets one flow run on "
            "Chrome and then on Firefox unchanged.\n\n"
            "The whole document is checked now, against the real tools, and a "
            "refusal lists every problem at once."
        ),
        annotations=hints("Save a flow", idempotent=True),
    )
    async def save_flow(
        name: str,
        steps: list,
        description: str = "",
        parameters: dict | None = None,
    ) -> dict:
        document = {"description": description, "steps": steps}
        if parameters:
            document["parameters"] = parameters
        return save_one(
            store, session_of(sessions), name, document, await schemas.get()
        )

    @mcp.tool(
        name=RUN_TOOL,
        description=(
            "Run a saved flow: every step, in order, server-side, in one call.\n\n"
            "This is the point of flows. A twelve-step form becomes one call and "
            "one decision instead of twelve of each, and it replays a sequence "
            "somebody already got right rather than re-deriving it.\n\n"
            "It runs in the browser you already have — call open_session first. "
            "That is also how you run the same flow on a different browser: open "
            "Firefox and run it again, unchanged.\n\n"
            "params supplies the values the flow declares; list_flows shows what "
            "each one takes. Returns a line per step plus the final page; pass "
            "verbose for every step's full result, or mark a step with "
            "return: true when only that one matters.\n\n"
            "Stops at the first failing step unless that step says "
            "onError: continue, and reports which step stopped it and what page "
            "the browser was on."
        ),
        annotations=hints("Run a saved flow", destructive=True),
    )
    def run_flow(
        name: str, params: dict | None = None, verbose: bool = False,
        session_id: str | None = None,
    ) -> dict:
        key = sessions.key()
        resolved = sessions.resolve(key, session_id)
        report = run_one(
            store,
            actions,
            session_of(sessions),
            name,
            params=params,
            verbose=verbose,
            session_id=resolved,
        )
        # One touch for the whole run, not one per step: the point of running
        # server-side is that the bookkeeping happens once.
        sessions.touch(key, report.get("url"), resolved)
        return report

    @mcp.tool(
        name=DELETE_TOOL,
        description=(
            "Delete one of this session's saved flows. Deleting one that is not "
            "there is not an error. A flow in the shared library is not yours to "
            "delete and is untouched."
        ),
        annotations=hints("Delete a flow", destructive=True, idempotent=True),
    )
    def delete_flow(name: str) -> dict:
        return delete_one(store, session_of(sessions), name)

    _routes(mcp, store, sessions, actions, schemas, token, prefix)
    return {LIST_TOOL, GET_TOOL, SCHEMA_TOOL}


async def _document_schema(schemas: Schemas) -> dict:
    """The flow document's shape, derived from the live tools.

    Published so a model writing a flow is given the shape rather than inferring
    it. It cannot describe a step that would not run, because it is built from
    the same schemas the validator checks against.
    """
    steps = await schemas.get()
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {
                "type": "object",
                "description": "JSON Schema for the values run_flow accepts.",
            },
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["tool"],
                    "properties": {
                        "tool": {"type": "string", "enum": sorted(steps)},
                        "params": {"type": "object"},
                        "valueFrom": {"type": "object"},
                        "id": {"type": "string"},
                        "note": {"type": "string"},
                        "onError": {"type": "string", "enum": list(flowdoc.ON_ERROR)},
                        "return": {"type": "boolean"},
                    },
                },
            },
        },
        "required": ["name", "steps"],
        "x-step-params": steps,
    }


def _routes(mcp, store, sessions, actions, schemas: Schemas, token, prefix) -> None:
    """The same five operations as plain JSON, for callers that are not MCP."""

    async def handle(request: Request, what: str) -> JSONResponse:
        if not auth.authorized(request, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - an empty body is fine for list
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "body must be a JSON object"}, status_code=400
            )
        try:
            session = session_of(sessions, body.get("session"))
            if what == "list":
                return JSONResponse(catalogue(store, session))
            if what == "schema":
                return JSONResponse(await _document_schema(schemas))
            name = body.get("name")
            if not name:
                raise ValueError("name is required")
            if what == "get":
                return JSONResponse(read_one(store, session, name))
            if what == "delete":
                return JSONResponse(delete_one(store, session, name))
            if what == "run":
                session_id = body.get("session_id")
                if not session_id:
                    raise ValueError(
                        "session_id is required: this surface is always "
                        "explicit, so open a browser with /browser/open and "
                        "pass the id it returns"
                    )
                return JSONResponse(
                    run_one(
                        store,
                        actions,
                        session,
                        name,
                        params=body.get("params"),
                        # as_bool, not bool: over HTTP "false" arrives as a
                        # string, and bool("false") is True — which would
                        # turn on full per-step results and return every
                        # extract in the flow.
                        verbose=as_bool(body.get("verbose"), False),
                        session_id=session_id,
                    )
                )
            document = {
                key: body[key]
                for key in ("description", "parameters", "steps")
                if key in body
            }
            return JSONResponse(
                save_one(store, session, name, document, await schemas.get())
            )
        except Exception as exc:  # errors.py decides what it means
            status = errors.status_for(exc)
            text = errors.message(exc)
            if status >= 500:
                log.exception("flows/%s failed", what)
            else:
                log.info("flows/%s refused (%s): %s", what, status, text)
            return JSONResponse({"error": text}, status_code=status)

    for path in FLOW_ENDPOINTS:
        _bind(mcp, prefix, path, handle)


def _bind(mcp, prefix, path, handle) -> None:
    @mcp.custom_route(f"{prefix}/{path}", methods=["POST"], name=f"flows_{path}")
    async def route(request: Request) -> JSONResponse:
        return await handle(request, path)
