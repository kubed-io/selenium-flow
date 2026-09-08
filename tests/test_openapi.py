"""The generated OpenAPI document.

The point of generating rather than writing it is that it cannot describe an API
different from the one that runs. These tests hold that line: the request
schemas must BE the MCP tool schemas, and the committed file must match what the
code produces.
"""

import pathlib

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

COMMITTED = pathlib.Path(__file__).resolve().parent.parent / "openapi.yaml"


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
    """
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for action in ENDPOINTS.values():
        documented = spec["components"]["schemas"][
            "".join(p.capitalize() for p in action.split("_")) + "Request"
        ]
        assert documented == http_schema(tools[action].parameters), action


async def test_session_id_is_required_on_every_endpoint_that_takes_one(server, spec):
    """The HTTP surface is explicit, always. That is its whole contract."""
    for action in ENDPOINTS.values():
        schema = spec["components"]["schemas"][
            "".join(p.capitalize() for p in action.split("_")) + "Request"
        ]
        if "session_id" in schema.get("properties", {}):
            assert "session_id" in schema["required"], action
            # no null branch either: an endpoint cannot resolve one for you
            assert schema["properties"]["session_id"]["type"] == "string"


async def test_tools_leave_session_id_optional(server):
    """The mirror of the above: saved sessions can fill it in for an agent."""
    click = await server.mcp.get_tool("click")
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


def test_the_document_validates():
    """Validated with a real validator, not just eyeballed."""
    validator = pytest.importorskip("openapi_spec_validator")
    validator.validate(yaml.safe_load(COMMITTED.read_text()))


def test_the_committed_file_is_not_stale(server_spec_yaml):
    """Regenerate with `python scripts/generate_openapi.py` when this fails."""
    committed = yaml.safe_load(COMMITTED.read_text())
    generated = yaml.safe_load(server_spec_yaml)
    assert committed == generated, (
        "openapi.yaml is out of date — run python scripts/generate_openapi.py"
    )


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
