"""Auth on both doors.

A token protects the MCP transport and the HTTP endpoints alike, while /health
stays open so a kubelet can probe a pod that has no credentials.
"""

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from kubed.selenium_flow.routes import _authorized

from .conftest import TOKEN

pytestmark = pytest.mark.unit


def _request(headers: dict) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw})


def test_bearer_scheme_is_accepted():
    assert _authorized(_request({"Authorization": f"Bearer {TOKEN}"}), TOKEN)


def test_bare_header_value_is_accepted():
    """Some clients cannot express a scheme; the token alone is honoured."""
    assert _authorized(_request({"Authorization": TOKEN}), TOKEN)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Basic " + TOKEN},
        {"Authorization": "Bearer"},
        {"Authorization": ""},
    ],
)
def test_everything_else_is_rejected(headers):
    assert not _authorized(_request(headers), TOKEN)


def test_a_token_turns_on_the_mcp_verifier(server):
    assert server.mcp.auth is not None


def test_no_token_leaves_the_server_open(open_server):
    """`docker compose up` runs without a token on purpose."""
    assert open_server.mcp.auth is None
    assert open_server.auth_token is None
