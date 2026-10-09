# The admin UI signs in with OIDC

**Status: DESIGNED 2026-10-09 on branch `issue-62-oidc` (issue #62, programme
E5); planned, not yet built. Assumes E1 (#60) merged. Plan:
`docs/superpowers/plans/2026-10-09-admin-oidc.md`. Penpot: *Admin UI* → page
*Admin*, board `login`, its `oidc` row (`or` · `btn / oidc` "Sign in with
OIDC" · note "the configured issuer · needs the admin role").** Written with no
human in the loop: every decision Claude took is under *Rulings* with what it
costs if wrong, for Dr K to confirm or overturn on the PR.

## Brief

Programme E5 (`2026-10-09-workspaces-and-observability-design.md`): *below the
token field, "Sign in with OIDC": the UI runs the PKCE flow against the
configured issuer; the admin API accepts a JWT whose roles include the admin
role. The token never goes away.* Programme R13: an OIDC principal with the
configured admin role is the admin on the admin API; the token stays for good.
R14: ownership and per-surface roles are E6, not this.

From the OIDC groundwork (spec 2026-10-04): ruling 2, the token stays as an
option for good — it is the admin login and the HMAC key for signed links;
ruling 3, the "sign in with OIDC" option sits *below* the token field; its next
round names `oidc.client_id`, PKCE and "the same verifier".

In scope (E5): `oidc.client_id`, `oidc.admin_roles`; the login view; the admin
API and event stream accepting the JWT through `http/auth.py`; signed links
stay HMAC on the token; the principal in the live list; the UI's token storage
reviewed. Out: ownership and per-workspace visibility (E6); OIDC on
`/browser/*` and the other REST routes.

## Research that shaped it

Checked 2026-10-09. *Measured* means run here; *read* means a source was read
and not run.

**The bearer middleware already verifies every bearer, everywhere** (measured,
fastmcp 4.1.0, probe in the scratchpad). FastMCP installs Starlette's
`AuthenticationMiddleware` with the MCP SDK's `BearerAuthBackend` app-wide, so
a custom route sees `scope["user"]`: an `AuthenticatedUser` carrying our
`PrincipalToken` for the server token or a JWT holding one of `oidc.roles`, and
an `UnauthenticatedUser` — **not a refusal** — for garbage, or for a valid JWT
that lacks `oidc.roles`. So the admin door cannot lean on that result: a JWT
holding `admin` but not `mcp` arrives unauthenticated. It has to verify the JWT
itself, and the cheap way to do that is the same `OidcVerifier` instance (one
JWKS cache, one refetch floor) with the role check split off.

**The admin API's check is one decorator and one inline copy** (read).
`answer.guarded(token)` wraps every `/admin/*` route and calls
`auth.authorized(request, token)` — synchronous, because a token compare is.
The event stream checks `auth.authorized(...) or links.valid(...)` inline,
because `EventSource` cannot send a header: the page gets a signed
`events_url` from the guarded `GET /admin/workspaces`. Verifying a JWT is
async (the JWKS fetch), so the guard becomes async — it already wraps an async
handler, so nothing else moves.

**Today's token storage** (read, `ui/src/admin/Admin.svelte`): the server token
lives in `sessionStorage['sf-token']`, deliberately not `localStorage` ("should
not outlive the tab it was typed into"), and a stored one is probed with
`GET /admin/workspaces` on load. The page's CSP is `frame-ancestors` only
(`http/admin/page.py`): no `connect-src`, so a fetch to the issuer is not
blocked; and `Referrer-Policy: same-origin`, so a URL with `?code=` in it is
never sent as a referrer to another origin.

**The page is served at the mount root and routes after the hash** (read). An
OAuth `redirect_uri` may not carry a fragment, so the hash route has to be
remembered across the round trip and put back.

**The browser can do the whole flow** (read, Keycloak):

- The discovery document (`{issuer}/.well-known/openid-configuration`) answers
  CORS for any origin — keycloak#33434 (2024), where the maintainers call that
  intended. The token endpoint answers CORS for the origins a client lists under
  *Web Origins* (`+` means "the redirect URIs' origins").
- Keycloak adds `iss` to the authorization response (RFC 9207) unless a client
  turns on *Exclude Issuer From Authentication Response*, so a mix-up check is
  free.
- A public client with *Standard flow* and PKCE S256 gets a code, and a refresh
  token by default.

**jsdom has no `crypto.subtle`** (measured, vitest 5.0.2 + jsdom 30.1.1):
`crypto.subtle` is `undefined` under the UI's test environment. Node's
`webcrypto`, installed in the setup file with `Object.defineProperty` (not
`vi.stubGlobal`, which `unstubGlobals: true` removes after the first test),
works and reproduces RFC 7636 Appendix B (`dBjftJeZ…` → `E9Melhoa…`).

**The homelab token** (groundwork, read): Keycloak, the `mcp` client scope puts
the shared MCP audience in `aud` and the gateway client's roles (`mcp`, `admin`)
in a flat `roles` claim. Keycloak puts the requesting client's id in `azp`.

**Penpot, *Admin UI* → *Admin* → `login`** (read with the Penpot MCP, not
edited): the card unchanged — title, the token explainer, `MCP token` field and
`Enter` — and below it one row: `or`, a secondary button **Sign in with OIDC**,
and a muted note **the configured issuer · needs the admin role**. No other
board on the *Admin* page draws an identity: the `workspaces` board's header has
only *Sign out*, and its cards show no principal.

**What was ruled out:**

| Approach | Why not |
|---|---|
| A server-side callback (backend-for-frontend: confidential client, the server holds the tokens, an HttpOnly session cookie) | The browser-apps BCP's strongest pattern, but it brings what this server has never had: a session cookie, CSRF defences on every admin route, server-side token storage that a second replica would not share, and a client secret to provision. The admin API is header-authenticated today, so it has no CSRF surface to defend; a cookie would create one. And the page already holds a *stronger* credential in script reach — the server token. A BFF is additive later if that ever changes (Next round). |
| `oidc-client-ts` or `keycloak-js` | 30–60 kB into a bundle that has one runtime dependency, for a flow that is ~150 lines on WebCrypto; `keycloak-js` also ties the UI to one issuer. |
| Silent renew in a hidden iframe (`prompt=none`) | Broken by third-party-cookie blocking in every current browser; a refresh token in memory does the same job. |
| A config endpoint (`GET /admin/oidc`) | The page shell already carries server values (`__MOUNT__`, `__CONSOLE__`); one more attribute needs no route, no auth decision, and no change to the admin route-table golden. |
| Discovery on the server | The groundwork refused a network dependency at boot for one string; the browser fetches the same document with CORS. |

## Rulings

Programme R13 and R14 and the groundwork's rulings 2–3 bind. The questions #62
leaves open are decided here; Dr K reviews them on the PR.

1. Claude, 2026-10-09: **Authorization Code + PKCE (S256), a public client, the
   code exchanged by the page itself** at the issuer's token endpoint. No server
   callback, no cookie, no client secret, no server-side session. Cost if
   wrong: an XSS in the admin page can read the tokens in memory — the same
   exposure the server token already has in `sessionStorage`, with a credential
   that expires in minutes rather than never; moving to a BFF later replaces
   `oidc.ts` and adds a cookie path to the door, and touches nothing else.
2. Claude, 2026-10-09: **OIDC tokens live in memory only.** `sessionStorage`
   holds only the in-flight PKCE verifier, `state` and the hash to return to
   (deleted on return), and a non-secret marker (`sf-signin=oidc`) so a reload
   re-runs the flow silently with `prompt=none`. Cost if wrong: a reload costs
   two redirects (a few hundred ms) instead of zero, and a lost SSO session
   shows the sign-in card instead of the page.
3. Claude, 2026-10-09: **the server token's storage was reviewed and stays in
   `sessionStorage`.** Memory would make every reload ask for the token; the
   threat that reads `sessionStorage` (script in the page) also reads memory by
   hooking `fetch`. The mitigation that matters is a `script-src` CSP, recorded
   for next round. Cost if wrong: none new — it is today's behaviour.
4. Claude, 2026-10-09: **renew with the refresh token 30 s before the access
   token expires; a failed renewal or any 401 signs out.** Sign out forgets the
   tokens and the marker and does *not* end the issuer's SSO session
   (no RP-initiated logout). Cost if wrong: on a shared browser the next person
   to click *Sign in with OIDC* goes straight through the issuer's still-live
   session; adding an `end_session_endpoint` redirect is a few lines.

   Claude, 2026-10-09, from the PR review; for Dr K to confirm: **an expired
   access token renews once and retries; only a refused renewal or a retried
   401 signs out.** As first ruled, a tab woken from sleep past the token's
   life sent its first call with the dead token, and the 401 signed out —
   with no message, marker and all — a sign-in the refresh token could still
   renew; a renewal whose network was not back yet did the same. Now a call
   whose token is at or within 5 s of expiry waits for the one shared
   renewal, a 401 renews and tries once more, and a renewal the issuer could
   not answer (offline, 5xx, 408, 429) is asked again, 2 s doubling to a
   minute, for as long as the page holds the sign-in; only the issuer's 4xx
   ends it. With no refresh token (Okta and Auth0 issue none without
   `offline_access`, which the UI does not ask for) the sign-in ends when the
   access token does. Every end says *Your sign-in ended; sign in again.*
   Cost if wrong: against an issuer that stays down, a call with an expiring
   token waits rather than fails, for as long as the page is open.
5. Claude, 2026-10-09: **the admin check is the `/mcp` verifier's, minus its
   role, plus two of its own.** One `OidcVerifier` per server: signature,
   `iss`, `aud` (`oidc.audience`), `exp`, `nbf` — then the admin door requires
   `azp` equal to `oidc.client_id` and a role from `oidc.admin_roles` in
   `oidc.roles_claim`. `oidc.roles` (the `/mcp` gate) does not apply to the
   admin API. Cost if wrong: a JWT minted for another client — Claude Code's,
   say, which holds `admin` for Dr K — cannot open the admin API; that is the
   point, and the token still can.
6. Claude, 2026-10-09: **401 for a credential the door does not know, 403 for
   one it knows that is not enough.** No credential, a wrong token, a bad
   signature, wrong `iss`/`aud`, expired, or another client's `azp` is
   `401 {"error": "unauthorized"}` as today; a valid admin-UI JWT without an
   admin role is `403 {"error": "this sign-in does not hold an admin role"}`, so
   the page can say *signed in, but not an admin* instead of *refused*. Cost if
   wrong: one status code in `answer.guarded`.
7. Claude, 2026-10-09: **two settings, no more.** `oidc.client_id` and
   `oidc.admin_roles`, set together or not at all, and only with the issuer
   triple. No redirect-URI setting: the redirect URI is the page's own URL,
   which is what the operator registers. No scope setting: the UI asks for
   `openid`, and the audience and roles come from a client scope the operator
   makes a *default* scope of the client. Discovery is from `oidc.issuer`. Cost
   if wrong: an issuer that needs a scope or an `audience` parameter requested
   needs one more setting then.
8. Claude, 2026-10-09: **the page carries `{issuer, client_id}` in a
   `data-oidc` attribute**, filled into the shell like `__MOUNT__`; empty when
   `client_id` is unset, and then the OIDC row is not rendered. Nothing else
   about OIDC reaches the page — not the audience, the JWKS URI or the roles.
   Cost if wrong: none; both values are public by nature.
9. Claude, 2026-10-09: **the event stream and signed links keep the token's
   HMAC.** The `events_url` and every file link are still signed with
   `auth.token`, now minted for an OIDC admin too (they come from guarded
   routes); `/admin/events` also admits an admin JWT in a header, through the
   same door. An open stream is not cut when the JWT expires or its holder loses
   the role elsewhere. Cost if wrong: up to a link lifetime (~70 min) of
   workspace list after a role is revoked; the UI closes its stream on sign-out.
10. Claude, 2026-10-09: **inside a frame, the button opens the UI in a new
    tab.** The issuer's login page refuses to be framed (Keycloak's default
    security headers; Verify first 7), and a popup cannot hand tokens back to a
    storage-partitioned iframe. The note under it says so. Cost if wrong: a
    dashboard framed in Nextcloud or Grafana signs in with the token.
11. Claude, 2026-10-09: **the principal in the live list is who opened each
    workspace's browser.** A workspace record gains `opened_by`
    (`{"kind", "username"}`), written by `open_session` from the caller's
    principal, kept across a silent reopen, and shown at the end of the card's
    meta line as `by drk` (`by token` for the server token). A REST call carries
    the admin principal when a token is configured. Display only: it decides
    nothing (R14). **Not drawn in Penpot** — the `workspaces` board shows no
    principal; this uses the existing meta line rather than a new element. Cost
    if wrong: one record field and one string to remove, or a board to draw.
12. Claude, 2026-10-09: **hand-written, no library.** `ui/src/admin/oidc.ts` on
    WebCrypto and `fetch`. Cost if wrong: we own the edge cases (listed in §9);
    `oidc-client-ts` is the drop-in if they multiply.

## Goal

A person whose issuer account holds an admin role signs in to the admin UI with
*Sign in with OIDC* instead of pasting the token, and sees and does everything
a token holder does there; the token keeps working beside it, and nothing
outside `/admin/*` changes who it lets in.

## Non-goals

| Not built | Why |
|---|---|
| A JWT on `/browser/*`, `/files*`, `/flows*`, `/site-data*`, `/secrets` | E6; the REST surface stays token-only |
| Ownership, per-workspace visibility, per-tool or per-surface roles | E6 (R14) |
| RP-initiated logout | ruling 4 |
| A BFF, a session cookie, server-side token storage | ruling 1 |
| OIDC sign-in inside a frame | ruling 10 |
| A `script-src` CSP for the admin page | Next round; not OIDC-specific |
| The principal in logs | Next round (groundwork) |
| Any change to `/mcp` | The groundwork's door, unchanged |

## Constraints

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

## Design

### 1. The flow

```
admin page ──(1) GET {issuer}/.well-known/openid-configuration──▶ issuer
    │       (2) redirect: authorize?response_type=code&client_id&redirect_uri
    │           &scope=openid&state&code_challenge&code_challenge_method=S256
    │           [&prompt=none on a reload]
    ◀──(3) {page}/?code&state&iss──────────────────────────────── issuer
    │   (4) POST token_endpoint  grant_type=authorization_code, code,
    │       redirect_uri, client_id, code_verifier          ──▶ issuer (CORS)
    │   (5) GET /admin/workspaces  Authorization: Bearer <access token>
    ▼
selenium-flow  answer.guarded → auth.AdminDoor.admit
               token? ADMIN : OidcVerifier.verify_jwt → azp → admin role
```

1. **Start.** The button calls `oidc.begin(config)`: fetch discovery (and
   check its `issuer` equals the configured one, OIDC Discovery §4.3), make a
   32-byte verifier and a 16-byte `state` (base64url), keep
   `{state, verifier, hash: location.hash, silent}` in
   `sessionStorage['sf-oidc-pending']`, and navigate to the authorization
   endpoint. `redirect_uri` is `location.origin + location.pathname` with one
   trailing `/` — the page's own URL, which is the mount root.
2. **Return.** On load, a `code` or `error` in `location.search` with a pending
   entry is a reply. The entry is read and deleted, and the query is removed
   with `history.replaceState` (the pending hash put back) before anything else
   runs, so the code never sits in the address bar, history or a bookmark.
   Then: `state` must match; `iss`, when present, must equal the issuer; an
   `error` is shown, except that a silent attempt's `login_required`,
   `interaction_required`, `consent_required` or `account_selection_required`
   just shows the card.
3. **Exchange.** `POST` the form to the token endpoint. The response's
   `access_token`, `refresh_token` and `expires_in` go into memory.
4. **Probe.** `GET /admin/workspaces` with the access token, as a token sign-in
   is probed today. 200: signed in, the marker set. 403: the card says
   *Signed in as drk, who does not hold an admin role.* 401: *The server
   refused this sign-in.* Either way the tokens are dropped.
5. **Renew.** 30 s before `expires_in` runs out (never sooner than 5 s from
   now), `grant_type=refresh_token`; the answer replaces the tokens (a missing
   new refresh token keeps the old one). An issuer that could not answer is
   asked again, 2 s doubling to a minute; one that refuses (a 4xx) signs out
   with *Your sign-in ended; sign in again.*, as does the access token running
   out with no refresh token. A call whose access token is at or within 5 s of
   expiry waits for the renewal, and a 401 renews once and retries (ruling 4).
6. **Reload.** Tokens are gone. The marker, if set, is removed and step 1 runs
   with `prompt=none`; the issuer's live session answers at once, and a dead one
   answers `login_required`, which shows the card. Because the marker is
   removed first, an issuer that is down leaves the next reload on the card,
   never in a redirect loop.
7. **Sign out.** Forget the tokens, the marker and `sf-token`; close the
   stream; show the card.

The server token path is unchanged: typed, probed, kept in `sf-token`.

### 2. Configuration

Two leaves in the existing `oidc` section (`config.py`, `OidcSettings`):

| Path | Env | Type | Default | Meaning |
|---|---|---|---|---|
| `oidc.client_id` | `OIDC_CLIENT_ID` | str | unset | The admin UI's public client; setting it offers *Sign in with OIDC*. |
| `oidc.admin_roles` | `OIDC_ADMIN_ROLES` | list, comma-separated in env | `[]` | A JWT from the admin UI holding one of these is the admin. |

`admin_roles` shares `roles`' comma split. Rules, in `config.oidc_problem` (so
after every layer merges, like the groundwork's):

- `client_id` and `admin_roles` are set together or not at all — an empty list
  would let nobody in, or, read the other way, everybody.
- `client_id` needs the issuer triple (and so, by the existing rule, the token).

The section description becomes *"Accept a JWT from an OIDC issuer beside the
token, on /mcp and in the admin UI."* Both leaves show on the Settings tab like
every other; neither is sensitive.

**The issuer side** (Keycloak, documented in the wiki's `Deployment.md`): a
client `selenium-flow-admin`, *Client authentication* off (public), *Standard
flow* on and nothing else, *PKCE* `S256`, *Valid redirect URIs* the UI's URL
(`https://selenium.example.com/flow/`), *Web origins* `+`, and the client scope
that puts the MCP audience and the `roles` claim in the token (the homelab's
`mcp`) as a **default** client scope. The admin role is a role the user holds
in that claim (`admin`).

### 3. What the server serves the page

`http/admin/page.py` gains `sign_in(oidc: OidcSettings) -> str`: the JSON
`{"issuer": …, "client_id": …}` when `client_id` is set, else `""`. The admin
shell gets `data-oidc="__OIDC__"` beside `data-mount` and `data-console`;
`page.mount` fills it (escaped for the attribute, as the others are), so the
ETag already covers it. `ui/src/admin.ts` passes `root.dataset.oidc` to
`Admin`. No route is added.

### 4. The decision, in `http/auth.py`

`OidcVerifier` splits its check in two:

- `verify_jwt(token) -> PrincipalToken | None` — FastMCP's signature, `iss`,
  `aud`, `exp`, then ours: a numeric `exp`, `nbf` within leeway. The principal
  carries the roles `roles_in` reads. No role gate.
- `verify_token(token)` — `verify_jwt`, then the `oidc.roles` gate, exactly as
  today. FastMCP calls this one; nothing about `/mcp` changes.

A new `AdminDoor` answers *may this request use the admin API?*:

```python
class Refused(Exception):          # .status (401 | 403), .reason
class AdminDoor:
    def __init__(self, token, jwt=None, *, client_id=None, roles=()): ...
    async def admit(self, request) -> Principal | None: ...
```

`admit`: no token configured → `None` (open, as today). The token, compared
with `hmac.compare_digest` → `ADMIN`. Otherwise, with no JWT verifier (no
`client_id`) or nothing presented → `Refused(401)`. Then `verify_jwt`; `None`
→ 401; `azp != client_id` → 401 (logged `not the admin UI's client`); no role
in `admin_roles` → `Refused(403, "this sign-in does not hold an admin role")`
(logged `no admin role`); else the JWT's `Principal`. Refusals log the subject
only, through the existing `_log_refusal`.

A new `doors(settings, *, http_client=None) -> Doors` builds both doors from one
verifier: `Doors.mcp` is what `provider()` returned (`provider()` stays, as
`doors(...).mcp`), and `Doors.admin` is the `AdminDoor`, holding the same
`OidcVerifier` instance as `MultiAuth`. `server.py` builds `Doors` once.

`answer.guarded(door)` awaits `door.admit(request)` and turns `Refused` into its
JSON status. `admin.register` takes the door (built from the token when not
given, so a caller passing only a token keeps today's behaviour).

### 5. The admin UI

**`Login.svelte`**, as drawn: below the form, when the page has an OIDC config,
a row — muted `or`, a secondary button `#oidcSignIn` **Sign in with OIDC**, and
the muted note **the configured issuer · needs the admin role**. In a frame
(`window !== window.top`) the button opens the page's URL in a new tab and the
note reads **opens in a new tab: the issuer will not load in a frame**. The
error line `#loginError` shows *That token was refused.* for a token, or the
OIDC message it is given.

**`oidc.ts`** (new): `readConfig`, `discover`, `challengeOf`, `begin`,
`complete`, `refresh` (which throws `Refused` for the issuer's 4xx, and
anything else for a failure worth retrying), `renewIn`, `usernameOf` (the access token's
`preferred_username`, decoded for display only — the server verified it).
`begin` takes the navigation as an argument so tests can catch it.

**`Admin.svelte`**: an `oidc` prop; tokens in a plain variable beside `token`;
the api's bearer is the access token when there is one, else the token; the
boot order is *a reply → a stored token → the marker → the card*; the renewal
timer; sign-out clears all of it.

**`api.ts`**: a refused call throws an `ApiError` carrying `status`, so the
probe can tell 403 from the rest. Messages are unchanged. A `ready` hook runs
before each call (where a call waits for a renewal), and the 401 hook can ask
for one retry with the bearer held then; the server token's never does.

**The live list**: `WorkspaceRow.opened_by`, and `metaLine` appends
`· by drk` / `· by token`.

### 6. Storage and lifetime

| What | Where | Lives until |
|---|---|---|
| server token | `sessionStorage['sf-token']` | the tab closes, or sign out, or a 401 (today's) |
| access, refresh token | a variable in `Admin.svelte` | reload, sign out, a refused renewal, a 401 a renewal does not cure, expiry with no refresh token |
| PKCE verifier, `state`, return hash | `sessionStorage['sf-oidc-pending']` | the reply is read (deleted first) |
| marker `sf-signin=oidc` | `sessionStorage` | read on reload (deleted first), or sign out |
| `localStorage` | nothing | — |

The access token lasts what the issuer says (Keycloak: 5 min by default); the
sign-in lasts as long as the issuer's SSO session lets refreshes succeed —
through a sleep or a dropped network, because a renewal that could not reach
the issuer is tried again — or, with no refresh token, as long as the access
token.

### 7. The event stream

`/admin/events` admits a request when its signed `exp`/`sig` are valid (HMAC on
the token, as today) **or** `AdminDoor.admit` admits it — the header path,
which a browser's `EventSource` never uses but `curl` might. The page's
`events_url` comes from `GET /admin/workspaces`, which an OIDC admin can now
call; nothing about the stream itself changes.

### 8. Signed links

Unchanged: `/files/*`, `/kept/*`, `/screenshots/*`, `/recordings/*` are
authorised by an HMAC over the path and expiry, keyed by `auth.token`. An OIDC
admin's page receives links signed with it like a token holder's. The token is
always there to sign with, because `oidc` without it does not boot.

### 9. Failures

| What happens | Who sees what |
|---|---|
| `oidc.client_id` without `admin_roles`, or the reverse | boot stops: *…set together or not at all* |
| `oidc.client_id` without the issuer triple | boot stops: *…needs oidc.issuer…* |
| `client_id` unset | no OIDC row; a JWT on the admin API is 401 |
| discovery unreachable, CORS refused, its `issuer` differs, or it names an `http:` endpoint for an `https:` issuer | card: *The issuer could not be reached.* |
| the reply's `state` does not match, or there is no pending entry | card: *That sign-in reply was not ours; try again.* URL cleaned |
| the reply's `iss` differs from the issuer | card: *That sign-in reply came from another issuer.* |
| the person cancels, or the issuer answers an `error` | card: *The issuer said: {error_description or error}.* |
| a silent attempt finds no session (`login_required`, …) | the card, no message |
| the token endpoint refuses the code (reused, expired, verifier mismatch) | card: *The issuer would not complete the sign-in.* |
| probe 403 (no admin role) | card: *Signed in as drk, who does not hold an admin role.* Tokens dropped |
| probe 401 (wrong `aud`, `azp`, signature; JWKS unreachable) | card: *The server refused this sign-in.* Server logs the reason with the subject |
| renewal refused (SSO session ended, refresh token spent), or the access token runs out with no refresh token | card: *Your sign-in ended; sign in again.* |
| renewal cannot reach the issuer (offline, 5xx, 408, 429) | asked again, 2 s doubling to a minute; a call whose token is expiring waits for it |
| a call 401s mid-session | the server token: signed out, as today. An OIDC sign-in: renewed and retried once; a second 401 signs out with *Your sign-in ended; sign in again.* |
| the page is framed | the button opens a tab (ruling 10) |
| the issuer is down on a reload with the marker | one failed navigation; the marker is already gone, so the next load shows the card |

### 10. Security considerations

- **Code interception and injection:** PKCE S256 binds the code to this tab's
  verifier; `state` ties the reply to a request this tab made; `iss` defends
  against a mix-up. The code leaves the URL before any other script runs, and
  `Referrer-Policy: same-origin` keeps it out of cross-origin referrers.
- **Token audience and client:** the admin door accepts only a token whose
  `aud` holds `oidc.audience` *and* whose `azp` is the admin UI's client. An ID
  token's `aud` is the client id, so it is refused unless an operator sets
  `oidc.audience` to the client id — documented as a thing not to do.
- **XSS:** tokens are in memory and short-lived; the server token, which never
  expires, is the bigger prize and already in `sessionStorage`. The fix for
  both is a `script-src` CSP (Next round).
- **CSRF:** none to defend — no cookie authenticates anything.
- **Clickjacking:** `frame-ancestors` unchanged; framing still needs
  `security.frame_ancestors`.
- **Open redirect:** the page only ever restores a hash it stored itself, and
  only as a hash.
- **Logging:** no token, code or verifier is logged on either side; refusals
  log `sub`.
- **Revocation:** removing the role takes effect at the next access token (≤ 5
  min); an open event stream and already-issued signed links run to their
  expiry (ruling 9). Rotating the token still revokes every link and stream.
- **Amplification:** a JWT on an admin route reaches the same floored JWKS
  fetch as one on `/mcp`; no new fetch path.

### 11. Documentation

- **README**, Auth: two lines and the two env vars — the admin UI can also sign
  in with the issuer, for a person holding an admin role.
- **AGENTS.md**: the auth rules — `http/auth.py` builds both doors from one
  verifier (`doors()`); the admin API admits the token or an admin-UI JWT
  holding an admin role (401/403); the REST routes and signed links stay
  token-only; the principal decides nothing beyond that.
- **CHANGELOG**: *The admin UI can sign in with OIDC (`oidc.client_id`,
  `oidc.admin_roles`) for a person holding an admin role; the token still works.*
- **wiki**: `Configuration.md` regenerated; `Deployment.md` (hand-written)
  gains the issuer-side client setup from §2 under *Behind agentgateway*;
  `Administration.md`'s *The admin UI* says the page also signs in with OIDC.

### 12. Testing

**Python, unit:** the two settings (env names, comma split, both rules, the
Settings payload); `verify_jwt` ignoring `oidc.roles` while `verify_token`
still requires it; `AdminDoor.admit` for open / token / nothing / admin JWT /
another client's JWT / a roleless JWT / a JWT with OIDC sign-in off; `doors()`
sharing one verifier; `page.sign_in`; the record's `opened_by` written by
`open_browser`, kept by a reopen, read back by `from_json`.

**Python, over real HTTP** (`server.mcp.http_app()`, the loopback issuer in
`tests/jwks.py`): an admin-UI JWT gets `/admin/workspaces` with a signed
`events_url`; a roleless one gets the 403 body; another client's gets 401;
`/admin/events` gives the same 403; an admin-UI JWT on every REST tree is 401;
one JWKS fetch serves `/mcp` and `/admin`; the page carries `data-oidc` and
never the audience or JWKS URI; a REST caller carries the admin principal
when a token is set, so its opens record `opened_by: admin`. The admin-workspaces golden gains `"opened_by": null`.

**UI (vitest):** `oidc.ts` — the RFC 7636 vector, the authorization URL, the
reply's every branch, the exchange's form body, renewal and a refusal told
from a failure, `https:` endpoints under an `https:` issuer, `renewIn`,
`usernameOf`; `Login` — the row hidden and shown, framed opens a tab;
`Admin` — a reply signs in with the bearer and stores no token, 403 says so, the
marker starts a silent sign-in, a woken tab renews once before its first call,
a failed renewal is retried, a 401 renews and retries once, every end says so; `metaLine` with `opened_by`.

**Live, on the cluster** (the acceptance test, Dr K): the Verify-first items,
then sign in through Keycloak, use every tab, reload, wait past an access-token
lifetime, sign out; a user without `admin` sees the 403 message.

## Verify first

1. *Measured:* FastMCP's bearer middleware verifies a bearer on every path and
   hands a custom route an unauthenticated user — not a refusal — for a JWT
   that lacks `oidc.roles`. So the admin door verifies on its own.
2. *Measured:* jsdom lacks `crypto.subtle`; Node's `webcrypto` installed by
   `Object.defineProperty` in the setup file survives `unstubGlobals` and gives
   the RFC 7636 vector.
3. *Read, unverified:* Keycloak's discovery answers CORS for any origin, and the
   token endpoint (both grants) answers it for the client's *Web origins* `+`.
4. *Read, unverified:* Keycloak sends `iss` in the authorization response by
   default.
5. *Unverified:* a token minted for `selenium-flow-admin` with the `mcp` scope
   as a default scope carries the MCP audience in `aud`, `roles: [mcp, admin]`
   for Dr K, and `azp: selenium-flow-admin`. If `roles` comes back empty, the
   client needs *Full scope allowed* or a scope mapping for the gateway client's
   roles.
6. *Unverified:* `prompt=none` in a top-level redirect returns
   `error=login_required` to the redirect URI when there is no SSO session.
7. *Unverified:* Keycloak's login page refuses to be framed by default.

Items 3–7 are the live test's first steps, not code: the plan builds on the
standards and lists them for Dr K.

## Next round

- A `script-src` CSP for the admin page (a hash of the inlined bundle), which
  protects the server token and the OIDC tokens alike.
- RP-initiated logout, if the shared-browser cost of ruling 4 bites.
- A BFF, if the admin page ever needs credentials out of script reach.
- OIDC sign-in in a frame (a popup with `postMessage` to its opener).
- The principal in logs; E6's ownership builds on `opened_by`.
