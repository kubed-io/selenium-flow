"""Auth on both doors.

A token protects the MCP transport, the HTTP endpoints and the admin API alike,
while /health stays open so a kubelet can probe a pod that has no credentials.

This is the unit-level home for the credential check itself. That the endpoints
are actually wired to it is covered once, end to end, in test_routes.py — the
question there is whether the route is guarded, not how the comparison works.
"""

import asyncio
import secrets
import time

import pytest
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from starlette.requests import Request

from kubed.selenium_flow.config import ConfigError, Settings
from kubed.selenium_flow.http import auth
from kubed.selenium_flow.principal import ADMIN

from .conftest import TOKEN
from .jwks import AUDIENCE

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


def test_a_non_ascii_bearer_is_refused_not_raised():
    """Starlette decodes headers as latin-1; a str compare_digest raises on that."""
    assert auth.authorized(_request({"Authorization": "Bearer tökén"}), TOKEN) is False


def test_no_token_configured_means_the_server_is_open():
    """A tokenless deployment is supported — a private network, or a sidecar.

    So this fails open by design, and the tokenless case is the one that must
    not accidentally start rejecting: `docker compose up` runs this way.
    """
    assert auth.authorized(_request({}), None)
    assert auth.authorized(_request({"Authorization": "Bearer anything"}), "")


# ---- the MCP door -------------------------------------------------------------

async def test_the_server_token_verifier_accepts_only_the_exact_token():
    verifier = auth.ServerTokenVerifier(TOKEN)
    token = await verifier.verify_token(TOKEN)
    assert token.principal == ADMIN and token.client_id == auth.CLIENT_ID
    for wrong in ("", TOKEN[:-1], TOKEN + "x", "Bearer " + TOKEN, "tökén"):
        assert await verifier.verify_token(wrong) is None


async def test_a_good_jwt_becomes_an_oidc_principal(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    token = await verifier.verify_token(issuer.mint())
    assert token.principal.kind == "oidc"
    assert (token.principal.subject, token.principal.username) == ("6b0f", "drk")


@pytest.mark.parametrize(
    "overrides",
    [
        {"audience": "https://other.example.com"},
        {"issuer": "https://auth.example.com/realms/other"},
        {"expires_in_seconds": -60},
        {"claims": {"roles": ["viewer"]}},
        {"claims": {"roles": "mcp"}},
    ],
    ids=["audience", "issuer", "expired", "no-role", "roles-not-a-list"],
)
async def test_a_bad_jwt_is_refused(issuer, overrides):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    assert await verifier.verify_token(issuer.mint(**overrides)) is None


async def test_a_jwt_signed_by_another_key_is_refused(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    assert await verifier.verify_token(issuer.mint(keys=RSAKeyPair.generate())) is None


async def test_no_roles_configured_checks_none(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN, roles=()).oidc)
    assert await verifier.verify_token(issuer.mint(claims={"roles": []})) is not None


def _raw(issuer, **claims):
    base = {"sub": "6b0f", "iss": issuer.issuer, "aud": AUDIENCE, "roles": ["mcp"]}
    return issuer.mint_raw(base | claims)


async def test_a_jwt_without_exp_is_refused(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    assert await verifier.verify_token(_raw(issuer)) is None


async def test_a_jwt_not_yet_valid_is_refused(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    now = int(time.time())
    token = _raw(issuer, exp=now + 7200, nbf=now + 3600)
    assert await verifier.verify_token(token) is None


async def test_a_jwt_valid_since_a_moment_ago_is_accepted(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    now = int(time.time())
    token = _raw(issuer, exp=now + 300, nbf=now - 5)
    assert await verifier.verify_token(token) is not None


async def test_an_unreachable_jwks_refuses_without_raising(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    token = issuer.mint()
    issuer.close()
    assert await verifier.verify_token(token) is None


def _unknown_kid(issuer) -> str:
    return issuer.mint(kid=secrets.token_hex(8))


async def test_unknown_kids_fetch_the_jwks_once_per_floor(issuer, monkeypatch):
    """Any bearer reaches the verifier on any path, so a new kid must not mean a GET."""
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    tokens = [_unknown_kid(issuer) for _ in range(8)]
    # Concurrent, as a burst arrives: the floor is taken before the first await.
    results = await asyncio.gather(*(verifier.verify_token(t) for t in tokens))
    assert results == [None] * 8 and issuer.fetches == 1
    # The one fetch cached the real key, so a good token is unaffected.
    assert await verifier.verify_token(issuer.mint()) is not None
    assert issuer.fetches == 1
    monkeypatch.setattr(auth, "JWKS_REFETCH_FLOOR", 0)
    assert await verifier.verify_token(_unknown_kid(issuer)) is None
    assert issuer.fetches == 2


async def test_a_failed_fetch_holds_the_floor_too(issuer):
    """A down issuer is not hammered: the attempt counts, not the success."""
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    issuer.down = True
    assert await verifier.verify_token(issuer.mint()) is None
    issuer.down = False
    assert await verifier.verify_token(issuer.mint()) is None
    assert issuer.fetches == 1


def test_the_provider_per_configuration(issuer):
    assert auth.provider(Settings()) is None
    assert isinstance(auth.provider(Settings(auth={"token": TOKEN})), auth.ServerTokenVerifier)
    both = auth.provider(issuer.settings(TOKEN))
    assert [type(v) for v in both.verifiers] == [auth.ServerTokenVerifier, auth.OidcVerifier]
    # No OAuth routes: the gateway and the issuer own discovery.
    assert both.get_routes("/mcp") == []


def test_the_provider_refuses_oidc_without_the_token(issuer):
    settings = issuer.settings(TOKEN).model_copy(update={"auth": Settings().auth})
    with pytest.raises(ConfigError, match=r"auth\.token"):
        auth.provider(settings)


def test_a_token_turns_on_the_mcp_verifier(server):
    assert isinstance(server.mcp.auth, auth.ServerTokenVerifier)


def test_no_token_leaves_the_server_open(open_server):
    """`docker compose up` runs without a token on purpose."""
    assert open_server.mcp.auth is None
    assert open_server.auth_token is None
