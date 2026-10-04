# OIDC groundwork: the MCP door accepts a forwarded JWT

**Status: DESIGNED 2026-10-04 on branch `oidc`; not yet built. Plan:
`docs/superpowers/plans/2026-10-04-oidc-groundwork.md`.** Dr K reads this as it
grows and drops rulings in chat; every ruling is recorded under *Rulings*.

## Brief

Dr K, 2026-10-04: the next big thing is OIDC. An MCP gateway (agentgateway) now
sits in front of the homelab's MCP servers, validating Keycloak tokens, and
mcp-kb is already behind it. Research FastMCP's OIDC support and the gateway, and
find the best strategy. His questions, answered in *Research*:

- Would FastMCP's OIDC integration work with the gateway, or are they
  alternative routes?
- If FastMCP's OIDC were fully configured, would the gateway not be doing the
  same thing?
- With the gateway doing OIDC, is it disabled on FastMCP, or do they work
  together?
- Even without FastMCP's OIDC, is there functionality it could unlock?

Then, narrowing the round: *"focus on the groundwork to enable multi-user with
oidc and not yet try and fully comprehend what that means yet."* One user uses
this. The admin UI is left alone, with the door open. The bare minimum on the
MCP protocol: accept and verify the JWT. Put the cluster's selenium-flow behind
the gateway like mcp-kb. **Done means the cluster's `.mcp.json` signs in to
selenium-flow through the gateway exactly as it does to kb.**

## Research that shaped it

**FastMCP's "OIDC" is three different things** (fastmcp 4.0.10 source,
`fastmcp/server/auth/`):

| Piece | What it does | Beside a gateway |
|---|---|---|
| `OAuthProxy` / `OIDCProxy` | FastMCP fronts the authorization server | An alternative route: competes with the gateway |
| `RemoteAuthProvider` / `KeycloakAuthProvider` | Serves RFC 9728 protected-resource metadata, verifies | Duplicates what the gateway serves |
| `JWTVerifier` | Signature, `iss`, `aud`, `exp` against a JWKS; **no routes** | Complements it: the piece used here |

`MultiAuth(verifiers=[...])` tries each verifier in turn, first success wins, and
contributes no routes when it has no `server`. `AccessToken.claims` holds the
whole JWT; `get_access_token()` reads it inside a request. All of this exists
from `fastmcp-slim` 4.0.0, so the `fastmcp>=4.0.0,<5` floor does not move.

**The gateway strips the token after validating it** (agentgateway 1.6,
`jwtAuthentication`: "By default, the gateway removes the token after
validation"). So a server behind it today knows nothing about the caller.
`backend.auth.passthrough: {}` puts the validated token back on the request to
the backend only; the docs prefer it to `preserveToken: true`, which exposes the
token to every later policy.

**The MCP spec puts the audience check on the server** in every revision through
2026-07-28: *"MCP servers MUST validate that access tokens were issued
specifically for them as the intended audience"*. Once the gateway forwards the
token, selenium-flow is the server that accepts it, so it validates it.

**Two doors bypass the gateway.** Pods reach selenium-flow through the
in-cluster Service, and the ingress routes `selenium.<domain>/flow/*` here with
the prefix stripped, so `/flow/mcp` is a second external MCP door without the
gateway's rate limits or audit. That is why the identity is a forwarded JWT the
server verifies, not a `x-user: jwt.sub` header the gateway injects: a header is
forgeable by any pod, a signature is not. It is also why the gateway's role
check is repeated here. The server's own checks are the same on every door.

**What crosses the gateway** (agentgateway `crates/agentgateway/src/mcp/upstream/
mod.rs`, `IncomingRequestContext::apply`, read 2026-10-04): every client header is
copied to the backend request except `Mcp-Session-Id`, `Content-Length` and
`Content-Encoding`. **The query string is not**: the upstream URI is the target's
fixed host and path. So `X-Session-Key` reaches selenium-flow through the
gateway and `?session=` does not.

**The token the gateway accepts** (the homelab's Keycloak `mcp` client scope):
`aud` holds the MCP audience shared by every path; `roles` is a flat list of the
gateway client's roles (`mcp`, `admin`); `preferred_username`, `email` and
`groups` ride along. The gateway allows a route on
`jwt.roles.exists(r, r == "mcp")`.

**So, Dr K's questions:** they are layers, not alternatives. The gateway answers
*may you come in* — discovery, the 401 challenge, the role, rate limits, audit.
selenium-flow answers *who is this*, by verifying the same token with a bare
`JWTVerifier`. Nothing is disabled on either side, and FastMCP serves no OAuth
routes. What it unlocks, later: workspaces with owners, per-tool roles that also
filter `tools/list`, an admin UI that signs in with Keycloak, an audit trail of
who drove which browser. This round lays the wire for those and builds none.

## Rulings

Dr K, 2026-10-04, in order:

1. **The gateway at the edge, selenium-flow verifies the forwarded token** — the
   recommended of three approaches (the others: gateway only, as mcp-kb does;
   FastMCP owns OIDC and the gateway goes).
2. **The token stays, as an option, for good.** n8n keeps using headers. The token
   is the admin login, with or without OIDC, and stays the HMAC key for signed
   links. *"We have the admin token as bearer auth to simply be admin if someone
   wanted to and has that token."*
3. **The admin UI is left alone.** When it grows OIDC, a "sign in with OIDC"
   option sits *below* the token field; the token never goes away.
4. **`session` names are a project or workspace, not an identity** — context and
   focus, like "the Grafana dashboards". Loose and global. **Not changed this
   round.** An agent that needs two registers the server twice.
5. **One user, groundwork only.** No multi-user product: no ownership, no
   per-user views. *"Wired up with the jwt stuff"*, and no extra functionality.
6. **Roles are checked this round** (Dr K took the recommendation): the in-cluster
   door must not accept a token the gateway would refuse.
7. **The route prefix is already configurable**, so where the gateway mounts this
   server is a deploy choice, not a design one.

## Goal

The MCP endpoint accepts a Keycloak JWT beside the server token, verifies it
fully (signature, issuer, audience, expiry, role), and hands the verified caller
to the code as a principal. The cluster's selenium-flow is reachable through the
gateway with OAuth, and Claude Code signs in to it the way it signs in to kb.

## Non-goals

- No change to sessions: no rename, no owner, no per-subject anything.
- No change to the admin UI, the REST routes (`/browser/*`, `/files*`,
  `/flows*`, `/site-data*`, `/secrets`), the admin API, signed links, `/ready`
  or `/info`. They stay token-only. The gateway only routes `/mcp`.
- No OAuth routes in selenium-flow: no protected-resource metadata, no
  authorization-server proxy, no DCR. The gateway and Keycloak own discovery.
- No per-tool authorization, no admin role from a JWT, no audit log of
  principals. All of it is next round's, once there is a principal to use.
- No token exchange. selenium-flow never forwards the caller's token anywhere.

## Constraints

- Every rule in `AGENTS.md` stays. In particular: one place decides whether a
  request is authorised (`http/auth.py`), and the token comparison is
  `hmac.compare_digest`.
- No `oidc` settings means the same auth behaviour as before; the one published
  change is `principal`, in `session://current` and `GET /browser`.
- No backwards compatibility to preserve (one user), but nothing here breaks a
  shape anyway: the only published change is one new key, `principal`.
- The repo names no real host: examples use `example.com` and the realm
  `example`.

## The design

```
Claude Code ──OAuth (DCR, PKCE)──▶ Keycloak
     │  Authorization: Bearer <JWT>, X-Session-Key: <name>
     ▼
agentgateway   protected-resource metadata, 401 challenge,
     │         iss/aud/exp + role `mcp`        backend.auth.passthrough: {}
     ▼
selenium-flow  /mcp: MultiAuth[ server token | JWTVerifier + role ]
               └─ principal → Caller → session://current
```

### Settings: a new `oidc` section

A setting's path is `section.key` (two levels; `config.leaves()` walks no
deeper), so the group is a top-level section, not `auth.oidc`. That also leaves
room for the admin UI's later `oidc.client_id`.

| Path | Env | Type | Default | Meaning |
|---|---|---|---|---|
| `oidc.issuer` | `OIDC_ISSUER` | str | unset | The `iss` a token must carry. Setting it turns OIDC on. |
| `oidc.audience` | `OIDC_AUDIENCE` | str | unset | The `aud` a token must include. |
| `oidc.jwks_uri` | `OIDC_JWKS_URI` | str | unset | Where the issuer publishes its signing keys. |
| `oidc.roles` | `OIDC_ROLES` | list, comma-separated in env | `[]` | A token must hold at least one. Empty checks none. |
| `oidc.roles_claim` | `OIDC_ROLES_CLAIM` | str | `roles` | Where the roles are; a dotted path walks objects (`realm_access.roles`). |

Section description: *"Accept a JWT from an OIDC issuer beside the token."*

Rules, each a `ConfigError` that stops the boot:

- `issuer`, `audience` and `jwks_uri` are set together or not at all. An issuer
  without an audience would accept any token that issuer ever minted.
- `issuer` and `jwks_uri` are `http(s)` URLs.
- **`oidc` needs `auth.token`.** The token is the admin login and the signing
  key; without it the REST and admin routes are open (unset is open), which is
  not a state OIDC should be able to produce. Checked **after** the layers merge,
  not in the model: the cluster sets `oidc` in the file and the token in env, and
  `load()` validates the file on its own first.

`jwks_uri` is required rather than derived: Keycloak's is
`{issuer}/protocol/openid-connect/certs`, other issuers differ, and discovering
it at boot adds a network dependency to startup for one string the operator
already knows.

### The MCP door

`http/auth.py` builds the provider FastMCP's `auth=` takes, so the one place
that decides authorisation keeps deciding it for `/mcp` too:

- **No token, no `oidc`:** `None`. Open, as today.
- **Token only:** `ServerTokenVerifier` alone. It replaces FastMCP's
  `StaticTokenVerifier`, whose `dict.get` is not a constant-time comparison; the
  new one uses `hmac.compare_digest`, like every other door already does.
- **Token and `oidc`:** `MultiAuth(verifiers=[ServerTokenVerifier,
  OidcVerifier])`. The token is tried first: it is a constant-time string
  compare, where the JWT check parses and verifies a signature.

`OidcVerifier` is FastMCP's `JWTVerifier` (`issuer`, `audience`, `jwks_uri`,
RS256) plus the role check: after the parent accepts a token, it reads
`roles_claim` from the claims, and refuses the token unless it holds one of
`roles`. A missing claim, or one that is not a list of strings, holds no roles.

**Every refusal is a 401**, the role included. A verifier that returns `None` is
FastMCP's `invalid_token`. A 403 `insufficient_scope` would be the more precise
answer for a missing role, but FastMCP's 403 path is for scopes, and the gateway
already refuses that token before it gets here; only a caller on a door that
bypasses the gateway (the in-cluster Service, the ingress) with a roleless token
sees this, and a 401 is a truthful answer to it.

The **JWKS** is fetched on the first JWT and cached by the verifier (an hour,
FastMCP's default; a token with an unknown `kid` refetches, at most once a
minute, since FastMCP's bearer middleware runs on every path). A JWKS that
cannot be fetched refuses that request, and the server keeps running.

The MCP transport still needs the `Bearer ` scheme (the MCP SDK's
`BearerAuthBackend`); the bare-token form stays an HTTP-routes convenience, as
today.

### The principal

A new `kubed/selenium_flow/principal.py`, pure and FastMCP-free:

```python
@dataclass(frozen=True)
class Principal:
    kind: Literal["admin", "oidc"]
    subject: str | None = None      # the JWT's `sub`
    username: str | None = None     # `preferred_username`, else None
    roles: tuple[str, ...] = ()

    @property
    def admin(self) -> bool: ...    # kind == "admin"

    def status(self) -> dict: ...   # what session://current shows

    @classmethod
    def from_claims(cls, claims: Mapping, roles_claim: str) -> Principal: ...


def roles_in(claims: Mapping, roles_claim: str) -> tuple[str, ...]: ...
```

`roles_in` is the one reading of the roles claim: the verifier's role check and
`from_claims` both call it, so they cannot disagree about what a token holds.

The server token is the `admin` principal: the operator, who sees everything.
A JWT is an `oidc` principal, whatever its roles; making a JWT role an admin is
next round's.

**The verifiers produce the principal.** Both return a `PrincipalToken`, an
`AccessToken` subclass carrying `principal: Principal`, so nothing downstream
re-reads claims or needs `roles_claim`. `Caller` gains
`principal: Principal | None = None`, and `mcp/clients.caller()` fills it from
`get_access_token()`. Nothing decides anything from it this round. `None` means
the server is open.

`session://current` gains one key, always present:

```json
"principal": {"kind": "oidc", "subject": "6b0f…", "username": "drk"}
```

`{"kind": "admin"}` for the server token, `null` on an open server. Roles are
not shown: they are an authorisation input, not something an agent needs.

### What does not change

The REST routes, the admin API, the event stream and signed links keep calling
`http/auth.authorized(request, token)` with the server token. Sessions, flows,
files, site data and the secrets catalogue stay keyed by session name alone.
`/ready` and `/info` stay tokenless.

## The cluster install

In the cluster repo (`apps/selenium/components/mcp`); files applied, no git
there (Dr K). **Live since 2026-10-04 with the server token:** the gateway
validates the Keycloak JWT and sends selenium-flow the server token
(`backend.auth.secretRef`), the pattern for any token-protected MCP server. This
round switches that to `passthrough` once the server verifies JWTs:

- **`gateway.yaml`**, modelled on `apps/mcp-kb/gateway.yaml`, added to the
  component's `resources:`:
  - `AgentgatewayBackend selenium-flow`: static MCP target, the
    `selenium-flow` Service on port 8000, `StreamableHTTP` (path `/mcp`).
  - `HTTPRoute selenium-flow`: parent `network/agentgateway-proxy`, section
    `mcp`; host `mcp.<domain>`; `PathPrefix /selenium-flow/mcp` and
    `/.well-known/oauth-protected-resource/selenium-flow/mcp`.
  - `AgentgatewayPolicy selenium-flow` on that route: `jwtAuthentication`
    `Strict`, the realm issuer, the shared MCP audience, the realm JWKS;
    `mcp.resourceMetadata` `resource: https://mcp.<domain>/selenium-flow/mcp`,
    `scopesSupported: [mcp]`; **no `mcp.provider`** (agentgateway#3668); the
    authorization rule on the `mcp` role; and `backend.auth.passthrough: {}`.
- **`config.yaml`** gains the `oidc:` block: the realm issuer, the shared MCP
  audience, the realm JWKS URL, `roles: [mcp]`. The token stays in env.
- **`.mcp.json`** (`/projects/cluster`): `selenium-flow` moves to
  `https://mcp.<domain>/selenium-flow/mcp`, drops `Authorization` (an
  `Authorization` header stops Claude Code from doing OAuth), and keeps
  `X-Session-Key` — a header, because the gateway drops the query string.
- `apps/agentgateway/AGENTS.md`: the components table says what is live.

`ROUTE_PREFIX` stays unset: the gateway's MCP backend calls `/mcp` on the Service
directly, and the ingress keeps stripping `/flow` for the admin UI.

## Testing

**Through the real entry point** (a Starlette `TestClient` on
`server.mcp.http_app()`), with a JWKS served for real: a fixture runs a stdlib
HTTP server on loopback publishing one RSA key, and tokens are signed with
FastMCP's `RSAKeyPair`.

- A valid JWT lists tools; an `initialize` with it succeeds.
- Refused with 401: wrong `aud`, wrong `iss`, expired, signed by another key,
  holding none of `oidc.roles`, a `roles` claim that is not a list.
- The server token still works on `/mcp` with OIDC on, and alone without it.
- `session://current` names the principal: `oidc` with subject and username for
  a JWT, `admin` for the token, `null` when open.
- A JWT is refused on a REST route (`/browser/...`): the round's boundary, held.

**Unit:** `Principal.from_claims` / `status`; the roles-claim walk; the settings
(env names, comma split, all-or-nothing, URL check); `load()` refusing `oidc`
without a token, and accepting `oidc` in the file with `AUTH_TOKEN` in env;
`ServerTokenVerifier` accepting only the exact token.

The Grid-backed integration suite needs no change: it runs with the token. There
is no Keycloak in CI; the live test below is where the real issuer is proven.

**Live, on the cluster** (the acceptance test):

1. Deploy the branch image; apply the cluster changes; the gateway route and
   policy report Accepted/Attached.
2. `GET /.well-known/oauth-protected-resource/selenium-flow/mcp` names Keycloak;
   an unauthenticated `POST /selenium-flow/mcp` is 401 with `WWW-Authenticate`.
3. In Claude Code, `/mcp` → authenticate `selenium-flow` (the localhost callback
   is pasted into the box, as for kb), then call a tool.
4. `session://current` shows `kind: oidc` and Dr K's username, and the session
   is `claudecode`: `X-Session-Key` crossed the gateway.
5. Check what only shows live: tools hidden per client (`clientInfo` across the
   gateway), a long `assert` and a `run_flow` (gateway timeouts), an MCP Apps
   view, a screenshot link.
6. In-cluster with the server token: unchanged. In-cluster with a JWT lacking
   the `mcp` role: 401.

## Documentation

- **README:** the auth section says the token, plus an OIDC issuer behind a
  gateway; one example block. It advertises, it does not explain.
- **AGENTS.md:** the auth rules — the provider is built in `http/auth.py`; the
  principal exists and decides nothing yet; the REST routes are token-only on
  purpose; OIDC needs the token.
- **CHANGELOG** (Unreleased): one line for OIDC, one for the constant-time token
  on `/mcp`, one for `principal` in `session://current`.
- **wiki** `Configuration.md` and `Deployment.md`: the `oidc` settings, and the
  gateway deployment.

## Next round (the doors this leaves open)

- The admin UI's "sign in with OIDC" option, under the token field
  (`oidc.client_id`, PKCE, the same verifier).
- Workspaces: the rename, and whether they get owners.
- A JWT on the REST routes.
- Per-tool roles (`execute_script`, `delete_flow`, secret binding), which also
  filter `tools/list`.
- The principal in logs and in the admin live list.
- A per-server audience, if the shared one ever lets a token for one server open
  another that should be stricter.
- Whether the ingress should stop routing `/mcp`, so the gateway is the only
  external MCP door.

## Settled while planning

- **`oidc`, not `auth.oidc`:** the schema is two levels deep.
- **The token-needed rule runs after the merge**, in `load()`, so a file with
  `oidc` and an env `AUTH_TOKEN` loads; `http/auth.py` refuses the same state
  for `Settings` built in code.
- **No version bump of FastMCP:** `MultiAuth`, `JWTVerifier(http_client=...)`,
  `RSAKeyPair`, `AccessToken.claims` and `get_access_token` are all in 4.0.0.
