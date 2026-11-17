"""Saved flows as MCP resources and tools, and as REST routes under `/flows`.

Reads are resources, writes are tools:

| Verb | Surface |
|---|---|
| list | `flow://flows` |
| get one | `flow://flows/{name}` |
| the step schema | `flow://schema` |
| save | `save_flow` |
| run | `run_flow` |
| delete | `delete_flow` |

`/flows` has its own route table, apart from the browser actions', so the
one-to-one check `test_surfaces.py` makes over browser actions does not count it.

Reads merge the caller's library with `global`, and the caller's flow wins a
name both hold. Writes go only to the caller's own library: `writable` enforces
it, and the admin UI does not come through it. A flow is validated against the
live tool schemas when it is saved.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Annotated

import anyio
import yaml
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core import guidance
from ..core.annotations import hints
from ..core.capabilities import ENDPOINTS, LIBRARY_ARG, capability, method_for
from ..core.coerce import as_bool
from ..http import answer as answer_module
from ..mcp import clients, progress
from ..mcp.tools import SecretRef
from ..names import GLOBAL_WORKSPACE, valid_name
from ..workspace import calls as calls_module
from . import document as flowdoc
from . import engine, places, redact, template
from . import library as flowlib
from . import run as flowrun

log = logging.getLogger(__name__)

# The only tools a step may dispatch to: the capability table's commands, less
# the lifecycle calls.
RUNNABLE = frozenset(ENDPOINTS.values()) - flowdoc.NOT_STEPS.keys()

# The run must not import the capability table or the skill; this module knows
# both and hands them in.
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

# Method, and the path under the /flows prefix. The route table and the
# published spec both read these rows.
FLOW_ROUTES = {
    "list": ("get", ""),
    "get": ("get", "/{name}"),
    "save": ("put", "/{name}"),
    "delete": ("delete", "/{name}"),
    "run": ("post", "/{name}/runs"),
    # Beside /flows, not under it.
    "schema": ("get", SCHEMA_PATH),
}

# By name, apart from the capability table: these are not browser actions and
# must not be counted as one.
FLOW_ENDPOINTS = tuple(FLOW_ROUTES)

OFF = (
    "saved flows are not enabled on this server: it was started with no "
    "DATA_DIR, so there is nowhere to keep them"
)

LIST_DESCRIPTION = (
    "The flows this workspace can run: saved sequences of tool calls that run "
    "server-side in one call.\n\n"
    "Each entry has a name, a description, the parameters it takes and how many "
    "steps it has — never the steps themselves, which flow://flows/{name} returns.\n\n"
    "Flows saved in this workspace come first; anything in the shared 'global' "
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


def catalogue(store, workspace: str) -> dict:
    """Every flow this workspace can run: its own, plus the shared library's.

    A name defined in both resolves to this workspace's, and each entry says
    which library it came from.
    """
    store = _require(store)
    entries = {}
    # `shared` says which directory holds the flow, never who is asking: the
    # admin reads it to choose the globe and which way the move button points.
    own_are_shared = workspace == GLOBAL_WORKSPACE
    if not own_are_shared:
        for summary in store.summaries(GLOBAL_WORKSPACE):
            entries[summary["name"]] = {**summary, "shared": True}
    for summary in store.summaries(workspace):
        entries[summary["name"]] = {**summary, "shared": own_are_shared}
    return {
        "workspace": workspace,
        "count": len(entries),
        "flows": [entries[name] for name in sorted(entries)],
    }


def read_one(store, workspace: str, name: str) -> dict:
    """One flow: this workspace's if it has one, else the shared library's."""
    store = _require(store)
    flow = store.get(workspace, name)
    # The flag describes the directory, as in `catalogue`.
    shared = workspace == GLOBAL_WORKSPACE
    if flow is None and workspace != GLOBAL_WORKSPACE:
        flow = store.get(GLOBAL_WORKSPACE, name)
        shared = flow is not None
    if flow is None:
        raise ValueError(
            f"no flow called {name!r} in {workspace} or the shared library. "
            "flow://flows lists what there is."
        )
    return {
        **flow,
        "workspace": GLOBAL_WORKSPACE if shared else workspace,
        "shared": shared,
    }


def writable(workspace: str) -> str:
    """``workspace`` if an agent may write to it, else refuse.

    `global` is read-and-run only here: every workspace runs what is in it, so
    a flow one agent rewrote or deleted would change or vanish under another
    mid-run. A caller with no workspace name is `global` and is refused too.
    The admin UI writes to the store directly and never comes through here.
    """
    if workspace == GLOBAL_WORKSPACE:
        raise ValueError(
            "the shared 'global' library is read-only: every workspace can list "
            "and run what is in it, so a flow you changed or deleted would "
            "change or vanish under another workspace mid-run. Name your "
            "workspace and save into your own library — ?workspace=<name> on the "
            "URL or the Sf-Workspace header. An operator moves a flow into "
            "global from the admin UI."
        )
    return workspace


def save_one(store, workspace: str, name: str, document: dict, schemas: dict) -> dict:
    """Create or replace one of this workspace's flows.

    Never the shared library's, even when a flow of that name was read from it:
    a save is a copy into the caller's own.
    """
    # `_require` first: with flows off, OFF is the answer for every caller, and
    # an unnamed one told to name its workspace would fix the wrong thing.
    store = _require(store)
    workspace = writable(workspace)
    document = dict(document or {})
    document.pop("workspace", None)
    document.pop("shared", None)
    # `null` means unset, as it does for every optional argument an MCP caller
    # leaves out. Kept, it would be saved and read back as a null where the
    # schema promises an integer.
    for key in (flowdoc.TIMEOUT, places.SITES, places.PATHS):
        if key in document and document[key] is None:
            del document[key]
    flowdoc.validate(document, schemas)
    # `"900"` is accepted, and stored as the integer the schema promises.
    if document.get(flowdoc.TIMEOUT) is not None:
        document[flowdoc.TIMEOUT] = flowdoc.declared_timeout(document)
    flowlib.check_size(flowlib.dump(document))
    stored = store.save(workspace, name, document)
    steps = len(stored.get("steps") or [])
    log.info("flow %s/%s saved (%s steps)", workspace, name, steps)
    saved = {"saved": True, "workspace": workspace, **stored}
    concerns = flowdoc.concerns(document)
    if concerns:
        saved["warnings"] = concerns
    return saved


def delete_one(store, workspace: str, name: str) -> dict:
    """Remove one of this workspace's flows. Never the shared library's."""
    store = _require(store)  # first, as in save_one
    workspace = writable(workspace)
    removed = store.delete(workspace, name)
    return {"deleted": removed, "workspace": workspace, "name": name}


async def save_text(
    store, workspace: str, name: str, text, schemas: Schemas
) -> dict:
    """Store the YAML a person typed in the admin editor as one flow, verbatim.

    Verbatim keeps the person's comments and key order, which ``save_one``'s
    parsed dict would drop. It is validated as ``save_one`` validates. It skips
    ``writable``: the admin may write `global`. A flow is written back where it
    lives, so editing a shared one edits the shared one; a new one is created in
    ``workspace``.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("yaml is required")
    flowlib.check_size(text)
    try:
        document = flowlib.parse(text)
    except yaml.YAMLError as exc:
        raise ValueError(
            f"that is not valid YAML: {flowlib.yaml_complaint(exc)}"
        ) from None
    if not isinstance(document, dict):
        raise ValueError("a flow document must be a YAML mapping")
    # The file name is the flow's identity (`FlowStore.get`), so an edited
    # `name:` would report success and rename nothing.
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
    existing = await run_in_threadpool(store.get, workspace, name)
    where = workspace
    if existing is None:
        shared = await run_in_threadpool(
            store.get, GLOBAL_WORKSPACE, name
        )
        if shared is not None:
            where = GLOBAL_WORKSPACE
    await run_in_threadpool(store.write_text, where, name, text)
    return {"saved": True, "workspace": where, "name": name}


class Schemas:
    """The step schemas, built once from the registered tools.

    Lazily, because `get_tool` is async and registration is not. From
    `ENDPOINTS`, never a listing: `mirror.HideMirrors` filters a listing per
    caller, and what a step may say must not depend on who asks.
    """

    def __init__(self, mcp):
        self.mcp = mcp
        self._cache: dict | None = None
        self._document: dict | None = None  # `_document_schema`'s

    async def get(self) -> dict:
        if self._cache is None:
            tools = {}
            for name in sorted(set(ENDPOINTS.values())):
                tool = await self.mcp.get_tool(name)
                tools[name] = tool.parameters or {}
            self._cache = flowdoc.step_schemas(tools)
        return self._cache


def run_one(
    store, actions, workspace: str, name: str, secrets_catalogue=None, **run_args
) -> dict:
    """Run one flow against an already-resolved browser.

    ``run_args`` are `flows.run.run`'s keyword arguments, ``session_id`` among them.
    """
    document = read_one(store, workspace, name)
    report = flowrun.run(
        actions,
        document,
        catalogue=secrets_catalogue,
        # The caller's library, not `document["workspace"]`: a flow read from
        # `global` still uploads a file this caller kept.
        library=workspace,
        **run_args,
    )
    return {"workspace": document["workspace"], **report}


def register(
    mcp, store, workspaces, actions, token: str | None, prefix: str = "",
    secrets_catalogue=None, schemas=None, skill_available: bool = True,
) -> None:
    """Register the flow resources, tools and endpoints."""
    # The server hands in the admin editor's, so both validate against one.
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
            "Declare what varies between runs in parameters, a JSON Schema "
            f"object such as {json.dumps(flowdoc.PARAMETERS_EXAMPLE)}, and write "
            "${email} in any argument. A value nobody may see is a secret: write's "
            "args.secret, never a string. timeout (seconds, default "
            f"{flowrun.RUN_TIMEOUT}) bounds the run.\n\n"
            "The whole document is checked against the live tools, and every "
            "problem is listed at once. sites and paths say where the flow "
            "applies, so a result that lands there names it in flows_here; a "
            "flow with neither is never named. flow://schema has the full shape; "
            "skill://selenium-flow/references/FLOWS.md explains writing one."
        ),
        annotations=hints("Save a flow", idempotent=True),
    )
    async def save_flow(
        name: str,
        steps: list,
        description: str = "",
        sites: Annotated[
            list[str] | None, Field(description=places.SITES_DESCRIPTION)
        ] = None,
        paths: Annotated[
            list[str] | None, Field(description=places.PATHS_DESCRIPTION)
        ] = None,
        parameters: Annotated[
            dict | None,
            Field(
                description=flowdoc.PARAMETERS_DESCRIPTION,
                examples=[flowdoc.PARAMETERS_EXAMPLE],
            ),
        ] = None,
        timeout: int | None = None,
    ) -> dict:
        # In DOCUMENT_KEYS order, which is the order the saved YAML lists them in.
        document = {"description": description}
        if sites is not None:
            document[places.SITES] = sites
        if paths is not None:
            document[places.PATHS] = paths
        if parameters:
            document["parameters"] = parameters
        if timeout is not None:
            document[flowdoc.TIMEOUT] = timeout
        document["steps"] = steps
        caller = clients.caller()  # on the request, before the thread
        library = caller.library
        steps_schemas = await schemas.get()
        # In a worker thread: the data directory may be NFS, and a slow write on
        # the loop stalls every request. The answer is checked for secrets as
        # `flow://flows/{name}` is.
        return await anyio.to_thread.run_sync(
            workspaces.scrubbed,
            caller.named,
            functools.partial(save_one, store, library, name, document, steps_schemas),
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
        # `name`, not `library`: a run drives a browser. Read on the request,
        # before the run moves to a thread.
        workspace = clients.caller().name
        watch = progress.Watch()

        def work():
            return run_for(
                store,
                actions,
                workspaces,
                workspace,
                name,
                params=params,
                verbose=verbose,
                secrets_catalogue=secrets_catalogue,
                skill_available=skill_available,
                before_step=watch.step,
                stop=watch.stop,
            )

        # The context is looked up, not declared, so it stays out of the tool's
        # published signature.
        return await progress.reporting(_context(), work, watch)

    @mcp.tool(
        name=DELETE_TOOL,
        description=(
            "Delete one of your saved flows. Deleting one that is not there is "
            "not an error.\n\n"
            "A flow in the shared 'global' library cannot be deleted here, and "
            "a caller that named no workspace has no library of its own to delete "
            "from."
        ),
        annotations=hints("Delete a flow", destructive=True, idempotent=True),
    )
    def delete_flow(name: str) -> dict:
        caller = clients.caller()
        return workspaces.scrubbed(
            caller.named, lambda: delete_one(store, caller.library, name)
        )

    _routes(
        mcp, store, workspaces, actions, schemas, token, prefix, secrets_catalogue,
        skill_available,
    )


async def _document_schema(schemas: Schemas) -> dict:
    """The flow document's shape, built from the step schemas the validator uses.

    Built once per `Schemas` and kept on it: it costs a pydantic schema
    generation.
    """
    if schemas._document is None:
        schemas._document = _build_document_schema(await schemas.get())
    return schemas._document


def _build_document_schema(steps: dict) -> dict:
    keys = {
        "description": {"type": "string"},
        places.SITES: {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": places.SITES_DESCRIPTION,
        },
        places.PATHS: {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": places.PATHS_DESCRIPTION,
        },
        "parameters": {
            "type": "object",
            "description": flowdoc.PARAMETERS_DESCRIPTION,
            "properties": {
                "type": {"const": "object"},
                "properties": {
                    "type": "object",
                    "additionalProperties": {"type": ["object", "boolean"]},
                    "description": "One JSON Schema per parameter, by name.",
                },
                "required": {"type": "array", "items": {"type": "string"}},
            },
            "examples": [flowdoc.PARAMETERS_EXAMPLE],
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
                    # No `secret`: it is `write`'s argument, in `args`.
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
        # A key in DOCUMENT_KEYS missing from `keys` fails on the first read.
        "properties": {
            "name": {"type": "string"},
            **{key: keys[key] for key in flowdoc.DOCUMENT_KEYS},
        },
        "required": ["name", "steps"],
        "x-step-params": steps,
        "x-parameters": {
            "description": (
                "A value that varies between runs is a parameter: declare it in "
                "`parameters` and write ${name} inside any argument, anywhere "
                "in the string. Substitution is single-pass, so a value "
                "containing ${...} resolves nothing. Write $${ for a literal."
            ),
            "pattern": template.PARAM_REFERENCE.pattern,
        },
        # The MCP tool's own model, closed to extra properties as the validator
        # is.
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
    store, actions, workspaces, workspace: str, name: str,
    params=None, verbose: bool = False,
    secrets_catalogue=None, skill_available: bool = True,
    before_step=None, stop=None,
) -> dict:
    """Run a saved flow in ``workspace``'s browser, and update its record.

    Shared by the tool and the endpoint. Each step is a call like any other
    (`workspace.calls`): begun after the caller's ``before_step`` has heard it,
    ended inside its turn on the browser. With capture on, the report carries
    ``page``, its steps' hints summed. Each step's line is checked for the
    secrets allowed where it ran, typed or not (`Workspaces.in_scope`).
    """
    # A missing flow is named before a missing browser.
    read_one(store, workspace, name)
    resolved, kind = workspaces.resolved(workspace)
    # The run's hint compares its end with this, since a flush before a save
    # moves the record's page part-way.
    before = workspaces.context(workspace).get("url", "")
    calls = workspaces.calls
    step_hints: list = []
    begun: list = []  # the step under way's, from `before_step` to `step_done`
    began_on: list = [None]  # the origin the step under way began on

    def step_started(entry, total):
        # Inside the step's turn, as a single call reads it
        # (`Workspaces._said`).
        began_on[0] = workspaces.origins.current(workspace, resolved)
        if before_step is not None:
            before_step(watched(entry), total)
        # A step refused because a person holds the browser is never begun, and
        # stops the run.
        begun[:] = [
            calls.begin(workspace, resolved, browser=kind, tool=entry["tool"])
        ]

    def watched(entry):
        """The progress line, its summary checked for the secrets allowed
        where the step begins, or only its tool when that check fails."""
        summary = entry.get("summary")
        if not summary:
            return entry
        try:
            forms = workspaces.scrubber.forms(workspace, resolved, [began_on[0]])
        except Exception as exc:  # noqa: BLE001 - said by type; the run goes on
            log.info("a progress line was not checked: %s", type(exc).__name__)
            return {**entry, "summary": str(entry.get("tool") or "")}
        return {**entry, "summary": redact.scrub(summary, forms)}

    def in_scope(result):
        # The origins the step began and ended on.
        return workspaces.in_scope(workspace, resolved, result, began_on[0])

    def step_done(tool, error):
        if begun:
            step_hints.append(
                calls.end(
                    begun.pop(), tool=tool, surface="flow", browser=kind, error=error
                )
            )

    def bound(forms):
        calls.bound(workspace, forms)

    def remember(tool, result):
        # What a single call gets from `Workspaces.act`: `resize` changes the
        # record, and a save's capture is stored and stripped from the result.
        # The touch is left to `before_save` and the end of the run.
        workspaces.settle(
            workspace,
            result,
            browser=resolved,
            reshapes=capability(tool).reshapes,
            touch=False,
        )

    # A save reads the localStorage of every site in the history, so just
    # before a save step the pages reached so far go in, and the end writes
    # only the rest. The flush holds its notice for the run's own result.
    count = [0]

    def before_save(pages):
        workspaces.settle(
            workspace, {}, url=pages[count[0]:], browser=resolved, hint=False,
            deliver=False,
        )
        count[0] = len(pages)

    try:
        report = run_one(
            store,
            actions,
            workspace,
            name,
            params=params,
            verbose=verbose,
            session_id=resolved,
            after_step=remember,
            before_step=step_started,
            before_save=before_save,
            bound=bound,
            step_done=step_done,
            in_scope=in_scope,
            stop=stop,
            secrets_catalogue=secrets_catalogue,
            skill_available=skill_available,
        )
        page = calls_module.total(step_hints)
        if page is not None:
            report["page"] = page
        # One touch at the end, carrying every page reached since the last
        # flush, in order. Only pages the report shows: a failed step's scrubbed
        # URL is left out, and so is a final page redaction touched, where
        # `Workspaces.resolve` would reopen the browser. The touch happens
        # regardless, to slide the TTL.
        visited = [
            step["url"] for step in report.get("steps") or []
            if step.get("ok") and step.get("url")
        ][count[0]:]
        if report.get("url") and not report.get("url_redacted"):
            visited.append(report["url"])
        workspaces.settle(
            workspace, report, url=visited, browser=resolved, since=before,
            # A withheld page never hints, and the last page `visited` holds is
            # one the run left.
            hint=not report.get("url_redacted"),
        )
    except Exception as exc:
        workspaces.failed(workspace, exc, browser=resolved)
        raise
    return report


def _routes(
    mcp, store, workspaces, actions, schemas: Schemas, token, prefix,
    secrets_catalogue=None, skill_available: bool = True,
) -> None:
    """The flow library as REST, under ``prefix``.

    The library is the caller's, from the header or ``?workspace=``; a caller
    that names no workspace gets the shared one, which it may read and may not
    write.
    """

    flows_root = f"{prefix}/flows"

    async def answer(
        request: Request, what: str, call, *, scrub: bool = True
    ) -> JSONResponse:
        """One request, answered as every other tree answers one.

        Unnamed callers are allowed: ``caller.library`` is the shared one then.
        The answer is checked for secrets as `flow://flows/{name}` is, unless
        ``scrub`` is False: the schema, which this server wrote, and a run,
        whose steps were each checked where they ran.
        """
        return await answer_module.answer(
            request, token, f"flows/{what}", call, log, named=False,
            scrubbed=workspaces.scrubbed if scrub else None,
        )

    @mcp.custom_route(flows_root, methods=["GET"], name="flows_list")
    async def list_flows(request: Request) -> JSONResponse:
        """This workspace's flows, and the shared ones it can run."""
        return await answer(
            request,
            "list",
            lambda caller, _body: catalogue(store, caller.library),
        )

    @mcp.custom_route(f"{prefix}{SCHEMA_PATH}", methods=["GET"], name="flows_schema")
    async def flow_schema(request: Request) -> JSONResponse:
        """What a flow document may contain: every tool that may be a step."""
        return await answer(
            request, "schema", lambda _caller, _body: _document_schema(schemas),
            scrub=False,
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
        """Create or replace one flow."""

        async def call(caller, body):
            document = {key: body[key] for key in flowdoc.DOCUMENT_KEYS if key in body}
            library = caller.library
            steps_schemas = await schemas.get()
            # Off the loop and checked for secrets, as the MCP save is.
            return await anyio.to_thread.run_sync(
                workspaces.scrubbed,
                caller.named,
                functools.partial(
                    save_one,
                    store,
                    library,
                    request.path_params["name"],
                    document,
                    steps_schemas,
                ),
            )

        return await answer(request, "save", call)

    @mcp.custom_route(flows_root + "/{name}", methods=["DELETE"], name="flows_delete")
    async def delete_flow(request: Request) -> JSONResponse:
        """Remove one of this workspace's flows."""
        return await answer(
            request,
            "delete",
            lambda caller, _body: delete_one(
                store, caller.library, request.path_params["name"]
            ),
        )

    @mcp.custom_route(flows_root + "/{name}/runs", methods=["POST"], name="flows_run")
    async def run_flow(request: Request) -> JSONResponse:
        """Run a flow in this workspace's browser. A run is created, not fetched."""
        return await answer(
            request,
            "run",
            scrub=False,
            call=lambda caller, body: run_for(
                store,
                actions,
                workspaces,
                caller.name,  # a run drives a browser
                request.path_params["name"],
                params=body.get("params"),
                # Over HTTP "false" arrives as a string, and bool("false") is
                # True.
                verbose=as_bool(body.get("verbose"), False),
                secrets_catalogue=secrets_catalogue,
                skill_available=skill_available,
            ),
        )
