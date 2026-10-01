# Site data: a session comes back signed in

Design record for saving and restoring cookies, localStorage and
sessionStorage, and for the history of where a session has been. Round 1
was written 2026-09-30 and shipped in #49 and #50. Round 2 was written
2026-10-01, after Dr K saw round 1 live. This round lives in
`docs/superpowers/` and not in the saga.

**Status:** round 2 is a draft. It waits on the BiDi spike (below) and one
more round of questions. The Penpot file *Admin UI*, page **Session · Site
data**, still shows round 1 and is redrawn once this settles.

## What round 2 changes, and why

Seen live, round 1 listed **every site any secret is allowed on, in every
session**, whether the session went there or not. Rows were the union of
saved data and secrets, so the tab answered "what could this session use"
rather than "where has it been". Two more things came out of the
discussion that followed:

- **A save overlaid storage.** The cookies were replaced on every save, but
  each origin's storage was added beside the last. A sign-out never removed
  the storage saved before it. browser-use has the same flaw, and Playwright
  does not.
- **A save only reached one site's storage.** Only the page the browser is
  on can read its own storage, so an agent signed in to two apps had to save
  on each. A save on the second one quietly left the first one out.

Round 2 adds a history, makes a save a full snapshot, writes a restore
straight into the browser, and rebuilds the tab as a view over the history.

## Goal

- **As an agent**, I restore a session's data so that the browser remembers
  me. One `save_site_data` after signing in is enough, however many sites
  the sign-in touched.
- **As an admin**, I see where the agent has been and what it saved, so
  that I have an audit trail and the context to follow while it works.

## Non-goals, and why

| Not built | Why |
|---|---|
| A capture on every call | Dr K: overkill, and every call would pay for it. The history records URLs, which every call already writes. |
| Capture only when a browser ends | A reap gives no warning, so it is not dependable. |
| A browser profile on the Grid | Nodes are disposable and KEDA-scaled; a profile holds one browser; credentials would sit on shared disks. Browserbase, Steel and Hyperbrowser do this, and none of them can show a per-site view. |
| Knowing which page set a cookie | No browser records it, and BiDi does not expose it (research, 2026-10-01). A cookie is shown under its own domain, which is the only truth the jar holds. |
| Grouping sites by registrable domain | Chrome and Firefox group by eTLD+1. This homelab lives under one domain, so every app would be one row. |
| A setter tool (cookie, localStorage, sessionStorage) | `execute_script` covers storage and ordinary cookies; setting an httpOnly cookie has no user yet. |
| An agent-facing delete for one site | Forgetting a site is an operator action. `restore_site_data=false` is the agent's clean start. |
| Sharing between sessions | Site data and history are session-bound; nothing reads another session's. |
| IndexedDB | Playwright made it opt-in in 1.51; nothing here needs it yet. Named in the skill as a limit (Firebase Auth lives there). |
| sessionStorage for every origin | It belongs to one tab, so only the current tab's current origin can be read. Playwright skips it entirely; we keep the one we can reach, because MSAL and this server's own admin page sign in through it. |

## The name

**Site data** — the browsers' own term for these three stores together
("Cookies and site data" in Chrome and Firefox, the `Clear-Site-Data`
header). The tab keeps the name and now opens on the history.

## Why WebDriver BiDi

Classic WebDriver only sees the site the browser is on. WebDriver BiDi is
the W3C standard half of WebDriver — not CDP — and works on Chrome and
Firefox alike. Round 2 uses three of its modules:

- `storage.getCookies` / `storage.setCookie` read and set **every** cookie,
  from any page, httpOnly included (proved 2026-09-30, both browsers).
- `browsingContext.create` opens a **spare tab** for the save and restore
  below, and closes it after.
- `network.addIntercept` + `network.provideResponse` answer that tab's
  requests with an empty page, so the tab can stand on any origin without
  the site loading. This is Playwright's own method for `storageState`.
  Firefox supports a response body from version 129.

**Every call reattaches.** The server holds no driver between calls.
`Grid.bidi(session_id)` binds a reattached driver whose `webSocketUrl` is
`ws://<grid>/session/<id>/se/bidi`, and closes the socket when done.

## The spike, before any code

The spare tab is the one unproved piece. Against this Grid, in Chrome and in
Firefox:

1. The Grid's browser versions (Firefox must be ≥ 129).
2. A spare tab with an intercept that answers every request with `' '`:
   navigate it to an https origin, set and read localStorage, close it.
   Then send the main tab to that origin for real, and the value is there.
3. The same for sessionStorage in the **main** tab: intercept it, stand on
   the origin, set it, drop the intercept, navigate for real, and it is
   still there.
4. Whether Selenium 4.49's Python BiDi API reaches `provideResponse`, or the
   raw command has to be sent.
5. The time per origin.
6. Whether a registered service worker answers the spare tab's navigation
   before the intercept does. A restore always starts in a fresh browser,
   which has none, so this only affects a save.

If Firefox fails 2 or 3, round 2 falls back: a save still becomes a
snapshot, and a restore keeps round 1's preload script.

## The model

Two fields on `SessionRecord`, in the session store (memory or Redis),
**never on this server's disk**; with the Redis store they are as durable as
Redis. Both expire with the record (`session.ttl`, 24 hours since the last
use). No new setting.

### `history`

```json
[
  {"origin": "https://grafana.kubed.io", "url": "https://grafana.kubed.io/d/abc", "at": 1790900000.0},
  {"origin": "https://keycloak.kubed.io", "url": "https://keycloak.kubed.io/realms/x/login", "at": 1790899000.0}
]
```

- **Every call that lands on a page** bumps that origin to the top with its
  URL and time, or adds it there. A flow writes its steps' origins in one
  update at the end of the run, in order.
- **`record.url` is gone: the current URL is `history[0].url`.** A reopen
  navigates there, as it did to `url`.
- Only what `touch` records today enters it. A URL withheld after a secret
  write (§F1.24) records nothing, and so does a page with no origin
  (`about:blank`, `data:`).
- One entry per origin. An entry older than `session.ttl` is dropped on the
  next write; `history[0]` is never dropped while the record lives. At most
  100 entries; the oldest goes first.
- Never contains a value. It holds what `url` already held.

### `site_data`: one snapshot

```json
{
  "cookies": [
    {"name": "rack.session", "value": "…", "domain": "the-internet.herokuapp.com",
     "path": "/", "http_only": true, "secure": true, "same_site": "lax",
     "expiry": null}
  ],
  "origins": {
    "https://the-internet.herokuapp.com": {"local": {"theme": "dark"}}
  },
  "session": {"origin": "https://the-internet.herokuapp.com", "items": {}},
  "saved_at": 1790800000.0
}
```

- **Each save replaces the whole field.** Nothing is merged, so a sign-out
  saved after a sign-in is the state that comes back.
- `cookies` — the whole jar.
- `origins` — localStorage for every origin in the history that has any.
  Origins with empty storage are left out.
- `session` — sessionStorage of the tab the browser was on, for its origin.
- Round 1's `pending`, its preload script and the tab marker are gone (see
  *What round 2 removes*).
- **Size cap: 1 000 000 bytes** of JSON. Over it, the storage of the origins
  visited longest ago is left out until it fits, each named in `skipped`. If
  the cookies alone pass it, the save is a 400 and the record is unchanged.
- Values are never logged.

## The surface

### `save_site_data(url=None)`

Same tool and endpoint (`POST /browser/save-site-data`). What happens
underneath:

1. Read the whole jar.
2. Read localStorage and sessionStorage from the page the browser is on.
3. For every other origin in the history, read localStorage in the spare
   tab, one origin at a time, then close the tab.
4. Replace the snapshot.

- If one origin's read fails, that origin keeps its storage from the last
  snapshot, and the result says so in `skipped` — a blip must not sign the
  agent out.
- The result names what was saved, never a value:
  `{"url": …, "title": …, "saved": {"cookies": 14, "sites": [...origins], "skipped": []}, "uri": "session://site-data"}`
- BiDi down is still a 503 with nothing saved. It is still a flow step.
- Annotations unchanged: not read-only, not destructive, idempotent.

### `open_session(restore_site_data=true)`

- **True (default):** before the browser goes to its page, write in every
  unexpired cookie, then every origin's localStorage through the spare tab,
  then the saved sessionStorage in the main tab. Then land. The browser is
  fully restored when `open_session` returns. The silent reopen after a reap
  does the same.
- **False:** nothing is restored and the snapshot is **deleted** (Dr K).
- `fresh` keeps its meaning. `insecure=true` still restores nothing and
  deletes nothing, with the same hint.
- Restore is best effort: nothing in it can fail an open.

### The hint

- `open_session` → `"site_data": {"restored": [...hosts], "skipped": [...], "uri": "session://site-data"}`
  — only when something was restored or skipped.
- `open_session(restore_site_data=false)` → `"site_data": {"forgotten": 2}`
  when there was something to forget.
- The first call after a silent reopen carries the reopen's report, once.
- **No arrival hint and no `waiting`:** every origin is in place before the
  open returns.

### Resources (agent)

- `session://site-data` — one entry per host the snapshot holds data for,
  counts only. Unchanged in shape; the history is not on the agent surface.
- `session://site-data/{site}` — one host in full, httpOnly values `"•••"`.
- `session://current` — `url` is `history[0].url`; `site_data` as before.

## What round 2 removes

The preload script and everything that kept it honest: `pending`, the
`waiting` list and the arrival hint, `site_data.preload_source`, `arrive`,
`retire`, `replace`, `Actions.retire_site_data`, the `stale` flag,
`SessionManager._hold_pending`, the `MARKER` tab key, and the per-origin
`saved_at`. Most of the bugs found in #49 and #50 lived here.

## The admin UI

A view, built per request by the admin API from three things: the history,
the snapshot, and the secret catalogue. Nothing in it is stored as a row.

- **The current site is on top**, then the history, most recent first.
  Each row is one **host** (its origins' storage shown per origin). Each row
  shows its latest URL, when it was visited, counts, and the secrets
  allowed on that host (🔑 name, description, one pill per key).
- **Secrets never make a row.** A site the session never landed on is not
  listed, whatever the secrets say.
- **Other cookies**, collapsed at the bottom: one row per cookie domain that
  no visited host covers — the ad server, the CDN, the identity provider
  reached only by a redirect. They are in the snapshot and are restored, so
  they are shown; they are not history, so they are not mixed into it.
- A cookie is listed under its own domain. A parent's leading-dot cookie is
  listed under every visited host it covers, marked shared; it belongs to
  none of them.
- One "saved 2m ago" for the snapshot, in the tab's header.
- **Forget (one row):** removes the host's storage and its own cookies
  (`host` or `.host`) from the snapshot, and its history entries. On the
  current site the history entry stays, and only the data goes. A row with
  nothing saved and no history to drop has no Forget.
- **Clear (the whole tab):** deletes the snapshot and the history, except
  the current site's entry.
- **Copy dropped:** "Nothing saved. It stays listed because a secret is
  allowed here." and "nothing saved". A row with nothing saved shows no
  counts.
- Live: `site_data_rev` covers the history's origins in order and the
  snapshot's `saved_at`, so the pane repaints on a navigation and on a save.
- Behind it: `GET /admin/sessions/{key}/site-data` (now with `history`),
  `DELETE /admin/sessions/{key}/site-data/{site}`, and
  `DELETE /admin/sessions/{key}/site-data` for Clear. Token-gated.

## The skill

`SITE_DATA.md` changes: one save after signing in covers every site the
sign-in touched; a restore is complete when `open_session` returns; no
arrival hint. The limits stay (IndexedDB; sessionStorage for one tab only; a
save after signing out saves you signed out).

## Testing

- Unit: the history's bump, expiry, cap and withheld URLs; `url` derived from
  `history[0]`; the snapshot replacing rather than merging; a failed origin
  keeping its last storage; the cap leaving out the oldest; the view (current
  on top, secrets joining only visited hosts, other cookies); Forget on a
  past site and on the current site; Clear. BiDi faked at `Grid.bidi`. Each
  new test broken on purpose once.
- The standing guards: `test_surfaces`, the declared response shapes, the
  wiki regenerating.
- The integration flow stays: sign in to the admin page, save, and the tab
  lists the token's sessionStorage key.
- **Live before the PR:** Chrome and Firefox, two apps signed in, one save, a
  real end and reopen, both signed in, timed.

## Open questions (round 2)

1. Do Forget and Clear also clear the **live** browser? Round 1 applied
   Forget from the next browser. With snapshots, a Forget followed by a save
   brings the data straight back, because the browser still holds it.
   Proposed: yes, through BiDi and the spare tab.
2. Is one row per host with its latest URL enough of an audit trail, or
   should a row expand to every URL visited on that host?
3. Does `restore_site_data=false` keep the history? Proposed: yes — it
   forgets the sign-in, not where the session has been.
4. What does the tab count: visited sites, or sites with saved data?

## Rulings

Round 1:

- Dr K: explicit save, not automatic capture; session-bound; httpOnly values
  masked; lifetime of the session; always save all three stores; no setter;
  the admin view is read-only with Forget; secret rows show key names.
- Planning: BiDi reattached per call; the restore pill dropped; the
  integration flow proves save, not restore.

Round 2 (2026-10-01):

- Dr K: the tab is a history of where the session went, not a list of what
  it could use; secrets join a row and never make one; history is per
  session and expires; Forget on the current site keeps the row; both
  Forget and Clear; the two "nothing saved" lines go; the
  `save_site_data` interface stays; keep sessionStorage.
- Dr K: the current URL becomes the top of the history; cookie domains the
  session never visited stay out of the history, in their own group.
- Research: a save is a full snapshot (Playwright), not a merge
  (browser-use's flaw); localStorage is read for every visited origin
  through a spare tab with network interception; cookie provenance is
  unknowable, so cookies show under their own domain; never-visited cookie
  domains are kept in their own group, out of the history.
