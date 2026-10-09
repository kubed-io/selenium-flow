# Workspaces and observability: the programme

**Status: DESIGNED 2026-10-09, programme level.** This spec holds what every
epic below shares — the research, the rulings, the boundaries between them —
so that each epic's own spec, written on its GitHub issue by an agent that can
read this repo and the Penpot design and nothing else, neither repeats the
research nor re-decides the rulings. Plan:
`docs/superpowers/plans/2026-10-09-workspaces-and-observability.md`. Penpot:
file *selenium-flow*, every page (the rename), the new pages *Workspace ·
Console* and *Workspace · Network*, and *Admin · login*.

An epic's spec cites this one as `programme R<n>` for a ruling and `programme
E<n>` for an epic. The saga (`docs/saga/`) is deprecated as of this round: the
design record is `docs/superpowers/specs/`, and AGENTS.md's pointer moves with
it (E1).

## Brief

Dr K, 2026-10-09, over one brainstorming session, in order:

- *"Network tools and console log tools. We basically want to add some
  debugging tools."* The jobs: **why didn't my action work** (the 500, the
  failed fetch, the JS error a call caused) and **debug the web app itself**
  (all traffic, headers, bodies, errors over time, idle included). Not scraping
  by network, not waiting on the network.
- *"I don't like per call opening a bidi socket, that is overly heavy."* Capture
  turns on *with the session, like the recording*; then resources and possibly
  tools appear; and a tool result that caused an error carries *"a network
  error occurred on this tool call, see foo://bar/baz"*.
- *"Can we reliably get the grid's session timeout from a call to the grid?"* —
  read it, never sync it by hand.
- *"Let the grid reap for now, we can add an end with our code later."*
- *"Monitor first, then capture"*; *"recommendation A"* (one monitor, one bus).
- *"I have wanted an otel/prometheus endpoint… every tool call would label it
  with the session and tool name and if it was part of a flow or not and
  things like how long it took."*
- *"They are actually workspaces. We recently changed the header to be
  x-workspace which is the correct and well known header."* Rename everywhere.
  *"The workspace is literally just a namespace in this app."*
- A workspace assumes the roles of its creator; the admin sees everything; the
  admin UI signs in with OIDC; per-surface roles — *"roles being assigned to
  anything as lowest priority… just have it as a ticket."*
- Prototypes: *"each page in penpot is one view and dynamically clicks to other
  boards on that page to show state changes… just a few clickables"* — no notes
  to click.

## Research that shaped it

Checked against source on 2026-10-09. *Unverified* marks a claim read but not
run.

### A workspace is a namespace

The OIDC groundwork already ruled it (spec 2026-10-04, ruling 4: *"session
names are a project or workspace, not an identity"*), and `X-Workspace` is the
header Claude.ai connectors send (#55). *Session* is overloaded three ways here
— the Grid's browser session, the MCP transport session, our named record —
and the third is the one that is not a session. AGENTS.md's *"a session name is
now the entire credential for driving that browser"* is retired by the rename:
the name is an address; the token or JWT is the credential.

Footprint of `\bsessions?\b`: `kubed/` 51 files, 1 242 hits; `ui/src` 42, 533;
`tests/` 80, 1 923; `wiki/` 47, 573; `skills/` 9, 128; README 69, AGENTS.md
84. Names that cross a wire: `session://current|files|site-data`,
`X-Session-Key`, `?session=`, `SESSION_TTL`, `SESSION_STORE`,
`/admin/sessions/…`, `DATA_DIR/sessions/<name>/`, the Redis prefix
`selenium-flow:session:`.

### How the Grid reaps, exactly

Selenium Grid trunk, `LocalNode.java`, `ProxyNodeWebsockets.java`,
`NodeStatus.java`:

- `currentSessions` is a Caffeine cache with `expireAfterAccess(sessionTimeout)`
  and a `cleanUp` every **30 s**: a browser is reaped between `sessionTimeout`
  and `sessionTimeout + 30 s` after its last access.
- **Access is any session command and any WebSocket frame the browser sends**
  (`DirectForwardingListener.onText/onBinary` → `sessionConsumer.accept` →
  `isSessionOwner` → `getIfPresent`). A BiDi *event* resets the timer as a
  click does.
- So a held socket with **no subscriptions** is silent and changes nothing. A
  socket with **subscriptions on a busy page** (polling, analytics, chat
  heartbeats) keeps the browser alive forever.
- **Measured 2026-10-09** (E2's spec, cluster Grid 4.48.0, Chrome 152): a
  subscribed socket on a page logging every 5 s kept its session alive past
  660 s (2.2× the 300 s timeout); a pinged silent socket was reaped at ~330 s,
  so **pings are not activity**. A silent socket is **not** closed by a
  `DELETE` or a reap: the hub keeps answering pings and swallows commands. A
  held socket is therefore a channel, never a liveness signal; "ended" comes
  from the `/status` listing (E2's spec, its ruling 4). This corrects the
  research above as first written.
- **The timeout is reported, per node**: `GET /status` carries `sessionTimeout`
  (ms) on every node; GraphQL `nodesInfo { nodes { id sessionTimeout } }` and
  `session(id) { nodeId }`. Standalone (`docker compose up`) is a node too, 300 s
  by default.
- Up to `--connection-limit-per-session` (default **10**) sockets per session
  (PR #14410).

### What BiDi can and cannot buffer

W3C WebDriver BiDi; chromium-bidi `EventManager.ts`, `NetworkStorage.ts`;
Firefox `remote/webdriver-bidi/`.

- **Network events are buffered nowhere.** A request completed while nobody is
  subscribed is gone.
- **Console has a replay buffer**: the spec's *log event buffer* replays
  `log.entryAdded` to a new subscriber. Chrome keeps the last **100 per tab**,
  survives navigation, clears on tab close, re-sends to every new socket
  (subscriptions are per `goog:channel`, i.e. per connection) — so a reader
  deduplicates. Firefox replays the current document's, only if the previous
  socket unsubscribed before closing (subscriptions are per session).
- **Session-owned, surviving a socket close**: preload scripts, intercepts (a
  matching request hangs with nobody to answer — never leave one), data
  collectors and their bodies, subscriptions.
- **Data collectors** (`network.addDataCollector`/`getData`/`disownData`):
  bodies kept in the browser, ~200 MB, oldest evicted, session-scoped. Chrome
  shipped; Firefox `response` from 143, `request` from 146; *unverified*:
  Firefox may collect only while a network subscription is active. `getData`
  needs the request id an event delivered.
- **Selenium Python 4.50's BiDi client**: `websocket-client` on a daemon
  thread, a new thread per event, no ping, replies matched by polling. Right
  for a per-call command; wrong for a held listener on an asyncio loop.
- **Legacy `get_log`**: Chrome only, drains on read, non-W3C; the `performance`
  log is raw CDP. Not used.

### How the others do it

| Tool | Console | Network | Window | Bodies | Lesson |
|---|---|---|---|---|---|
| Playwright MCP | `browser_console_messages(level, all)` | `browser_network_requests(static, filter)` + `browser_network_request(index, part)` | since last navigation | by `part=` | Console on every response cost ~6× tokens (#889); now a count and a pointer. Successful static resources hidden by default (#1237). |
| Chrome DevTools MCP | `list_console_messages` + `get_console_message(msgid)` | `list_network_requests` + `get_network_request(reqid)` | last 3 navigations on request | inline, 10k chars | Stable ids; pagination; nothing attached to other results. |
| mcp-selenium | `diagnostics(type, clear)` | same | since open | none | Holds the socket for the session's life; unbounded. |
| WebdriverIO MCP | resource over `get_log` | none | drains on read | — | Chrome only. |
| BrowserStack, Sauce, Moon | capability at open | HAR | whole session | behind a flag | Opt-in at session start. |

Recurring: **list then get by id; hide noise but count it; bounded buffers
evicting oldest; bodies separate and truncated; counts on other results, never
content.**

### Telemetry already half exists

FastMCP 4 emits OpenTelemetry spans for every `tools/call` and
`resources/read` through the OTel API, a no-op until an SDK is configured
(https://gofastmcp.com/servers/telemetry); `mcp.session.id` there is the
*transport* session. Nothing covers HTTP routes or flow steps; nothing is a
metric. Prometheus stores numbers over time; events belong to logs and traces.

### What was ruled out

| Option | Why not |
|---|---|
| A BiDi socket per action, capture what the call caused | "overly heavy" (Dr K); and idle-time traffic is still missed |
| A preload script buffering in the page | per document, lost on navigation, visible to the page; site data removed one after it bred most of #49/#50's bugs |
| Chrome `get_log` / `performance` log | Chrome only; the latter is CDP |
| Ending browsers on our own timer | the Grid owns lifetime; noted for later |
| The workspace name as a Prometheus label | caller-chosen, unbounded series, scraped without auth |
| Two replicas now | three things already pin one; the bus is shaped for it, not built for it |

## Rulings

Dr K, 2026-10-09, unless marked *recommended* (Claude's, to be confirmed on the
epic's issue):

1. **Workspace, and session inside it.** The named record a caller addresses
   is a *workspace*: the outer box, persistent, holding flows, files, site
   data, history and settings. A *session* is the live browser open in a
   workspace: at most one at a time, started by `open_session`, holding and
   controlling the Grid's session (Dr K, 2026-10-09: *"the workspace is the
   outer box and the session is the thing holding and controlling the grid
   session"*). *Session* otherwise means only the Grid's and the MCP
   transport's. The rename reaches every surface. *Recommended:*
   `X-Session-Key` and `?session=` go rather than linger as aliases (spec
   2026-10-02, no compatibility while there is one user). E1's spec rules the
   details (`2026-10-09-workspaces-rename-design.md`, rulings 8–10).
2. **A connection is held only for a browser that owes something.** The
   per-call BiDi socket for site data stays. The monitor holds a socket only
   while a browser is *owed* — capture on, a recording owed — and lets go when
   nothing is. AGENTS.md's "BiDi only for site data" and "do not add a
   scheduler" are amended to say exactly this.
3. **The Grid reaps.** A held subscription keeps a browser alive, so a watched
   browser carries a deadline: last call plus its node's `sessionTimeout`. At
   the deadline the monitor unsubscribes, closes, and lets the Grid's timer
   run. Our code ends no browser on a timer; "end it ourselves" is a later
   option.
4. **The Grid's timeout is read, never configured here.** Per node, from
   `/status`, at every open and reopen, stored on the workspace and shown in
   `workspace://current` and the admin UI.
5. **One monitor, one bus, in process** ("recommendation A"). A `monitor/`
   package below the protocol layers: a registry of watched browsers, an
   asyncio BiDi socket holder of our own, a typed publish/subscribe bus.
   Publishing never blocks a call (hand-off to the loop, bounded queue per
   subscriber, overflow dropped and counted). The bus is an interface a Redis
   backend could implement later; it is not built now.
6. **Events carry no values.** Workspace, browser, tool, surface, outcome, time
   — never arguments, URLs with query strings, headers, bodies, secrets. Any
   subscriber is safe by construction.
7. **One seam for every call.** `call.finished` is published once, where every
   call already passes. Consumers: metrics, capture (the lock means one call
   at a time, so a call's start and end bound the events it caused), the
   admin UI.
8. **Capture is an open choice, exactly like `record`.** `open_session(capture=
   true)` on MCP, `POST /browser {"capture": true}` over HTTP. It is a workspace
   setting with no client default (`"capture": (None, None, _as_flag)` beside
   `record` in `session/settings.py`), replayed on a silent reopen after a reap,
   never inherited by an explicit `open_session`. Off by default, free when off.
   One boolean.
9. **Captured data lives in memory and expires on its own** (Dr K, 2026-10-09:
   *"we don't need to keep any of this, it can all be in mem"*). Per workspace,
   in this process — never the session store, never Redis, never disk — as
   capped ring buffers behind a TTL: a workspace's entries go `capture.ttl`
   after its last entry arrived (default **3 h**). Nothing is cleaned when the
   browser ends; the TTL does it. A restart loses them, and that is accepted.
   An explicit `open_session` without capture discards them. Read by URI:
   `workspace://console`, `workspace://network`, `workspace://network/{id}`;
   default window "since the last navigation", the last few navigations on
   request. A **body** is fetched from the browser on demand through a
   capability (tool and endpoint, read-only), so it exists only while that
   browser does: a resource read never spends a browser round trip.
10. **A result hints, never carries.** A hint only when *this call* caused an
    error — a 4xx/5xx or failed fetch/XHR/document, an `error` console entry
    or uncaught exception — as counts and a URI. Warnings and failed images
    stay silent. Flows carry the hint on the run.
11. **Secrets never reach the buffer.** Values `write` typed from the catalogue
    are masked at capture; `Authorization`, `Cookie`, `Set-Cookie` are masked;
    URLs keep their query strings only where the history does.
12. **Metrics are numbers, not events.** `/metrics` labels: tool, surface
    (`mcp|http|flow`), outcome, browser — never the workspace name. The
    workspace goes on spans and logs. Pods are scraped directly; PromQL sums.
13. **The admin signs in with the token or with OIDC.** An OIDC principal with
    the configured admin role is the admin on the admin API; the token stays
    for good (2026-10-04, ruling 2).
14. **Roles last.** Ownership and per-surface roles are one ticket, specced
    when reached; nothing above waits for it.
15. **Penpot is read by the epics, not edited.** Every board an epic needs is
    drawn in this round; an epic's spec names boards and reads them. Each page
    is one view; its other boards are that view's states, reached by clicking
    the real control (a filter, a row, Clear, Back). No notes to click.
16. **Capture is visible wherever the workspace is** (Dr K, 2026-10-09).
    `workspace://current` reports `capture` (the setting) and `capturing`
    (whether the monitor holds the subscription right now), as it reports
    `recording` today; the `show` app's context view draws both. The admin UI
    shows a pill per state on the workspace card and the summary: **● REC**
    while recording, **● capture** while entries are coming in, **capture
    paused** while capture is on and nothing is listening (the browser is gone,
    or the idle deadline let go). The Console and Network tabs are **usable only
    while capture is on**: with it off they are muted, not clickable, and their
    tooltip names `open_session(capture=true)`.
17. **`show` draws every resource** (Dr K, 2026-10-09). Every URI the server
    serves — every listed resource and every template — has a view in `show`'s
    table, and a test holds it: enumerate the resources and templates through
    the real MCP surface and assert `view_for` matches each. A new resource
    without a view fails the build, the way a capability without both surfaces
    does. Today eight of the URI forms have views; the gap is single files
    (`files/{name}`, `screenshots/{name}`, `recordings/{name}`,
    `downloads/{name}`), `site-data` and `site-data/{site}`, `flow://schema`
    and the skill's files; E3 adds `console`, `network` and `network/{id}`. A
    single file is drawn from its listing entry (as `recordings/{name}` already
    returns one), never from its bytes; markdown and the flow schema are drawn
    as a document. `MAX_SHOWN` still applies.

## Goal

An agent driving a workspace can see what the page did — its console and its
traffic — when it asks, is told when a call it made caused an error, and
operators can see every call and every browser in Prometheus and traces; all
of it under one word for the thing a caller addresses.

## Non-goals

| Not built | Why |
|---|---|
| Interception, mocking, offline, HAR files | not a debugging need anyone named |
| Per-call capture without the flag | ruled out above |
| A dashboard, alerting | the cluster repo's |
| Ownership, per-tool roles, OIDC on `/browser/*` | E6, last |
| Keeping captured console or network past a restart, or in Redis | R9: memory with a TTL is the whole lifecycle |
| Editing the saga for the rename | deprecated, kept as it was |

## Design: the epics

Each epic is one issue (`enhancement` + `agent`), one spec on that issue in
this directory, one plan, one PR. An epic's spec keeps the house form (Brief ·
Research · Rulings · Goal · Non-goals · Design · Verify first · Next round),
cites this document, and names the Penpot boards it reads.

### E1 — Workspaces: the rename

**Brief.** Everything that says *session* and means our named record says
*workspace*: wire, code, disk, UI, skill, wiki, AGENTS.md, README. R1.

**In scope.** `X-Workspace` and `?workspace=`; `workspace://current|files|
site-data`; the `workspace` config section (`WORKSPACE_TTL`,
`WORKSPACE_STORE`); `/admin/workspaces/…`; `DATA_DIR/workspaces/<name>/`; the
`session/` package → `workspace/`, `SessionManager` → `Workspaces` (or the
spec's choice), `SessionRecord` → `Workspace`; the Svelte views, labels and
tests; `references/SESSIONS.md` → `WORKSPACES.md`; wiki notes; OpenAPI;
`tests/golden/`; one changelog line. AGENTS.md rewritten where it explains
sessions, its design-record pointer moved to `docs/superpowers/`, and the
sentence calling the name a credential removed. The Redis prefix default
`selenium-flow:session:` → `selenium-flow:workspace:`.

**Out.** New behaviour. The Grid's session and the MCP transport session keep
their word. The browser's own sessionStorage keeps its name everywhere.

**Open for the spec.** Aliases for one release or none (recommended: none).
Disk: rename `sessions/` → `workspaces/` once at boot, or read both. Redis keys:
migrate, or let them expire ("everything is ephemeral").

**Penpot.** Already renamed: pages *Workspace · Files/Flows/Site data/History*,
components `nav / workspaces`, `workspace-tabs / *`, `toolbar / workspace`,
`block / workspace`, `workspace-card / *`, the Admin page's `workspaces` board,
`setting-row / workspace.*`, every text. Read, don't edit.

**Order.** First and alone: every other epic touches the same files.

### E2 — The session monitor

**Brief.** A monitor that knows as it happens which browsers are open, ended,
and owed; reads each browser's Grid timeout; holds a BiDi socket for the owed
ones; publishes lifecycle events on a bus the recordings collector, the admin
broadcast and later epics consume. R2–R7.

**In scope.** `monitor/`: `events.py` (typed events, the bus), `bidi.py`
(asyncio socket holder: connect, subscribe/unsubscribe by id, events, a few
commands, reconnect, never an intercept), `Monitor` (watches: Grid id,
workspace, `owed: set[str]`, browser, timeout, socket, deadline). Started in
the server lifespan; its task runs only while a watch exists. Events
`browser.opened`, `browser.ended` (cause `ended|gone|lost`); `call.finished`
reserved, first emitted by its first consumer. The recordings collector's
liveness (`/status` listing, `ended`) moves onto the monitor; the admin SSE
stream subscribes and keeps its poll. `grid_timeout` on the workspace, in
`workspace://current` and the summary card. A `tests/test_boundaries.py` rule.
AGENTS.md rulings rewritten (R2, R3).

**Out.** Capture, metrics, ending browsers, a Redis bus, two replicas.

**Open for the spec.** The websocket library (`websockets` is not a dependency
today — uvicorn's `standard` extra only); ping/idle through the ingress
(*unverified*); what a reconnect after a reap owes; the collector's note queue
stays (recommended).

**Verify first.** Through the Grid: a second socket beside a per-call one; the
reap closing a silent socket; a subscribed socket on a polling page not being
reaped; `/status.sessionTimeout` on standalone.

**Penpot.** `summary / live` and `summary / idle` carry the rows `IDLE TIMEOUT`
and `CAPTURE` (Components page; every workspace page shows them).

### E3 — Console and network capture

**Brief.** `open_session(capture=true)`: the page's console and traffic,
captured by the monitor's socket, readable by URI, shown in the admin UI, and
pointed at by a tool result when the call caused an error. R8–R11, R16.

**In scope.** The flag beside `record` and its replay; `log.entryAdded` and
`network.*` subscriptions on the held socket; in-memory ring buffers per
workspace (order of 1 000 each, oldest evicted) with navigation marks, behind
the `capture.ttl` expiry (default 3 h, `cachetools` is already a dependency);
the three resources and their `show` views (`console`, `network`, `request`,
R17); `capture` and `capturing` on `workspace://current` and in the `show`
context view; a read-only capability to fetch one body on demand (data
collectors), visible to the app as well as the model so the request view's
Fetch body can call it; masking at capture; the error hint on results and on
flow runs; the hint routing entry and a TROUBLESHOOTING section; the Console
and Network tabs, disabled while capture is off; the three pills; wiki notes;
changelog.

**Out.** Interception, mocking, offline, HAR, screencast, CDP.

**Open for the spec.** Static-resource noise (hide successful ones with a count,
as drawn, or filter by type); deduplicating Chrome's replayed console entries;
body cap and truncation; the buffer caps.

**Depends on.** E2 merged; E1 vocabulary.

**Penpot.** Page *Workspace · Console*: `console` (the view), its states
`console-errors` (the Errors filter), `console-cleared` (Clear), and
`capture-off` (a workspace with capture off: the tabs muted, the tooltip a
disabled tab shows); flow *Console*. Page *Workspace · Network*: `network`,
`network-detail` (a row), `network-body` (Fetch body), `network-cleared`; flow
*Network*. Components: `pill / capture`, `pill / capture-paused` beside
`pill / rec`; `workspace-card / live` and `summary / live` carry REC and
capture, `workspace-card / idle` and `summary / idle` carry capture paused;
`workspace-tabs / console|network|capture-off`, and Console and Network on
every other tab variant. Page *App · show*: `context` (its Console and Network
chips drill in), `console`, `network`, `request` (a network row); flow *App*.

### E4 — Telemetry: `/metrics` and traces

**Brief.** A `/metrics` endpoint Prometheus scrapes, and OpenTelemetry traces
naming the workspace, the tool, the surface and the flow step — history in
Prometheus, Loki and Tempo, not here. R12, R7.

**In scope.** `call.finished` emitted at the recipe's host, consumed by a
metrics subscriber: counters and histograms by tool, surface, outcome,
browser; gauges for live browsers and Grid slots; `/metrics` at the root and
under the mount like the other ops endpoints. FastMCP's spans enabled by
config (OTel SDK, OTLP exporter from env), our spans for HTTP routes and flow
steps, the workspace as a span attribute, logs correlated. A `telemetry`
config section. README, AGENTS.md.

**Out.** A dashboard, alerting, the workspace as a label, per-request logs.

**Open for the spec.** `prometheus_client` or the OTel Prometheus exporter (one
SDK for both is simpler); whether `/metrics` is token-gated (recommended: no,
by convention — which is why R12 keeps names off it); `outcome` cardinality
(status class, not status).

**Depends on.** E2's bus merged.

**Penpot.** None.

### E5 — The admin UI signs in with OIDC

**Brief.** Below the token field, "Sign in with OIDC": the UI runs the PKCE
flow against the configured issuer; the admin API accepts a JWT whose roles
include the admin role. The token never goes away. R13; 2026-10-04 rulings 2–3.

**In scope.** `oidc.client_id`, `oidc.admin_roles`; the login view; the admin
API and event stream accepting the JWT through `http/auth.py`; signed links
stay HMAC on the token; the principal in the live list; the UI's token storage
reviewed.

**Out.** Ownership and per-workspace visibility (E6); OIDC on `/browser/*`.

**Depends on.** E1 for the UI (merge order only).

**Penpot.** *Admin · login*: the `oidc` row under the form (`btn / oidc`).

### E7 — `show` draws every resource

**Brief.** Close the gap between what the server serves and what `show` can
draw, and add the test that keeps it closed. R17.

**In scope.** Views for single files (from the listing entry: a screenshot as
an image, a recording as a player, a download or kept file as a card with
Open), `site-data` and `site-data/{site}` (a drill-down pair, httpOnly values
`•••` as everywhere), and documents (the skill's markdown, rendered;
`flow://schema` as a JSON tree). The `files` and `folder` views drill into a
single file through `show`. The guard test over every resource and template.
The skill's `show` line and the wiki note.

**Out.** The capture views (E3 draws its own under the same rule); fullscreen
for the player (the show spec's next round).

**Open for the spec.** Whether a single screenshot is shown from its signed
link or as an inline image (size against `MAX_SHOWN`); how a document view
handles a skill file with relative links (drill into `show`, or plain text).

**Depends on.** E1 merged (the URIs it tests are the renamed ones). Parallel
with E2.

**Penpot.** Page *App · show*: `site-data` (a row drills to `site`), `site`,
`file` (a recording), `document`; and `context`, shared with E3.

### E6 — Workspace ownership and per-surface roles (ticket; last)

**Brief.** A workspace created by an OIDC caller takes that caller's roles; a
later caller must hold one; the admin sees everything. Roles per surface —
mcp, console, api — and per tool (`execute_script`, `delete_flow`, secret
binding) that also filter `tools/list`. R14. Opened without the `agent` label;
specced when Dr K reaches it.

## Verify first

The unknowns an epic's plan proves before building on them, each with the
epic that owns it:

1. (E2) A second BiDi socket on one Grid session, beside the per-call one.
   *Measured: yes, two held plus the per-call one.*
2. (E2) The reap closes a silent held socket; a subscribed socket on a polling
   page is not reaped within 2× the timeout. *Measured: the reap does **not**
   close it; the subscribed socket outlived 2.2× the timeout.*
3. (E2) `/status.sessionTimeout` on standalone and under KEDA. *Measured under
   KEDA: 300000 ms; standalone unverified.*
4. (E3) Chrome re-sends the console buffer to a new socket; Firefox replays
   only after an unsubscribe.
5. (E3) Firefox data collectors with and without a network subscription.
6. (E4) FastMCP's spans appear under a configured SDK without touching
   FastMCP's settings beyond `telemetry_mode`.

## Next round

- Ending a watched browser at its deadline ourselves (R3's later option).
- A Redis-backed bus and the lease that lets a second replica hold sockets.
- Console or network as a flow condition ("no console errors on this page").
- E6.

## Sources

W3C WebDriver BiDi (https://w3c.github.io/webdriver-bidi/); SeleniumHQ/selenium
`LocalNode.java`, `ProxyNodeWebsockets.java`, `NodeStatus.java`, PR #14410,
issues #12223, #17978; GoogleChromeLabs/chromium-bidi `EventManager.ts`,
`NetworkStorage.ts`; Firefox `remote/webdriver-bidi`, WebDriver newsletters
143, 146; microsoft/playwright `packages/playwright-core/src/tools/backend/`,
playwright-mcp issues #271, #376, #889, #1031, #1216, #1237;
ChromeDevTools/chrome-devtools-mcp `src/tools/`, `src/collectors/`;
angiejones/mcp-selenium; webdriverio/mcp; https://gofastmcp.com/servers/telemetry;
this repo's spec 2026-10-04 (OIDC groundwork).
