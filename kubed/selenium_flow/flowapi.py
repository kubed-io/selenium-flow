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
  disturbing it, and nothing an agent does can publish to the shared library —
  including a caller that has no name of its own, which used to be the one
  exception and is now refused like any other. `writable` is where that is
  enforced, and the admin UI deliberately does not come through it.
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
from .tools import SecretRef

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


# Which session owns a caller's documents, and its kept files with them. It
# lives in `flows.py` because the rule is about the session directory rather
# than about flows — `files.py` needs the identical answer, and two functions
# deciding who owns a directory is how one of them starts disagreeing.
session_of = flows.session_of


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


def writable(session: str) -> str:
    """``session`` if an agent may write to it, else refuse (§F1.2).

    **`global` is read-and-run only on this surface**, and the argument is
    concurrency rather than tidiness: the shared library is *live*. Every
    session lists and runs what is in it, so a flow rewritten or deleted by one
    agent changes or vanishes underneath another that is part-way through using
    it — a race with no error message and nothing recording who did it.

    This used to have an exception that swallowed the rule: a caller with no
    session name *is* `global`, so unnamed callers could write to the shared
    library while named ones could not. That was written down and apologised
    for; now it is simply closed.

    **The operator path must not come through here.** Moving a flow into
    `global` from the admin UI is a person doing it deliberately, on a surface
    that can show what a change affects. That writes to the store directly.
    """
    if session == flows.GLOBAL_SESSION:
        raise ValueError(
            "the shared 'global' library is read-only: every session can list "
            "and run what is in it, so a flow you changed or deleted would "
            "change or vanish under another session mid-run. Name your session "
            "and save into your own library — ?session=<name> on the MCP URL or "
            "the X-Session-Key header, or \"session\" in the body over HTTP. An "
            "operator moves a flow into global from the admin UI."
        )
    return session


def save_one(store, session: str, name: str, document: dict, schemas: dict) -> dict:
    """Create or replace one of *this session's* flows.

    Never the shared library, even when a flow of that name was read from it:
    a save is a copy into your own, which is the copy-on-write half of §F1.2.
    Moving a flow into `global` is an operator action in the admin UI.
    """
    session = writable(session)
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
    """Remove one of *this session's* flows. Never the shared library."""
    session = writable(session)
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
    after_step=None,
    secrets_catalogue=None,
) -> dict:
    """Run one flow against an already-resolved browser."""
    document = read_one(store, session, name)
    report = flowrun.run(
        actions,
        document,
        session_id,
        params=params,
        verbose=verbose,
        after_step=after_step,
        catalogue=secrets_catalogue,
    )
    return {"session": document["session"], **report}


def register(
    mcp, store, sessions, actions, token: str | None, prefix: str = "/flows",
    secrets_catalogue=None,
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
            "order, where params is exactly the arguments of that call. A step "
            "may also carry id, note, onError ('abort' or 'continue') and "
            "return (include its full result in the run report). To bound one "
            "step, set wait_timeout in its params.\n\n"
            "To take a value from somewhere instead of writing it in, put "
            "value_from in the params beside the others: "
            "{'tool': 'write', 'params': {'css': '#p', 'value_from': "
            "{'secret': {'name': 'x', 'key': 'password'}}}} types a secret you "
            "never see, and {'value_from': {'param': 'email'}} takes the value "
            "from this flow's own parameters. Exactly one source, and you may "
            "not also give the value literally. There is no {{templating}}.\n\n"
            "open_session and end_browser are not steps: a flow runs in the "
            "browser you already have, which is what lets one flow run on "
            "Chrome and then on Firefox unchanged.\n\n"
            "The whole document is checked now, against the real tools, and a "
            "refusal lists every problem at once.\n\n"
            "Saves into your own library, which means you need a session name: "
            "add ?session=<name> to the MCP URL, or send X-Session-Key. Without "
            "one you can list and run the shared 'global' flows but not save, "
            "because that library is live for every session at once."
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

        def remember(tool, result):
            # `resize` changes something the session RECORD stores, not just the
            # page it is on. The per-call path in tools.py has always done this;
            # a flow that skipped it would resize the live browser and then come
            # back the old size the next time the Grid reaped it — the silent
            # shape change `sessions.reshape` exists to prevent.
            if tool == "resize" and isinstance(result, dict):
                sessions.reshape(key, result, resolved)

        report = run_one(
            store,
            actions,
            session_of(sessions),
            name,
            params=params,
            verbose=verbose,
            session_id=resolved,
            after_step=remember,
            secrets_catalogue=secrets_catalogue,
        )
        # One touch for the whole run, not one per step: the point of running
        # server-side is that the bookkeeping happens once.
        #
        # But not a page the redaction had to touch. A submitting bound write
        # lands on `?q=<what was typed>`, which comes back scrubbed — storing
        # that would persist a URL which does not exist, and `sessions.resolve`
        # would reopen the browser there after the Grid reaped it. Keeping the
        # last page we genuinely know is the lesser wrong, and it is the same
        # rule the direct write path follows.
        #
        # The touch happens regardless: it slides the TTL, and a run is the
        # clearest evidence there is that a session is in use. Only the page is
        # withheld.
        sessions.touch(
            key, None if report.get("url_redacted") else report.get("url"), resolved
        )
        return report

    @mcp.tool(
        name=DELETE_TOOL,
        description=(
            "Delete one of this session's saved flows. Deleting one that is not "
            "there is not an error.\n\n"
            "It deletes from your own library only. A flow in the shared "
            "'global' library is not yours to remove — every session runs those, "
            "so one vanishing mid-run would break somebody else's work — and "
            "trying is refused. If you have no session name you have no library "
            "of your own, and there is nothing here you may delete."
        ),
        annotations=hints("Delete a flow", destructive=True, idempotent=True),
    )
    def delete_flow(name: str) -> dict:
        return delete_one(store, session_of(sessions), name)

    _routes(
        mcp, store, sessions, actions, schemas, token, prefix, secrets_catalogue
    )
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
                        # No `valueFrom` here: it is a parameter, so it lives
                        # in `params` and the per-action schemas below describe
                        # it. A step key would be a second place to say it, and
                        # a caller following this resource would have built a
                        # document save_flow rejects.
                        "params": {"type": "object"},
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
        # `x-step-params` comes from the direct tool schemas, where `value_from`
        # can only name a secret — a flow's own parameters mean nothing to a
        # caller outside a flow. Inside one they do, so the extra source is
        # described here rather than left to be discovered by a rejection.
        "x-value-from": {
            "description": (
                "In a flow step, params.value_from may also take its value from "
                "one of the flow's own parameters. The tool schemas describe "
                "only the secret source, which is all a direct call can use."
            ),
            # Every object here is CLOSED, as the validator and the MCP model
            # are. JSON Schema's default is to allow any extra property, so a
            # consumer building from this schema could produce `{secret, config}`
            # or a misspelt reference that it accepts and `save_flow` refuses.
            #
            # The secret reference is the MCP tool's own model rather than a
            # copy of it — a hand-written one is how this repo keeps finding its
            # bugs, and it had already drifted by not being closed.
            "oneOf": [
                {
                    "type": "object",
                    "required": ["secret"],
                    "additionalProperties": False,
                    "properties": {"secret": SecretRef.model_json_schema()},
                },
                {
                    "type": "object",
                    "required": ["param"],
                    "additionalProperties": False,
                    "properties": {
                        "param": {
                            "type": "string",
                            "description": "A name from this flow's parameters.",
                        }
                    },
                },
            ],
        },
    }


def _routes(
    mcp, store, sessions, actions, schemas: Schemas, token, prefix,
    secrets_catalogue=None,
) -> None:
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
                        secrets_catalogue=secrets_catalogue,
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
            if status == 500:
                # See files.py: a traceback only for the status we cannot
                # explain, since a Grid refusal's text carries its URL.
                log.exception("flows/%s failed", what)
            elif status > 500:
                log.warning("flows/%s unavailable (%s): %s", what, status, text)
            else:
                log.info("flows/%s refused (%s): %s", what, status, text)
            return JSONResponse({"error": text}, status_code=status)

    for path in FLOW_ENDPOINTS:
        _bind(mcp, prefix, path, handle)


def _bind(mcp, prefix, path, handle) -> None:
    @mcp.custom_route(f"{prefix}/{path}", methods=["POST"], name=f"flows_{path}")
    async def route(request: Request) -> JSONResponse:
        return await handle(request, path)
