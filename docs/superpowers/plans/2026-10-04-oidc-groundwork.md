# OIDC Groundwork Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/mcp` accepts a JWT from an OIDC issuer beside the server token,
verifies it fully (signature, `iss`, `aud`, `exp`, role) and hands the caller to
the code as a principal; the cluster's selenium-flow sits behind agentgateway and
Claude Code signs in to it the way it signs in to kb.

**Architecture:** A new top-level `oidc` settings section. `http/auth.py` builds
the provider FastMCP's `auth=` takes: a constant-time `ServerTokenVerifier`, plus
an `OidcVerifier` (FastMCP's `JWTVerifier` with a role check) under `MultiAuth`.
Both return a `PrincipalToken` carrying a `Principal` (new `principal.py`);
`mcp/clients.caller()` copies it onto `Caller`, and `session://current` shows it.
Nothing else reads it this round.

**Tech Stack:** Python 3.10+, FastMCP 4 (`fastmcp-slim` ≥ 4.0.0: `MultiAuth`,
`JWTVerifier`, `RSAKeyPair`, `get_access_token`), pydantic 2, Starlette,
pytest + pytest-asyncio; kustomize + agentgateway 1.6 CRDs in the cluster repo.

**Spec:** `docs/superpowers/specs/2026-10-04-oidc-groundwork-design.md` — read
*Rulings*, *Non-goals* and *The design* before any task.

## Global Constraints

- No `oidc` setting set ⇒ behaviour identical to `main`. Every existing test passes unchanged except where a task says otherwise.
- The REST routes, admin API, event stream and signed links stay token-only (`http/auth.authorized`). Do not touch them.
- Sessions are not renamed and get no owner. Nothing decides anything from a `Principal` this round.
- Token comparisons are `hmac.compare_digest`. One module decides authorisation: `kubed/selenium_flow/http/auth.py`.
- Every refusal on `/mcp` is a 401 (a verifier returning `None`), the missing role included.
- FastMCP floor stays `fastmcp>=4.0.0,<5`. No new runtime dependency.
- Real hosts never appear in this repo: examples and tests use `example.com`, realm `example`, user `drk`. Before any push, `git grep -i` the diff for the homelab's domain and the maintainer's name (the session knows both; this repo must not) and find nothing.
- Copy is terse (no helper paragraphs in UI or errors beyond one sentence). Comments earn their lines: keep the non-obvious why.
- Running tests in the code-server pod: there is no venv. Install the test extra with `pip install --quiet --target $PY -e ".[test]"` into a scratch dir, then `PYTHONPATH=$PY:. $PY/bin/pytest -q -p no:randomly <paths>` and `$PY/bin/ruff check kubed tests` / `ruff format --check`. Never run the whole suite without `-x` first; never run Behat/psalm-like tools (none here).
- Commits: one per task, message in the house style (sentence, no prefix tag), ending with the `Co-Authored-By` line the session gives.

## File structure

| File | Responsibility |
|---|---|
| Create `kubed/selenium_flow/principal.py` | `Principal`, `roles_in`: who a verified caller is. Pure; imports nothing from FastMCP. |
| Modify `kubed/selenium_flow/config.py` | `OidcSettings` section; `oidc_problem()`; the post-merge check in `load()`; URL leaves. |
| Modify `kubed/selenium_flow/http/auth.py` | `PrincipalToken`, `ServerTokenVerifier`, `OidcVerifier`, `provider()`. |
| Modify `kubed/selenium_flow/server.py` | `auth=http_auth.provider(settings)` replaces the `StaticTokenVerifier` block. |
| Modify `kubed/selenium_flow/session/sessions.py` | `Caller.principal`; `describe()` shows it. |
| Modify `kubed/selenium_flow/mcp/clients.py` | `principal()`; `caller()` fills it. |
| Create `tests/test_principal.py` | Unit: `Principal`, `roles_in`. |
| Create `tests/test_config_oidc.py` | The `oidc` section and the load rules. |
| Create `tests/jwks.py` | Test helper: a loopback JWKS server and token minting. |
| Modify `tests/test_auth.py` | Verifiers and `provider()`. |
| Create `tests/test_oidc_http.py` | Through the real entry point: `/mcp` over HTTP. |
| Docs | `README.md`, `AGENTS.md`, `CHANGELOG.md`, `wiki/Configuration.md`, `wiki/Deployment.md`. |
| Cluster repo | `apps/selenium/components/mcp/{gateway.yaml,kustomization.yaml,config.yaml}`, `.mcp.json`, `apps/agentgateway/AGENTS.md`. |

---

### Task 1: The principal

**Files:**
- Create: `kubed/selenium_flow/principal.py`
- Test: `tests/test_principal.py`

**Interfaces:**
- Produces: `Principal(kind: Literal["admin","oidc"], subject: str|None=None, username: str|None=None, roles: tuple[str,...]=())`, `.admin -> bool`, `.status() -> dict`, `Principal.from_claims(claims: Mapping, roles_claim: str) -> Principal`, `ADMIN: Principal` (the server token's), `roles_in(claims: Mapping, roles_claim: str) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

```python
"""Who a verified caller is: the server token's admin, or an OIDC subject."""

import pytest

from kubed.selenium_flow.principal import ADMIN, Principal, roles_in

pytestmark = pytest.mark.unit


def test_the_server_token_is_the_admin():
    assert ADMIN.admin and ADMIN.kind == "admin"
    assert ADMIN.status() == {"kind": "admin"}


def test_a_jwt_is_an_oidc_principal_with_its_subject_and_username():
    claims = {"sub": "6b0f", "preferred_username": "drk", "roles": ["mcp", "admin"]}
    who = Principal.from_claims(claims, "roles")
    assert (who.kind, who.subject, who.username, who.roles) == (
        "oidc", "6b0f", "drk", ("mcp", "admin"),
    )
    # A JWT role named admin is not the admin this round (spec, The principal).
    assert not who.admin
    assert who.status() == {"kind": "oidc", "subject": "6b0f", "username": "drk"}


def test_a_jwt_without_a_username_says_none():
    assert Principal.from_claims({"sub": "x"}, "roles").username is None


@pytest.mark.parametrize(
    ("claims", "path", "held"),
    [
        ({"roles": ["mcp"]}, "roles", ("mcp",)),
        ({"realm_access": {"roles": ["a", "b"]}}, "realm_access.roles", ("a", "b")),
        ({}, "roles", ()),
        ({"roles": "mcp"}, "roles", ()),               # a string is not a list
        ({"roles": ["mcp", 3, None]}, "roles", ("mcp",)),  # non-strings dropped
        ({"realm_access": ["x"]}, "realm_access.roles", ()),  # walk hits a list
    ],
)
def test_roles_in_reads_a_dotted_path_and_nothing_else(claims, path, held):
    assert roles_in(claims, path) == held
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=$PY:. $PY/bin/pytest -q tests/test_principal.py`
Expected: FAIL, `ModuleNotFoundError: kubed.selenium_flow.principal`.

- [ ] **Step 3: Implement**

```python
"""Who a verified caller is.

Two kinds, from the two credentials `/mcp` accepts: the server token, which is
the operator and so the admin, and a JWT from the configured OIDC issuer, which
is a subject. Built by the verifiers in ``http/auth.py`` and carried on
``Caller``; nothing decides anything from it yet (spec 2026-10-04, groundwork).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


def roles_in(claims: Mapping[str, Any], roles_claim: str) -> tuple[str, ...]:
    """The roles a token holds: ``roles_claim`` walked by dots, strings only.

    The one reading of the claim, shared by the role check and the principal so
    they cannot disagree. Anything that is not a list of strings holds none.
    """
    value: Any = claims
    for part in roles_claim.split("."):
        if not isinstance(value, Mapping):
            return ()
        value = value.get(part)
    if not isinstance(value, list):
        return ()
    return tuple(role for role in value if isinstance(role, str))


@dataclass(frozen=True)
class Principal:
    kind: Literal["admin", "oidc"]
    subject: str | None = None
    username: str | None = None
    roles: tuple[str, ...] = ()

    @property
    def admin(self) -> bool:
        return self.kind == "admin"

    def status(self) -> dict:
        """What `session://current` shows. Roles are an input, not news."""
        if self.admin:
            return {"kind": "admin"}
        return {"kind": "oidc", "subject": self.subject, "username": self.username}

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any], roles_claim: str) -> Principal:
        sub = claims.get("sub")
        name = claims.get("preferred_username")
        return cls(
            "oidc",
            subject=sub if isinstance(sub, str) else None,
            username=name if isinstance(name, str) else None,
            roles=roles_in(claims, roles_claim),
        )


ADMIN = Principal("admin")
```

- [ ] **Step 4: Run them to see them pass**

Run: `PYTHONPATH=$PY:. $PY/bin/pytest -q tests/test_principal.py && $PY/bin/ruff check kubed/selenium_flow/principal.py tests/test_principal.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/principal.py tests/test_principal.py
git commit -m "A principal: who a verified caller is, admin or an OIDC subject"
```

---

### Task 2: The `oidc` settings

**Files:**
- Modify: `kubed/selenium_flow/config.py` (new `OidcSettings` after `AuthSettings` ~line 106; `Settings.oidc` after `auth` ~line 297; `URL_LEAVES` ~line 704; `oidc_problem()` before `load()`; the check at the end of `load()` ~line 675)
- Test: `tests/test_config_oidc.py`

**Interfaces:**
- Produces: `config.OidcSettings` (`issuer`, `audience`, `jwks_uri`: `str | None`; `roles: list[str]`; `roles_claim: str = "roles"`), `Settings.oidc`, `config.oidc_problem(settings: Settings) -> str | None` (the reason this settings' OIDC cannot run, else None).

- [ ] **Step 1: Write the failing tests**

```python
"""The `oidc` section: a JWT issuer beside the token, never instead of it."""

import pytest

from kubed.selenium_flow import config
from kubed.selenium_flow.config import ConfigError, Settings, load

pytestmark = pytest.mark.unit

ISSUER = "https://auth.example.com/realms/example"
JWKS = ISSUER + "/protocol/openid-connect/certs"
ON = {
    "OIDC_ISSUER": ISSUER,
    "OIDC_AUDIENCE": "https://mcp.example.com",
    "OIDC_JWKS_URI": JWKS,
    "AUTH_TOKEN": "t0ken",
}


def test_off_by_default():
    oidc = Settings().oidc
    assert (oidc.issuer, oidc.audience, oidc.jwks_uri, oidc.roles) == (None, None, None, [])
    assert oidc.roles_claim == "roles"
    assert config.oidc_problem(Settings()) is None


def test_the_env_names_follow_the_rule():
    envs = {leaf.env for leaf in config.leaves() if leaf.section == "oidc"}
    assert envs == {
        "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URI", "OIDC_ROLES", "OIDC_ROLES_CLAIM",
    }


def test_roles_are_a_comma_list_from_env():
    loaded = load([], ON | {"OIDC_ROLES": "mcp, admin,"})
    assert loaded.settings.oidc.roles == ["mcp", "admin"]


def test_all_three_load_with_a_token():
    oidc = load([], ON).settings.oidc
    assert (oidc.issuer, oidc.jwks_uri) == (ISSUER, JWKS)


@pytest.mark.parametrize("missing", ["OIDC_AUDIENCE", "OIDC_JWKS_URI", "OIDC_ISSUER"])
def test_issuer_audience_and_jwks_are_all_or_nothing(missing):
    env = {k: v for k, v in ON.items() if k != missing}
    with pytest.raises(ConfigError, match="together"):
        load([], env)


def test_oidc_needs_the_token():
    env = {k: v for k, v in ON.items() if k != "AUTH_TOKEN"}
    with pytest.raises(ConfigError, match="auth.token"):
        load([], env)


def test_oidc_in_the_file_and_the_token_in_env_loads(tmp_path):
    """The cluster's shape: `load()` validates the file alone first."""
    path = tmp_path / "config.yaml"
    path.write_text(
        f"oidc:\n  issuer: {ISSUER}\n  audience: https://mcp.example.com\n"
        f"  jwks_uri: {JWKS}\n  roles: [mcp]\n"
    )
    loaded = load(["--config-file", str(path)], {"AUTH_TOKEN": "t0ken"})
    assert loaded.settings.oidc.roles == ["mcp"]
    assert loaded.sources["oidc.issuer"] == "config"


@pytest.mark.parametrize("key", ["OIDC_ISSUER", "OIDC_JWKS_URI"])
def test_the_urls_are_http(key):
    with pytest.raises(ConfigError, match="http"):
        load([], ON | {key: "ftp://auth.example.com/x"})


def test_oidc_problem_is_the_same_rule_for_settings_built_in_code():
    built = Settings(oidc={"issuer": ISSUER, "audience": "a", "jwks_uri": JWKS})
    assert "auth.token" in config.oidc_problem(built)
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=$PY:. $PY/bin/pytest -q tests/test_config_oidc.py`
Expected: FAIL (`Settings` has no `oidc`).

- [ ] **Step 3: Implement**

After `AuthSettings`:

```python
class OidcSettings(Section):
    # Every cross-field rule lives in oidc_problem(), not a model validator:
    # load() validates the config file on its own before env and args merge in,
    # and the cluster sets `oidc` in the file with the token in env.
    issuer: str | None = Field(
        None, description="The issuer a JWT must name; setting it turns OIDC on."
    )
    audience: str | None = Field(None, description="The audience a JWT must include.")
    jwks_uri: str | None = Field(
        None, description="Where the issuer publishes its signing keys."
    )
    # NoDecode, like security.frame_ancestors: comma-separated in env.
    roles: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="A JWT must hold one of these roles; empty checks none.",
    )
    roles_claim: str = Field(
        "roles", description="The claim holding the roles; dots walk into objects."
    )

    @field_validator("roles", mode="before")
    @classmethod
    def _split(cls, value):
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, list):
            return value
        return [
            part.strip() if isinstance(part, str) else part
            for part in value
            if not (isinstance(part, str) and not part.strip())
        ]

    @field_validator("issuer", "jwks_uri")
    @classmethod
    def _http(cls, value):
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("must be an http(s) URL")
        return value
```

In `Settings`, directly after `auth`:

```python
    oidc: OidcSettings = Field(
        default_factory=OidcSettings,
        description="Accept a JWT from an OIDC issuer beside the token.",
    )
```

`URL_LEAVES` gains `"oidc.issuer"` and `"oidc.jwks_uri"`.

Before `load()`:

```python
def oidc_problem(settings: Settings) -> str | None:
    """Why this server's OIDC cannot run, or None. Checked after every layer merges."""
    oidc = settings.oidc
    named = [oidc.issuer, oidc.audience, oidc.jwks_uri]
    if not any(named):
        return None
    if not all(named):
        # An issuer without an audience accepts any token that issuer ever minted.
        return "oidc.issuer, oidc.audience and oidc.jwks_uri are set together or not at all"
    if not (settings.auth.token and settings.auth.token.get_secret_value()):
        # The token is the admin login and the signing key; without it the REST
        # and admin routes are open, and OIDC must not be a way to get there.
        return "oidc needs auth.token: the token stays the admin login"
    return None
```

At the end of `load()`, after `settings = Settings.model_validate(merged)` succeeds and before `return`:

```python
    problem = oidc_problem(settings)
    if problem:
        raise ConfigError(problem)
```

- [ ] **Step 4: Run the new tests and the config suites**

Run: `PYTHONPATH=$PY:. $PY/bin/pytest -q tests/test_config_oidc.py tests/test_config_schema.py tests/test_config_load.py tests/test_admin_settings.py tests/test_config_wiring.py`
Expected: PASS. If `test_admin_settings.py` or a golden lists every section, the new `oidc` card is the expected difference: update the expectation to include it and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/config.py tests/test_config_oidc.py tests/test_admin_settings.py
git commit -m "An oidc section: issuer, audience, JWKS and roles, and never without the token"
```

---

### Task 3: The verifiers and the provider

**Files:**
- Modify: `kubed/selenium_flow/http/auth.py`
- Modify: `kubed/selenium_flow/server.py:14` (import) and `:123-136` (the auth block)
- Create: `tests/jwks.py`
- Modify: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Principal`, `ADMIN`, `roles_in` (Task 1); `Settings.oidc`, `config.oidc_problem`, `config.ConfigError` (Task 2).
- Produces: `PrincipalToken(AccessToken)` with `principal: Principal`; `ServerTokenVerifier(token: str)`; `OidcVerifier(oidc: OidcSettings, *, http_client=None)`; `provider(settings: Settings, *, http_client=None) -> AuthProvider | None` (raises `ConfigError` on `oidc_problem`); `CLIENT_ID = "selenium-flow"`. Test helper `tests/jwks.py`: `Issuer` with `.issuer`, `.jwks_uri`, `.mint(**overrides) -> str`, `.settings(token, roles=("mcp",)) -> Settings`, and a pytest fixture `issuer` in `tests/conftest.py`.

- [ ] **Step 1: Write the test helper**

`tests/jwks.py`:

```python
"""A real OIDC issuer for tests: one RSA key, published over loopback HTTP.

Real HTTP on purpose: the verifier under test fetches `jwks_uri` itself, and a
mocked fetch would test the mock (memory: test the property, not the helper).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastmcp.server.auth.providers.jwt import RSAKeyPair
from joserfc.jwk import RSAKey

from kubed.selenium_flow.config import Settings

KID = "test-key"
AUDIENCE = "https://mcp.example.com"


class Issuer:
    def __init__(self):
        self.keys = RSAKeyPair.generate()
        jwk = RSAKey.import_key(self.keys.public_key).as_dict(private=False)
        body = json.dumps({"keys": [jwk | {"kid": KID, "use": "sig", "alg": "RS256"}]})

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - the stdlib's name
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        port = self.http.server_address[1]
        self.issuer = f"http://127.0.0.1:{port}/realms/example"
        self.jwks_uri = f"{self.issuer}/protocol/openid-connect/certs"

    def mint(self, *, keys: RSAKeyPair | None = None, **overrides) -> str:
        claims = {"preferred_username": "drk", "roles": ["mcp"]}
        claims |= overrides.pop("claims", {})
        return (keys or self.keys).create_token(
            subject=overrides.pop("subject", "6b0f"),
            issuer=overrides.pop("issuer", self.issuer),
            audience=overrides.pop("audience", AUDIENCE),
            expires_in_seconds=overrides.pop("expires_in_seconds", 300),
            additional_claims=claims,
            kid=KID,
        )

    def settings(self, token: str, roles=("mcp",)) -> Settings:
        return Settings(
            grid={"url": "http://grid.invalid:4444"},
            auth={"token": token},
            oidc={
                "issuer": self.issuer,
                "audience": AUDIENCE,
                "jwks_uri": self.jwks_uri,
                "roles": list(roles),
            },
        )

    def close(self):
        self.http.shutdown()
```

In `tests/conftest.py`, beside the `server` fixtures:

```python
@pytest.fixture
def issuer():
    """A loopback OIDC issuer: its JWKS is fetched for real."""
    from .jwks import Issuer

    made = Issuer()
    yield made
    made.close()
```

- [ ] **Step 2: Write the failing tests** (append to `tests/test_auth.py`)

```python
# ---- the MCP door -------------------------------------------------------------

from kubed.selenium_flow.config import ConfigError, Settings
from kubed.selenium_flow.principal import ADMIN

from .jwks import AUDIENCE


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
    from fastmcp.server.auth.providers.jwt import RSAKeyPair

    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    assert await verifier.verify_token(issuer.mint(keys=RSAKeyPair.generate())) is None


async def test_no_roles_configured_checks_none(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN, roles=()).oidc)
    assert await verifier.verify_token(issuer.mint(claims={"roles": []})) is not None


def test_the_provider_per_configuration(issuer):
    assert auth.provider(Settings()) is None
    assert isinstance(auth.provider(Settings(auth={"token": TOKEN})), auth.ServerTokenVerifier)
    both = auth.provider(issuer.settings(TOKEN))
    assert [type(v) for v in both.verifiers] == [auth.ServerTokenVerifier, auth.OidcVerifier]
    # No OAuth routes: the gateway and the issuer own discovery.
    assert both.get_routes("/mcp") == []


def test_the_provider_refuses_oidc_without_the_token(issuer):
    settings = issuer.settings(TOKEN).model_copy(update={"auth": Settings().auth})
    with pytest.raises(ConfigError, match="auth.token"):
        auth.provider(settings)
```

Replace the two old tests at the bottom of the file, which assert on
`server.mcp.auth` / `open_server.mcp.auth`, with:

```python
def test_a_token_turns_on_the_mcp_verifier(server):
    assert isinstance(server.mcp.auth, auth.ServerTokenVerifier)


def test_no_token_leaves_the_server_open(open_server):
    """`docker compose up` runs without a token on purpose."""
    assert open_server.mcp.auth is None
    assert open_server.auth_token is None
```

- [ ] **Step 3: Run them to see them fail**

Run: `PYTHONPATH=$PY:. $PY/bin/pytest -q tests/test_auth.py`
Expected: FAIL (`auth.ServerTokenVerifier` missing).

- [ ] **Step 4: Implement** — append to `http/auth.py` (update its module docstring's last paragraph to say it also builds the MCP door's verifiers)

```python
from fastmcp.server.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier

from .. import config
from ..principal import ADMIN, Principal, roles_in

# The client id the server token reports as. Shown nowhere; FastMCP needs one.
CLIENT_ID = "selenium-flow"


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
        return PrincipalToken(token=token, client_id=CLIENT_ID, scopes=[], principal=ADMIN)


class OidcVerifier(JWTVerifier):
    """A JWT from the configured issuer: signature, iss, aud, exp, then a role.

    No routes: the gateway serves the protected-resource metadata and the issuer
    serves the rest. A missing role is a refusal like any other, so a 401.
    """

    def __init__(self, oidc: config.OidcSettings, *, http_client=None):
        super().__init__(
            jwks_uri=oidc.jwks_uri,
            issuer=oidc.issuer,
            audience=oidc.audience,
            algorithm="RS256",
            http_client=http_client,
        )
        self._roles = frozenset(oidc.roles)
        self._roles_claim = oidc.roles_claim

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await super().verify_token(token)
        if verified is None:
            return None
        claims = verified.claims
        if self._roles and not self._roles.intersection(roles_in(claims, self._roles_claim)):
            return None
        return PrincipalToken(
            **verified.model_dump(),
            principal=Principal.from_claims(claims, self._roles_claim),
        )


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
```

Check the import path of `AccessToken`/`TokenVerifier`/`MultiAuth` against the
installed package (`python -c "from fastmcp.server.auth import MultiAuth, TokenVerifier, AccessToken"`);
if `fastmcp.server.auth` does not re-export one, import it from
`fastmcp.server.auth.auth`. Do not import `config` at module top if it creates
a cycle (`config` imports nothing from `http`, so it should not).

In `server.py`, drop the `StaticTokenVerifier` import and replace the block at
`:123-130` with:

```python
        # The token turns on auth for both surfaces; `oidc` adds a JWT beside it
        # on /mcp only. Absent, the server is open — correct for a local
        # `docker compose up`, and the reason the deployment always sets one.
        auth = http_auth.provider(settings)
```

importing `from .http import auth as http_auth` (check the name is free in
`server.py`; the existing `from .http import access_log, admin, files` line is
where it goes).

- [ ] **Step 5: Run the auth tests, then everything that builds a server**

Run: `PYTHONPATH=$PY:. $PY/bin/pytest -q -x tests/test_auth.py tests/test_routes.py tests/test_http_answer.py tests/test_files_and_admin.py tests/test_golden.py`
Expected: PASS. Then the whole unit suite: `PYTHONPATH=$PY:. $PY/bin/pytest -q -x -n 4` — PASS.

- [ ] **Step 6: Commit**

```bash
git add kubed/selenium_flow/http/auth.py kubed/selenium_flow/server.py tests/jwks.py tests/conftest.py tests/test_auth.py
git commit -m "/mcp takes the token or a JWT from the oidc issuer, and the token compares in constant time"
```

---

### Task 4: The principal reaches the caller, and `session://current`

**Files:**
- Modify: `kubed/selenium_flow/session/sessions.py` (`Caller` ~line 109; `describe()` ~line 284)
- Modify: `kubed/selenium_flow/mcp/clients.py` (`caller()` ~line 62; new `principal()`)
- Create: `tests/test_oidc_http.py`
- Modify: whichever existing tests assert the whole `describe()` dict (find with `grep -rn '"named_by"' tests`), adding `"principal"`.

**Interfaces:**
- Consumes: `PrincipalToken` (Task 3), `Principal` (Task 1), the `issuer` fixture (Task 3).
- Produces: `Caller.principal: Principal | None = None`; `clients.principal() -> Principal | None`; `session://current` key `"principal"` (`Principal.status()` or `None`).

- [ ] **Step 1: Write the failing tests** — `tests/test_oidc_http.py`, through the real entry point

```python
"""The MCP door over real HTTP: a JWT beside the token, and who it says you are.

Through `server.mcp.http_app()` with FastMCP's own client, so the bearer
middleware, the verifiers and `get_access_token()` all run as deployed. The
issuer's JWKS is fetched over loopback (tests/jwks.py).
"""

import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.testclient import TestClient

from kubed.selenium_flow.mcp import mirror
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

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


def test_a_jwt_does_not_open_a_rest_route(oidc_server, issuer):
    """The round's boundary: the REST routes stay token-only."""
    with TestClient(oidc_server.mcp.http_app()) as client:
        answer = client.get(
            "/browser/url", headers={"Authorization": f"Bearer {issuer.mint()}"}
        )
    assert answer.status_code == 401


async def current(server, bearer):
    app = server.mcp.http_app()

    def factory(**kwargs):
        kwargs.pop("auth", None)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", **kwargs
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
```

Plus one unit test in `tests/test_sessions.py` beside the other `describe`
tests: an open server's caller (`Caller.from_request({}, {"x-session-key": ["desk"]})`)
describes with `"principal": None`.

Before writing `current()`, check how `tests/test_resources.py::read` unwraps
`mirror.READ_TOOL`'s result and reuse that unwrapping exactly; the
`json.loads(result.content[0].text)` above is the expected shape, not a promise.
If `StreamableHttpTransport`'s factory signature differs in the installed
fastmcp (`McpHttpClientFactory`), match it; the requirement is real HTTP
through the ASGI app with the bearer and `X-Session-Key` headers.

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=$PY:. $PY/bin/pytest -q tests/test_oidc_http.py`
Expected: the status-code tests PASS already (Task 3 wired the door); the two
`session_current` tests FAIL with `KeyError: 'principal'`.

- [ ] **Step 3: Implement**

`Caller` (frozen dataclass), new last field, and a docstring line:

```python
    # Who the verified credential says this is (principal.py), or None on an
    # open server. Carried, not consulted: nothing decides anything from it yet.
    principal: Principal | None = None
```

with `from ..principal import Principal` at the top of `session/sessions.py`.

`describe()`, right after `"named_by"`:

```python
            "principal": caller.principal.status() if caller.principal else None,
```

`mcp/clients.py`:

```python
from dataclasses import replace

from fastmcp.server.dependencies import get_access_token

from ..principal import Principal


def principal() -> Principal | None:
    """Who this request's verified bearer is, or None on an open server."""
    try:
        token = get_access_token()
    except Exception:  # noqa: BLE001 - off a request there is no bearer
        return None
    return getattr(token, "principal", None)
```

and in `caller()`, the HTTP branch becomes:

```python
    return replace(Caller.from_request(*values, client=client), principal=principal())
```

(stdio stays `Caller.stdio(client=client)`: a stdio process has no bearer.)

- [ ] **Step 4: Run the new tests and the session/resource suites**

Run: `PYTHONPATH=$PY:. $PY/bin/pytest -q -x tests/test_oidc_http.py tests/test_sessions.py tests/test_resources.py tests/test_site_data_surface.py tests/test_files_and_admin.py`
Expected: PASS. Fix every full-dict `describe()` assertion the grep found by
adding `"principal": None` (open server) or `{"kind": "admin"}` (token server).
Then the whole unit suite, `-n 4`: PASS.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/session/sessions.py kubed/selenium_flow/mcp/clients.py tests/
git commit -m "session://current says who the bearer is: the admin, or the OIDC subject"
```

---

### Task 5: Documentation

**Files:**
- Modify: `README.md` (the auth section, ~line 265), `AGENTS.md` (the auth rules ~lines 133-137 and the session rules ~300-313), `CHANGELOG.md` (`## [Unreleased]`)
- Modify (submodule): `wiki/Configuration.md`, `wiki/Deployment.md`

- [ ] **Step 1: README** — under the existing token paragraph, add (advertise, don't explain):

```markdown
Behind an OIDC gateway, `/mcp` also takes your identity provider's JWT. Set the
issuer, and the server checks the signature, audience, expiry and role itself:

    OIDC_ISSUER=https://auth.example.com/realms/example
    OIDC_AUDIENCE=https://mcp.example.com
    OIDC_JWKS_URI=https://auth.example.com/realms/example/protocol/openid-connect/certs
    OIDC_ROLES=mcp

The token keeps working beside it, and stays the admin's.
```

- [ ] **Step 2: AGENTS.md** — in the auth rules, add:
  - `http/auth.py` also builds `/mcp`'s verifier (`provider()`): the token alone, or `MultiAuth[token, OidcVerifier]`. A third credential goes there too.
  - The verifiers return a `PrincipalToken`; `Caller.principal` carries it. **It decides nothing yet** — ownership, per-tool roles and the admin UI's OIDC sign-in are the next round's, and the spec lists them.
  - The REST routes, admin API and signed links are token-only **on purpose**: the gateway only routes `/mcp`.
  - `oidc` without `auth.token` is a `ConfigError`, checked after the layers merge (`config.oidc_problem`).
  - Behind agentgateway, `X-Session-Key` crosses and `?session=` does not.

- [ ] **Step 3: CHANGELOG** (`## [Unreleased]`, top):

```markdown
- `/mcp` accepts a JWT from an OIDC issuer beside the token (`oidc.issuer`, `oidc.audience`, `oidc.jwks_uri`, `oidc.roles`), for a server behind an OIDC gateway.
- The token is compared in constant time on `/mcp` too.
- `session://current` says who the caller is: `admin` for the token, or the OIDC subject.
```

- [ ] **Step 4: wiki** — `Configuration.md`: an `oidc` section in the same table format as its neighbours (all five keys, env names, defaults; "needs `auth.token`"). `Deployment.md`: a short "Behind agentgateway" part: `backend.auth.passthrough: {}`, no `mcp.provider` for Keycloak, use the `X-Session-Key` header. Commit inside `wiki/`, then commit the submodule pointer in the main repo. **Push the wiki before the main branch** (a `Test` failure with no pytest output is an unpushed submodule SHA).

- [ ] **Step 5: Check and commit**

Run the Global Constraints grep (the homelab's domain, the maintainer's name) over the repo and inside `wiki/` — expect nothing.

```bash
git add README.md AGENTS.md CHANGELOG.md wiki
git commit -m "Docs: OIDC beside the token, and what the principal does not do yet"
```

---

### Task 6: The PR

- [ ] **Step 1:** Full local gate: `ruff check kubed tests`, `ruff format --check kubed tests`, the whole unit suite with `-n 4`. All green.
- [ ] **Step 2:** Push the wiki submodule, then `git push -u origin oidc`.
- [ ] **Step 3:** PR #54 is open as a draft (2026-10-04). Update its body with the three CHANGELOG lines and mark it ready for review (`gh pr ready 54`).
- [ ] **Step 4:** Watch `test`, `ui`, `package`, `quality`, `integration`, `bench`. Answer every Copilot and code-scanning thread with `gh` (fix, or decline with evidence) until none is open.
- [ ] **Step 5:** Build the branch image: `gh workflow run image.yml --ref oidc -f push=true` (check the input name in `.github/workflows/image.yml` first); wait for `:oidc`.

---

### Task 7: The gateway hands on the caller's JWT (cluster files, no git)

**Already live (2026-10-04, before this round's code):**
`apps/selenium/components/mcp/gateway.yaml` registers selenium-flow at
`https://mcp.<domain>/selenium-flow/mcp`: `AgentgatewayBackend`, `HTTPRoute`
and an `AgentgatewayPolicy` that validates the Keycloak JWT (Strict, the `mcp`
role) and then sends the backend the **server token** from the
`selenium-flow-auth` Secret (`backend.auth.secretRef`, key `MCP_AUTH_TOKEN`).
That is the pattern for any token-protected MCP server; selenium-flow sees the
admin until this task.

**Dr K's rule: the cluster repo gets no git from this round.** Edit its files and
apply them; never stage, commit or push there.

- [ ] **Step 1: `gateway.yaml`** — replace the policy's `backend.auth.secretRef`
  block with `passthrough: {}` and update its comment (the server now verifies
  the JWT itself, and sees who is calling).
- [ ] **Step 2: `config.yaml`** gains the `oidc:` block (real values: the issuer
  and JWKS URL in `gateway.yaml`'s `jwtAuthentication`):

```yaml
# A Keycloak JWT on /mcp beside the token, handed on by the agent gateway.
oidc:
  issuer: https://auth.<domain>/realms/<realm>
  audience: https://mcp.<domain>
  jwks_uri: https://auth.<domain>/realms/<realm>/protocol/openid-connect/certs
  roles:
  - mcp
```

  Check the pod can reach the JWKS URL first:
  `kubectl exec -n flow deploy/selenium-flow -- python -c "import urllib.request;print(urllib.request.urlopen('<jwks url>').status)"`.
- [ ] **Step 3:** `kustomization.yaml` `images:` → `newTag: oidc` for the live test.
- [ ] **Step 4: Plan, then apply.** `kubectl plan apps/selenium`: expect the
  policy, the ConfigMap hash and the image tag. If the Grid node Deployments show
  `replicas 1 → 0`, a browser is live: tell Dr K and `end_browser` first. Then
  `kubectl up apps/selenium` and `kubectl get agentgatewaypolicy -n flow`
  (Accepted/Attached).

---

### Task 8: The live acceptance test

- [ ] **Step 1:** `curl -s https://mcp.<domain>/.well-known/oauth-protected-resource/selenium-flow/mcp` names the Keycloak realm; `curl -si -X POST https://mcp.<domain>/selenium-flow/mcp` is 401 with `WWW-Authenticate`.
- [ ] **Step 2:** `.mcp.json`: the `selenium-flow` entry becomes

```json
"selenium-flow": {
  "type": "http",
  "url": "https://mcp.<domain>/selenium-flow/mcp",
  "headers": {"X-Session-Key": "claudecode"}
}
```

Dr K authenticates it in Claude Code (`/mcp` → selenium-flow → authenticate; paste the localhost callback into the box, as for kb).
- [ ] **Step 3:** Read `session://current`: `principal.kind == "oidc"`, Dr K's username, `session == "claudecode"`.
- [ ] **Step 4:** What only shows live: the tool list matches the client (the mirror tools hidden per `clientInfo` across the gateway); a 30 s `assert` and a `run_flow` complete through the gateway; a screenshot link opens; an MCP Apps view renders where the client supports it. Record each result in the spec's status line.
- [ ] **Step 5:** In-cluster: the server token still works on the Service; a JWT missing the `mcp` role is 401 there (mint one by requesting a token for a user without the role, or skip with a note if no such user exists).
- [ ] **Step 6:** Report to Dr K. After merge (his call): `newTag: main` back in the kustomization and `kubectl up apps/selenium` (browsers first again). No git in the cluster repo.
