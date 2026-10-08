"""Does this request carry the server's token?

One question, asked from three places — the action endpoints, the admin API and
the event stream — and previously answered by two hand-rolled copies of the same
header parsing that had already drifted in shape. Answering it in one place is
what lets the comparison below be careful exactly once.

This is the *bearer* half of the server's auth. The other half is `links.py`,
which signs a URL for one file so it can be opened by something that cannot send
a header at all. The split is deliberate: this module answers "are you the
operator?", `links.py` answers "may this one URL be fetched?".

It also builds the MCP door's verifiers: the server token, and a JWT from the
configured OIDC issuer.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import logging
import time
from typing import Any

from fastmcp.server.auth.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request

from .. import config
from ..principal import ADMIN, Principal, roles_in

log = logging.getLogger(__name__)

BEARER = "bearer"


def presented(request: Request) -> str:
    """The credential this request is offering, as a bare string.

    Accepts ``Authorization: Bearer <token>`` and a bare ``Authorization:
    <token>``. The first is what MCP clients send; the second is what a curl or
    an n8n credential field usually ends up sending, and rejecting it would buy
    nothing but support questions.
    """
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == BEARER:
        return value.strip()
    return header.strip()


def authorized(request: Request, token: str | None) -> bool:
    """Whether ``request`` may proceed.

    No token configured means the server is deliberately open — that is a
    supported deployment (a private network, or a sidecar), not a
    misconfiguration to fail closed on.

    The comparison is :func:`hmac.compare_digest` rather than ``==``. Python's
    string equality returns as soon as two bytes differ, so the time it takes
    leaks how long a common prefix was, and these routes are reachable by anyone
    who can reach the port — which is enough to recover a token a byte at a
    time. ``links.py`` already took this care for signatures; the token itself
    had been compared with ``==`` in two separate copies of this check.
    """
    if not token:
        return True
    # Bytes: a str compare_digest raises on non-ASCII, and headers arrive latin-1.
    return hmac.compare_digest(presented(request).encode(), token.encode())


# ---- the MCP door: verifiers FastMCP runs on /mcp ----

# Clock skew the issuer and this server may disagree by, for `nbf`.
NBF_LEEWAY = 60

# The client id the server token reports as. Shown nowhere; FastMCP needs one.
CLIENT_ID = "selenium-flow"

# Seconds between JWKS fetch attempts. Without one, any bearer with an unknown
# kid, on any path, is a GET to the issuer. Keycloak signs with a newly added
# higher-priority key at once, so a rotation can cost up to one floor of
# refusals for the new kid: the accepted price of closing that amplifier.
JWKS_REFETCH_FLOOR = 60

# FastMCP 4.1 floors JWKS refreshes itself (`jwks_refresh_interval`, attempts
# counted, refreshes serialised under a lock), at 30 s by default. Where it can,
# this module sets that rather than keeping a second floor beside it; the
# `_fetch_jwks` override below is the same floor for 4.0, and goes when the
# dependency's lower bound reaches 4.1.
_NATIVE_FLOOR = (
    "jwks_refresh_interval" in inspect.signature(JWTVerifier.__init__).parameters
)


class PrincipalToken(AccessToken):
    """An access token that says who it is, so nothing re-reads its claims."""

    principal: Principal


class ServerTokenVerifier(TokenVerifier):
    """The server token on `/mcp`: the admin. Constant-time, unlike a dict lookup."""

    def __init__(self, token: str):
        super().__init__()
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        # Bytes, not str: compare_digest raises on a non-ASCII str, and a
        # bearer is whatever a client sends.
        if not hmac.compare_digest(token.encode(), self._token.encode()):
            return None
        return PrincipalToken(
            token=token, client_id=CLIENT_ID, scopes=[], principal=ADMIN
        )


class OidcVerifier(JWTVerifier):
    """A JWT from the configured issuer: signature, iss, aud, exp, then a role.

    No routes: the gateway serves the protected-resource metadata and the issuer
    serves the rest. A missing role is a refusal like any other, so a 401.
    """

    def __init__(self, oidc: config.OidcSettings, *, http_client=None):
        # Read once, here, on either path: the floor a verifier was built with.
        self._floor = JWKS_REFETCH_FLOOR
        floor = {"jwks_refresh_interval": self._floor} if _NATIVE_FLOOR else {}
        super().__init__(
            jwks_uri=oidc.jwks_uri,
            issuer=oidc.issuer,
            audience=oidc.audience,
            algorithm="RS256",
            http_client=http_client,
            **floor,
        )
        self._roles = frozenset(oidc.roles)
        self._roles_claim = oidc.roles_claim
        self._fetched_at: float | None = None
        self._inflight: asyncio.Future | None = None

    async def _fetch_jwks(self) -> dict[str, Any]:
        if _NATIVE_FLOOR:
            return await super()._fetch_jwks()
        # FastMCP 4.0: JWTVerifier calls this on every cache miss, and its caller
        # turns the ValueError into a refusal and keeps the cached keys.
        # Single-flight: a burst waits for the one fetch in progress, so a valid
        # token at cold start is not refused by the floor. Shielded, so one
        # waiter's cancellation does not cancel the fetch for the rest.
        if self._inflight is not None:
            return await asyncio.shield(self._inflight)
        now = time.monotonic()
        if self._fetched_at is not None and now - self._fetched_at < self._floor:
            raise ValueError(f"JWKS fetched under {self._floor}s ago")
        self._fetched_at = now
        self._inflight = fetch = asyncio.ensure_future(super()._fetch_jwks())
        fetch.add_done_callback(self._fetch_done)
        return await asyncio.shield(fetch)

    def _fetch_done(self, fetch: asyncio.Future) -> None:
        # Cleared when the fetch ends, not when its first caller does: that
        # caller may be cancelled while others still wait. Reading the
        # exception keeps a fetch nobody awaited from warning at GC.
        if self._inflight is fetch:
            self._inflight = None
        if not fetch.cancelled():
            fetch.exception()

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await super().verify_token(token)
        if verified is None:
            return None
        claims = verified.claims
        # FastMCP checks `exp` only when present and never looks at `nbf`; a
        # token that never expires, or is not yet valid, is refused here.
        exp, nbf = claims.get("exp"), claims.get("nbf")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            _log_refusal("no numeric exp", claims)
            return None
        if nbf is not None and (
            isinstance(nbf, bool)
            or not isinstance(nbf, (int, float))
            or nbf > time.time() + NBF_LEEWAY
        ):
            _log_refusal("nbf not numeric or in the future", claims)
            return None
        held = roles_in(claims, self._roles_claim)
        if self._roles and not self._roles.intersection(held):
            _log_refusal("no allowed role", claims)
            return None
        return PrincipalToken(
            **verified.model_dump(),
            principal=Principal.from_claims(claims, self._roles_claim),
        )


def _log_refusal(reason: str, claims: dict) -> None:
    # The subject only: FastMCP logs its own iss/aud refusals, and claims and
    # the token itself stay out of the log.
    log.info("JWT refused for sub %r: %s", claims.get("sub"), reason)


def provider(settings: config.Settings, *, http_client=None) -> AuthProvider | None:
    """What `/mcp` checks a bearer with: nothing, the token, or the token then a JWT."""
    problem = config.oidc_problem(settings)
    if problem:
        raise config.ConfigError(problem)
    token = settings.auth.token.get_secret_value() if settings.auth.token else None
    if not token:
        return None
    server_token = ServerTokenVerifier(token)
    if not settings.oidc.issuer:
        return server_token
    # The token first: a string compare, where a JWT costs a signature check.
    return MultiAuth(
        verifiers=[server_token, OidcVerifier(settings.oidc, http_client=http_client)]
    )
