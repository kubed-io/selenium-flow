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
    workspace (`write` with a secret; carried to the session that replaces
    one, PR review) is replaced wherever it appears in a URL,
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
17. Claude, PR review: **the deadline close counts as Grid activity**, so the
    browser gets one more idle timeout (Verify first 5): bounded, and closing
    clean beats leaving Firefox's fan-out and the collector behind. Wrong, it
    costs one idle timeout per pause; revisit with ending at the deadline.
18. Claude, PR review: **Chrome reports a fetch's failure only once its body is
    read** (Verify first 2), so such a call has no hint;
    `references/TROUBLESHOOTING.md` says where to look instead.
19. Claude, PR review: **a reply waits up to 0.5 s behind a flood**, the price
    of dropping nothing (Verify first 4); E4's metrics watch it.
20. Claude, PR review: **the admin bundle budget is 50 KB**, from 45, for the
    Console and Network tabs.

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
  false; the admin shows **capture paused**. The next call holds it again
  before it runs. **Never under a call:** `Monitor.calling` / `called` count a
  session's calls in flight (each a touch), the connection is kept while any
  is, and the deadline counts from the last one's end — a call borrows the
  connection (its flush, a save's spare tab), and a close mid-call took them
  with it. A hold a call tried and failed backs off like a look's, so a hung
  `/se/bidi` costs one call `ATTACH_TIMEOUT`, not every call.
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
  navigation: bool, request_headers, response_headers, headers_dropped}` —
  each side's headers kept while names and values fit 16 kB, a name cut to
  256 characters, the rest counted in `headers_dropped`; `id` our own
  (`<nav>.<n>`), mapped to the browser's id in the index; resource type from
  the request's `destination`/`initiatorType` or the MIME type; timings
  normalised per browser (Chrome relative, Firefox epoch), unknown when two
  clocks were mixed (below 0 or past an hour); size from
  `bodySize`/`bytesReceived`, never `content.size`. Successful static counted,
  not kept (ruling 3).
- **Navigation marks** from `navigationStarted`/`Committed` of the top-level
  context; the last 5 kept.
- **Masking** (ruling 13) by one `Masker` the buffer and the tail share; the
  catalogue values bound in the workspace come from the secrets binding, in
  every spelling a URL, a JSON or an HTML echo gives them, and carry to the
  session that replaces one. A session still held is never evicted.
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
  unknown id. A binary body is answered from its row (type and size), never
  asked; a text body its row says is over 8 MB is refused (400) unasked; the
  rest is asked on a connection of its own (`open_channel(private=True)`),
  because Chrome answers `network.getData` decoded in one frame, and a frame
  past the 16 MiB limit closes its socket — that one, a 400, never the one
  capture holds. The text is masked before its cut.
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

**Live results, 2026-10-10** (plan Task 13), against the cluster Grid: hub and
nodes `4.48.0 (revision 27f5213)`, KEDA from zero, `sessionTimeout` 300 s;
Chrome `152.0.7977.82`, Firefox `155.0.1`; this branch as built by its plan
tasks, before the final review's fixes, on Python 3.13.5, `websockets` 17.2,
`selenium` 4.51.0, `fastmcp` 4.1.0. The server ran from the worktree with a
throwaway token; the in-process probes held a real
`bidi.Connection` and `Capture` exactly as `Monitor._hold` does. No CDP. The
scripts are throwaway and not in the repo. **Every session was ended**: the Grid
listed **0** sessions before (nodes at zero) and **0** after; 26 were opened,
each ended in a `finally` by the server's `DELETE /browser` or the Grid's own
`DELETE`, and none needed the fallback.

2. **`session.status` as a flush** — *measured, it is one; `FLUSH_GRACE` stays
   0.* `script.evaluate` of 1 000 `console.log`s, then at once `flush()`: on
   **20 of 20** runs per browser (each browser twice) all 1 000 were in
   `Capture` when it returned. At the evaluate's own reply only 76–1 000
   (Chrome) and 186–998 (Firefox) had landed, so the round trip is what lets
   them in; a flush took 2.6 ms (Chrome) and 3.8 ms (Firefox) at the median, 23
   ms at most. What it cannot do is wait for an event the browser has not sent:
   **Chrome sends a request's `responseCompleted` only once its body is read**,
   so a script that awaits `fetch()` of a 404 and not its body ends before the
   404 exists — not in the hint on **10 of 10** runs, in it on 10 of 10 once
   the script read the body; Firefox counted both 10 of 10. The flush is right;
   the call window is what the browser has said by its end.
3. **The spare tab on the held connection** — *measured, both hear it and
   capture ignores it, on both browsers.* A site-data save's spare tab run
   through the capture's own connection: the intercept's
   `network.beforeRequestSent` (`isBlocked: true`, `…/__selenium-flow-spare__`)
   reached **both** the capture subscriber and the spare tab's scoped listener
   (Firefox's scoped listener also heard the tab's own `favicon.ico`);
   `Capture.counts` and the request total did not move (6 spare-tab events on
   Chrome, 7 on Firefox, all ignored by context). Over HTTP, `POST
   /browser/save-site-data` with a second origin in the history left no
   `__selenium-flow-spare__` row and the counts unchanged; afterwards a
   navigation to `<origin>/__selenium-flow-spare__` loaded the site's own 404 in
   0.07 s, so no intercept was left behind.
4. **Dispatcher latency and queue depth (ruling 16)** — *measured.* One held
   connection, an HTML page on this server, six floods per browser of 5 000
   `console.log` and 200 same-origin `fetch`es (≈ 5 400 events), during each 20
   `session.status` round trips and one site-data save; idle figures from the
   same connection just before. Per flood, across the six, *before the fix*:

   | | Chrome | Firefox |
   |---|---|---|
   | Flood, page side | 1.6–1.8 s | 0.8–0.9 s |
   | Capture lag p50 / p95 / max | 2.1–3.9 / 11–25 / 14–26 ms | 3.8–8.8 / 13–40 / 16–40 ms |
   | Queue `peak` (limit 1 000) | 472–1 000 | 692–1 000 |
   | `dropped` | 0 in 5 runs; **740** in 1 (and 474 in 1 of 2 earlier runs) | 0 in 3 runs; **28, 637, 990** in 3 |
   | `session.status` RTT p50 / max, idle | 0.7–1.7 / 1.3–2.9 ms | 0.7–2.1 / 1.4–42 ms |
   | `session.status` RTT p50 / max, flood | 1.4–30 / 48–146 ms | 1.7–2.7 / 88–127 ms |
   | Site-data save, idle → flood | 0.06–0.12 → 1.6–1.8 s | 0.21–0.38 → 0.49–0.68 s |

   Replies were never queued behind capture: the RTT tracks the browser's own
   load, and the save during a flood most likely waits on the page's busy main
   thread for its classic `execute_script` (not isolated here). But the capture
   queue itself overflowed. Handling an event costs well under 0.1 ms, so the
   live pass's hypothesis was that the queue fills because the reader routes a
   whole buffered burst before the subscriber's task gets a turn, not because
   capture is slow. The final review confirmed it on a loopback probe
   (`websockets` reads a buffered burst without suspending, while the
   subscriber handled one event per turn): of 5 000 frames, 3 998 were dropped
   as built and 0 with both fixes below, at 5 000 and 20 000. A dropped
   `responseCompleted` leaves a request without its end.

   **After the fix, 2026-10-10** (the reader yields every 64 frames, each
   subscriber drains a batch of up to 5 ms per turn, and the fell-behind
   warning re-arms only once its queue has drained to empty): the same six
   floods per browser, same Grid, browsers and page, with each flood's 200
   fetches' `took_ms` added (D1's fix). Both sessions were quit in a `finally`;
   the Grid listed 0 sessions before and after.

   | | Chrome | Firefox |
   |---|---|---|
   | Flood, page side | 1.5–2.1 s | 0.8–1.3 s |
   | Capture lag p50 / p95 / max | 0.1–0.3 / 0.5–1.6 / 1.2–4.9 ms | 0.2–0.4 / 0.5–1.7 / 1.0–11 ms |
   | Queue `peak` (limit 1 000) | 55–64 | 64 |
   | `dropped` | **0 in 6 of 6** | **0 in 6 of 6** |
   | "fell behind" warnings | 0 | 0 |
   | `session.status` RTT p50 / max, idle | 0.9–2.2 / 1.9–3.4 ms | 1.6–3.4 / 1.9–44 ms |
   | `session.status` RTT p50 / max, flood | 1.1–53 / 81–513 ms | 1.5–17 / 83–323 ms |
   | Site-data save, idle → flood | 0.06–0.10 → 1.5–2.1 s | 0.21–0.38 → 0.52–1.04 s |
   | The fetches' `took_ms` p50 / max | 111–209 / 199–389 ms, none 0 or unknown | 35–188 / 65–209 ms |

   Every event of every flood was handled. The lag is now short because the
   reader no longer reads ahead of the subscriber: the wait has moved into the
   socket, and a reply sent behind thousands of events waits until they are
   handled, so the RTT's maximum during a flood rose (Chrome 513 ms against 146
   before; Firefox 323 against 127), well inside the 5 s reply timeout. For E4
   this says what to watch: `dropped` and `peak` per subscriber, and the RTT
   under load. Firefox's `took_ms` is now network time, as DevTools shows it,
   rather than the event stamps' 128–147 ms, which included delivering the body.
5. **Live pass** — over HTTP and the admin API, per browser, page
   `https://example.com/`:
   - **Open**: `POST /browser {"capture": true}` answered `capture: true`; `GET
     /browser` `capturing: true` (both).
   - **The hint**: the script call (`console.error`, an uncaught throw, a 404
     `fetch`, a `fetch` to port 9) returned `page: {errors: 3,
     failed_requests: 1, see: workspace://console?level=error}` on Chrome (3 of
     3 runs) and Firefox (2 of 3; the third had `failed_requests: 0`); the
     quiet `extract` 1 s later had no `page`. The three errors are `boom`, the
     throw and the unhandled rejection. Chrome's one failed request is port 9
     (`net::ERR_UNSAFE_PORT`) — its 404 lands after the call (item 2); Firefox
     sends no network event at all for port 9, so its one is the 404, counted
     only when it beats the call's end.
   - **Captured view**: `console?level=error` 3 entries, `network?filter=failed`
     the failures, newest first, `Authorization` shown `•••` and an ordinary
     header as sent.
   - **Body**: `POST /browser/network-body` of the 404: text, 577 characters,
     `truncated: false`, on both (the row's `size` is the encoded 334–475 bytes).
   - **Save** and **no intercept left**: as item 3.
   - **Live tail**: `tail_url&kind=console` streamed the script's three error
     rows within the 5 s window; no `dropped` (both).
   - **Capture off**: after `DELETE /browser` and an open without capture, the
     console answered `{"capture": false, "how": "open_session(capture=true)"}`;
     an open with capture after it found **0** entries, so the buffer had gone.
   - **Admin page vs the Penpot boards** (Firefox's data, viewed in a Chrome
     browser driven over this server's own tools; *Console*, *Network*, the
     request detail with *Fetch body*, *Live*, *Pause* and capture off). Same
     layout, toolbar, chips with counts, *This page*, Captured/Live switch,
     Pause/Resume, the *Live · n* and *Paused · n · k new* footers, the CAPTURE
     row and the pills. Differences: a console row's source is the full URL and
     line (`https://example.com/:2`), the boards a file name (`app.js:142`); with
     capture off the boards grey out *Console* and *Network* behind a tooltip,
     the page keeps both tabs clickable and says so in the pane; the pills read
     *live · capture*, the boards *REC · capture · live*; no initiator, *during*,
     page or request-body rows (plan ruling 9). Not compared: *Network* in Live
     mode and the cleared boards.

   **Also measured: the deadline close is activity.** A session with only a
   held connection (subscribed, one collector), closed clean at 240 s — the
   monitor's deadline close, `session.unsubscribe` and
   `network.removeDataCollector` — was reaped **306 s after the close** on both
   browsers (Chrome 545.8 s, Firefox 547.5 s after opening), not at ~330 s. So
   closing at the deadline gives the browser one more idle timeout.

   **Open, for the controller** (found here; the first three, and the admin's
   console source and muted tabs, were fixed in the final review's wave):
   - **Chrome's `took_ms` is always 0.** Chrome stamps every network event of a
     request with the request's start (`timestamp` equals
     `request.timings.timeOrigin`; 200 of 200 requests), so the duration taken
     from the events' own timestamps (plan ruling 14) is 0 for every Chrome
     request. Firefox's are right (12–147 ms). Plan ruling 14's fallback — a
     duration from `timings`, relative ms on Chrome, epoch ms on Firefox —
     applies. *Fixed:* `timeOrigin + responseEnd` less `timeOrigin +
     requestTime` on both browsers, the event stamps only without a
     `responseEnd`, and unknown rather than a false 0.
   - **Drops under a burst** (item 4): the capture subscriber's queue of 1 000
     overflows on a page logging thousands of entries at once. *Fixed*; see
     item 4's second table.
   - **The fell-behind warning repeats.** It is meant to be logged once per
     streak, but the streak ends at the next event accepted, so a saturated
     queue logged it 114 (Chrome) and 288 (Firefox) times in six floods.
     *Fixed:* a streak ends only once the queue has drained to empty.
   - **Ending a captured Firefox logs a failed hold.** Firefox closes the held
     socket when its session is deleted, so the monitor tries to hold again
     before the end reaches it and logs `no BiDi connection … trying again`
     (6 of 6 captured Firefox ends; never on Chrome, whose socket stays open).
     Nothing leaks: the end drops the watch a moment later.

   **Also changed in the final review's wave** (the controller's rulings, for
   Dr K; §2, §4 and §7 above now describe the design after them, and after
   the PR review's):
   - The first call after the deadline closed the connection holds it again
     before it runs, so the call that comes back from a pause is captured.
   - Each side of a request keeps headers only while their names and values
     fit 16 kB, names cut to 256 characters; later headers are dropped and
     counted in the request's `headers_dropped`. 32 of 4 kB a side, 1 000 rows
     a workspace, was a bound a hostile page could make half a gigabyte.
   - `network_body` masks the session's bound secrets in the text before its
     cut; answers a binary body from its row's type and size without asking
     the browser; and refuses (400) a text body its row says is over 8 MB,
     because Chrome answers `network.getData` in one frame and one past the
     connection's 16 MiB limit closes the connection capture holds.

## Next round

- Ending a watched browser at its deadline ourselves (programme R3).
- A flow condition on console or network.
- Request bodies (`dataTypes: ["request"]`) — both browsers support them.
- HAR export of the buffer.
