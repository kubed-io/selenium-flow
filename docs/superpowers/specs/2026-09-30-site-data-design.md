# Site data: a session comes back signed in

Design record for saving and restoring cookies, localStorage and
sessionStorage. Written 2026-09-30, planned in chat with Dr K the same day.
This round lives in `docs/superpowers/` and not in the saga.

**Status:** spec written and drawn. The Penpot file *Admin UI* holds the
drawing on the page **Session · Site data**, in version *Site data design,
round 1 — secret key pills*.

## Goal

A session already outlives its browser: a reaped or ended browser comes back
on the same browser, window and page. It comes back **signed out**, because
sign-in state is not part of what a session remembers. Site data closes that
gap:

- **An agent saves a site's data when it knows it is worth keeping** — after a
  sign-in is confirmed — with one tool, `save_site_data`.
- **A browser opened for the session gets it back**: every cookie at once, and
  each site's storage the moment the browser arrives on that site. The silent
  reopen after a reap does the same, so an agent that comes back hours later is
  signed in without a single extra call.
- **An operator sees it** on a third session subtab, Site data, beside the
  secrets that are allowed on each site.

## Non-goals, and why

| Not built | Why |
|---|---|
| A capture on every call | Dr K: overkill, and every call would pay for it. |
| Capture only when a browser ends | A reap gives no warning, so it is not dependable. |
| A browser profile on the Grid | Nodes are disposable and KEDA-scaled; a profile holds one browser; credentials would sit on shared disks. |
| A setter tool (cookie, localStorage, sessionStorage) | `execute_script` covers storage and ordinary cookies; the one thing only WebDriver can do — set an httpOnly cookie — has no user yet. The skill carries the recipes instead. |
| An agent-facing delete for one site | Forgetting one site is an operator action in the admin UI. `restore_site_data=false` is the agent's clean start. |
| Sharing between sessions | Site data is session-bound: two sessions on the same site each keep their own, and nothing on the MCP surface reads another session's. |
| Choosing which stores to save | Always all three. MSAL keeps its sign-in in sessionStorage and Auth0's SPA SDK in localStorage; a narrower save is a sign-in that does not come back. |
| IndexedDB | WebDriver has no reasonable way to dump it. Named in the skill as a limit (Firebase Auth lives there). |

## The name

**Site data** — the browsers' own term for exactly these three stores
together ("Cookies and site data" in Chrome and Firefox, the
`Clear-Site-Data` header). "State" collided with what a session already
remembers, and "sign-in" undersold preferences, flags and consent.

## Why WebDriver BiDi

Classic WebDriver only sees the site the browser is on, and a cookie can only
be set while the browser is on its domain. WebDriver BiDi is the W3C standard
half of WebDriver — not CDP — and works on Chrome and Firefox alike. Two of its
modules make this feature work, and both were proved against this Grid on
2026-09-30 in both browsers:

- `storage.getCookies` reads **every** cookie the browser holds, from any page,
  and `storage.setCookie` sets one for a site **never visited**, httpOnly
  included.
- `script.addPreloadScript` runs code at the start of every new document,
  **before the page's own scripts** — so a site's storage is in place before
  the app reads it, redirects the server never saw included.

**Every call reattaches.** The server holds no driver between calls: each
action binds a fresh `ReattachDriver` to the Grid's session id. BiDi does the
same: `Grid.bidi(session_id)` binds a reattached driver whose `webSocketUrl`
is derived from `GRID_URL` — `ws://<grid>/session/<id>/se/bidi` — and closes
its socket when done. Proved: reattach, set a cookie and a preload script,
drop the socket, reattach again, and both are there; ~200 ms per reconnect,
paid only when saving and restoring.

Every browser is opened with BiDi enabled (`enable_bidi`, the `webSocketUrl`
capability). Every existing tool keeps using classic WebDriver; only site
data speaks BiDi. The AGENTS.md rule "plain W3C WebDriver only, no CDP" still
holds and now names BiDi as part of it.

## The model

A `site_data` field on `SessionRecord`, in the session store (memory or
Redis) and **never on disk**. It rides the record's own expiry — a session
unused for `session.ttl` goes, and its site data with it. No new setting.

```json
{
  "cookies": [
    {"name": "rack.session", "value": "…", "domain": "the-internet.herokuapp.com",
     "path": "/", "http_only": true, "secure": true, "same_site": "lax",
     "expiry": null}
  ],
  "origins": {
    "https://the-internet.herokuapp.com": {
      "local": {"theme": "dark"}, "session": {}, "saved_at": 1790800000.0
    }
  },
  "saved_at": 1790800000.0,
  "pending": {"browser": "<grid id>", "origins": ["https://keycloak.example.com"],
              "script": "<preload id>", "announce": false}
}
```

- `cookies` — the whole jar, replaced by each save.
- `origins` — storage per origin, added or replaced one origin per save.
- `pending` — volatile, per browser: which origins still wait for their
  storage, and the preload script that carries it. Reset whenever a browser
  is opened.
- **A site is a host.** Cookies carry a domain and no scheme or port, so the
  view groups by host: every host with saved storage, plus every cookie
  domain no such host covers. A cookie whose domain starts with `.` is
  **shared** and shows under every host it covers.
- **Size cap: 1 000 000 bytes** of JSON for the whole field. A save that would
  pass it keeps the cookies, leaves that origin's storage out, and says so in
  its result — it neither fails nor truncates.
- Values are never logged.

## The surface

### `save_site_data(url=None)`

One new tool, and its endpoint `POST /browser/save-site-data` (one to one).

- Saves every cookie the browser holds, for every site, and the localStorage
  and sessionStorage of the page it is on. `url` navigates there first, as on
  every action.
- The result names what was saved, never a value:
  `{"url": …, "title": …, "saved": {"cookies": 14, "sites": ["https://app.example.com"], "skipped": []}, "uri": "session://site-data"}`
- A page with no origin (`about:blank`, `data:`) saves cookies only.
- It is a flow step like any other, so a login flow ends with it.
- Annotations: not read-only (it writes the session's store), not destructive
  (it tells the page nothing), idempotent.

### `open_session(restore_site_data=true)`

- **True (default):** a new or reopened browser gets every saved, unexpired
  cookie at once and each origin's storage on arrival. The silent reopen after
  a reap does the same, because restoring is what the session does, not what
  one call asks for.
- **False:** the browser opens with nothing and the saved site data is
  **deleted** (Dr K). It is also how an agent starts as a brand new user.
- `fresh` is independent and keeps its meaning: a blank page, possibly signed
  in.

### The hint

Only when something happened in that call, and never a value (§F2.10: what an
agent reads is the result, not the docstring):

- `open_session` →
  `"site_data": {"restored": [...], "waiting": [...], "skipped": [...], "uri": "session://site-data"}`
  `restored` is every host whose data is fully in place: the cookie-only hosts
  and the landing page's host. `waiting` is every origin whose storage fills
  on arrival. `skipped` names a cookie the browser refused, with the reason.
- `open_session(restore_site_data=false)` → `"site_data": {"forgotten": 2}`
  when there was something to forget.
- Any call that lands on a waiting origin →
  `"site_data": {"restored": ["https://keycloak.example.com"], "uri": "session://site-data/keycloak.example.com"}`
- The first call after a silent reopen carries the reopen's own report, once.
- A flow step that restored carries it in its own entry: each step's entry
  carries its own `site_data`, built via `SessionManager.settle`, which both
  `act` and the flow runner call.

`waiting` appears only in `open_session`'s answer; a quiet result means
nothing changed.

### Resources

- `session://site-data` — the listing: counts per site, never values.
- `session://site-data/{site}` — one site in full: cookies with name, domain,
  path, expiry and flags; localStorage and sessionStorage keys with values.
  **httpOnly cookie values are `"•••"`** — the one thing the page itself cannot
  read (Dr K). Everything else is shown.
- `session://current` gains `"site_data": {"sites": 2, "uri": "session://site-data"}`
  when there is any.
- The caller's own session only. HTTP: `GET /site-data` and
  `GET /site-data/{site}`, beside `/files`.

## Restore, step by step

On every browser open (`open_session`, and the reopen inside `resolve`), when
the record has site data and restore is on:

1. Drop expired cookies; set the rest with `storage.setCookie`. A refused
   cookie is skipped with its reason, never fatal.
2. If any origin has storage, add **one** preload script carrying every
   origin's storage. On each new document it checks `location.origin`, fills
   that origin's localStorage and sessionStorage **once per tab**, and marks
   the tab with a sessionStorage key `selenium-flow:restored:<origin>`. Keys
   with that prefix are never saved.
3. Navigate to the landing page as before; its storage is already in place.
4. Record `pending` for this browser.

After each call, `SessionManager.act` compares the page's origin with
`pending.origins`. A match is announced in the result and leaves `pending`;
when nothing is waiting any more, the preload script is removed. Restore is
best effort: nothing in it can fail an open.

## Forgetting a site (admin)

Removes that host's origins and its own cookies — those whose domain is the
host or `.host`, so a row that exists only because of a parent-domain cookie
can be forgotten. Other parents' leading-dot cookies stay, because other sites
use them; the confirm says which stay. **Secrets are never touched.**

## The admin UI

Drawn: page **Session · Site data**, flow *Site data*.

- A third subtab: **Files · Flows · Site data**, counting sites with saved data.
- **Rows are the union** of sites with saved data and sites a secret is
  allowed on (Dr K): a secret keeps its site listed when nothing is saved,
  because secrets are not ephemeral. Forget removes the saved data; the row
  stays while a secret matches, and goes when nothing is left.
- A row header: origin (or host), a "saved 2m ago" pill, counts
  ("2 cookies · 2 local · 0 session · 1 secret"), and **Forget** when there is
  saved data.
- Expanded: COOKIES (name, value or dots, domain · expiry in grey, pills
  httpOnly / secure / shared), LOCAL STORAGE, SESSION STORAGE ("none" when
  empty), SECRETS (🔑 name, description, **one pill per key name**, as the
  Secrets tab shows them).
- Empty state: "Nothing saved — an agent calls `save_site_data` after signing
  in."
- The Forget confirm names what goes and what stays.
- There is **no restore on/off pill**: `restore_site_data=false` deletes, so it
  could only ever say "on".
- Live: the session row's `site_data_rev` changes on a save or a Forget, and
  the pane reloads, the way Files and Flows follow theirs. A restore changes
  nothing the pane displays, so it does not repaint for one.
- Secrets with no `allowed_urls` are not listed under every site.

Behind it: `GET /admin/sessions/{key}/site-data` and
`DELETE /admin/sessions/{key}/site-data/{site}`, token-gated like the rest.

## The skill

A new reference, `skill://selenium-flow/references/SITE_DATA.md`: when to save
(after a confirmed sign-in, never before), what restore does and how to read
the hint, "restored but still signed out" (the site ended the session itself —
sign in and save again), starting as a new user, debugging recipes with
`execute_script`, and the honest limits (IndexedDB; httpOnly cookies cannot be
set from a script; a save after signing out saves you signed out).
`SKILL.md`, `SESSIONS.md`, `SECRETS.md`, `FLOWS.md` and `TROUBLESHOOTING.md`
each change a line or two. The wiki page for `save_site_data` is generated;
guidance goes in `wiki/notes/save_site_data.notes.md`.

## Testing

- Unit: the merge rules, expiry, the cap, masking, the view's grouping and
  shared cookies, the hint only when something happened, delete on
  `restore_site_data=false`, Forget keeping shared cookies and secret rows,
  secrets matched by host, the record's round trip through both stores. BiDi
  is faked at `Grid.bidi`. Each new test is broken on purpose once.
- The standing guards: `test_surfaces` (tool ↔ endpoint), a declared response
  shape, the wiki regenerating.
- **One integration flow:** sign in to the admin page, `save_site_data`, then
  open the Site data tab and assert the session's own site lists the admin
  token's sessionStorage key. Flows cannot open or end browsers, so restore is
  proved by unit tests and by the live check.
- **Live, before the PR:** the branch in the pod against the real Grid, Chrome
  and Firefox, including a real end and reopen, timed.

## Rulings

- Dr K: explicit save, not automatic capture; session-bound; httpOnly values
  masked; lifetime of the session; always save all three stores; no setter;
  the admin view is read-only with Forget; rows include sites with secrets;
  secret rows show key names.
- Planning: restore on arrival via a preload script rather than server-side
  URL watching; BiDi reattached per call; the restore pill dropped; the
  integration flow proves save, not restore.
