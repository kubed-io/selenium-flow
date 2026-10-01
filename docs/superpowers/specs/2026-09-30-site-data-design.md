# Site data: a session comes back signed in

Design record for saving and restoring cookies, localStorage and
sessionStorage, and for the history of where a session has been. Round 1
was written 2026-09-30 and shipped in #49 and #50. Round 2 was written
2026-10-01, after Dr K saw round 1 live. This round lives in
`docs/superpowers/` and not in the saga.

**Status:** round 2 is settled. The BiDi spike passed (below) and every
question is ruled. The Penpot file *Admin UI* holds the drawing on the pages
**Session · Site data** and **Session · History**, in version *Site data
design, round 2 — History rows fold*; the session tab bar on every page gains
History.

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
straight into the browser, and splits the tab in two: History and Site data.

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
header). The tab keeps the name and shows only the snapshot; **History** is
its own tab beside it.

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

## The spike (2026-10-01): the spare tab works

Run against this Grid: Chrome 152 and Firefox 155. Throwaway; nothing kept.

| Check | Chrome | Firefox |
|---|---|---|
| Write another origin's localStorage from a spare tab, then load it for real | pass | pass |
| Read another origin's localStorage from a spare tab | pass | pass |
| sessionStorage in the main tab before a real load | pass | pass |
| An origin with a registered service worker | the worker answers | the worker answers |

- **The site never loads** in the spare tab: it stood on
  `the-internet.herokuapp.com` with a blank body. An unresolvable host works
  too, since no DNS lookup happens.
- **Selenium 4.49's own BiDi API is enough**, with no raw commands:
  `browsing_context.create(background=True)`, a context-scoped
  `network.add_intercept`, `network.provide_response` with a body, and
  `add_event_handler("before_request", …, contexts=[ctx])`. The other
  event name (`before_request_sent`) drops the request fields, and one
  subscription per event means a handler is removed before the next tab.
- **Time per origin**, one tab reused: 30–75 ms in Chrome and 55–140 ms in
  Firefox, plus about 50–100 ms to set up and tear down the tab. That needs
  the client's WebSocket polling lowered from Selenium's 100 ms, which
  otherwise puts a 100 ms floor under **every** BiDi call. `Grid.bidi` sets
  only the timeout today; round 2 lowers the polling too, which speeds up
  round 1's calls as well.
- **A service worker answers first.** On an origin it controls (Squoosh), the
  real app loaded in the spare tab in both browsers. Chrome's intercept never
  saw the navigation; Firefox's saw it and then failed with "no such request".
  Only a CDP call got Chrome past it, and CDP is ruled out. A restore always
  starts in a fresh browser, which has no service worker, so **restore is
  unaffected**; only a save's read of other origins is. See open question 5.

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
- Before a `save_site_data` step, a flow first writes the pages it reached so
  far, in one update and in order, so the save reads their storage; the end of
  the run then writes only what came after.
- **`record.url` is gone: the current URL is `history[0].url`.** A reopen
  navigates there, as it did to `url`.
- Only what `touch` records today enters it. A URL withheld after a secret
  write (§F1.24) records nothing, and so does a page with no origin
  (`about:blank`, `data:`).
- One entry per origin. An entry older than `session.ttl` is dropped on the
  next write; `history[0]` is never dropped while the record lives. At most
  100 entries; the oldest goes first.
- Never contains a value. It holds what `url` already held.
- No migration: a record written before round 2 reads as one that has been
  nowhere and loses its last page once; its browser, settings and site data
  stand.

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
  then the saved sessionStorage in the main tab, which then returns to
  about:blank so an open with no url never shows the stand-in page. Then
  land. The browser is fully restored when `open_session` returns. The silent
  reopen after a reap does the same.
- **False:** nothing is restored and the snapshot is **deleted** (Dr K). The
  history stays: it forgets the sign-in, not where the session has been.
- `fresh` keeps its meaning. `insecure=true` still restores nothing and
  deletes nothing, with the same hint.
- `restore_site_data=false` with `insecure=true` still deletes, as false
  always does.
- Restore is best effort: nothing in it can fail an open.

### The hint

- `open_session` → `"site_data": {"restored": [...hosts], "skipped": [...], "uri": "session://site-data"}`
  — only when something was restored or skipped.
- `open_session(restore_site_data=false)` → `"site_data": {"forgotten": 2}`
  when there was something to forget.
- The first call after a silent reopen carries the reopen's report, once.
  `SessionRecord.reopened` holds it until `touch` hands it to the first
  result from that browser. A flow carries it at run level
  (`FlowRun.site_data`); no step carries `site_data` any more.
- **No arrival hint and no `waiting`:** every origin is in place before the
  open returns.

### Resources (agent)

- `session://site-data` — one entry per host the snapshot holds data for,
  counts only, the hosts the session went to first: `{site, uri, cookies,
  storage}` per row, and `saved_at` at the top of the listing (one save, one
  time). Round 1's admin-only fields (`saved`, `secrets`,
  `unleashed_secrets`, `saved_sites`) and the per-row `saved_at` are gone. The
  history is not on the agent surface.
- `session://site-data/{site}` — one host in full, httpOnly values `"•••"`.
- `session://current` — `url` is `history[0].url`; `site_data` as before.

## What round 2 removes

The preload script and everything that kept it honest: `pending`, the
`waiting` list and the arrival hint, `site_data.preload_source`, `arrive`,
`retire`, `replace`, `Actions.retire_site_data`, the `stale` flag,
`SessionManager._hold_pending`, the `MARKER` tab key, and the per-origin
`saved_at`. Most of the bugs found in #49 and #50 lived here.

## The admin UI: two tabs

History and site data are kept apart, in the model and on screen, the way a
browser keeps its history apart from its cookies and storage. The session
subtabs become **Files · Flows · Site data · History**. Neither tab stores a
row: each is built per request by the admin API.

### History

Where the session has been, joined by **host** to what can be used there.
Drawn: page **Session · History**, flow *History*.

- **The current site is on top**, then the history, most recent first. One
  row per host, **folding like Site data's rows** (Dr K: consistent): the top
  row opens, the rest stay shut, once; after that the fold is the reader's.
  The head: the caret, its latest URL in bold, when it was visited and how
  many secrets ("1m ago · 1 secret"), and a pill with the snapshot's counts
  for that host ("2 cookies · 2 local", zero counts left out), which opens
  that host in Site data. No pill when nothing is saved for it. The top row
  carries no marker: it is the session card's last page.
- Opened, one line per **secret allowed on that host**: 🔑 name, description,
  one pill per key — the Secrets tab's own line. A row with no secrets has
  nothing to open: no caret, its place kept.
- **Secrets never make a row.** A site the session never landed on is not
  listed, whatever the secrets say.
- The tab counts the hosts listed. A session that has been nowhere shows
  "Nowhere yet." — the session card's own words.
- **Clear** sits above the rows on the right, and only while there is more
  than the current site. Its confirm, *Clear history*: "History only: site
  data and the browser are untouched.", then `goes` (the hosts) and `stays`
  (the current site). There is no per-row action: the history expires on
  its own.
- Clear keeps only the top entry: another origin of the current host (a
  second port) goes too, and the host's row stays.
- Later, not now: flows and runs on a row, and every path visited.

### Site data

What a reopened browser gets back: the snapshot and nothing else. Drawn:
page **Session · Site data**, flow *Site data*.

- Above the rows: one "saved 2m ago" pill for the snapshot on the left, and
  **Clear** on the right.
- One row per host the snapshot holds data for — the ad server, the CDN and
  the identity provider reached only by a redirect included, because they
  are restored. Hosts in the history come first, most recent first, then the
  rest alphabetically. No secrets here.
- A row head: its origin (the host when it has none or several), its counts
  ("2 cookies · 2 local · 0 session", or "3 cookies" for a cookie-only
  host), and **Forget**. Expanded as in round 1: COOKIES, LOCAL STORAGE and
  SESSION STORAGE ("none" when empty), per origin.
- A cookie is listed under its own domain. A parent's leading-dot cookie is
  listed under every row it covers, marked shared; it belongs to none.
- The tab counts the hosts listed.
- **Forget (one row)** removes the host's storage and its own cookies
  (`host` or `.host`) from the snapshot; its confirm keeps round 1's
  `goes`/`stays` lines without the secret line. **Clear** deletes the
  snapshot; its confirm, *Clear site data*: "3 sites — a reopened browser
  comes back signed out.", then `goes` and the hosts. Neither touches the
  history or the live browser: the browser keeps what it has, and only the
  next browser opened comes back without it.
- Empty: "Nothing saved — an agent calls `save_site_data` after signing in."
- **Copy dropped:** "Nothing saved. It stays listed because a secret is
  allowed here." and "nothing saved". Every row here has something saved.

### Behind them

- `GET /admin/sessions/{key}/history` — the history joined with secrets and
  per-host snapshot counts; `DELETE …/history` for Clear.
- `GET /admin/sessions/{key}/site-data` — the snapshot by host, no secrets;
  `DELETE …/site-data/{site}` for Forget; `DELETE …/site-data` for Clear.
- Token-gated. The session row gains `history_count` and `history_rev` beside
  `site_data_count` and `site_data_rev`, so each pane repaints for its own
  changes.
- `history_rev` is the history's origins in order and `history[0]`'s URL: a
  new site, a return to an older one, or a page within the top site repaints
  History, whose top row is the session card's last page; only the clock
  moving does not.
- `site_data_rev` is `[saved_at, hosts]`: a save moves the time, a Forget or a
  Clear moves the hosts (neither is a save, so neither moves `saved_at`).

## The skill

`SITE_DATA.md` changes: one save after signing in covers every site the
sign-in touched; a restore is complete when `open_session` returns; no
arrival hint. The limits stay (IndexedDB; sessionStorage for one tab only; a
save after signing out saves you signed out).

## Testing

- Unit: the history's bump, expiry, cap and withheld URLs; `url` derived from
  `history[0]`; the snapshot replacing rather than merging; a failed origin
  keeping its last storage; the cap leaving out the oldest; the History view
  (current on top, secrets joining only visited hosts, snapshot counts per
  host); the Site data view (history hosts first, then the rest); Forget and
  both Clears leaving the other tab's data alone. BiDi faked at `Grid.bidi`.
  Each new test broken on purpose once.
- The standing guards: `test_surfaces`, the declared response shapes, the
  wiki regenerating.
- Fixtures use `example.com`-style hosts, never a real person's domain — the
  drawing's hosts included.
- The integration flow stays: sign in to the admin page, save, and the Site
  data tab lists the token's sessionStorage key.
- **Live before the PR:** Chrome and Firefox, two apps signed in, one save, a
  real end and reopen, both signed in, timed.

## Service workers at save time

A save meets a site with a service worker. The spare tab is answered by the
site's own worker, so the real app runs for a moment in a hidden tab, and an
app that rotates its sign-in token there can leave the saved one stale.

**W3C only, with a fallback (Dr K).** The spare tab serves a page with a
marker; when the marker is missing, the tab closes without reading, that
origin keeps its storage from the last snapshot, and `skipped` says
`{site, reason: "a service worker answered: save while on this site"}`. A save
made while on that site reads its storage directly, as always. The worst case
is round 1's behaviour for that one site, and cookies are never affected.

Why not CDP here: service workers are W3C (the spec is a Candidate
Recommendation Draft, implemented by every engine), but BiDi has no
switch to bypass one — Puppeteer lists `setBypassServiceWorker` as
unsupported over BiDi, and the nearest BiDi issue (#846, only a "from
service worker" flag) is still in discussion. CDP's switch is
Chromium-only: Firefox turned CDP off by default in 129 and Selenium
dropped it for Firefox in February 2025. So CDP would fix Chrome alone,
Firefox would still need the skip, and the rule "no CDP" would buy two
code paths for one narrow case. If a real site needs it, a single
Chromium-only call can be added later without undoing anything.

## Rulings

Round 1:

- Dr K: explicit save, not automatic capture; session-bound; httpOnly values
  masked; lifetime of the session; always save all three stores; no setter;
  the admin view is read-only with Forget; secret rows show key names.
- Planning: BiDi reattached per call; the restore pill dropped; the
  integration flow proves save, not restore.

Round 2 (2026-10-01):

- Dr K: the history is where the session went, not a list of what it could
  use; secrets join a row and never make one; history is per session and
  expires; the two "nothing saved" lines go; the `save_site_data` interface
  stays; keep sessionStorage; the current URL becomes the top of the
  history.
- Dr K: **History is its own tab**, a join view by host of the history, the
  secrets and the snapshot. Site data is only the snapshot. The two are
  separate in the model and meet only in the admin view.
- Dr K: Forget and Clear change the session's store, never the live
  browser. A save after a Forget saves the browser again, forgotten site
  included: a save is intentional, and wiping the browser can be added
  later rather than taken away.
- Dr K: one row per host with its latest URL is the audit trail for now;
  deeper history (flows, runs, paths) is a later discussion.
- Dr K: `restore_site_data=false` deletes the snapshot and keeps the
  history.
- Research: a save is a full snapshot (Playwright), not a merge
  (browser-use's flaw); localStorage is read for every visited origin
  through a spare tab with network interception; cookie provenance is
  unknowable, so cookies show under their own domain.
- Dr K: History rows fold like Site data's.
- Proposed, and taken as the simple rule when Dr K moved on to the PR: a
  page with no origin (`about:blank`, so `open_session(fresh=true)`) records
  nothing. The last page stays the previous site until the next real page,
  and a reap in that gap reopens there.
- Dr K: service workers are handled W3C only, with the fallback in *Service
  workers at save time*; no CDP.
- No browser history API: WebDriver and BiDi can step back and forward but
  cannot list where a tab has been, and a browser's own history dies with
  it on a reap. The session records its own, from the URL each call already
  reports.
