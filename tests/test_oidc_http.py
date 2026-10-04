"""The MCP door over real HTTP: a JWT beside the token, and who it says you are.

Through `server.mcp.http_app()` with FastMCP's own client, so the bearer
middleware, the verifiers and `get_access_token()` all run as deployed. The
issuer's JWKS is fetched over loopback (tests/jwks.py).
"""

import json
import secrets

# mcp's own HTTP client (a required dependency of mcp>=2.0): the test talks to
# the app the way the real client does.
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.testclient import TestClient

from kubed.selenium_flow.mcp import mirror
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN
from .test_http_answer import TREES

INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def oidc_server(issuer):
    return SeleniumMCP(issuer.settings(TOKEN))


def status_of(server, bearer):
    with TestClient(server.mcp.http_app()) as client:
        return client.post(
            "/mcp", json=INIT, headers=ACCEPT | {"Authorization": f"Bearer {bearer}"}
        ).status_code


def test_a_jwt_opens_the_mcp_door(oidc_server, issuer):
    assert status_of(oidc_server, issuer.mint()) == 200


def test_the_token_still_opens_it(oidc_server):
    assert status_of(oidc_server, TOKEN) == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"audience": "https://other.example.com"},
        {"issuer": "https://auth.example.com/realms/other"},
        {"expires_in_seconds": -60},
        {"claims": {"roles": ["viewer"]}},
    ],
    ids=["audience", "issuer", "expired", "no-role"],
)
def test_a_bad_jwt_is_a_401(oidc_server, issuer, overrides):
    assert status_of(oidc_server, issuer.mint(**overrides)) == 401


@pytest.mark.parametrize(("method", "path"), TREES)
def test_a_jwt_does_not_open_a_rest_route(oidc_server, issuer, method, path):
    """The round's boundary: the REST routes stay token-only."""
    with TestClient(oidc_server.mcp.http_app()) as client:
        answer = getattr(client, method)(
            path, json={}, headers={"Authorization": f"Bearer {issuer.mint()}"}
        )
    assert answer.status_code == 401


def test_unknown_kids_on_any_path_fetch_the_jwks_at_most_once(oidc_server, issuer):
    """The bearer middleware runs app-wide, so an open route reaches the verifier too."""
    with TestClient(oidc_server.mcp.http_app()) as client:
        for _ in range(5):
            bearer = issuer.mint(kid=secrets.token_hex(8))
            client.get("/info", headers={"Authorization": f"Bearer {bearer}"})
    assert issuer.fetches <= 1


async def current(server, bearer):
    app = server.mcp.http_app()

    def factory(headers=None, timeout=None, auth=None, **kwargs):
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
            timeout=timeout if timeout is not None else httpx2.Timeout(30),
            **kwargs,
        )

    transport = StreamableHttpTransport(
        "http://test/mcp",
        headers={"Authorization": f"Bearer {bearer}", "X-Session-Key": "desk"},
        httpx_client_factory=factory,
    )
    async with app.router.lifespan_context(app), Client(transport) as client:
        result = await client.call_tool(mirror.READ_TOOL, {"uri": "session://current"})
    return json.loads(result.content[0].text)


async def test_session_current_names_the_jwt_subject(oidc_server, issuer):
    status = await current(oidc_server, issuer.mint())
    assert status["principal"] == {"kind": "oidc", "subject": "6b0f", "username": "drk"}
    assert status["session"] == "desk"


async def test_session_current_names_the_admin_for_the_token(oidc_server):
    assert (await current(oidc_server, TOKEN))["principal"] == {"kind": "admin"}
