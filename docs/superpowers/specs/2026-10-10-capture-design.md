# Console and network capture

**Status: DESIGNED 2026-10-10, branch `issue-64-capture` (#64). Plan:
`docs/superpowers/plans/2026-10-10-capture.md`.** Epic E3 of the programme
`2026-10-09-workspaces-and-observability-design.md` (cited `programme R<n>`),
built on E2's monitor (`2026-10-09-session-monitor-design.md`, cited `E2 r<n>`).
Penpot file *Admin UI*: pages *Workspace · Console*, *Workspace · Network*,
*App · show*, and the `mode / captured|live|paused` components — read, never
edited, by the implementers.

## Brief

`open_session(capture=true)`: the page's console and traffic, captured by the
session's one BiDi connection, readable by URI, shown in the admin UI, and
pointed at by a tool result when the call caused an error (programme R8–R11,
R16, R17).

Dr K, 2026-10-10, on the shape of it:

- *"Can we use only one socket for everything? … make the socket more like an
  event feed and some dispatcher … if capture is off … we do what we do now and
  open and close it. If it's on, then we bank on the currently open socket."*
- *"If we are getting all of it anyway … the admin view [gets] a toggle to show
  it all … if we 'show it all' we don't necessarily even need to keep it … raw
  feed sent to the admin view with the SSE … the admin view itself can keep a
  sane max in the browser."*
- *"If we need to, we can make a larger refactor to get to an ideal state. I
  want this code to ultimately work like we had all the requirements up front …
  Also remember we have profiling to handle the performance."*
- *"Let's drop 3.10 support … wrap this into E3."*

## Research that shaped it

**Measured on the live Grid, 2026-10-10** (Grid 4.48.0, Chrome 152, Firefox
155; probes and raw output in the session scratchpad, all 13 sessions ended):

| | Chrome | Firefox |
|---|---|---|
| Console replay to a new subscriber (programme Verify first 4) | every new connection that subscribes `log.entryAdded` gets the whole buffer, in order: **100 per browsing context**, surviving navigation; a second subscribe on the same connection replays nothing | replayed **only to the session's first log subscription**, current document only; later subscribers get nothing |
| Events delivered to | the subscribing connection only | **every websocket on the session**, subscribed or not; subscriptions outlive the socket that made them |
| Entry identity | none: `type, level, method, text, args, timestamp(ms), source.context, source.realm, stackTrace`; timestamps collide (4 of 6 in one batch) | same fields |
| Uncaught exception / unhandled rejection | `type: javascript`, `level: error`, `text: "Error: …"`, a `stackTrace`, no `method` | same |
| Data collector (Verify first 5): `network.addDataCollector({dataTypes, maxEncodedDataSize, collectorType: "blob"})` | accepted; `maxEncodedDataSize` (1–200 000 000) **not enforced**; collects **with no network subscription** | accepted, enforced (oversized → `no such network data`); collects **only while some `network.*` subscription is active** |
| `network.getData` | session-owned: works from any socket, after navigation, after the adding socket closed; `{type: string}` text, `{type: base64}` binary, gzip decoded | same |
| `request` dataType | yes | yes (≥ 146) |
| Secrets in events | `request.headers` (`Authorization` twice, `cookie`), `request.cookies[].value`, `goog:postData` (the body inline) | `Authorization`, `Cookie` |
| Navigation marks | `browsingContext.navigationStarted → navigationCommitted → domContentLoaded → load` on the same socket, in order with log entries; `historyUpdated`, `fragmentNavigated`; `contextCreated` (with `parent`) replayed on subscribe | same |
| iframes | console and network on a session-wide subscription, `source.context` / `context` the iframe's | same |

**How others multiplex BiDi** (2026-10-10): Puppeteer's `BidiConnection` and
Playwright's `bidiConnection` hold **one transport per browser**, route replies
by `id` through a callback registry with a per-command timeout, and events by
`method` to an emitter — Playwright off the receive path
(`Promise.resolve().then`). Selenium Python's `WebSocketConnection` is one per
driver, polls for replies, runs callbacks on its socket thread. The known
failure of a shared socket is a slow subscriber on the receive path starving
command replies. BiDi subscriptions filter by event name and by context or user
context only — never by log level or resource type (W3C
`session.SubscribeParameters`).

**Live tails** (2026-10-10): Datadog Live Tail streams everything, stores
nothing, samples under load and says so in its docs; the query view reads
stored, indexed data. Chrome DevTools records only while open. Playwright MCP
defaults to no static resources and `info`+; chrome-devtools-mcp filters on
read, since the last navigation, last three on request.

**Toolbars** (2026-10-10): DevTools and Firefox put Clear first; counts go in
a status line ("24 requests"); nobody shows "0 dropped". Datadog makes Live a
mode of the view, not a filter.

## Rulings

Programme R2, R3, R5–R11, R16, R17 and E2 r6–r8, r12, r16 bind this spec.
Dr K's are marked; the rest are Claude's, for Dr K on the PR, each with what it
costs if wrong.

1. Dr K, 2026-10-10: **one BiDi connection per session, a dispatcher, and two
   lifetimes.** Everything that speaks BiDi — capture, site data, the monitor —
   goes through one client. While the monitor holds a connection for a session
   (capture on), every user of that session shares it; otherwise a user opens
   one, uses it, and closes it. Selenium's BiDi client leaves our code path.
2. Dr K, 2026-10-10: **a stored, filtered buffer for agents; a live, unstored
   tail for the admin page.** The buffer is what `workspace://console|network`,
   the admin's Captured view and the hint read. The tail streams everything,
   masked, to an admin page that asks for it, and is never kept on the server;
   the page keeps the last 2 000 rows.
3. Dr K, 2026-10-10: **successful static requests are not kept, only counted.**
   A request whose resource type is stylesheet, image, font, script or media and
   whose response is 2xx or 3xx is counted per navigation and dropped; every
   failure is kept. Cost if wrong: an agent cannot list a successful image
   after the fact (the tail can).
4. Claude: **`debug` console entries are not kept, only counted** — the same
   rule as static requests, as the Penpot boards draw (`console-live` shows a
   `debug` row the Captured view does not). Cost: one predicate.
5. Dr K, 2026-10-10: **drop Python 3.10; the floor is 3.11**, in this PR, with
   every 3.10 workaround removed.
6. Claude: **capture subscribes before the first page.** `open_session(capture=
   true)` opens the connection, subscribes, and adds the data collector before
   the browser navigates — Firefox replays nothing to a late subscriber and
   collects bodies only while subscribed. A reopen after a reap does the same.
   Cost if wrong: none measured; the alternative loses the first page.
7. Claude: **a connection closes clean.** Before closing, it unsubscribes by
   subscription id and removes the data collectors it added, so Firefox's
   fan-out never floods another socket and no body outlives its reader.
8. Claude: **nothing runs on the read loop.** The reader parses and routes;
   each subscriber has its own bounded queue (1 000) drained by its own task,
   overflow dropped and counted; each command its own timeout. A slow capture
   can never delay a site-data reply or a spare-tab intercept answer.
9. Claude: **Chrome's replay is aligned, not deduplicated by key.** On a
   reconnect the replayed burst (same `source.context`) is matched in order
   against that context's stored tail on `(realm, timestamp, type, level,
   text, first stack frame)`, and only the unmatched suffix is appended —
   identical tuples are legitimate, so a set would drop real entries.
10. Claude: **buffer sizes.** Per workspace: 1 000 console entries and 1 000
    requests (oldest evicted), the last 5 navigations marked; expiring
    `capture.ttl` after the last entry (default 10 800 s); a request's detail
    keeps at most 32 headers of 4 kB each. Cost: two constants.
11. Claude: **bodies stay in the browser.** `network_body(id)` fetches one on
    demand through the session's connection (`network.getData`), capped at
    100 kB of text (marked `truncated`); binary returns its type and size
    only. The capture index maps our request id to the browser's. A body for a
    browser that has ended is a 404 naming why. Cost: the browser decides how
    long a body lives (session-owned, evicted at ~200 MB).
12. Claude: **the hint counts what this call caused.** One call at a time per
    session (the lock), so a call's start and end bound its events; at the end
    one `session.status` round trip on the session's connection lets in-flight
    events land first. Counted: `error` console entries and uncaught
    exceptions; 4xx/5xx and failed document/fetch/xhr requests. Not warnings,
    not failed images (programme R10). The hint is
    `"page": {"errors": n, "failed_requests": n, "see": "<uri>"}`, present only
    when a count is non-zero; a flow run sums its steps'.
13. Claude: **masking happens at capture, once, for the buffer and the tail.**
    Header names `authorization`, `proxy-authorization`, `cookie`,
    `set-cookie` (any case) become `•••`; `request.cookies[].value` too;
    `goog:postData` is dropped; any value the secrets catalogue bound in this
    session (`write` with a secret) is replaced wherever it appears in a URL,
    header, console text or argument. URLs keep their query string only on the
    newest navigation, as the history does (AGENTS.md, site data).
14. Claude: **E2's bus stays value-free.** The tail is capture's own fan-out,
    not a bus event; `call.finished` (E2 r8) carries no values and is emitted
    here, its first consumer being the hint.
15. Claude: **the spare tab is invisible to capture.** Site data's spare tab
    (`site_data/spare.py`) is a context capture ignores, by id, for its whole
    life; its intercepted requests are neither kept nor tailed.
16. Claude: **performance is measured, not guessed.** The plan's last task
    measures dispatcher latency and queue depth on a real page load with
    capture on; E4 (metrics, traces) and the cluster's profiler watch it after.

## Goal

An agent driving a workspace with capture on can read what the page logged and
fetched since its last navigation, is told when a call it made caused an error,
can fetch one response body; an operator sees the same in the admin UI and can
watch everything live; and every BiDi conversation with a browser goes through
one client.

## Non-goals

| Not built | Why |
|---|---|
| Interception, mocking, offline, HAR export, screencast, CDP | programme E3 Out |
| Console or network as a flow condition | programme Next round |
| Keeping captured data across a restart, or in Redis | programme R9 |
| Keeping the live tail on the server | ruling 2 |
| Per-level or per-type subscriptions | BiDi has none (Research) |
| A second replica holding connections | programme R5 |

## Design

### 1. `bidi/` — one client

A new package below every other layer (no protocol imports; `tests/test_boundaries.py`
gains it). It replaces `monitor/bidi.py` and `Grid.bidi()`.

- **`Connection`** (asyncio, `websockets`): `open(url)`, `close()` (unsubscribe
  every subscription id it holds, remove every collector it added, then close —
  ruling 7), `command(method, params, timeout) -> dict` (id-matched, its own
  timeout, raises `BidiError(error, message)`), `subscribe(events, contexts=None)
  -> subscription id`, `unsubscribe(id)`, and `listen(events) -> Subscriber`: a
  bounded queue (1 000, overflow dropped and counted) drained by a task that
  calls the subscriber's handler. The reader only parses and routes (ruling 8).
  Unsolicited events no subscriber asked for are discarded (Firefox fan-out).
  `dropped` and an `on_close` callback as E2's socket had.
- **`Channel`** — the synchronous facade worker threads use, bound to a loop:
  `call(method, params, timeout=…)` and a context-managed `listen(events,
  handler)`. It hands work to the loop (`anyio.from_thread` or
  `asyncio.run_coroutine_threadsafe`) and blocks only the calling worker.
- **`open_channel(session_id)`** — a context manager: the monitor's held
  `Connection` for that session if there is one (closing the context does not
  close it), else a new one opened for the block and closed after.

### 2. `monitor/` — holds the connection for capture

E2's monitor keeps its watches, deadlines and `/status` liveness, and now holds
a `bidi.Connection` (not its own socket class) for a watch owed `capture`.

- `events_for["capture"]` = `log.entryAdded`, `network.beforeRequestSent`,
  `network.responseCompleted`, `network.fetchError`,
  `browsingContext.contextCreated`, `browsingContext.contextDestroyed`,
  `browsingContext.navigationStarted`, `browsingContext.navigationCommitted`,
  `browsingContext.historyUpdated`, `browsingContext.fragmentNavigated`.
- **Synchronous attach**: `Monitor.attach(workspace, session_id, reason)` is
  awaited by the open path: open the connection, subscribe, add the data
  collector (`dataTypes: ["response"]`, `maxEncodedDataSize: 10 000 000`,
  `collectorType: "blob"`), return — before the first navigation (ruling 6).
  A failure to attach fails the open with a 503 naming the Grid's BiDi route,
  and quits the browser.
- E2's deferred minors this closes: resubscribe when a reason is added; a
  cancelled open leaks nothing; `_socket_failed` cleared on success; reconnect
  backs off (1, 2, 4 … 30 s); `stop()` iterates a copy; a raising monitor never
  fails `end_browser`.
- At the deadline (programme R3): close clean (ruling 7); `capturing` goes
  false; the admin shows **capture paused**. The next call touches the watch and
  re-attaches.
- On reconnect: resubscribe, then Chrome's replay is aligned (ruling 9).

### 3. `site_data/` — over `Channel`, raw BiDi

`transfer.capture`, `transfer.restore` and `spare.spare_tab` take a `Channel`,
not Selenium's driver. Every call becomes a BiDi command:
`browsingContext.create|navigate|close`, `network.addIntercept|provideResponse|
removeIntercept`, `script.evaluate`, `storage.getCookies|setCookie`; the
intercept's `network.beforeRequestSent` arrives through `channel.listen` on its
own subscriber (ruling 8). The spare tab's context id is registered with
capture for its life (ruling 15). Behaviour, results and every site-data test
stay as they are; `core/actions.py` opens `open_channel(session_id)` where it
opened `grid.bidi(session_id)`.

### 4. `capture/` — buffers, masking, the index, the tail

Below the protocol layers. `Capture.ingest(workspace, session_id, method,
params)` is the monitor's `on_bidi`.

- **Console entry**: `{seq, at, level, type, text, source: {url, line},
  context, nav}` — `text` capped at 4 kB; `debug` counted, not kept (ruling 4).
- **Request** (merged from its events): `{id, seq, at, method, url, status,
  status_text, type, mime, size, took_ms, failed, error, context, nav,
  navigation: bool, request_headers, response_headers}` — `id` our own
  (`<nav>.<n>`), mapped to the browser's id in the index; resource type from
  the request's `destination`/`initiatorType` or the MIME type; timings
  normalised per browser (Chrome relative, Firefox epoch); size from
  `bodySize`/`bytesReceived`, never `content.size`. Successful static counted,
  not kept (ruling 3).
- **Navigation marks** from `navigationStarted`/`Committed` of the top-level
  context; the last 5 kept.
- **Masking** (ruling 13) by one `Masker` the buffer and the tail share; the
  catalogue values bound in the session come from the secrets binding.
- **Store**: per workspace, a `cachetools.TTLCache`-backed holder of two
  `collections.deque(maxlen=1000)`, counts, marks and the index; TTL slides on
  every entry. An explicit `open_session` without capture discards the
  workspace's buffers (programme R9).
- **Tail**: `Capture.tail(workspace) -> TailSubscription` — exists only while a
  page listens; bounded per-listener queue (500), overflow counted and reported
  in the stream as `{"dropped": n}`.
- **The call window**: `Capture.mark(workspace) -> seq` at a call's start;
  `Capture.caused(workspace, since_seq) -> {errors, failed_requests}` at its
  end, after the `session.status` round trip (ruling 12).

### 5. Settings and the open

- `"capture": (None, None, _as_flag)` beside `record` in
  `workspace/settings.py`; replayed on a silent reopen after a reap; never
  inherited by an explicit `open_session`; off by default (programme R8).
- `capture.ttl` (seconds, default 10 800) — a new config section `capture`
  (a valid section name: no underscore, no env collision).
- `open_session(capture=true)` on MCP, `POST /browser {"capture": true}` over
  HTTP: the open path calls `Monitor.attach(..., "capture")` before navigating.

### 6. Every call: `call.finished` and the hint

At the recipe's host — `Workspaces.act`, a flow step, the bound write — E2 r8's
seam: `Monitor.touch(session_id)`, `call.finished` published (values-free:
workspace, tool, surface, outcome, browser, ms), and, with capture on, the
hint added to the result (ruling 12). Both surfaces return it; an HTTP result
carries the same `page` object.

### 7. Resources and the body capability

| URI | What |
|---|---|
| `workspace://console` | entries since the last navigation; `?level=error\|warn\|all` (default all kept), `?pages=n` (1–5) |
| `workspace://network` | requests since the last navigation, newest first; `?filter=failed\|xhr\|all` (default all kept), `?pages=n`; counts of what was not kept |
| `workspace://network/{id}` | one request in detail, headers masked |

- With capture off, each answers `{"capture": false, "how": "open_session(capture=true)"}` — not an error.
- `network_body(id)` — a capability row in `core/capabilities.py`: tool and
  `GET /browser/network/{id}/body`; read-only (`reads()` annotation — it writes
  nothing); MCP visibility `["model", "app"]` so the request view's Fetch body
  can call it; ruling 11's caps; 404 when the browser is gone, 400 for an
  unknown id.
- `workspace://current` gains `capture` and `capturing` (programme R16).
- Every text an agent reads names these by URI (AGENTS.md). Skill: a routing
  row and `references/TROUBLESHOOTING.md` gains "a call failed and the page said
  why" (read the hint, then the console).

### 8. `show`

Rows for `console`, `network` and `network/{id}` (component `console`,
`network`, `request`), per Penpot *App · show*: the context view draws
`capture`/`capturing` and its Console and Network chips drill in; the request
view's Fetch body calls `network_body` through `callServerTool`. The inventory
guard (E7) covers them; `MAX_SHOWN` applies.

### 9. Admin UI

Per Penpot *Workspace · Console* and *Workspace · Network*:

- **Toolbar**, identical on both tabs, left to right: **Clear** · filter chips
  (Console: All, Errors, Warnings; Network: All, XHR/fetch, Failed) · filter box
  (`Filter messages` / `Filter URLs`) · **This page ▾** (Captured only) · … ·
  **Pause**/**Resume** (Live only) · the **Captured | Live** switch
  (`mode / captured|live|paused`).
- **Captured** reads the buffer through the admin API
  (`GET /admin/workspaces/{key}/console|network`, the same shapes as the
  resources). Footer: `5 requests · 19 static not kept · workspace://network`.
- **Live** opens `GET /admin/workspaces/{key}/tail?kind=console|network` (SSE,
  admin door, signed like `/admin/events`), keeps the last 2 000 rows in the
  page, Pause holds rendering and counts new rows. Footer: `Live · 1,204
  requests`, `Paused · 1,204 requests · 37 new`, `12 dropped` in amber only
  when non-zero.
- The tabs are usable only with capture on (`capture-off` board); the pills
  **● REC**, **● capture**, **capture paused** on the card and summary; the
  summary's CAPTURE row.

### 10. Dropping Python 3.10 (ruling 5)

`requires-python = ">=3.11"`, classifiers from 3.11; `test.yml` PR legs 3.11 and
3.14, the sweep 3.11 → 3.14; `publish.yml` comments; the `tomli` fallbacks gone
(`pyproject.toml` test extra, Dockerfile, `tests/test_packaging.py`,
`tests/test_prompts.py`, `tests/test_skill.py`); `tests/fakes.py patch_os`'s
3.10 accessor branch gone; `.github/instructions/python.instructions.md`,
`.github/copilot-instructions.md`, AGENTS.md and CONTRIBUTING.md say 3.11, and
"advisory" moves to `Test (3.11)`. 3.11 features become allowed (`tomllib`,
`except*`, `TaskGroup`, `typing.Self`); nothing is rewritten to use them.

### 11. Documentation

AGENTS.md: "WebDriver BiDi … used only for site data" becomes the one-client
rule (rulings 1, 7, 8); the capture section (rulings 2–4, 12–15); the 3.11
floor. README within its budget, CHANGELOG `[Unreleased]` (capture; network_body;
3.11 floor as BREAKING), wiki regenerated plus `notes/network_body.notes.md`,
skill as §7.

### 12. Testing

- `bidi/`: against a local websockets server (E2's harness): routing, timeouts,
  per-subscriber queues (a blocked subscriber does not delay a reply), clean
  close, unsolicited events discarded.
- `site_data/`: the existing suite, against a fake `Channel` that records
  commands; one integration flow is **not** added (AGENTS "less is more").
- `capture/`: merging, masking (every rule in 13), static/debug counting,
  alignment (Chrome replay fixtures from the probes), TTL, the hint window.
- Surfaces: `test_surfaces.py` parity for `network_body`; resources; `show`
  inventory; admin API and tail (auth, signing, bounded queue).
- **Live verification** (plan's last tasks): on the Grid, Chrome and Firefox,
  capture on: a page load, console errors, a failed fetch, a body, save and
  restore site data over the shared connection, a reconnect; then dispatcher
  latency and queue depth measured (ruling 16).

## Verify first

1. *Measured 2026-10-10*: programme Verify first 4 and 5 — see Research.
2. *Unverified*: `session.status` as a flush — events emitted before the reply
   arrive before it on one connection. The plan's live task checks it; if not,
   the hint waits a fixed 50 ms after the round trip.
3. *Unverified*: Firefox delivering a spare-tab intercept event to the held
   connection and to a scoped `listen` alike, with capture ignoring it.

## Next round

- Ending a watched browser at its deadline ourselves (programme R3).
- A flow condition on console or network.
- Request bodies (`dataTypes: ["request"]`) — both browsers support them.
- HAR export of the buffer.
