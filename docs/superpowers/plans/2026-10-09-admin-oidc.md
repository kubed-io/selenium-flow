# The admin UI signs in with OIDC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person whose issuer account holds an admin role signs in to the admin UI with *Sign in with OIDC* (Authorization Code + PKCE, in the page) and gets everything a token holder gets on `/admin/*`; the token keeps working, and nothing outside `/admin/*` changes who it lets in.

**Architecture:** `http/auth.py` builds both doors from one `OidcVerifier`: `/mcp` as today, and a new `AdminDoor` that admits the token or a JWT whose `azp` is the admin UI's client and whose roles include one of `oidc.admin_roles` (401/403). `answer.guarded` and the event stream ask that door. The page receives `{issuer, client_id}` in a `data-oidc` attribute and runs the whole flow itself in `ui/src/admin/oidc.ts`, keeping tokens in memory. Each workspace records who opened its browser (`opened_by`), shown on the live list.

**Tech Stack:** Python 3.10–3.14, FastMCP 4 (`JWTVerifier`, `MultiAuth`), Starlette, pydantic-settings, pytest (asyncio auto); Svelte 5, TypeScript, WebCrypto, vitest + jsdom, svelte-check, eslint.

**Spec:** `docs/superpowers/specs/2026-10-09-admin-oidc-design.md` (programme `2026-10-09-workspaces-and-observability-design.md`, E5, R13–R14; groundwork `2026-10-04-oidc-groundwork-design.md`).

**Assumes E1 (#60) merged:** the code is the post-rename tree — `workspace/`, `Workspaces`, `Workspace`, `X-Workspace`, `/admin/workspaces/…`, `http/admin/workspaces.py`. Rebase on `main` once #60 lands and before Task 1.

## Global Constraints

Verbatim from the spec's *Constraints*:

- Every rule in `AGENTS.md` stays. One place decides whether a request is
  authorised: `http/auth.py`. The token comparison is `hmac.compare_digest`.
- The token never goes away: it stays the admin login, the HMAC key for signed
  links and the `events_url`, and works with or without OIDC.
- Only `/admin/*` and `/admin/events` admit an admin JWT. The REST routes stay
  token-only; `test_a_jwt_does_not_open_a_rest_route` keeps passing, with an
  admin-UI JWT added to it.
- One `OidcVerifier` per server, shared by `/mcp` and the admin door: one JWKS
  cache and one refetch floor.
- No new route: the admin route-table golden (`tests/golden/admin-routes.json`)
  does not change.
- No cookie and no server-side session.
- No token, verifier or code is ever logged, stored on the server, or put in
  `localStorage`; refusals log the subject only, as today.
- Without `oidc.client_id` every behaviour is today's, the login card included.
- The repo names no real host: `example.com`, realm `example`, client
  `selenium-flow-admin`.
- Refusal texts, verbatim:
  - `401 {"error": "unauthorized"}` (unchanged)
  - `403 {"error": "this sign-in does not hold an admin role"}`
  - `oidc.client_id and oidc.admin_roles are set together or not at all`
  - `oidc.client_id needs oidc.issuer, oidc.audience and oidc.jwks_uri: the admin UI signs in with the issuer /mcp trusts`
- CHANGELOG: one line under `[Unreleased]`.

## Commands

Run from the repository root (`$R`). Local runs put the checkout ahead of any installed `kubed`:

```bash
export R=$(git rev-parse --show-toplevel)
export PYTHONPATH=$R:$PYTHONPATH            # plus the deps dir, where one is used
# one file
python3 -m pytest -q -p no:randomly tests/test_x.py
# whole unit suite
python3 -m pytest -q -p no:randomly -n 4 --ignore=tests/integration --ignore=tests/bench
# goldens: rewrite, then READ the diff before committing
GOLDEN_UPDATE=1 python3 -m pytest -q -p no:randomly tests/test_golden.py
python3 -m ruff check kubed tests scripts && python3 -m ruff format --check kubed tests scripts
# UI (npm --prefix ui ci first if ui/node_modules is missing)
npm --prefix ui test && npm --prefix ui run -s check && npm --prefix ui run -s lint
```

Record the baseline (pass/skip counts for both suites) before Task 1. Before every commit, run `python3 -m ruff format` on the Python files the task touched: CI checks the format, and the code below is written to it but not machine-formatted.

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `kubed/selenium_flow/config.py` | modify | `oidc.client_id`, `oidc.admin_roles`; their rules in `oidc_problem` |
| `kubed/selenium_flow/http/auth.py` | modify | `OidcVerifier.verify_jwt`; `Refused`, `AdminDoor`, `Doors`, `doors()`; `provider()` via `doors()` |
| `kubed/selenium_flow/http/answer.py` | modify | `guarded(door)` awaits the door; a REST caller carries `ADMIN` when there is a token |
| `kubed/selenium_flow/http/admin/__init__.py` | modify | `register(..., door=, oidc_page=)` |
| `kubed/selenium_flow/http/admin/workspaces.py` | modify | the event stream asks the door; rows carry `opened_by` |
| `kubed/selenium_flow/http/admin/page.py` | modify | `sign_in(oidc)`; `mount(..., oidc=)` fills `__OIDC__` |
| `kubed/selenium_flow/server.py` | modify | builds `Doors` once; passes `door` and `oidc_page` |
| `kubed/selenium_flow/principal.py` | modify | `Principal.opener()` |
| `kubed/selenium_flow/workspace/store.py` | modify | `Workspace.opened_by`, read back by `from_json` |
| `kubed/selenium_flow/workspace/workspaces.py` | modify | `remember(..., opened_by=KEEP)`; `open_browser` records it |
| `ui/public/admin.html` | modify | `data-oidc="__OIDC__"` |
| `ui/src/admin.ts` | modify | passes `oidc` to `Admin` |
| `ui/src/admin/oidc.ts` | create | the flow: config, discovery, PKCE, begin, complete, refresh |
| `ui/src/admin/api.ts` | modify | `ApiError` with `status` |
| `ui/src/admin/Login.svelte` | modify | the `oidc` row, the message line |
| `ui/src/admin/Admin.svelte` | modify | boot order, memory tokens, renewal, sign-out |
| `ui/src/lib/types.ts`, `ui/src/lib/format.ts` | modify | `opened_by`; `metaLine` says `by …` |
| `ui/src/test/setup.ts`, `ui/src/test/helpers.ts` | modify | WebCrypto under jsdom; form bodies; `fakeJwt` |
| `tests/jwks.py` | modify | `ADMIN_CLIENT`, `settings(admin_roles=)`, `mint_admin()` |
| `tests/conftest.py` | modify | the stand-in shell carries `data-oidc` |
| `tests/test_config_oidc.py`, `tests/test_auth.py`, `tests/test_oidc_http.py`, `tests/test_ui_serving.py`, `tests/test_workspaces.py`, `tests/test_http_answer.py` | modify | the tests below |
| `tests/golden/admin-workspaces.json` | regenerate | `"opened_by": null` per row |
| `ui/src/admin/oidc.test.ts`, `ui/src/admin/Login.test.ts`, `ui/src/admin/AdminOidc.test.ts` | create | UI tests |
| `ui/src/admin/api.test.ts`, `ui/src/lib/format.test.ts` | modify | UI tests |
| `README.md`, `AGENTS.md`, `CHANGELOG.md`, `wiki/` | modify | Task 11 |

---

### Task 1: The two settings and their rules

**Files:**
- Modify: `kubed/selenium_flow/config.py` (`OidcSettings`, `Settings.oidc` description, `oidc_problem`)
- Test: `tests/test_config_oidc.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_config_oidc.py`, add below `ON`:

```python
ADMIN_UI = {"OIDC_CLIENT_ID": "selenium-flow-admin", "OIDC_ADMIN_ROLES": "admin, ops,"}
```

Replace `test_off_by_default` and `test_the_env_names_follow_the_rule`:

```python
def test_off_by_default():
    oidc = Settings().oidc
    assert (oidc.issuer, oidc.audience, oidc.jwks_uri, oidc.roles) == (
        None,
        None,
        None,
        [],
    )
    assert oidc.roles_claim == "roles"
    assert (oidc.client_id, oidc.admin_roles) == (None, [])
    assert config.oidc_problem(Settings()) is None


def test_the_env_names_follow_the_rule():
    envs = {leaf.env for leaf in config.leaves() if leaf.section == "oidc"}
    assert envs == {
        "OIDC_ISSUER",
        "OIDC_AUDIENCE",
        "OIDC_JWKS_URI",
        "OIDC_ROLES",
        "OIDC_ROLES_CLAIM",
        "OIDC_CLIENT_ID",
        "OIDC_ADMIN_ROLES",
    }
```

And append:

```python
def test_the_admin_ui_settings_load_beside_the_issuer():
    oidc = load([], ON | ADMIN_UI).settings.oidc
    assert oidc.client_id == "selenium-flow-admin"
    assert oidc.admin_roles == ["admin", "ops"]


@pytest.mark.parametrize("missing", ["OIDC_CLIENT_ID", "OIDC_ADMIN_ROLES"])
def test_client_id_and_admin_roles_come_together(missing):
    env = {k: v for k, v in (ON | ADMIN_UI).items() if k != missing}
    with pytest.raises(
        ConfigError,
        match="oidc.client_id and oidc.admin_roles are set together or not at all",
    ):
        load([], env)


def test_the_admin_ui_needs_the_issuer():
    with pytest.raises(ConfigError, match=r"oidc\.client_id needs oidc\.issuer"):
        load([], ADMIN_UI | {"AUTH_TOKEN": "t0ken"})


def test_the_admin_ui_settings_show_on_the_settings_tab():
    loaded = load([], ON | ADMIN_UI)
    rows = {
        row["key"]: row
        for section in config.describe(loaded.settings, loaded.sources)["sections"]
        for row in section["settings"]
    }
    assert rows["oidc.client_id"]["value"] == "selenium-flow-admin"
    assert rows["oidc.admin_roles"]["value"] == ["admin", "ops"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_config_oidc.py`
Expected: FAIL — `AttributeError: 'OidcSettings' object has no attribute 'client_id'`, and the env-name set missing two names.

- [ ] **Step 3: Implement**

In `OidcSettings` (after `roles_claim`):

```python
    client_id: str | None = Field(
        None,
        description=(
            "The admin UI's public client; setting it offers Sign in with OIDC."
        ),
    )
    # NoDecode, like `roles`: comma-separated in env.
    admin_roles: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="A JWT from the admin UI holding one of these is the admin.",
    )
```

Widen the existing split validator to both lists:

```python
    @field_validator("roles", "admin_roles", mode="before")
    @classmethod
    def _split(cls, value):
```

In `Settings`, the section's description:

```python
    oidc: OidcSettings = Field(
        default_factory=OidcSettings,
        description=(
            "Accept a JWT from an OIDC issuer beside the token, "
            "on /mcp and in the admin UI."
        ),
    )
```

Replace `oidc_problem`:

```python
def oidc_problem(settings: Settings) -> str | None:
    """Why this server's OIDC cannot run, or None. Checked after every layer merges."""
    oidc = settings.oidc
    if bool(oidc.client_id) != bool(oidc.admin_roles):
        # An empty list would let nobody in — or, read the other way, everybody.
        return "oidc.client_id and oidc.admin_roles are set together or not at all"
    named = [oidc.issuer, oidc.audience, oidc.jwks_uri]
    if not any(named):
        if oidc.client_id:
            return (
                "oidc.client_id needs oidc.issuer, oidc.audience and "
                "oidc.jwks_uri: the admin UI signs in with the issuer /mcp trusts"
            )
        return None
    if not all(named):
        # An issuer without an audience accepts any token that issuer ever minted.
        return (
            "oidc.issuer, oidc.audience and oidc.jwks_uri "
            "are set together or not at all"
        )
    if not (settings.auth.token and settings.auth.token.get_secret_value()):
        # The token is the admin login and the signing key; without it the REST
        # and admin routes are open, and OIDC must not be a way to get there.
        return "oidc needs auth.token: the token stays the admin login"
    return None
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest -q -p no:randomly tests/test_config_oidc.py tests/test_config_schema.py tests/test_config_load.py tests/test_admin_settings.py`
Expected: PASS. If a golden of the settings payload or the wiki check fails only because two rows and one description were added, regenerate it (`GOLDEN_UPDATE=1`) and read the diff: it must show exactly the two new leaves and the new description.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/config.py tests/test_config_oidc.py tests/golden
git commit -m "Admin OIDC: oidc.client_id and oidc.admin_roles, set together, only with the issuer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The verifier's check without its role

**Files:**
- Modify: `kubed/selenium_flow/http/auth.py` (`OidcVerifier`)
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py` (it already has `_raw`, `issuer`, `TOKEN`):

```python
async def test_verify_jwt_checks_everything_but_the_role(issuer):
    """The admin door applies its own roles; `oidc.roles` is `/mcp`'s gate."""
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)  # roles: mcp
    admin_only = issuer.mint(claims={"roles": ["admin"]})
    assert await verifier.verify_token(admin_only) is None
    verified = await verifier.verify_jwt(admin_only)
    assert verified.principal.roles == ("admin",)
    assert verified.principal.username == "drk"


@pytest.mark.parametrize(
    "overrides",
    [
        {"audience": "https://other.example.com"},
        {"issuer": "https://auth.example.com/realms/other"},
        {"expires_in_seconds": -60},
    ],
    ids=["audience", "issuer", "expired"],
)
async def test_verify_jwt_still_refuses_a_bad_jwt(issuer, overrides):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    assert await verifier.verify_jwt(issuer.mint(**overrides)) is None


async def test_verify_jwt_still_refuses_a_jwt_without_exp(issuer):
    verifier = auth.OidcVerifier(issuer.settings(TOKEN).oidc)
    assert await verifier.verify_jwt(_raw(issuer)) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_auth.py -k verify_jwt`
Expected: FAIL — `AttributeError: 'OidcVerifier' object has no attribute 'verify_jwt'`.

- [ ] **Step 3: Implement**

Replace `OidcVerifier.verify_token` with the two methods:

```python
    async def verify_jwt(self, token: str) -> PrincipalToken | None:
        """Signature, iss, aud, exp and nbf: everything but a role.

        The admin door asks this and applies its own roles (spec
        2026-10-09-admin-oidc, ruling 5); `/mcp` asks `verify_token`, which
        adds `oidc.roles`. One instance serves both, so one JWKS cache and one
        refetch floor.
        """
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
        return PrincipalToken(
            **verified.model_dump(),
            principal=Principal.from_claims(claims, self._roles_claim),
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await self.verify_jwt(token)
        if verified is None:
            return None
        if self._roles and not self._roles.intersection(verified.principal.roles):
            _log_refusal("no allowed role", verified.claims)
            return None
        return verified
```

`auth.py` no longer reads the claim itself (`Principal.from_claims` does, through `roles_in`), so its import becomes:

```python
from ..principal import ADMIN, Principal
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest -q -p no:randomly tests/test_auth.py tests/test_oidc_http.py`
Expected: PASS — the new three, and every existing verifier and floor test unchanged.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/http/auth.py tests/test_auth.py
git commit -m "Admin OIDC: OidcVerifier.verify_jwt, the check without /mcp's role

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The admin door, and both doors from one verifier

**Files:**
- Modify: `kubed/selenium_flow/http/auth.py`
- Modify: `tests/jwks.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Extend the loopback issuer**

In `tests/jwks.py`, below `AUDIENCE`:

```python
ADMIN_CLIENT = "selenium-flow-admin"
```

Add to `Issuer`:

```python
    def mint_admin(self, *, roles=("admin",), azp=ADMIN_CLIENT, **overrides) -> str:
        """A token the admin UI's client would hold: its `azp`, these roles."""
        claims = {"azp": azp, "roles": list(roles)} | overrides.pop("claims", {})
        return self.mint(claims=claims, **overrides)
```

Replace `settings`:

```python
    def settings(self, token: str, roles=("mcp",), admin_roles=()) -> Settings:
        oidc = {
            "issuer": self.issuer,
            "audience": AUDIENCE,
            "jwks_uri": self.jwks_uri,
            "roles": list(roles),
        }
        if admin_roles:
            oidc |= {"client_id": ADMIN_CLIENT, "admin_roles": list(admin_roles)}
        return Settings(
            grid={"url": "http://grid.invalid:4444"},
            auth={"token": token},
            oidc=oidc,
        )
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_auth.py`:

```python
def _bearer(value: str) -> Request:
    return _request({"Authorization": f"Bearer {value}"})


def _admin_door(issuer):
    return auth.doors(issuer.settings(TOKEN, admin_roles=("admin",))).admin


async def test_the_admin_door_on_an_open_server_admits_everyone():
    assert await auth.AdminDoor(None).admit(_request({})) is None


async def test_the_admin_door_admits_the_token_as_the_admin():
    assert await auth.AdminDoor(TOKEN).admit(_bearer(TOKEN)) == ADMIN


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}])
async def test_the_admin_door_refuses_an_unknown_credential_with_a_401(headers):
    with pytest.raises(auth.Refused) as refused:
        await auth.AdminDoor(TOKEN).admit(_request(headers))
    assert (refused.value.status, refused.value.reason) == (401, "unauthorized")


async def test_an_admin_ui_jwt_holding_an_admin_role_is_the_admin(issuer):
    """No `mcp` role needed: `oidc.roles` is `/mcp`'s gate, not this one's."""
    principal = await _admin_door(issuer).admit(_bearer(issuer.mint_admin()))
    assert (principal.kind, principal.username) == ("oidc", "drk")


async def test_another_clients_jwt_is_a_401(issuer, caplog):
    """Claude Code's token may hold `admin` too; it was not minted for this UI."""
    bearer = issuer.mint_admin(azp="claude-code")
    with (
        caplog.at_level(logging.INFO, logger=auth.__name__),
        pytest.raises(auth.Refused) as refused,
    ):
        await _admin_door(issuer).admit(_bearer(bearer))
    assert refused.value.status == 401
    assert "not the admin UI's client" in caplog.text
    assert bearer not in caplog.text


async def test_an_admin_ui_jwt_without_an_admin_role_is_a_403(issuer):
    with pytest.raises(auth.Refused) as refused:
        await _admin_door(issuer).admit(_bearer(issuer.mint_admin(roles=("mcp",))))
    assert (refused.value.status, refused.value.reason) == (
        403,
        "this sign-in does not hold an admin role",
    )


async def test_without_a_client_id_a_jwt_never_opens_the_admin_api(issuer):
    door = auth.doors(issuer.settings(TOKEN)).admin
    with pytest.raises(auth.Refused) as refused:
        await door.admit(_bearer(issuer.mint_admin()))
    assert refused.value.status == 401


def test_both_doors_share_one_verifier(issuer):
    built = auth.doors(issuer.settings(TOKEN, admin_roles=("admin",)))
    assert built.admin._jwt is built.mcp.verifiers[1]


def test_the_doors_per_configuration():
    assert auth.doors(Settings()).mcp is None
    token_only = auth.doors(Settings(auth={"token": TOKEN}))
    assert isinstance(token_only.mcp, auth.ServerTokenVerifier)
    assert token_only.admin._jwt is None
```

- [ ] **Step 3: Run them to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_auth.py -k "door or doors"`
Expected: FAIL — `AttributeError: module 'kubed.selenium_flow.http.auth' has no attribute 'AdminDoor'`.

- [ ] **Step 4: Implement**

In `kubed/selenium_flow/http/auth.py`, add to the imports:

```python
from collections.abc import Iterable
from dataclasses import dataclass
```

Update the module docstring's last paragraph:

```python
It also builds both doors that can take a JWT from the configured OIDC issuer:
`/mcp`'s verifiers, and the admin API's `AdminDoor` (spec
2026-10-09-admin-oidc). One `OidcVerifier` serves both.
```

Below `_log_refusal`, add:

```python
UNAUTHORIZED = "unauthorized"
NOT_ADMIN = "this sign-in does not hold an admin role"


class Refused(Exception):
    """The admin door's no: 401 for a credential it does not know, 403 for one it
    knows that is not enough (spec 2026-10-09-admin-oidc, ruling 6)."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class AdminDoor:
    """Who may use the admin API and its event stream.

    The server token, as ever; and, when the admin UI signs in with OIDC
    (`oidc.client_id`), a JWT minted for that client holding one of
    `oidc.admin_roles`. The JWT check is `/mcp`'s own verifier minus its role,
    so `oidc.roles` does not apply here and one JWKS cache serves both.
    """

    def __init__(
        self,
        token: str | None,
        jwt: OidcVerifier | None = None,
        *,
        client_id: str | None = None,
        roles: Iterable[str] = (),
    ):
        self._token = token or None
        self._jwt = jwt
        self._client_id = client_id
        self._roles = frozenset(roles)

    async def admit(self, request: Request) -> Principal | None:
        """The admin this request is, None on an open server, or `Refused`."""
        if not self._token:
            return None
        bearer = presented(request)
        # Bytes: a str compare_digest raises on non-ASCII, and headers arrive latin-1.
        if hmac.compare_digest(bearer.encode(), self._token.encode()):
            return ADMIN
        if self._jwt is None or not bearer:
            raise Refused(401, UNAUTHORIZED)
        verified = await self._jwt.verify_jwt(bearer)
        if verified is None:
            raise Refused(401, UNAUTHORIZED)
        if verified.claims.get("azp") != self._client_id:
            _log_refusal("not the admin UI's client", verified.claims)
            raise Refused(401, UNAUTHORIZED)
        if not self._roles.intersection(verified.principal.roles):
            _log_refusal("no admin role", verified.claims)
            raise Refused(403, NOT_ADMIN)
        return verified.principal


@dataclass(frozen=True)
class Doors:
    """Both doors a server has, built from one verifier."""

    mcp: AuthProvider | None
    admin: AdminDoor
```

Replace `provider` with `doors` and a thin `provider`:

```python
def doors(settings: config.Settings, *, http_client=None) -> Doors:
    """`/mcp`'s verifiers and the admin door, sharing one `OidcVerifier`."""
    problem = config.oidc_problem(settings)
    if problem:
        raise config.ConfigError(problem)
    token = settings.auth.token.get_secret_value() if settings.auth.token else None
    if not token:
        return Doors(None, AdminDoor(None))
    server_token = ServerTokenVerifier(token)
    oidc = settings.oidc
    if not oidc.issuer:
        return Doors(server_token, AdminDoor(token))
    jwt = OidcVerifier(oidc, http_client=http_client)
    admin = AdminDoor(
        token,
        jwt if oidc.client_id else None,
        client_id=oidc.client_id,
        roles=oidc.admin_roles,
    )
    # The token first: a string compare, where a JWT costs a signature check.
    return Doors(MultiAuth(verifiers=[server_token, jwt]), admin)


def provider(settings: config.Settings, *, http_client=None) -> AuthProvider | None:
    """What `/mcp` checks a bearer with: nothing, the token, or the token then a JWT."""
    return doors(settings, http_client=http_client).mcp
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m pytest -q -p no:randomly tests/test_auth.py tests/test_oidc_http.py`
Expected: PASS, the existing `test_the_provider_per_configuration` included.

- [ ] **Step 6: Commit**

```bash
git add kubed/selenium_flow/http/auth.py tests/jwks.py tests/test_auth.py
git commit -m "Admin OIDC: AdminDoor admits the token or an admin-UI JWT; one verifier for both doors

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The admin API and the event stream ask the door

**Files:**
- Modify: `kubed/selenium_flow/http/answer.py` (`guarded`)
- Modify: `kubed/selenium_flow/http/admin/__init__.py` (`register`)
- Modify: `kubed/selenium_flow/http/admin/workspaces.py` (`mount`, `admin_events`)
- Modify: `kubed/selenium_flow/server.py`
- Test: `tests/test_oidc_http.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_oidc_http.py`, below the existing fixtures:

```python
@pytest.fixture
def admin_server(issuer):
    return SeleniumMCP(issuer.settings(TOKEN, admin_roles=("admin",)))


def get(server, path, bearer):
    with TestClient(server.mcp.http_app()) as client:
        return client.get(path, headers={"Authorization": f"Bearer {bearer}"})


def test_an_admin_ui_jwt_opens_the_admin_api(admin_server, issuer):
    answer = get(admin_server, "/admin/workspaces", issuer.mint_admin())
    assert answer.status_code == 200
    # Signed with the server token, as ever: the stream does not need the JWT.
    assert "sig=" in answer.json()["events_url"]


def test_a_jwt_without_an_admin_role_is_told_so(admin_server, issuer):
    answer = get(admin_server, "/admin/workspaces", issuer.mint_admin(roles=("mcp",)))
    assert answer.status_code == 403
    assert answer.json() == {"error": "this sign-in does not hold an admin role"}


def test_another_clients_jwt_is_refused_on_the_admin_api(admin_server, issuer):
    answer = get(admin_server, "/admin/settings", issuer.mint_admin(azp="claude-code"))
    assert answer.status_code == 401
    assert answer.json() == {"error": "unauthorized"}


def test_the_event_stream_asks_the_same_door(admin_server, issuer):
    roleless = issuer.mint_admin(roles=("mcp",))
    assert get(admin_server, "/admin/events", roleless).status_code == 403


def test_the_token_still_opens_the_admin_api(admin_server):
    assert get(admin_server, "/admin/workspaces", TOKEN).status_code == 200


@pytest.mark.parametrize(("method", "path"), TREES)
def test_an_admin_ui_jwt_does_not_open_a_rest_route(admin_server, issuer, method, path):
    """The REST routes stay token-only, whoever the JWT is."""
    with TestClient(admin_server.mcp.http_app()) as client:
        answer = getattr(client, method)(
            path, json={}, headers={"Authorization": f"Bearer {issuer.mint_admin()}"}
        )
    assert answer.status_code == 401


def test_one_jwks_fetch_serves_both_doors(admin_server, issuer):
    with TestClient(admin_server.mcp.http_app()) as client:
        admin = {"Authorization": f"Bearer {issuer.mint_admin()}"}
        assert client.get("/admin/settings", headers=admin).status_code == 200
        mcp = ACCEPT | {"Authorization": f"Bearer {issuer.mint()}"}
        assert client.post("/mcp", json=INIT, headers=mcp).status_code == 200
    assert issuer.fetches == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_oidc_http.py`
Expected: FAIL — the admin-UI JWT gets 401 on `/admin/workspaces`, and the roleless one 401 instead of 403.

- [ ] **Step 3: Implement `guarded`**

In `kubed/selenium_flow/http/answer.py`, replace `guarded`:

```python
def guarded(door: auth.AdminDoor) -> Callable:
    """A decorator: refuse a request the admin door does not admit.

    A decorator rather than two lines at the top of each route, and the
    reason is not the fourteen lines: every one of these returns data only an
    admin may see, so the check has to be impossible to leave out of the
    next one. Written per-route it was seven chances to forget.

    The door decides who the admin is — the token, or an admin-UI JWT holding
    an admin role (spec 2026-10-09-admin-oidc) — and its refusal is a 401 or a
    403 with its reason.
    """

    def decorate(handler):
        @wraps(handler)
        async def wrapper(request: Request):
            try:
                await door.admit(request)
            except auth.Refused as refused:
                return JSONResponse(
                    {"error": refused.reason}, status_code=refused.status
                )
            return await handler(request)

        return wrapper

    return decorate
```

- [ ] **Step 4: Implement `register` and the stream**

In `kubed/selenium_flow/http/admin/__init__.py`: import `auth` (`from .. import answer, auth, links`), add a keyword parameter after `frame_ancestors`:

```python
    door: auth.AdminDoor | None = None,
```

and replace `guarded = answer.guarded(token)` with:

```python
    # Built from the token when not given, so a caller passing only a token
    # keeps today's behaviour; the server passes the one `auth.doors` built.
    door = door or auth.AdminDoor(token)
    guarded = answer.guarded(door)
```

and pass it to the workspace list:

```python
    broadcast = workspace_list.mount(
        mcp, actions, workspaces, flow_store, token, prefix, guarded, door
    )
```

Update the module docstring's paragraph *"There is no user database and no session cookie…"* to end: *"…and, when `oidc.client_id` is set, a JWT the page signed in for with the issuer, holding an admin role (spec 2026-10-09-admin-oidc). Still no session cookie."*

In `kubed/selenium_flow/http/admin/workspaces.py`, change the signature:

```python
def mount(
    mcp, actions, workspaces, flow_store, token, prefix, guarded, door
):
```

and in `admin_events` replace the `if not (auth.authorized(...) or (...)):` block with:

```python
        # A signed URL is how a page's EventSource gets in (it cannot send a
        # header); anything else is asked of the same door as the admin API.
        signed = bool(token) and links.valid(
            EVENTS_PATH,
            request.query_params.get("exp"),
            request.query_params.get("sig"),
            token,
        )
        if not signed:
            try:
                await door.admit(request)
            except auth.Refused as refused:
                return JSONResponse(
                    {"error": refused.reason}, status_code=refused.status
                )
```

- [ ] **Step 5: Build the doors once in the server**

In `kubed/selenium_flow/server.py`, replace

```python
        auth = http_auth.provider(settings)
```

with

```python
        # Both doors from one verifier: /mcp's, and the admin API's, which
        # also admits an admin-UI JWT when `oidc.client_id` is set.
        self.doors = http_auth.doors(settings)
```

pass `auth=self.doors.mcp` to `FastMCP(...)`, and add `door=self.doors.admin,` to the `admin.register(...)` call.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest -q -p no:randomly tests/test_oidc_http.py tests/test_auth.py tests/test_admin_events.py tests/test_admin_routes.py tests/test_files_and_admin.py tests/test_http_answer.py`
Expected: PASS; `admin-routes.json` unchanged.

- [ ] **Step 7: Commit**

```bash
git add kubed/selenium_flow/http/answer.py kubed/selenium_flow/http/admin/__init__.py kubed/selenium_flow/http/admin/workspaces.py kubed/selenium_flow/server.py tests/test_oidc_http.py
git commit -m "Admin OIDC: the admin API and its event stream admit an admin-UI JWT

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The page carries the issuer and the client

**Files:**
- Modify: `kubed/selenium_flow/http/admin/page.py` (`sign_in`, `mount`)
- Modify: `kubed/selenium_flow/http/admin/__init__.py` (`oidc_page`)
- Modify: `kubed/selenium_flow/server.py`
- Modify: `ui/public/admin.html`
- Modify: `tests/conftest.py` (`SHELL`)
- Test: `tests/test_ui_serving.py`

- [ ] **Step 1: Write the failing tests**

In `tests/conftest.py`, the stand-in shell gains the attribute:

```python
SHELL = (
    '<title>{name}</title><style>__CSS__</style>'
    '<div id="root" data-mount="__MOUNT__" data-console="__CONSOLE__"'
    ' data-oidc="__OIDC__"></div>'
    '<script type="module">__JS__</script>'
)
```

In `tests/test_ui_serving.py`, add `import html` and `import json` to the imports, and `from .jwks import AUDIENCE`; in `test_the_real_shells_are_filled_completely` pass `OIDC=""` too:

```python
    out = admin.page(name, MOUNT="/flow", CONSOLE="/", OIDC="")
```

and append:

```python
def test_the_page_offers_oidc_only_with_an_admin_ui_client(built_ui, issuer):
    off = TestClient(_server().mcp.http_app()).get("/")
    assert 'data-oidc=""' in off.text
    server = SeleniumMCP(issuer.settings(TOKEN, admin_roles=("admin",)))
    on = TestClient(server.mcp.http_app()).get("/")
    raw = re.search(r'data-oidc="([^"]*)"', on.text)[1]
    assert json.loads(html.unescape(raw)) == {
        "issuer": issuer.issuer,
        "client_id": "selenium-flow-admin",
    }
    # The two public values and nothing else.
    assert issuer.jwks_uri not in on.text and AUDIENCE not in on.text


def test_sign_in_is_empty_without_a_client():
    assert admin.sign_in(Settings().oidc) == ""
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_ui_serving.py`
Expected: FAIL — `data-oidc="__OIDC__"` left unfilled, and `AttributeError: … has no attribute 'sign_in'`.

- [ ] **Step 3: Implement**

In `kubed/selenium_flow/http/admin/page.py`, add `import json` and:

```python
def sign_in(oidc) -> str:
    """What the page needs to offer Sign in with OIDC, or "" when it should not.

    The issuer and the public client id, and nothing else: the audience, the
    JWKS URI and the roles are the server's business (spec
    2026-10-09-admin-oidc §3). Both values are public by nature.
    """
    if not oidc.client_id:
        return ""
    return json.dumps({"issuer": oidc.issuer, "client_id": oidc.client_id})
```

Change `mount`'s signature and the page build:

```python
def mount(
    mcp,
    prefix: str,
    console_url: str | None,
    frame_ancestors: list[str] | None = None,
    oidc: str = "",
) -> None:
```

```python
        html, etag = page_with_etag(
            "admin", CONSOLE=console, MOUNT=prefix, OIDC=oidc
        )
```

In `kubed/selenium_flow/http/admin/__init__.py` add the keyword parameter `oidc_page: str = "",` after `door`, and call `page.mount(mcp, prefix, console_url, frame_ancestors, oidc_page)`.

In `kubed/selenium_flow/server.py`, add to the `admin.register(...)` call:

```python
            oidc_page=admin_page.sign_in(settings.oidc),
```

In `ui/public/admin.html`, the comment and the root:

```html
<!-- The admin dashboard. Filled in by admin.page(): __CSS__ and __JS__ are the
     built bundle, __MOUNT__, __CONSOLE__ and __OIDC__ come from the server. -->
```

```html
<div id="root" data-mount="__MOUNT__" data-console="__CONSOLE__" data-oidc="__OIDC__"></div>
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest -q -p no:randomly tests/test_ui_serving.py tests/test_admin_routes.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/http/admin/page.py kubed/selenium_flow/http/admin/__init__.py kubed/selenium_flow/server.py ui/public/admin.html tests/conftest.py tests/test_ui_serving.py
git commit -m "Admin OIDC: the page carries the issuer and the admin UI's client in data-oidc

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Who opened each workspace's browser

**Files:**
- Modify: `kubed/selenium_flow/principal.py` (`Principal.opener`)
- Modify: `kubed/selenium_flow/workspace/store.py` (`Workspace.opened_by`, `from_json`)
- Modify: `kubed/selenium_flow/workspace/workspaces.py` (`KEEP`, `remember`, `open_browser`)
- Modify: `kubed/selenium_flow/http/answer.py` (`answer`)
- Modify: `kubed/selenium_flow/http/admin/workspaces.py` (the row)
- Test: `tests/test_workspaces.py`, `tests/test_http_answer.py`, `tests/golden/admin-workspaces.json`

- [ ] **Step 1: Write the failing tests**

In `tests/test_workspaces.py` add `import json` and `from kubed.selenium_flow.principal import Principal`, then append:

```python
# ---- who opened the browser (spec 2026-10-09-admin-oidc, ruling 11) ---------


DRK = {"kind": "oidc", "username": "drk"}


def test_open_session_records_who_opened_the_browser():
    workspaces = manager()
    drk = Principal("oidc", subject="6b0f", username="drk")
    workspaces.open_browser(Caller(NAMED, "header", principal=drk))
    assert workspaces.store.get(NAMED).opened_by == DRK


def test_an_open_server_records_nobody():
    workspaces = manager()
    workspaces.open_browser(Caller(NAMED, "header"))
    assert workspaces.store.get(NAMED).opened_by is None


def test_a_reopen_after_a_reap_keeps_who_opened_it():
    workspaces = manager(RecordingActions())
    workspaces.store.set(NAMED, Workspace(session_id="dead", opened_by=DRK))
    workspaces.resolve(NAMED)
    assert workspaces.store.get(NAMED).opened_by == DRK


@pytest.mark.parametrize(
    ("stored", "read"),
    [
        (DRK, DRK),
        ({"kind": "admin"}, {"kind": "admin", "username": None}),
        ({"kind": "root", "username": "x"}, None),
        ("drk", None),
    ],
)
def test_opened_by_is_read_back_or_dropped(stored, read):
    raw = json.dumps({"session_id": "s", "opened_by": stored})
    assert Workspace.from_json(raw).opened_by == read
```

In `tests/test_http_answer.py` add the imports `import logging`, `from starlette.applications import Starlette`, `from starlette.routing import Route`, `from kubed.selenium_flow.principal import ADMIN`, and append:

```python
def _probe(token, seen):
    async def route(request):
        def call(caller, _body):
            seen.append(caller.principal)
            return {"ok": True}

        log = logging.getLogger("t")
        return await answer.answer(request, token, "probe", call, log)

    return Starlette(routes=[Route("/p", route, methods=["POST"])])


@pytest.mark.parametrize(("token", "principal"), [(TOKEN, ADMIN), (None, None)])
def test_a_rest_caller_is_the_admin_when_there_is_a_token(token, principal):
    """Only a token holder gets past the check, so that is who is calling."""
    seen = []
    headers = {"X-Workspace": WORKSPACE}
    if token:
        headers["Authorization"] = f"Bearer {TOKEN}"
    answered = TestClient(_probe(token, seen)).post("/p", json={}, headers=headers)
    assert answered.status_code == 200
    assert seen == [principal]
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_workspaces.py tests/test_http_answer.py -k "opened or rest_caller"`
Expected: FAIL — `TypeError: Workspace.__init__() got an unexpected keyword argument 'opened_by'`, and `seen == [None]` for the token case.

- [ ] **Step 3: Implement the record**

In `kubed/selenium_flow/principal.py`, add to `Principal`:

```python
    def opener(self) -> dict:
        """What a workspace keeps about who opened its browser. Shown, never
        consulted (spec 2026-10-09-admin-oidc, ruling 11)."""
        return {"kind": self.kind, "username": self.username}
```

In `kubed/selenium_flow/workspace/store.py`, add the field after `reopened`:

```python
    # Who opened the browser this workspace holds — {"kind", "username"} — for
    # the admin list. Shown, never consulted (spec 2026-10-09-admin-oidc).
    opened_by: dict | None = None
```

In `from_json`, pass `opened_by=_opener(data.get("opened_by")),` to `cls(...)`, and add at module level:

```python
def _opener(value) -> dict | None:
    """A stored `opened_by`, or None for anything that is not one."""
    if not isinstance(value, dict) or value.get("kind") not in ("admin", "oidc"):
        return None
    name = value.get("username")
    return {"kind": value["kind"], "username": name if isinstance(name, str) else None}
```

In `kubed/selenium_flow/workspace/workspaces.py`, at module level:

```python
# `remember`'s "leave who opened it alone": a silent reopen after a reap is the
# same caller's browser coming back, not a new opener.
KEEP = object()
```

Give `remember` a keyword parameter `opened_by: dict | None | object = KEEP,` (after `report`), and in `bound` extend the `replace(...)` that builds `fresh`:

```python
            fresh = replace(
                r if r is not None else Workspace(),
                session_id=session_id,
                opened_at=time.time(),
                settings=dict(settings or {}),
                site_data={} if forget_site_data or r is None else dict(r.site_data),
                # Reset explicitly: any other bind drops an earlier reopen's report.
                reopened={"browser": session_id, "report": report} if report else {},
                **({} if opened_by is KEEP else {"opened_by": opened_by}),
            )
```

In `open_browser`, the `remember` call:

```python
        kept = self.remember(
            name, opened["session_id"], opened.get("url", ""), resolved,
            replacing=ended, forget_site_data=forgotten is not None,
            opened_by=caller.principal.opener() if caller.principal else None,
        )
```

(`resolve`'s reopen passes nothing, so `KEEP` holds the opener.)

- [ ] **Step 4: Implement the REST caller and the row**

In `kubed/selenium_flow/http/answer.py`, import `from dataclasses import replace` and `from ..principal import ADMIN`, and right after `caller = Caller.from_request(*values_of(request))`:

```python
        if token:
            # Only a token holder gets this far, so the caller is the admin.
            caller = replace(caller, principal=ADMIN)
```

In `kubed/selenium_flow/http/admin/workspaces.py`, in the row dict after `"started": record.opened_at or None,`:

```python
                    # Who opened this browser, for the meta line ("by drk").
                    "opened_by": record.opened_by,
```

- [ ] **Step 5: Regenerate the golden and read it**

Run: `GOLDEN_UPDATE=1 python3 -m pytest -q -p no:randomly tests/test_golden.py -k admin_workspaces && git diff tests/golden/admin-workspaces.json`
Expected: the only change is `"opened_by": null,` added to each row.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest -q -p no:randomly tests/test_workspaces.py tests/test_http_answer.py tests/test_golden.py tests/test_routes.py tests/test_principal.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kubed/selenium_flow/principal.py kubed/selenium_flow/workspace/store.py kubed/selenium_flow/workspace/workspaces.py kubed/selenium_flow/http/answer.py kubed/selenium_flow/http/admin/workspaces.py tests/test_workspaces.py tests/test_http_answer.py tests/golden/admin-workspaces.json
git commit -m "Admin OIDC: each workspace records who opened its browser, for the live list

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The flow, in `oidc.ts`

**Files:**
- Modify: `ui/src/test/setup.ts`, `ui/src/test/helpers.ts`
- Create: `ui/src/admin/oidc.ts`
- Test: `ui/src/admin/oidc.test.ts`

- [ ] **Step 1: Give the tests WebCrypto, form bodies and a JWT**

Append to `ui/src/test/setup.ts`:

```ts
import { webcrypto } from 'node:crypto'

// jsdom has `crypto.getRandomValues` but no `crypto.subtle`, which PKCE needs.
// Node's own, defined rather than stubbed: `unstubGlobals` would remove a stub
// after the first test (spec 2026-10-09-admin-oidc, Verify first 2).
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
}
```

(Move the `import` to the top of the file with the others.)

In `ui/src/test/helpers.ts`, record a non-JSON body as its text — replace the `body:` line in `fakeFetch`:

```ts
      body: init.body ? parsed(init.body) : undefined,
```

and add:

```ts
/** A JSON body as its value; anything else — a form — as its text. */
function parsed(body: BodyInit): unknown {
  const text = String(body)
  try { return JSON.parse(text) } catch { return text }
}

/** An unsigned JWT-shaped string: enough for code that only reads the claims.
    UTF-8 first, so a username like `zoë` survives `btoa`. */
export function fakeJwt(claims: object): string {
  const part = (o: object) => btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(o))))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `${part({ alg: 'none' })}.${part(claims)}.sig`
}
```

- [ ] **Step 2: Write the failing tests**

Create `ui/src/admin/oidc.test.ts`:

```ts
import { beforeEach, expect, test, vi } from 'vitest'
import { fakeFetch, fakeJwt } from '../test/helpers'
import {
  begin, challengeOf, complete, NOT_COMPLETED, NOT_OURS, NOT_REACHED, OTHER_ISSUER,
  PENDING, readConfig, refresh, renewIn, usernameOf,
} from './oidc'

const ISSUER = 'https://auth.example.com/realms/example'
const CONFIG = { issuer: ISSUER, client_id: 'selenium-flow-admin' }
const DISCOVERY = {
  issuer: ISSUER,
  authorization_endpoint: ISSUER + '/protocol/openid-connect/auth',
  token_endpoint: ISSUER + '/protocol/openid-connect/token',
}
const WELL_KNOWN = 'GET /realms/example/.well-known/openid-configuration'
const TOKEN = 'POST /realms/example/protocol/openid-connect/token'

beforeEach(() => {
  sessionStorage.clear()
  history.replaceState(null, '', '/flow/#/')
})

function replyWith(query: string, pending: object | null = { state: 's1', verifier: 'v1', hash: '#/w/desk', silent: false }) {
  if (pending) sessionStorage.setItem(PENDING, JSON.stringify(pending))
  history.replaceState(null, '', '/flow/?' + query)
}

test('readConfig takes the two public values and nothing malformed', () => {
  expect(readConfig('')).toBeNull()
  expect(readConfig(undefined)).toBeNull()
  expect(readConfig('{nope')).toBeNull()
  expect(readConfig('{"issuer":"x"}')).toBeNull()
  expect(readConfig(JSON.stringify(CONFIG))).toEqual(CONFIG)
})

test('the S256 challenge is RFC 7636 Appendix B', async () => {
  expect(await challengeOf('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')
})

test('begin keeps the verifier and asks for a code for the page itself', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY } })
  const go = vi.fn()
  await begin(CONFIG, false, go)
  const url = new URL(go.mock.calls[0][0])
  const pending = JSON.parse(sessionStorage.getItem(PENDING)!)
  expect(url.origin + url.pathname).toBe(DISCOVERY.authorization_endpoint)
  expect(Object.fromEntries(url.searchParams)).toEqual({
    response_type: 'code',
    client_id: 'selenium-flow-admin',
    redirect_uri: location.origin + '/flow/',
    scope: 'openid',
    state: pending.state,
    code_challenge: await challengeOf(pending.verifier),
    code_challenge_method: 'S256',
  })
  expect(pending).toMatchObject({ hash: '#/', silent: false })
  expect(pending.verifier.length).toBeGreaterThanOrEqual(43)
})

test('a silent begin asks for no prompt', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY } })
  const go = vi.fn()
  await begin(CONFIG, true, go)
  expect(new URL(go.mock.calls[0][0]).searchParams.get('prompt')).toBe('none')
})

test('a discovery document naming another issuer is refused', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: { ...DISCOVERY, issuer: 'https://evil.example.com' } } })
  const go = vi.fn()
  await expect(begin(CONFIG, false, go)).rejects.toThrow(NOT_REACHED)
  expect(go).not.toHaveBeenCalled()
})

test('no reply is nothing to do', async () => {
  expect(await complete(CONFIG)).toEqual({ kind: 'none' })
})

test('a reply exchanges its code, cleans the URL and keeps nothing behind', async () => {
  const { calls } = fakeFetch({
    [WELL_KNOWN]: { body: DISCOVERY },
    [TOKEN]: { body: { access_token: 'a1', refresh_token: 'r1', expires_in: 300 } },
  })
  replyWith('code=c1&state=s1&iss=' + encodeURIComponent(ISSUER))
  const before = Date.now()
  const reply = await complete(CONFIG)
  expect(reply).toMatchObject({ kind: 'tokens', tokens: { access: 'a1', refresh: 'r1' } })
  if (reply.kind === 'tokens') expect(reply.tokens.expiresAt).toBeGreaterThanOrEqual(before + 300_000)
  expect(location.search).toBe('')
  expect(location.hash).toBe('#/w/desk')
  expect(sessionStorage.getItem(PENDING)).toBeNull()
  const post = calls.find((c) => c.method === 'POST')!
  expect(Object.fromEntries(new URLSearchParams(post.body as string))).toEqual({
    grant_type: 'authorization_code',
    code: 'c1',
    redirect_uri: location.origin + '/flow/',
    code_verifier: 'v1',
    client_id: 'selenium-flow-admin',
  })
})

test('a reply whose state is not ours is refused, and its code still leaves the URL', async () => {
  fakeFetch({})
  replyWith('code=c1&state=forged')
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_OURS })
  expect(location.search).toBe('')
})

test('a reply with no sign-in pending is refused', async () => {
  fakeFetch({})
  replyWith('code=c1&state=s1', null)
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_OURS })
})

test('a reply from another issuer is refused', async () => {
  fakeFetch({})
  replyWith('code=c1&state=s1&iss=' + encodeURIComponent('https://evil.example.com'))
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: OTHER_ISSUER })
})

test("an issuer's error is said; a silent miss is quiet", async () => {
  fakeFetch({})
  replyWith('error=access_denied&error_description=Cancelled&state=s1')
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: 'The issuer said: Cancelled.' })
  replyWith('error=login_required&state=s1', { state: 's1', verifier: 'v1', hash: '#/', silent: true })
  expect(await complete(CONFIG)).toEqual({ kind: 'quiet' })
})

test('a refused exchange says so', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY }, [TOKEN]: { status: 400, body: { error: 'invalid_grant' } } })
  replyWith('code=c1&state=s1')
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_COMPLETED })
})

test('refresh swaps the tokens and keeps a refresh token the issuer did not reissue', async () => {
  const { calls } = fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY }, [TOKEN]: { body: { access_token: 'a2', expires_in: 300 } } })
  const next = await refresh(CONFIG, { access: 'a1', refresh: 'r1', expiresAt: 0 })
  expect(next).toMatchObject({ access: 'a2', refresh: 'r1' })
  const post = calls.find((c) => c.method === 'POST')!
  expect(Object.fromEntries(new URLSearchParams(post.body as string))).toEqual({
    grant_type: 'refresh_token', refresh_token: 'r1', client_id: 'selenium-flow-admin',
  })
})

test('a refused refresh throws', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY }, [TOKEN]: { status: 400 } })
  await expect(refresh(CONFIG, { access: 'a1', refresh: 'r1', expiresAt: 0 })).rejects.toThrow()
})

test('renewal is 30 s before expiry, never sooner than 5 s', () => {
  expect(renewIn({ access: 'a', expiresAt: 300_000 }, 0)).toBe(270_000)
  expect(renewIn({ access: 'a', expiresAt: 10_000 }, 0)).toBe(5_000)
})

test('usernameOf reads preferred_username, for display only', () => {
  expect(usernameOf(fakeJwt({ preferred_username: 'drk' }))).toBe('drk')
  expect(usernameOf(fakeJwt({ preferred_username: 'zoë' }))).toBe('zoë')
  expect(usernameOf('not-a-jwt')).toBeUndefined()
})
```

- [ ] **Step 3: Run them to see them fail**

Run: `npm --prefix ui test -- src/admin/oidc.test.ts`
Expected: FAIL — `Failed to resolve import "./oidc"`.

- [ ] **Step 4: Implement**

Create `ui/src/admin/oidc.ts`:

```ts
/* Sign in with OIDC: Authorization Code + PKCE, run by the page itself (spec
   2026-10-09-admin-oidc). The page talks to the issuer — discovery, the
   redirect, the code exchange, the renewals — and hands the server nothing but
   the access token, as a bearer. Tokens live in memory; sessionStorage holds
   only what must survive the redirect, and only until it comes back. */

export interface OidcConfig { issuer: string; client_id: string }
export interface Tokens { access: string; refresh?: string; expiresAt: number }
export type Reply =
  | { kind: 'none' }
  | { kind: 'quiet' }
  | { kind: 'error'; message: string }
  | { kind: 'tokens'; tokens: Tokens }

interface Endpoints { authorization_endpoint: string; token_endpoint: string }
interface Pending { state: string; verifier: string; hash: string; silent: boolean }

export const PENDING = 'sf-oidc-pending'
export const MARKER = 'sf-signin'

export const NOT_REACHED = 'The issuer could not be reached.'
export const NOT_OURS = 'That sign-in reply was not ours; try again.'
export const OTHER_ISSUER = 'That sign-in reply came from another issuer.'
export const NOT_COMPLETED = 'The issuer would not complete the sign-in.'

// What a `prompt=none` attempt answers when the issuer has no session: show
// the card, say nothing.
const SILENT_MISSES = new Set(['login_required', 'interaction_required', 'consent_required', 'account_selection_required'])

export function readConfig(raw: string | undefined): OidcConfig | null {
  if (!raw) return null
  try {
    const c = JSON.parse(raw) as Partial<OidcConfig>
    return typeof c.issuer === 'string' && typeof c.client_id === 'string'
      ? { issuer: c.issuer, client_id: c.client_id }
      : null
  } catch {
    return null
  }
}

export function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

const random = (n: number): string => base64url(crypto.getRandomValues(new Uint8Array(n)))

export async function challengeOf(verifier: string): Promise<string> {
  return base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))))
}

/* The page's own URL — the mount root — which is what the operator registers. */
export const redirectUri = (): string => location.origin + location.pathname.replace(/\/*$/, '/')

export const hasReply = (): boolean => {
  const params = new URLSearchParams(location.search)
  return params.has('code') || params.has('error')
}

export async function discover(config: OidcConfig): Promise<Endpoints> {
  const res = await fetch(config.issuer.replace(/\/+$/, '') + '/.well-known/openid-configuration')
  if (!res.ok) throw new Error(NOT_REACHED)
  const doc = (await res.json()) as Partial<Endpoints> & { issuer?: string }
  // OIDC Discovery 4.3: the document must name the issuer it was fetched for.
  if (doc.issuer !== config.issuer || !doc.authorization_endpoint || !doc.token_endpoint) throw new Error(NOT_REACHED)
  return { authorization_endpoint: doc.authorization_endpoint, token_endpoint: doc.token_endpoint }
}

export async function begin(
  config: OidcConfig,
  silent: boolean,
  go: (url: string) => void = (url) => location.assign(url),
): Promise<void> {
  const { authorization_endpoint } = await discover(config)
  const pending: Pending = { state: random(16), verifier: random(32), hash: location.hash, silent }
  sessionStorage.setItem(PENDING, JSON.stringify(pending))
  const url = new URL(authorization_endpoint)
  const params: Record<string, string> = {
    response_type: 'code',
    client_id: config.client_id,
    redirect_uri: redirectUri(),
    scope: 'openid',
    state: pending.state,
    code_challenge: await challengeOf(pending.verifier),
    code_challenge_method: 'S256',
  }
  if (silent) params.prompt = 'none'
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v)
  go(url.toString())
}

export async function complete(config: OidcConfig): Promise<Reply> {
  if (!hasReply()) return { kind: 'none' }
  const params = new URLSearchParams(location.search)
  let pending: Pending | null = null
  try { pending = JSON.parse(sessionStorage.getItem(PENDING) ?? 'null') as Pending | null } catch { /* not ours */ }
  sessionStorage.removeItem(PENDING)
  // The code leaves the address bar, and history, before anything else runs.
  history.replaceState(null, '', location.pathname + (pending?.hash ?? location.hash))
  if (!pending || params.get('state') !== pending.state) return { kind: 'error', message: NOT_OURS }
  const iss = params.get('iss')
  if (iss !== null && iss !== config.issuer) return { kind: 'error', message: OTHER_ISSUER }
  const error = params.get('error')
  if (error) {
    if (pending.silent && SILENT_MISSES.has(error)) return { kind: 'quiet' }
    return { kind: 'error', message: `The issuer said: ${params.get('error_description') || error}.` }
  }
  let endpoints: Endpoints
  try { endpoints = await discover(config) } catch { return { kind: 'error', message: NOT_REACHED } }
  const tokens = await grant(config, endpoints.token_endpoint, {
    grant_type: 'authorization_code',
    code: params.get('code') ?? '',
    redirect_uri: redirectUri(),
    code_verifier: pending.verifier,
  })
  return tokens ? { kind: 'tokens', tokens } : { kind: 'error', message: NOT_COMPLETED }
}

export async function refresh(config: OidcConfig, tokens: Tokens): Promise<Tokens> {
  if (!tokens.refresh) throw new Error('no refresh token')
  const { token_endpoint } = await discover(config)
  const next = await grant(config, token_endpoint, { grant_type: 'refresh_token', refresh_token: tokens.refresh }, tokens)
  if (!next) throw new Error('the issuer refused the refresh')
  return next
}

/* When to renew: 30 s before the access token runs out, never sooner than 5 s. */
export const renewIn = (tokens: Tokens, now = Date.now()): number => Math.max(5_000, tokens.expiresAt - now - 30_000)

/* The access token's username, for a message. Display only: the server verified it. */
export function usernameOf(access: string): string | undefined {
  try {
    const b64 = access.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const bytes = Uint8Array.from(atob(b64.padEnd(Math.ceil(b64.length / 4) * 4, '=')), (c) => c.charCodeAt(0))
    const claims = JSON.parse(new TextDecoder().decode(bytes)) as { preferred_username?: unknown }
    return typeof claims.preferred_username === 'string' ? claims.preferred_username : undefined
  } catch {
    return undefined
  }
}

async function grant(config: OidcConfig, endpoint: string, form: Record<string, string>, previous?: Tokens): Promise<Tokens | null> {
  let res: Response
  try {
    res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ ...form, client_id: config.client_id }),
    })
  } catch {
    return null
  }
  if (!res.ok) return null
  const body = (await res.json()) as { access_token?: string; refresh_token?: string; expires_in?: number }
  if (!body.access_token) return null
  return {
    access: body.access_token,
    refresh: body.refresh_token ?? previous?.refresh,
    expiresAt: Date.now() + (body.expires_in ?? 300) * 1000,
  }
}
```

- [ ] **Step 5: Run the tests**

Run: `npm --prefix ui test -- src/admin/oidc.test.ts src/test/helpers.test.ts && npm --prefix ui run -s check && npm --prefix ui run -s lint`
Expected: PASS, no svelte-check or eslint findings.

- [ ] **Step 6: Commit**

```bash
git add ui/src/admin/oidc.ts ui/src/admin/oidc.test.ts ui/src/test/setup.ts ui/src/test/helpers.ts
git commit -m "Admin OIDC: oidc.ts, Authorization Code + PKCE run by the page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `ApiError`, and the OIDC row on the card

**Files:**
- Modify: `ui/src/admin/api.ts`, `ui/src/admin/Login.svelte`
- Test: `ui/src/admin/api.test.ts`; create `ui/src/admin/Login.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `ui/src/admin/api.test.ts` (import `ApiError` beside `createApi`):

```ts
test('a refusal carries its status, so a 403 can be told from the rest', async () => {
  fakeFetch({ 'GET /x': { status: 403, body: { error: 'this sign-in does not hold an admin role' } } })
  const failed = await createApi({ base: '', token: () => 't', onUnauthorized: () => {} })('/x').catch((e) => e)
  expect(failed).toBeInstanceOf(ApiError)
  expect(failed.status).toBe(403)
  expect(failed.message).toBe('this sign-in does not hold an admin role')
})
```

Create `ui/src/admin/Login.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import Login from './Login.svelte'

test('no OIDC config, no OIDC row', () => {
  const { container } = render(Login, { onsubmit: vi.fn(), refused: false })
  expect(container.querySelector('#oidcSignIn')).toBeNull()
})

test('the OIDC row sits below the token form, as drawn', async () => {
  const start = vi.fn()
  const { container } = render(Login, { onsubmit: vi.fn(), refused: false, oidc: { framed: false, start } })
  const form = container.querySelector('#loginForm')!
  const button = container.querySelector('#oidcSignIn')!
  expect(form.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(button).toHaveTextContent('Sign in with OIDC')
  expect(screen.getByText('the configured issuer · needs the admin role')).toBeInTheDocument()
  await fireEvent.click(button)
  expect(start).toHaveBeenCalledOnce()
})

test('framed, the button opens the page in a new tab instead', async () => {
  const open = vi.fn()
  vi.stubGlobal('open', open)
  const start = vi.fn()
  const { container } = render(Login, { onsubmit: vi.fn(), refused: false, oidc: { framed: true, start } })
  await fireEvent.click(container.querySelector('#oidcSignIn')!)
  expect(open).toHaveBeenCalledWith(location.href, '_blank', 'noopener')
  expect(start).not.toHaveBeenCalled()
  expect(screen.getByText('opens in a new tab: the issuer will not load in a frame')).toBeInTheDocument()
})

test('an OIDC message takes the error line', () => {
  render(Login, { onsubmit: vi.fn(), refused: false, said: 'Your sign-in ended; sign in again.' })
  expect(screen.getByText('Your sign-in ended; sign in again.')).toBeVisible()
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `npm --prefix ui test -- src/admin/api.test.ts src/admin/Login.test.ts`
Expected: FAIL — no `ApiError` export; no `#oidcSignIn`.

- [ ] **Step 3: Implement `ApiError`**

In `ui/src/admin/api.ts`:

```ts
/* A refused call: the server's own message, and the status that says what kind of no. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}
```

and in `api`, throw it in both places:

```ts
    if (res.status === 401) { opts.onUnauthorized(); throw new ApiError('unauthorized', 401) }
    if (!res.ok) {
      // The server's own message: it names the rule an operator hit.
      let said = ''
      try { said = (await res.json()).error || '' } catch { /* not JSON */ }
      throw new ApiError(said || 'request failed (' + res.status + ')', res.status)
    }
```

- [ ] **Step 4: Implement the row**

Replace `ui/src/admin/Login.svelte`:

```svelte
<script lang="ts">
  let { onsubmit, refused, said = null, oidc = null }: {
    onsubmit: (token: string) => void
    refused: boolean
    said?: string | null
    oidc?: { framed: boolean; start: () => void } | null
  } = $props()
  let token = $state('')

  // In a frame the issuer's login page will not load, so the button opens this
  // page in a tab of its own (spec 2026-10-09-admin-oidc, ruling 10).
  function startOidc() {
    if (!oidc) return
    if (oidc.framed) window.open(location.href, '_blank', 'noopener')
    else oidc.start()
  }
</script>

<div id="login" class="wrap login">
  <div class="card">
    <h2 style="margin-top:0">Sign in</h2>
    <p class="muted small">
      There are no accounts here. The server's token is the whole credential —
      anyone holding it can already drive every browser through the API, so this
      box asks for that rather than inventing a second identity to get wrong.
    </p>
    <form id="loginForm" class="row" onsubmit={(e) => { e.preventDefault(); onsubmit(token.trim()) }}>
      <input id="token" type="password" placeholder="MCP token" autocomplete="off" required bind:value={token}>
      <button class="primary" type="submit">Enter</button>
    </form>
    {#if oidc}
      <!-- Below the token, as drawn (Penpot Admin UI → Admin → login, `oidc`):
           the token never goes away. -->
      <div id="oidcRow" class="row oidc">
        <span class="small muted">or</span>
        <button id="oidcSignIn" type="button" onclick={startOidc}>Sign in with OIDC</button>
        <span class="small muted">{oidc.framed ? 'opens in a new tab: the issuer will not load in a frame' : 'the configured issuer · needs the admin role'}</span>
      </div>
    {/if}
    <p id="loginError" class="small error" hidden={!refused && !said}>{said ?? 'That token was refused.'}</p>
  </div>
</div>

<style>
  .oidc { margin-top: 12px; }
</style>
```

- [ ] **Step 5: Run the tests**

Run: `npm --prefix ui test -- src/admin && npm --prefix ui run -s check && npm --prefix ui run -s lint`
Expected: PASS, including the unchanged `Admin.test.ts`.

- [ ] **Step 6: Commit**

```bash
git add ui/src/admin/api.ts ui/src/admin/api.test.ts ui/src/admin/Login.svelte ui/src/admin/Login.test.ts
git commit -m "Admin OIDC: the Sign in with OIDC row under the token form; ApiError carries its status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `Admin` signs in, renews and signs out

**Files:**
- Modify: `ui/src/admin/Admin.svelte`, `ui/src/admin.ts`
- Create: `ui/src/admin/AdminOidc.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `ui/src/admin/AdminOidc.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource, fakeFetch, fakeJwt } from '../test/helpers'
import Admin from './Admin.svelte'
import * as oidc from './oidc'

// Only the navigation is faked: everything else in oidc.ts runs for real.
vi.mock('./oidc', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./oidc')>()),
  begin: vi.fn(async () => {}),
}))

const ISSUER = 'https://auth.example.com/realms/example'
const CONFIG = JSON.stringify({ issuer: ISSUER, client_id: 'selenium-flow-admin' })
const DISCOVERY = {
  issuer: ISSUER,
  authorization_endpoint: ISSUER + '/protocol/openid-connect/auth',
  token_endpoint: ISSUER + '/protocol/openid-connect/token',
}
const WELL_KNOWN = 'GET /realms/example/.well-known/openid-configuration'
const TOKEN = 'POST /realms/example/protocol/openid-connect/token'
const WORKSPACES = { workspaces: [{ key: 'k1', name: 'claudecode', live: true }], events_url: '/e' }
const ACCESS = fakeJwt({ preferred_username: 'drk' })

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear()
  history.replaceState(null, '', '/#/')
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.mocked(oidc.begin).mockClear()
})

function returning(query = 'code=c1&state=s1', silent = false) {
  sessionStorage.setItem(oidc.PENDING, JSON.stringify({ state: 's1', verifier: 'v1', hash: '#/', silent }))
  history.replaceState(null, '', '/?' + query)
}

const issuerAnd = (workspaces: { status?: number; body?: unknown }) => fakeFetch({
  [WELL_KNOWN]: { body: DISCOVERY },
  [TOKEN]: { body: { access_token: ACCESS, refresh_token: 'r1', expires_in: 300 } },
  'GET /admin/workspaces': workspaces,
})

const stored = (): string => Array.from({ length: sessionStorage.length }, (_, i) => sessionStorage.getItem(sessionStorage.key(i)!)).join('|')

test("without an OIDC config the card is today's", () => {
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid' })
  expect(container.querySelector('#oidcSignIn')).toBeNull()
})

test('with one, the button starts a sign-in', async () => {
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await fireEvent.click(container.querySelector('#oidcSignIn')!)
  expect(oidc.begin).toHaveBeenCalledWith({ issuer: ISSUER, client_id: 'selenium-flow-admin' }, false)
})

test('a reply signs in with the access token and stores no token (ruling 2)', async () => {
  returning()
  const { calls } = issuerAnd({ body: WORKSPACES })
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  expect(calls.find((c) => c.path === '/admin/workspaces')!.headers.get('Authorization')).toBe('Bearer ' + ACCESS)
  expect(sessionStorage.getItem('sf-token')).toBeNull()
  expect(sessionStorage.getItem(oidc.MARKER)).toBe('oidc')
  expect(stored()).not.toContain(ACCESS)
  expect(stored()).not.toContain('r1')
  expect(localStorage.length).toBe(0)
})

test('a sign-in without the admin role is told so, and keeps nothing', async () => {
  returning()
  issuerAnd({ status: 403, body: { error: 'this sign-in does not hold an admin role' } })
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Signed in as drk, who does not hold an admin role.')).toBeVisible())
  expect(container.querySelector('#login')).toBeVisible()
  expect(sessionStorage.getItem(oidc.MARKER)).toBeNull()
})

test('a sign-in the server refuses says so', async () => {
  returning()
  issuerAnd({ status: 401 })
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('The server refused this sign-in.')).toBeVisible())
})

test('a reload with the marker signs in again silently, once', async () => {
  sessionStorage.setItem(oidc.MARKER, 'oidc')
  fakeFetch({})
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(oidc.begin).toHaveBeenCalledWith(expect.objectContaining({ issuer: ISSUER }), true))
  expect(sessionStorage.getItem(oidc.MARKER)).toBeNull()
})

test('a silent miss lands on the card with nothing to say', async () => {
  returning('error=login_required&state=s1', true)
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(container.querySelector('#login')).toBeVisible())
  expect(container.querySelector('#loginError')).not.toBeVisible()
})

test('Sign out forgets the OIDC sign-in', async () => {
  returning()
  issuerAnd({ body: WORKSPACES })
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  await fireEvent.click(screen.getByText('Sign out'))
  expect(container.querySelector('#login')).toBeVisible()
  expect(sessionStorage.getItem(oidc.MARKER)).toBeNull()
  expect(container.querySelector('#loginError')).not.toBeVisible()
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `npm --prefix ui test -- src/admin/AdminOidc.test.ts`
Expected: FAIL — `Admin` has no `oidc` prop, no `#oidcSignIn`.

- [ ] **Step 3: Implement**

In `ui/src/admin.ts`:

```ts
mount(Admin, {
  target: root,
  props: { mount: root.dataset.mount ?? '', console: root.dataset.console ?? '/', oidc: root.dataset.oidc ?? '' },
})
```

In `ui/src/admin/Admin.svelte`, the imports:

```ts
  import { onDestroy, onMount } from 'svelte'
  import { ApiError, createApi } from './api'
  import { begin, complete, hasReply, MARKER, NOT_REACHED, readConfig, refresh, renewIn, usernameOf, type Tokens } from './oidc'
```

the props:

```ts
  let { mount, console: consoleUrl, oidc: oidcRaw = '' }: { mount: string; console: string; oidc?: string } = $props()
```

and replace everything from the `// sessionStorage, not localStorage` comment down to (not including) `const route = $derived(router.route)` with:

```ts
  // svelte-ignore state_referenced_locally (oidcRaw is a boot-time prop, read once by design)
  const oidc = readConfig(oidcRaw)
  const framed = window !== window.top

  // sessionStorage, not localStorage: the server's full-privilege token should
  // not outlive the tab it was typed into. A plain variable: nothing renders
  // it, and the api reads it afresh on every call.
  let token = sessionStorage.getItem('sf-token') || ''
  // An OIDC sign-in's tokens: memory only, so a reload signs in again —
  // silently while the issuer's session lives (spec 2026-10-09-admin-oidc).
  let tokens: Tokens | null = null
  let renewal: ReturnType<typeof setTimeout> | undefined
  const resuming = !!oidc && (hasReply() || !!sessionStorage.getItem(MARKER))
  let phase = $state<'probing' | 'login' | 'in'>(token || resuming ? 'probing' : 'login')
  let refused = $state(false)
  let said = $state<string | null>(null)

  const api = createApi({ base: BASE, token: () => tokens?.access ?? token, onUnauthorized: () => signOut() })
  const live = new Live(api, ROOT)

  function signOut(message: string | null = null) {
    live.stop()
    clearTimeout(renewal)
    token = ''
    tokens = null
    sessionStorage.removeItem('sf-token')
    sessionStorage.removeItem(MARKER)
    said = message
    phase = 'login'
  }

  async function signIn(value: string) {
    token = value
    said = null
    try {
      await api('/admin/workspaces')
      sessionStorage.setItem('sf-token', token)
      refused = false
      phase = 'in'
    } catch {
      refused = true
    }
  }

  async function startOidc(silent: boolean) {
    if (!oidc) return
    try {
      await begin(oidc, silent)
    } catch {
      said = NOT_REACHED
      phase = 'login'
    }
  }

  async function finishOidc() {
    if (!oidc) return
    const reply = await complete(oidc)
    if (reply.kind !== 'tokens') {
      said = reply.kind === 'error' ? reply.message : null
      phase = 'login'
      return
    }
    tokens = reply.tokens
    try {
      await api('/admin/workspaces')
      sessionStorage.setItem(MARKER, 'oidc')
      schedule()
      refused = false
      phase = 'in'
    } catch (e) {
      const who = usernameOf(reply.tokens.access) ?? 'this account'
      signOut(e instanceof ApiError && e.status === 403
        ? `Signed in as ${who}, who does not hold an admin role.`
        : 'The server refused this sign-in.')
    }
  }

  // Renew 30 s before the access token runs out; a failed renewal ends it.
  function schedule() {
    clearTimeout(renewal)
    if (!oidc || !tokens?.refresh) return
    renewal = setTimeout(async () => {
      if (!tokens) return
      try {
        tokens = await refresh(oidc, tokens)
        schedule()
      } catch {
        signOut('Your sign-in ended; sign in again.')
      }
    }, renewIn(tokens))
  }

  onMount(() => {
    sync()
    if (oidc && hasReply()) { void finishOidc(); return }
    if (token) { api('/admin/workspaces').then(() => { phase = 'in' }, () => { phase = 'login' }); return }
    if (oidc && sessionStorage.getItem(MARKER)) {
      // Removed before leaving: an issuer that is down must not loop the page.
      sessionStorage.removeItem(MARKER)
      void startOidc(true)
    }
  })
  onDestroy(() => clearTimeout(renewal))
```

Keep the existing `$effect` that loads the list. In the template, the sign-out button and the card:

```svelte
  {#if phase === 'in'}<button id="signout" onclick={() => signOut()}>Sign out</button>{/if}
```

```svelte
  <Login onsubmit={signIn} {refused} {said} oidc={oidc ? { framed, start: () => void startOidc(false) } : null} />
```

- [ ] **Step 4: Run the tests**

Run: `npm --prefix ui test && npm --prefix ui run -s check && npm --prefix ui run -s lint`
Expected: PASS — the new file and every existing `Admin.test.ts` case (a stored token is still probed; a dead one still lands on the card).

- [ ] **Step 5: Commit**

```bash
git add ui/src/admin.ts ui/src/admin/Admin.svelte ui/src/admin/AdminOidc.test.ts
git commit -m "Admin OIDC: the page signs in with the issuer, renews in memory, and signs out

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `by drk` on the live list

**Files:**
- Modify: `ui/src/lib/types.ts`, `ui/src/lib/format.ts`
- Test: `ui/src/lib/format.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `ui/src/lib/format.test.ts` (top level, `metaLine` is already imported):

```ts
test('metaLine ends with who opened the browser', () => {
  expect(metaLine({ key: 'k', browser: 'chrome', opened_by: { kind: 'oidc', username: 'drk' } })).toBe('chrome · by drk')
  expect(metaLine({ key: 'k', browser: 'chrome', opened_by: { kind: 'admin', username: null } })).toBe('chrome · by token')
  expect(metaLine({ key: 'k', browser: 'chrome', opened_by: null })).toBe('chrome')
})
```

- [ ] **Step 2: Run it to see it fail**

Run: `npm --prefix ui test -- src/lib/format.test.ts`
Expected: FAIL — a type error on `opened_by`, then `'chrome'` instead of `'chrome · by drk'`.

- [ ] **Step 3: Implement**

In `ui/src/lib/types.ts`, add to `WorkspaceRow`:

```ts
  opened_by?: { kind: 'admin' | 'oidc'; username?: string | null } | null
```

In `ui/src/lib/format.ts`:

```ts
/* Who opened a workspace's browser: the OIDC username, or the token. */
export const openerText = (o: NonNullable<WorkspaceRow['opened_by']>): string =>
  o.kind === 'admin' ? 'token' : o.username || 'OIDC'

export function metaLine(s: WorkspaceRow, now = Date.now()): string {
  const meta = countsText(s)
  return [s.browser, s.version].filter(Boolean).join(' ')
    + (meta ? ' · ' + meta : '')
    + (s.started ? ' · ' + ago(s.started * 1000, now) : '')
    + (s.node ? ' · ' + s.node : '')
    + (s.opened_by ? ' · by ' + openerText(s.opened_by) : '')
}
```

- [ ] **Step 4: Run the tests**

Run: `npm --prefix ui test && npm --prefix ui run -s check && npm --prefix ui run -s lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/types.ts ui/src/lib/format.ts ui/src/lib/format.test.ts
git commit -m "Admin OIDC: the live list says who opened each browser

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: README, AGENTS.md, the changelog and the wiki

**Files:**
- Modify: `README.md` (Auth), `AGENTS.md` (the auth rules), `CHANGELOG.md` (`[Unreleased]`)
- Modify (wiki submodule): `wiki/Configuration.md` (regenerated), `wiki/Deployment.md`, `wiki/Administration.md`

- [ ] **Step 1: README**

After the `OIDC_ROLES=mcp` block in *🔐 Auth*, replace *"The token keeps working beside it, and stays the admin's."* with:

```markdown
The admin UI can sign in with the same issuer, for a person holding an admin
role — register a public client for it and add:

    OIDC_CLIENT_ID=selenium-flow-admin
    OIDC_ADMIN_ROLES=admin

The token keeps working beside both, and stays the admin's.
```

Then run `python3 -m pytest -q -p no:randomly tests/test_readme.py` (the Docker Hub length limit).

- [ ] **Step 2: AGENTS.md**

Replace the four bullets from *"**`http/auth.py` also builds `/mcp`'s verifier (`provider()`)**"* through *"**The REST routes, admin API and signed links are token-only on purpose**…"* with:

```markdown
- **`http/auth.py` builds both doors from one verifier (`doors()`)**: `/mcp`'s —
  the token alone, or `MultiAuth[token, OidcVerifier]` — and the admin API's
  `AdminDoor`. One `OidcVerifier` instance serves both, so one JWKS cache and one
  refetch floor. A third credential goes there too.
- **The admin API admits the token, or a JWT the admin UI signed in for**: `azp`
  is `oidc.client_id` and a role from `oidc.admin_roles` is held. `oidc.roles`
  (`/mcp`'s gate) does not apply there. Unknown credential 401, known but not an
  admin 403. The page runs Authorization Code + PKCE itself and keeps the tokens
  in memory; there is no cookie and no server-side session (spec
  2026-10-09-admin-oidc).
- **The verifiers return a `PrincipalToken`; `Caller.principal` carries it. It
  decides nothing beyond the admin door** — a workspace records who opened its
  browser (`opened_by`) for the live list, and ownership and per-tool roles are
  E6's.
- **The REST routes and signed links are token-only on purpose**: the gateway
  only routes `/mcp`. Signed links and the `events_url` stay HMAC on the token,
  for an OIDC admin too.
```

and after the *"`oidc` without `auth.token` is a `ConfigError`"* bullet add:

```markdown
- **`oidc.client_id` and `oidc.admin_roles` come together, and only with the
  issuer**, checked in the same place (`config.oidc_problem`).
```

- [ ] **Step 3: CHANGELOG**

Under `## [Unreleased]`, one line:

```markdown
- The admin UI can sign in with OIDC (`oidc.client_id`, `oidc.admin_roles`) for a person holding an admin role; the token still works.
```

- [ ] **Step 4: The wiki** (the `wiki/` submodule; skip with a note in the PR if it is not checked out)

Run: `python3 scripts/generate_wiki.py` — `Configuration.md` gains the two leaves and the new section description. Then, by hand:

In `wiki/Deployment.md`, under *Behind agentgateway*, after the *"The server verifies."* paragraph:

```markdown
**The admin UI signs in with the issuer.** Add a client for it in the realm:
`selenium-flow-admin`, *Client authentication* off (public), *Standard flow*
only, PKCE `S256`, *Valid redirect URIs* the UI's own URL
(`https://selenium.example.com/flow/`), *Web origins* `+`, and the client scope
that puts the MCP audience and the `roles` claim in a token as a **default**
scope. Then set `oidc.client_id: selenium-flow-admin` and
`oidc.admin_roles: [admin]`. The page shows *Sign in with OIDC* under the token
field; a person without an admin role is told so. Do not set `oidc.audience` to
the client id: the admin door would then accept an ID token.
```

In `wiki/Administration.md`, *The admin UI*, after the paragraph beginning *"There are no accounts."*:

```markdown
With `oidc.client_id` set, the card also offers **Sign in with OIDC**: the page
signs in with the issuer itself and keeps the tokens in memory, so a reload
signs in again, silently while the issuer's session lasts. It needs an admin
role (`oidc.admin_roles`). Inside a frame the button opens the UI in a new tab.
```

- [ ] **Step 5: Run the doc checks**

Run: `python3 -m pytest -q -p no:randomly tests/test_readme.py tests/test_wiki.py`
Expected: PASS.

- [ ] **Step 6: Commit** (the wiki in its own repository first, then the pointer)

```bash
git -C wiki add Configuration.md Deployment.md Administration.md
git -C wiki commit -m "Admin OIDC: the admin UI's client, and signing in with it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git add README.md AGENTS.md CHANGELOG.md wiki
git commit -m "Admin OIDC: README, AGENTS.md, the changelog and the wiki

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Everything green, and the build

**Files:** none new.

- [ ] **Step 1: The full suites**

Run:

```bash
python3 -m pytest -q -p no:randomly -n 4 --ignore=tests/integration --ignore=tests/bench
python3 -m ruff check kubed tests scripts && python3 -m ruff format --check kubed tests scripts
npm --prefix ui test && npm --prefix ui run -s check && npm --prefix ui run -s lint
npm --prefix ui run build && npm --prefix ui run -s size
```

Expected: every suite passes with the baseline counts plus this plan's new tests; `admin-routes.json` unchanged; the build writes `kubed/selenium_flow/http/static/admin.{html,css,js}` and the size check passes.

- [ ] **Step 2: The audit**

Run:

```bash
grep -rn "localStorage" ui/src --include=*.ts --include=*.svelte | grep -v test
grep -rn "authorized(request" kubed/selenium_flow/http/admin
```

Expected: the first finds nothing that writes a token; the second finds nothing (the admin package asks the door).

- [ ] **Step 3: Commit anything the format check rewrote**

```bash
git status --short   # expect clean; if ruff format changed files:
git commit -am "Admin OIDC: format

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Hand the live test to Dr K** (the spec's *Verify first* 3–7 and *Testing → Live*): create the Keycloak client, deploy the branch image with `OIDC_CLIENT_ID`/`OIDC_ADMIN_ROLES`, sign in, use every tab, reload, wait past five minutes, sign out; a user without `admin` sees the 403 message.
