"""The generated OpenAPI document.

The point of generating rather than writing it is that it cannot describe an API
different from the one that runs. These tests hold that line: the request
schemas must BE the MCP tool schemas.

Everything here builds the spec in-process. There is no file on disk to compare
against — ``openapi.yaml`` is a build artifact, produced by
``scripts/generate_openapi.py`` and not committed — so the thing under test is
what the server would actually serve.
"""

import pytest
import yaml
from starlette.testclient import TestClient

from kubed.selenium_flow.routes import ACTION_IN_PATH, ENDPOINTS
from kubed.selenium_flow.spec import PLACEHOLDER_VERSION, RESPONSES, build_spec
from kubed.selenium_flow.spec.builder import _hoisted


def route_for(path: str, action: str) -> str:
    """Where one action is served, which the spec must describe it at."""
    return f"/browser/{path}" + ("/{action}" if action == ACTION_IN_PATH else "")

pytestmark = pytest.mark.unit


@pytest.fixture
async def spec(server):
    return await build_spec(server.mcp, ENDPOINTS, "", authenticated=True)


async def test_it_is_openapi_31(spec):
    """3.1, because it is a superset of JSON Schema.

    That is what lets the tool schemas be embedded verbatim rather than
    down-converted, which is the whole basis of the no-drift guarantee.
    """
    assert spec["openapi"] == "3.1.0"


async def test_every_endpoint_is_documented(spec):
    for path, action in ENDPOINTS.items():
        assert route_for(path, action) in spec["paths"]
    assert "/health" in spec["paths"]


async def test_the_browser_resource_carries_its_three_methods(spec):
    """Opening, reading and ending are methods on the browser this caller
    holds, not paths of their own (§F2.13)."""
    assert set(spec["paths"]["/browser"]) == {"post", "get", "delete"}


async def test_every_operation_says_how_to_name_a_session(spec):
    """It is the one thing a caller must supply and the only thing that is not
    a body field, so an operation that does not publish it cannot be called by
    a generated client."""
    ops = {"/health", "/started", "/ready", "/info"}
    for path, operations in spec["paths"].items():
        # The ops endpoints are about the process, not about a session.
        if path in ops:
            continue
        for method, operation in operations.items():
            names = {p["name"] for p in operation.get("parameters", [])}
            assert {"X-Session-Key", "session"} <= names, f"{method} {path}"


async def test_request_schemas_are_the_tool_schemas(server, spec):
    """The anti-drift guarantee, asserted rather than assumed — and it is now
    plain equality.

    There used to be three sanctioned transforms on the way through, all of them
    consequences of the HTTP surface having no session of its own. §F2.12 and
    §F2.13 removed the cause, so a request body here IS the tool's schema.

    The only thing still applied is `_hoisted`, which is presentation rather
    than content: a nested model's definition moves from the schema's own
    `$defs` into the document's components, because a `$ref` resolves against
    the document root and would otherwise point at nothing.

    Compared against the *registered* tool, not a listing of them. A listing is
    shaped for the client asking for it, and comparing the document to one
    client's view is what let session_id disappear from the spec unnoticed.
    """
    for action in ENDPOINTS.values():
        if action == ACTION_IN_PATH:
            continue  # its `action` is a path parameter; asserted below
        tool = await server.mcp.get_tool(action)
        documented = spec["components"]["schemas"][
            "".join(p.capitalize() for p in action.split("_")) + "Request"
        ]
        expected, _ = _hoisted(tool.parameters)
        assert documented == expected, action


async def test_the_mouse_action_is_a_path_parameter_not_a_body_field(server, spec):
    """The one place the body is not the whole tool schema, and it is a shape
    difference rather than a content one: the choice moved into the path, so it
    is published there — with its enum intact, so a generated client still sees
    every action."""
    operation = spec["paths"]["/browser/interact/{action}"]["post"]
    in_path = next(p for p in operation["parameters"] if p["in"] == "path")
    assert in_path["name"] == "action"
    assert "click" in in_path["schema"].get("enum", [])
    body = spec["components"]["schemas"]["InteractRequest"]
    assert "action" not in body["properties"]


async def test_a_nested_model_is_published_as_its_own_schema(spec):
    """`secret` is a typed model, not a bare object, so a client is told it
    needs `name` and `key` instead of guessing at a blob."""
    defined = spec["components"]["schemas"]
    assert "SecretRef" in defined
    assert set(defined["SecretRef"]["required"]) == {"name", "key"}
    # And every reference to it resolves inside the document.
    ref = defined["WriteRequest"]["properties"]["secret"]
    assert "$defs" not in defined["WriteRequest"]
    assert "#/$defs/" not in str(ref)


async def test_no_endpoint_publishes_a_browser_id(server, spec):
    """The inversion of what this file used to assert, and the whole of E18: the
    Grid's session id is how a browser is reached and is not part of the
    contract. A caller names a session; it never holds an id."""
    for action in ENDPOINTS.values():
        schema = spec["components"]["schemas"][
            "".join(p.capitalize() for p in action.split("_")) + "Request"
        ]
        assert "session_id" not in schema.get("properties", {}), action


async def test_tools_leave_session_id_optional(server):
    """The mirror of the above: saved sessions can fill it in for an agent."""
    click = await server.mcp.get_tool("interact")
    assert "session_id" not in click.parameters["required"]


async def test_every_action_declares_a_response_shape(spec):
    """Responses are the hand-written half, so coverage is checked."""
    assert set(ENDPOINTS.values()) <= set(RESPONSES)
    # The browser resource's own methods are actions too, and are documented
    # from the same table even though they are not in ENDPOINTS.
    assert {"open_session", "end_browser"} <= set(RESPONSES)


async def test_health_is_exempt_from_security(spec):
    """A kubelet has no token; the document must say so."""
    assert spec["paths"]["/health"]["get"]["security"] == []
    assert spec["security"] == [{"bearerAuth": []}]


async def test_operations_carry_a_summary_and_description(spec):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op["summary"], f"{method} {path} has no summary"
            assert op["operationId"], f"{method} {path} has no operationId"


def test_the_document_validates(server_spec_yaml):
    """Validated with a real validator, not just eyeballed.

    Run against the generated document rather than a checked-in copy, so what is
    validated is what the server serves and what the artifact is written from.
    """
    validator = pytest.importorskip("openapi_spec_validator")
    validator.validate(yaml.safe_load(server_spec_yaml))


@pytest.fixture
async def server_spec_yaml(server):
    spec = await build_spec(server.mcp, ENDPOINTS, "", authenticated=True)
    spec["info"]["version"] = PLACEHOLDER_VERSION
    return yaml.safe_dump(spec, sort_keys=False, width=100)


def test_the_endpoint_serves_it_without_a_token(open_server):
    """The contract is readable before you hold a credential."""
    client = TestClient(open_server.mcp.http_app())
    response = client.get("/openapi.yaml")
    assert response.status_code == 200
    assert "yaml" in response.headers["content-type"]
    assert yaml.safe_load(response.text)["openapi"] == "3.1.0"


def test_json_is_served_too(open_server):
    client = TestClient(open_server.mcp.http_app())
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Selenium Flow"


async def test_the_multipart_upload_schema_accepts_every_selector(spec):
    """The one request schema that is hand-written, and so the one that drifts.

    Every other request body IS the tool schema, so adding a parameter updates
    the contract for free. The multipart form for /browser/upload is written out
    by hand — it has to be, because a file part is not something a JSON tool
    schema can describe — and it silently kept describing an xpath-only,
    xpath-required upload after the route had started accepting css.
    """
    form = spec["paths"]["/browser/upload"]["post"]["requestBody"]["content"]
    properties = form["multipart/form-data"]["schema"]["properties"]
    assert "selector" in properties


async def test_the_multipart_schema_does_not_require_a_named_selector(spec):
    """It cannot say "exactly one of these two" without a oneOf, so it must not
    claim either is mandatory — a contract that forbids a call the route accepts
    is worse than one that is merely permissive. `browser.locator` is what
    actually refuses, with a 400 naming both."""
    schema = spec["paths"]["/browser/upload"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    assert "required" not in schema, "the session is a header, not a form field"


async def test_every_multipart_field_is_one_the_action_accepts(actions, spec):
    """The other direction of the same drift: a hand-written field that no
    longer exists would be advertised to every generated client."""
    import inspect

    accepted = set(inspect.signature(actions.upload_file).parameters)
    form = spec["paths"]["/browser/upload"]["post"]["requestBody"]["content"]
    advertised = set(form["multipart/form-data"]["schema"]["properties"])
    assert advertised - {"session_id"} <= accepted


# ---- the /flows half, which is hand-written and therefore drifts ------------


async def test_every_flow_endpoint_is_in_the_published_contract(spec):
    """The guard the multipart upload schema did not have until it had already
    drifted: these paths are written by hand, so the list is held against the
    one the server actually binds."""
    from kubed.selenium_flow.flows.api import FLOW_ROUTES

    published = {
        (method, path)
        for path, operations in spec["paths"].items()
        for method in operations
        if path.startswith("/flows")
    }
    assert published == {
        (method, f"/flows{template}")
        for method, template in FLOW_ROUTES.values()
        if not template.startswith("/schemas")
    }


async def test_the_flow_endpoints_are_tagged_apart_from_the_browser_ones(spec):
    """They are a layer above /browser, and a docs UI should group them so."""
    tags = {t["name"] for t in spec["tags"]}
    assert "flows" in tags
    for path, operations in spec["paths"].items():
        if path.startswith("/flows") or path == "/schemas/flow":
            for method, operation in operations.items():
                assert operation["tags"] == ["flows"], f"{method} {path}"


async def test_a_flow_listing_does_not_advertise_the_steps(spec):
    """`step_count`, not `steps` — the contract has to say the same thing the
    store does, or a generated client unpacks a list that is an int."""
    summary = spec["components"]["schemas"]["FlowSummary"]["properties"]
    assert "step_count" in summary
    assert "steps" not in summary


async def test_saving_requires_the_steps_and_takes_the_name_from_the_path(spec):
    """A flow is a resource: PUT /flows/{name} says which flow in the path, so
    `name` is not a body field and cannot disagree with the one in the URL."""
    operation = spec["paths"]["/flows/{name}"]["put"]
    assert {p["name"] for p in operation["parameters"] if p["in"] == "path"} == {"name"}
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["steps"]
    assert "name" not in schema["properties"]


async def test_every_flow_response_schema_it_references_exists(spec):
    """A $ref to a schema nobody defined renders as a blank box in every docs
    UI and fails a strict linter."""
    defined = set(spec["components"]["schemas"])
    for path, operations in spec["paths"].items():
        if not (path.startswith("/flows") or path == "/schemas/flow"):
            continue
        for method, operation in operations.items():
            for block in operation["responses"].values():
                schema = block["content"]["application/json"]["schema"]
                ref = schema.get("$ref")
                if ref:
                    assert ref.split("/")[-1] in defined, f"{method} {path} -> {ref}"


async def test_the_http_surface_has_a_session_to_be_fresh_from_now(spec, server):
    """`fresh` says "do not go back to the page my session was last on". It used
    to be dropped from this contract because `routes.py` never touched
    `SessionManager` and there was no session to go back to — the difference
    §F2.13 removed by giving both surfaces the same one."""
    published = spec["components"]["schemas"]["OpenSessionRequest"]["properties"]
    assert "fresh" in published
    assert "fresh" in (await server.mcp.get_tool("open_session")).parameters["properties"]


async def test_a_saved_flows_warnings_are_in_the_published_contract(spec):
    """`save_one` returns them conditionally, and the /flows schemas are the
    hand-written half of this document — so a field added there is invisible to
    a generated client until it is declared (Copilot, #31)."""
    saved = spec["components"]["schemas"]["FlowSaved"]["properties"]
    assert saved["warnings"]["type"] == "array"


async def test_a_run_reports_step_url_in_the_published_contract(spec):
    step = spec["components"]["schemas"]["FlowRun"]["properties"]["steps"]["items"]
    assert "url" in step["properties"]


async def test_the_upload_form_offers_every_source_the_action_takes(spec, server):
    """The multipart schema is hand-written while the JSON one is derived, so
    the two drift in exactly one direction: a new source appears in JSON and
    not in the form."""
    upload = await server.mcp.get_tool("upload_file")
    sources = {"text", "content", "file", "path"} & set(upload.parameters["properties"])
    form = spec["paths"]["/browser/upload"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]["properties"]
    # `path` is a server-side filesystem path and has no place in a form, so it
    # is the one source deliberately absent; everything else must be offered.
    assert (sources - {"path"}) <= set(form)


async def test_uploading_a_kept_file_needs_no_library_field_on_either_surface(
    spec, server
):
    """`upload_file(session=...)` existed because an HTTP caller had no other
    way to say which library a kept file came from. It names its session like
    everything else now, so the field is gone from both surfaces rather than
    published on one (Copilot, #31; §F2.13)."""
    upload = spec["paths"]["/browser/upload"]["post"]["requestBody"]["content"]
    published = spec["components"]["schemas"]["UploadFileRequest"]["properties"]
    assert "session" not in published
    assert "session" not in upload["multipart/form-data"]["schema"]["properties"]
    tool = await server.mcp.get_tool("upload_file")
    assert "session" not in tool.parameters["properties"]
