"""Saved flows, offered every way a client might reach them.

A listing is *state to read*, so it is a resource; writes are only ever tools,
because a resource cannot write.

| Verb | Surface |
|---|---|
| list | `flow://flows` |
| get one | `flow://flows/{name}` |
| the step schema | `flow://schema` |
| save | `save_flow` |
| run | `run_flow` |
| delete | `delete_flow` |

A client that cannot read resources reads the same URIs with `read_resource`
(§F3.6). That is the answer to "how do we avoid five CRUD tools" (§F1.5) — not
by overloading one verb, but by putting reads where reads belong.

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

import anyio
import yaml
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core.coerce import as_bool
from ..http import answer as answer_module
from ..mcp import clients, guidance, progress
from ..mcp.annotations import hints
from ..mcp.tools import SecretRef
from ..names import GLOBAL_SESSION, valid_name
from ..routes import ENDPOINTS, LIBRARY_ARG, method_for
from . import document as flowdoc
from . import engine, template
from . import library as flowlib
from . import run as flowrun

log = logging.getLogger(__name__)

# The only tools a step may dispatch to. ENDPOINTS is the canonical list of
# browser actions and is already held to the tool surface by test_surfaces.py;
# the lifecycle calls are not steps (`flowdoc.NOT_STEPS`).
RUNNABLE = frozenset(ENDPOINTS.values()) - flowdoc.NOT_STEPS.keys()

# A run is handed what it must not import: the route table's tools and the
# skill's URIs. This module knows both, so it is where the two are joined.
flowrun.wire(
    engine.Toolbox(runnable=RUNNABLE, method_for=method_for, library_arg=LIBRARY_ARG),
    guidance.pointer,
)

LIST_URI = "flow://flows"
FLOW_URI = "flow://flows/{name}"
SCHEMA_URI = "flow://schema"

RUN_TOOL = "run_flow"
SAVE_TOOL = "save_flow"
DELETE_TOOL = "delete_flow"

# Not under /flows: `schema` there would be indistinguishable from a flow of
# that name.
SCHEMA_PATH = "/schemas/flow"

# The REST shape of each one: method, and the path under the /flows prefix. The
# route table and the published spec read the same rows, so a path can only be
# described the way it is actually served (§F2.13).
FLOW_ROUTES = {
    "list": ("get", ""),
    "get": ("get", "/{name}"),
    "save": ("put", "/{name}"),
    "delete": ("delete", "/{name}"),
    "run": ("post", "/{name}/runs"),
    # Its own tree beside /flows, and mounted like every other tree.
    "schema": ("get", SCHEMA_PATH),
}

# The endpoints by name, read off the route table rather than listed beside it.
# Separate from routes.ENDPOINTS on purpose: these are not browser actions and
# must not be counted as though they were. Kept as names rather than paths
# because the spec and the wiki describe capabilities, and the path each one
# lives at is the route table's business (§F2.13).
FLOW_ENDPOINTS = tuple(FLOW_ROUTES)

OFF = (
    "saved flows are not enabled on this server: it was started with no "
    "FLOW_DATA_DIR, so there is nowhere to keep them"
)

LIST_DESCRIPTION = (
    "The flows this session can run: saved sequences of tool calls that run "
    "server-side in one call.\n\n"
    "Each entry has a name, a description, the parameters it takes and how many "
    "steps it has — never the steps themselves, which flow://flows/{name} returns.\n\n"
    "Flows named by this session come first; anything in the shared 'global' "
    "library is also listed, and a flow of your own with the same name wins."
)


def _require(store):
    if store is None:
        raise ValueError(OFF)
    return store


def _context():
    """The MCP request being answered, or None outside one."""
    from fastmcp.server.dependencies import get_context

    try:
        return get_context()
    except RuntimeError:
        return None


def catalogue(store, session: str) -> dict:
    """Every flow this session can run: its own, plus the shared library.

    A name defined in both resolves to this session's, and the entry says which
    library it came from — "why am I running the wrong login" is otherwise
    unanswerable.
    """
    store = _require(store)
    entries = {}
    # `shared` says which directory the flow is in, never who is asking. A
    # caller whose own library *is* the shared one still has to be told so: the
    # admin decides from this flag whether to show the globe and which way the
    # move button points, and "not shared" on a flow sitting in `global` offers
    # a move that would be a no-op.
    own_are_shared = session == GLOBAL_SESSION
    if not own_are_shared:
        for summary in store.summaries(GLOBAL_SESSION):
            entries[summary["name"]] = {**summary, "shared": True}
    for summary in store.summaries(session):
        entries[summary["name"]] = {**summary, "shared": own_are_shared}
    return {
        "session": session,
        "count": len(entries),
        "flows": [entries[name] for name in sorted(entries)],
    }


def read_one(store, session: str, name: str) -> dict:
    """One flow: this session's if it has one, else the shared library's."""
    store = _require(store)
    flow = store.get(session, name)
    # A caller whose library is the shared one reads a shared flow. See
    # `catalogue`: the flag describes the directory, not the reader.
    shared = session == GLOBAL_SESSION
    if flow is None and session != GLOBAL_SESSION:
        flow = store.get(GLOBAL_SESSION, name)
        shared = flow is not None
    if flow is None:
        raise ValueError(
            f"no flow called {name!r} in {session} or the shared library. "
            "flow://flows lists what there is."
        )
    return {
        **flow,
        "session": GLOBAL_SESSION if shared else session,
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
    if session == GLOBAL_SESSION:
        raise ValueError(
            "the shared 'global' library is read-only: every session can list "
            "and run what is in it, so a flow you changed or deleted would "
            "change or vanish under another session mid-run. Name your session "
            "and save into your own library — ?session=<name> on the URL or the "
            "X-Session-Key header. An operator moves a flow into global from "
            "the admin UI."
        )
    return session


def save_one(store, session: str, name: str, document: dict, schemas: dict) -> dict:
    """Create or replace one of *this session's* flows.

    Never the shared library, even when a flow of that name was read from it:
    a save is a copy into your own, which is the copy-on-write half of §F1.2.
    Moving a flow into `global` is an operator action in the admin UI.
    """
    # `_require` first, deliberately. With flows switched off there is nowhere
    # to keep one, and that is the true answer for every caller; telling an
    # unnamed one to go and name its session would send it to fix the wrong
    # thing entirely. A named caller already got OFF here, so this ordering is
    # also what makes the two consistent.
    store = _require(store)
    session = writable(session)
    document = dict(document or {})
    document.pop("session", None)
    document.pop("shared", None)
    # `null` means unset, as it does for every optional argument an MCP caller
    # leaves out. Kept, it would be saved and read back as a null where the
    # schema promises an integer (Copilot, #37).
    if document.get(flowdoc.TIMEOUT, 0) is None:
        del document[flowdoc.TIMEOUT]
    flowdoc.validate(document, schemas)
    # Stored as the integer it was accepted as. `"900"` is coerced on the way
    # in, as every boundary value is, and keeping the string would publish a
    # document that does not match its own schema (Copilot, #37).
    if document.get(flowdoc.TIMEOUT) is not None:
        document[flowdoc.TIMEOUT] = flowdoc.declared_timeout(document)
    stored = store.save(session, name, document)
    steps = len(stored.get("steps") or [])
    log.info("flow %s/%s saved (%s steps)", session, name, steps)
    saved = {"saved": True, "session": session, **stored}
    # Said on the way out rather than refused on the way in: the flow is valid
    # and has been kept. See `flowdoc.concerns`.
    concerns = flowdoc.concerns(document)
    if concerns:
        saved["warnings"] = concerns
    return saved


def delete_one(store, session: str, name: str) -> dict:
    """Remove one of *this session's* flows. Never the shared library."""
    store = _require(store)  # see save_one: the disabled answer comes first
    session = writable(session)
    removed = store.delete(session, name)
    return {"deleted": removed, "session": session, "name": name}


async def save_text(
    store, session: str, name: str, text, schemas: Schemas
) -> dict:
    """Rewrite one flow from the YAML a person typed — the **operator** path.

    The admin editor's PUT. It stores the text **verbatim** (§F1.14): a person
    wrote these, comments and ordering included, and a save that round-tripped
    through a parsed dict (``save_one``) would quietly discard both. It is
    validated against the live step schemas first, exactly as ``save_one`` is,
    so the editor cannot store something that would not run.

    It deliberately does not go through ``writable``: that gate keeps agents
    out of the live shared library, and this is the surface where a person is
    present and allowed in. A flow is written back where it already lives, so
    editing a shared one edits the shared one; a new one is created in
    ``session``.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("yaml is required")
    try:
        document = flowlib.parse(text)
    except yaml.YAMLError as exc:
        raise ValueError(
            f"that is not valid YAML: {flowlib.yaml_complaint(exc)}"
        ) from None
    if not isinstance(document, dict):
        raise ValueError("a flow document must be a YAML mapping")
    # The file name is the flow's identity — `LocalFlowStore.get` says
    # so, and overwrites whatever the document claims. So an edited
    # `name:` cannot rename anything: without this the save reports
    # success, the flow keeps its old name, and the file is left saying
    # otherwise. Refusing is not a smaller feature than renaming, it is
    # an honest one; a rename is a move to a new name and belongs with
    # the move verb whenever someone wants it.
    claimed = document.get("name")
    if claimed is not None and valid_name(
        claimed, "flow name"
    ) != valid_name(name, "flow name"):
        raise ValueError(
            f"this flow is called {name!r} and the file name is what "
            "names it, so the document cannot rename it. Put "
            f"{name!r} back, or delete this one and save a new flow "
            "under the name you want."
        )
    flowdoc.validate(document, await schemas.get())
    # Written back where it already lives, so editing a shared flow
    # edits the shared one rather than silently forking a copy into
    # this session. A flow that does not exist yet is created here.
    existing = await run_in_threadpool(store.get, session, name)
    where = session
    if existing is None:
        shared = await run_in_threadpool(
            store.get, GLOBAL_SESSION, name
        )
        if shared is not None:
            where = GLOBAL_SESSION
    await run_in_threadpool(store.write_text, where, name, text)
    return {"saved": True, "session": where, "name": name}


class Schemas:
    """The step schemas, built once from the registered tools.

    Built lazily because `get_tool` is async and registration is not, and from
    `ENDPOINTS` rather than from a listing: a listing is filtered per request
    by `mirror.HideMirrors`, so what it contains depends on who is asking. What a
    flow step may say must not.
    """

    def __init__(self, mcp):
        self.mcp = mcp
        self._cache: dict | None = None
        # The published document schema, built from the step schemas once.
        self._document: dict | None = None

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
    skill_available: bool = True,
    before_step=None,
    stop=None,
    before_save=None,
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
        before_step=before_step,
        stop=stop,
        before_save=before_save,
        catalogue=secrets_catalogue,
        skill_available=skill_available,
        # The CALLER's library, not `document["session"]`: a flow read from the
        # shared `global` library still uploads a file this caller kept.
        library=session,
    )
    return {"session": document["session"], **report}


def register(
    mcp, store, sessions, actions, token: str | None, prefix: str = "",
    secrets_catalogue=None, schemas=None, skill_available: bool = True,
) -> None:
    """Register the flow resources, tools and endpoints."""
    # Shared with the admin surface when the server hands one in, so the editor
    # there validates against the same step schemas these tools do. Two
    # instances would only mean building the same thing twice, but two
    # *sources* of truth is the failure this package keeps finding.
    schemas = schemas or Schemas(mcp)

    # ---- resources ---------------------------------------------------------

    @mcp.resource(
        LIST_URI,
        name="Saved Flows",
        description=LIST_DESCRIPTION,
        mime_type="application/json",
    )
    def flows_resource() -> dict:
        return catalogue(store, clients.caller().library)

    @mcp.resource(
        FLOW_URI,
        name="Saved Flow",
        description=(
            "One saved flow, with its steps. The name comes from the "
            f"{LIST_URI} listing."
        ),
        mime_type="application/json",
    )
    def flow_resource(name: str) -> dict:
        return read_one(store, clients.caller().library, name)

    @mcp.resource(
        SCHEMA_URI,
        name="Flow Document Schema",
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
        name=SAVE_TOOL,
        description=(
            "Save a flow, a sequence of tool calls run server-side in one call, "
            "creating or replacing it by name.\n\n"
            "steps is a list of {tool, args}, where args are exactly that "
            "tool's arguments; a step may also carry id, note, onError (abort "
            "or continue) and return. open_session and end_browser are not "
            "steps: a flow runs in the browser you already hold.\n\n"
            "Declare what varies between runs in parameters and write ${name} "
            "in any argument. A value nobody may see is a secret: write's "
            "args.secret, never a string. timeout (seconds, default "
            f"{flowrun.RUN_TIMEOUT}) bounds the run.\n\n"
            "The whole document is checked against the live tools, and every "
            "problem is listed at once. flow://schema has the full shape; "
            "skill://selenium-flow/references/FLOWS.md explains writing one."
        ),
        annotations=hints("Save a flow", idempotent=True),
    )
    async def save_flow(
        name: str,
        steps: list,
        description: str = "",
        parameters: dict | None = None,
        timeout: int | None = None,
    ) -> dict:
        # In this order, which is the order the saved YAML lists them in.
        document = {"description": description, "steps": steps}
        if parameters:
            document["parameters"] = parameters
        if timeout is not None:
            document[flowdoc.TIMEOUT] = timeout
        library = clients.caller().library  # on the request, before the thread
        steps_schemas = await schemas.get()
        # Validating, dumping and writing in a worker thread, as every other
        # save does: the data directory may be NFS, and a slow write on the
        # loop stalls every other request.
        return await anyio.to_thread.run_sync(
            save_one, store, library, name, document, steps_schemas
        )

    @mcp.tool(
        name=RUN_TOOL,
        description=(
            "Run a saved flow in one call: every step, in order, in the browser "
            "you already hold. Call open_session first, and open Firefox to run "
            "the same flow there.\n\n"
            "params supplies what the flow declares; flow://flows lists them. "
            "The result has a line per step and the final page, and "
            "verbose=true adds every step's full result. It stops at the first "
            "failing step unless that step says onError: continue, and says "
            "where.\n\n"
            "A long run reports progress while it works, and stops if the call "
            "is cancelled."
        ),
        annotations=hints("Run a saved flow", destructive=True),
    )
    async def run_flow(
        name: str, params: dict | None = None, verbose: bool = False
    ) -> dict:
        # `name`, not `library`: a run drives a browser, so this is one of the
        # calls that has to know who is asking. Asked here, on the request,
        # before the run moves to a thread.
        session = clients.caller().name
        watch = progress.Watch()

        def work():
            return run_for(
                store,
                actions,
                sessions,
                session,
                name,
                params=params,
                verbose=verbose,
                secrets_catalogue=secrets_catalogue,
                skill_available=skill_available,
                before_step=watch.step,
                stop=watch.stop,
            )

        # Async so the run can report progress while it works (§F2.15). The
        # context is looked up rather than declared, so the tool's published
        # signature is exactly what it was.
        return await progress.reporting(_context(), work, watch)

    @mcp.tool(
        name=DELETE_TOOL,
        description=(
            "Delete one of your saved flows. Deleting one that is not there is "
            "not an error.\n\n"
            "A flow in the shared 'global' library cannot be deleted here, and "
            "a caller that named no session has no library of its own to delete "
            "from."
        ),
        annotations=hints("Delete a flow", destructive=True, idempotent=True),
    )
    def delete_flow(name: str) -> dict:
        return delete_one(store, clients.caller().library, name)

    _routes(
        mcp, store, sessions, actions, schemas, token, prefix, secrets_catalogue,
        skill_available,
    )


async def _document_schema(schemas: Schemas) -> dict:
    """The flow document's shape, derived from the live tools.

    Published so a model writing a flow is given the shape rather than inferring
    it. It cannot describe a step that would not run, because it is built from
    the same schemas the validator checks against.

    Built once per `Schemas` and kept beside the step schemas it is made of:
    both are fixed for the life of the server, and this one costs a pydantic
    schema generation to build.
    """
    if schemas._document is None:
        schemas._document = _build_document_schema(await schemas.get())
    return schemas._document


def _build_document_schema(steps: dict) -> dict:
    keys = {
        "description": {"type": "string"},
        "parameters": {
            "type": "object",
            "description": "JSON Schema for the values run_flow accepts.",
        },
        flowdoc.TIMEOUT: {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Seconds the whole run may take before no further step "
                f"starts. Defaults to {flowrun.RUN_TIMEOUT}."
            ),
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["tool"],
                "properties": {
                    "tool": {"type": "string", "enum": sorted(steps)},
                    # No `secret` here: it is an argument of `write`, so it
                    # lives in `args` and the per-action schemas below describe
                    # it. A step key would be a second place to say it, and a
                    # caller following this resource would have built a
                    # document save_flow rejects.
                    "args": {"type": "object"},
                    "id": {"type": "string"},
                    "note": {"type": "string"},
                    "onError": {"type": "string", "enum": list(flowdoc.ON_ERROR)},
                    "return": {"type": "boolean"},
                },
            },
        },
    }
    return {
        "type": "object",
        # A key in DOCUMENT_KEYS with nothing said about it here fails on the
        # first read, rather than going unpublished.
        "properties": {
            "name": {"type": "string"},
            **{key: keys[key] for key in flowdoc.DOCUMENT_KEYS},
        },
        "required": ["name", "steps"],
        "x-step-params": steps,
        # How a value that is not written literally reaches an argument. Two
        # mechanisms, and the schema says so rather than leaving an author to
        # discover the difference through a rejection (§F1.38).
        "x-parameters": {
            "description": (
                "A value that varies between runs is a parameter: declare it in "
                "`parameters` and write ${name} inside any argument, anywhere "
                "in the string. Substitution is single-pass, so a value "
                "containing ${...} resolves nothing. Write $${ for a literal."
            ),
            "pattern": template.PARAM_REFERENCE.pattern,
        },
        # The secret reference is the MCP tool's own model rather than a copy of
        # it — a hand-written one is how this repo keeps finding its bugs. It is
        # CLOSED, as the validator is: JSON Schema allows extra properties by
        # default, so a consumer building from this could produce a misspelt
        # reference that it accepts and `save_flow` refuses.
        "x-secret": {
            "description": (
                "A value nobody may see is a secret, and it is never part of a "
                "string. Only `write` has a `secret` argument; the server reads "
                "it, checks it against the page the browser is on, and types "
                "it. Give `secret` or `text`, never both."
            ),
            "schema": SecretRef.model_json_schema(),
        },
    }


def run_for(
    store, actions, sessions, session: str, name: str,
    params=None, verbose: bool = False,
    secrets_catalogue=None, skill_available: bool = True,
    before_step=None, stop=None,
) -> dict:
    """Run a saved flow in ``session``'s browser, and keep the record honest.

    Shared by the tool and the endpoint. The bookkeeping either side of the run
    is the part that used to be duplicated, and the HTTP surface simply did not
    have it: a run there slid no TTL and recorded no page, so a workflow that
    ran flows for an hour could expire out of the store while it worked.
    """
    resolved = sessions.resolve(session)

    def remember(tool, result):
        # The same post-action work a single call gets from `sessions.act`:
        # `resize` changes something the session RECORD stores, and a save's
        # capture is stored as the snapshot and stripped from the result. The
        # touch is left to `before_save` and the one at the end of the run.
        sessions.settle(
            session, result, browser=resolved, reshapes=tool == "resize", touch=False
        )

    # A save reads the localStorage of every site in the history, and the run
    # writes its pages once, at the end: so just before a save step the pages
    # reached so far go in, in one write, and the end writes only the rest.
    # A reopen's report a flush is handed waits in `flushed` for the run's.
    flushed: dict = {}
    count = [0]

    def before_save(pages):
        sessions.settle(session, flushed, url=pages[count[0]:], browser=resolved)
        count[0] = len(pages)

    report = run_one(
        store,
        actions,
        session,
        name,
        params=params,
        verbose=verbose,
        session_id=resolved,
        after_step=remember,
        before_step=before_step,
        before_save=before_save,
        stop=stop,
        secrets_catalogue=secrets_catalogue,
        skill_available=skill_available,
    )
    # A touch at the end, not one per step: the point of running server-side
    # is that the bookkeeping happens once (a save step flushes the pages so
    # far first, above). It carries every page the run reached since, in
    # order, so the history has each step's site and not only the last.
    #
    # Only pages the report itself shows. A step's `url` is there only when
    # the step moved and the page was safe to show, so a failed step's — the
    # scrubbed one, a URL that does not exist — is left out. And the run's own
    # page is withheld when redaction had to touch it: a submitting bound
    # write lands on `?q=<what was typed>`, and `sessions.resolve` would
    # reopen the browser there after the Grid reaped it.
    #
    # The touch happens regardless: it slides the TTL, and a run is the
    # clearest evidence there is that a session is in use.
    visited = [
        step["url"] for step in report.get("steps") or []
        if step.get("ok") and step.get("url")
    ][count[0]:]
    if report.get("url") and not report.get("url_redacted"):
        visited.append(report["url"])
    # The run's browser replaced a reaped one: what came back, once — on the
    # run, whether this write or a flush before a save step was handed it.
    sessions.settle(session, report, url=visited, browser=resolved)
    if "site_data" in flushed and "site_data" not in report:
        report["site_data"] = flushed["site_data"]
    return report


def _routes(
    mcp, store, sessions, actions, schemas: Schemas, token, prefix,
    secrets_catalogue=None, skill_available: bool = True,
) -> None:
    """The flow library as REST (§F2.13).

    ``prefix`` is where the whole server is mounted; `/flows` is fixed beneath
    it, as is the schema route (§F1.11).

    A flow is a resource: ``GET /flows/{name}``, ``PUT`` to create or replace
    it, ``DELETE`` to remove it. Running one **creates a run**, so that is a
    POST to a sub-collection rather than a verb in the path.

    Which library is a question about who is calling, so it comes from the
    header or ``?session=`` like everything else — and a caller that names no
    session gets the shared one, which it may read and may not write.
    """

    flows_root = f"{prefix}/flows"

    async def answer(request: Request, what: str, call) -> JSONResponse:
        """One request, answered the way every other tree answers one.

        Unnamed is allowed here: a flow belongs to a library, and ``caller.library``
        is the shared one when the request named no session. A run asks for
        ``caller.name``, because it drives a browser.
        """
        return await answer_module.answer(
            request, token, f"flows/{what}", call, log, named=False
        )

    @mcp.custom_route(flows_root, methods=["GET"], name="flows_list")
    async def list_flows(request: Request) -> JSONResponse:
        """This session's flows, and the shared ones it can run."""
        return await answer(
            request,
            "list",
            lambda caller, _body: catalogue(store, caller.library),
        )

    @mcp.custom_route(f"{prefix}{SCHEMA_PATH}", methods=["GET"], name="flows_schema")
    async def flow_schema(request: Request) -> JSONResponse:
        """What a flow document may contain — every tool that may be a step.

        Not under /flows, where `schema` would be indistinguishable from a flow
        of that name.
        """
        return await answer(
            request, "schema", lambda _caller, _body: _document_schema(schemas)
        )

    @mcp.custom_route(flows_root + "/{name}", methods=["GET"], name="flows_get")
    async def get_flow(request: Request) -> JSONResponse:
        """One flow, with its steps."""
        return await answer(
            request,
            "get",
            lambda caller, _body: read_one(
                store, caller.library, request.path_params["name"]
            ),
        )

    @mcp.custom_route(flows_root + "/{name}", methods=["PUT"], name="flows_save")
    async def save_flow(request: Request) -> JSONResponse:
        """Create or replace one flow. One verb for both, as §F1.5 has it."""

        async def call(caller, body):
            document = {key: body[key] for key in flowdoc.DOCUMENT_KEYS if key in body}
            library = caller.library
            steps_schemas = await schemas.get()
            # Off the loop, as the MCP save is: see `save_flow` above.
            return await anyio.to_thread.run_sync(
                save_one,
                store,
                library,
                request.path_params["name"],
                document,
                steps_schemas,
            )

        return await answer(request, "save", call)

    @mcp.custom_route(flows_root + "/{name}", methods=["DELETE"], name="flows_delete")
    async def delete_flow(request: Request) -> JSONResponse:
        """Remove one of this session's flows."""
        return await answer(
            request,
            "delete",
            lambda caller, _body: delete_one(
                store, caller.library, request.path_params["name"]
            ),
        )

    @mcp.custom_route(flows_root + "/{name}/runs", methods=["POST"], name="flows_run")
    async def run_flow(request: Request) -> JSONResponse:
        """Run a flow in this session's browser. A run is created, not fetched."""
        return await answer(
            request,
            "run",
            lambda caller, body: run_for(
                store,
                actions,
                sessions,
                # `name`, not `library`: a run drives a browser, so this is one
                # of the calls that has to know who is asking.
                caller.name,
                request.path_params["name"],
                params=body.get("params"),
                # as_bool, not bool: over HTTP "false" arrives as a string, and
                # bool("false") is True — which would turn on full per-step
                # results and return every extract in the flow.
                verbose=as_bool(body.get("verbose"), False),
                secrets_catalogue=secrets_catalogue,
                skill_available=skill_available,
            ),
        )
