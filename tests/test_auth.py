"""Auth on both doors.

A token protects the MCP transport, the HTTP endpoints and the admin API alike,
while /health stays open so a kubelet can probe a pod that has no credentials.

This is the unit-level home for the credential check itself. That the endpoints
are actually wired to it is covered once, end to end, in test_routes.py — the
question there is whether the route is guarded, not how the comparison works.
"""

import pytest
from starlette.requests import Request

from kubed.selenium_flow import auth

from .conftest import TOKEN

pytestmark = pytest.mark.unit


def _request(headers: dict) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw})


@pytest.mark.parametrize(
    "header",
    [
        f"Bearer {TOKEN}",
        # Some clients cannot express a scheme; the token alone is honoured.
        TOKEN,
        # Scheme matching is case-insensitive, per RFC 7235.
        f"bearer {TOKEN}",
    ],
)
def test_the_token_is_accepted_however_it_is_presented(header):
    assert auth.authorized(_request({"Authorization": header}), TOKEN)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Basic " + TOKEN},
        {"Authorization": "Bearer"},
        {"Authorization": ""},
        # A prefix of the real token must not pass — the check compares the
        # whole value, and this is the case a timing attack would be building
        # towards one byte at a time.
        {"Authorization": f"Bearer {TOKEN[:-1]}"},
    ],
)
def test_everything_else_is_rejected(headers):
    assert not auth.authorized(_request(headers), TOKEN)


def test_no_token_configured_means_the_server_is_open():
    """A tokenless deployment is supported — a private network, or a sidecar.

    So this fails open by design, and the tokenless case is the one that must
    not accidentally start rejecting: `docker compose up` runs this way.
    """
    assert auth.authorized(_request({}), None)
    assert auth.authorized(_request({"Authorization": "Bearer anything"}), "")


def test_a_token_turns_on_the_mcp_verifier(server):
    assert server.mcp.auth is not None


def test_no_token_leaves_the_server_open(open_server):
    """`docker compose up` runs without a token on purpose."""
    assert open_server.mcp.auth is None
    assert open_server.auth_token is None
