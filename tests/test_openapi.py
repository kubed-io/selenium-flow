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

from kubed.selenium_flow.openapi import (
    PLACEHOLDER_VERSION,
    RESPONSES,
    build_spec,
    http_schema,
)
from kubed.selenium_flow.routes import ENDPOINTS

pytestmark = pytest.mark.unit


@pytest.fixture
async def spec(server):
    return await build_spec(server.mcp, ENDPOINTS, "/browser", authenticated=True)


async def test_it_is_openapi_31(spec):
    """3.1, because it is a superset of JSON Schema.

    That is what lets the tool schemas be embedded verbatim rather than
    down-converted, which is the whole basis of the no-drift guarantee.
    """
    assert spec["openapi"] == "3.1.0"


async def test_every_endpoint_is_documented(spec):
    for path in ENDPOINTS:
        assert f"/browser/{path}" in spec["paths"]
    assert "/health" in spec["paths"]


async def test_request_schemas_are_the_tool_schemas(server, spec):
    """The anti-drift guarantee, asserted rather than assumed.

    Equality holds through `http_schema`, which applies the one sanctioned
    difference: session_id is required on an endpoint and optional on a tool.

    Compared against the *registered* tool, not a listing of them. A listing is
    shaped for the client asking for it, and comparing the document to one
    client's view is what let session_id disappear from the spec unnoticed.
    """
    for action in ENDPOINTS.values():
        tool = await server.mcp.get_tool(action)
        documented = spec["components"]["schemas"][
            "".join(p.capitalize() for p in action.split("_")) + "Request"
        ]
        assert documented == http_schema(tool.parameters), action


async def test_session_id_is_required_on_every_endpoint_that_takes_one(server, spec):
    """The HTTP surface is explicit, always. That is its whole contract."""
    for action in ENDPOINTS.values():
        schema = spec["components"]["schemas"][
            "".join(p.capitalize() for p in action.split("_")) + "Request"
        ]
        if action == "open_session":
            continue  # it hands one out rather than taking one
        # Unconditional. This was `if "session_id" in properties`, which passed
        # silently for a year while the document omitted the field entirely:
        # the builder read a tool listing that had it stripped for the caller.
        assert "session_id" in schema.get("properties", {}), action
        assert "session_id" in schema["required"], action
        # no null branch either: an endpoint cannot resolve one for you
        assert schema["properties"]["session_id"]["type"] == "string"


async def test_tools_leave_session_id_optional(server):
    """The mirror of the above: saved sessions can fill it in for an agent."""
    click = await server.mcp.get_tool("interact")
    assert "session_id" not in click.parameters["required"]


async def test_every_action_declares_a_response_shape(spec):
    """Responses are the hand-written half, so coverage is checked."""
    assert set(RESPONSES) == set(ENDPOINTS.values())


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
    spec = await build_spec(server.mcp, ENDPOINTS, "/browser", authenticated=True)
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
    assert {"xpath", "css"} <= set(properties)


async def test_the_multipart_schema_does_not_require_a_named_selector(spec):
    """It cannot say "exactly one of these two" without a oneOf, so it must not
    claim either is mandatory — a contract that forbids a call the route accepts
    is worse than one that is merely permissive. `browser.locator` is what
    actually refuses, with a 400 naming both."""
    schema = spec["paths"]["/browser/upload"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    assert schema["required"] == ["session_id"]


async def test_every_multipart_field_is_one_the_action_accepts(actions, spec):
    """The other direction of the same drift: a hand-written field that no
    longer exists would be advertised to every generated client."""
    import inspect

    accepted = set(inspect.signature(actions.upload_file).parameters)
    form = spec["paths"]["/browser/upload"]["post"]["requestBody"]["content"]
    advertised = set(form["multipart/form-data"]["schema"]["properties"])
    assert advertised - {"session_id"} <= accepted
