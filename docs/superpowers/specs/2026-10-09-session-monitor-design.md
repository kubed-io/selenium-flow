# The session monitor: which sessions are open, ended and owed

**Status: DESIGNED 2026-10-09, branch `issue-61-monitor` (#61), for Dr K's
review. Plan: `docs/superpowers/plans/2026-10-09-session-monitor.md`.** Epic E2
of the programme `2026-10-09-workspaces-and-observability-design.md`, cited
here as `programme R<n>` and `programme E<n>`. Code starts after E1 (#60)
merges, so this spec is written in E1's vocabulary, as Dr K settled it on
2026-10-09: a **workspace** is the outer box (named, persistent: flows, files,
site data, history, settings); a **session** is the live browser open inside a
workspace — at most one at a time, started by `open_session`, ended by
`end_browser` — and it holds the Grid's session, whose id is its `session_id`.
An idle workspace has no session. E1 is implemented (branch
`issue-60-workspaces`, `8d33c6e`), and every path and name this spec gives is
E1's code, checked against it on 2026-10-09 (rulings 17–19 record what it
changed). Penpot: Components page, boards `summary / live` and
`summary / idle`, which carry the rows `IDLE TIMEOUT` and `CAPTURE` (read, not
edited: programme R15).

## Brief

Programme E2, Dr K, 2026-10-09: *"Monitor first, then capture"*;
*"recommendation A"* — one monitor, one bus. A monitor that knows as it happens
which sessions are open, ended and owed; reads each session's Grid timeout;
holds a BiDi socket for the owed ones; publishes lifecycle events on a bus that
the recordings collector, the admin broadcast and the later epics (E3 capture,
E4 telemetry) consume. Rulings R2–R7 bind it, with R1's vocabulary.

*"Can we reliably get the grid's session timeout from a call to the grid?"* —
read it, never sync it by hand. *"Let the grid reap for now, we can add an end
with our code later."* And, on the vocabulary: E2 is rightly *the session
monitor* — it watches sessions, and the Grid sessions they hold.

## Research that shaped it

The programme's research (how the Grid reaps, what BiDi buffers, how others do
it) is not repeated. What this epic added, measured on 2026-10-09 against the
cluster's Grid (`http://10.43.225.177:4444`, hub and node **4.48.0**
`27f5213`, Chrome **152.0.7977.82**, a node scaled from zero by KEDA,
`maxSessions 1`) with throwaway scripts using `websockets` 17.2 and `requests`
— full results under *Verify first*:

- **A held socket never learns its session ended.** A silent socket (no
  subscriptions) stayed open for the whole minute watched after `DELETE
  /session/<id>`, and stayed open through the Grid's own reap. After the delete
  the hub still **answered a ping** at once and **swallowed a command** without
  a reply or a close. So a socket closing is not the "instant this browser
  ended" the programme expected, a ping proves the hub and never the browser,
  and a monitor that does not close its own sockets leaks one zombie connection
  per ended session.
- **`/status` knows at once.** The node holding a browser lists its session in
  a slot the moment `POST /session` returns, beside the node's `sessionTimeout`
  (`300000`, ms), and drops it when the session ends. One `GET /status` touches
  no session, so it is the liveness signal the recordings collector already
  trusts (`recordings/collector.py`, `live`) — now for every watched session.
- **A ping is not activity.** A socket pinging every 20 s did not keep its
  browser alive: reaped 330 s after it opened, as was a silent one (331 s) —
  inside the programme's `sessionTimeout` to `sessionTimeout + 30 s` window.
- **Sockets coexist.** Two held sockets and Selenium's per-call one
  (`Grid.bidi`, used by site data) on one session at the same time, each
  answering its own commands; the held one still answered after the per-call
  one closed.
- **`session.subscribe` returns an id** on Chrome 152 (`{"subscription":
  "<uuid>"}`), and `session.unsubscribe({"subscriptions": [id]})` takes it back.
- **The Grid's advertised socket address is its own.** The capability
  `webSocketUrl` came back as `ws://selenium-grid-selenium-hub.flow:4444/…`, an
  in-cluster name. The address is derived from `grid.url`, as `Grid.bidi`
  already does, never taken from the capability.
- **`websockets` is already installed.** `fastmcp-slim[server]` (which
  `fastmcp` pulls in) requires `websockets>=15.0.1`; 17.2 is what resolves. It
  is not declared by this package and nothing here imports it yet. Selenium's
  own client is `websocket-client` on a daemon thread per connection (programme
  research: wrong for a held listener on an asyncio loop); `wsproto` is sans-IO
  and would mean writing the framing loop ourselves.
- **The collector already does half of this.** One `/status` listing per tick,
  never a session command, a listing older than the browser ignored, an empty
  listing after a hub restart undone by a later one (`_listed`, `_gone`,
  `_back`, `_list` in `recordings/collector.py`), and `ended(grid_id)` fed from
  `end_browser`. That logic moves; the note queue does not.
- **The admin broadcast polls and can be poked.** `Broadcast.poke()`
  (`http/admin/workspaces.py`) forgets the last
  payload and ticks now; the collector already calls it when a recording is
  filed.

## Rulings

Programme R2–R7 bind. The open items of #61 and what the measurements raised
are decided here; Dr K reviews them on the PR.

1. Claude, 2026-10-09: **`websockets`, its asyncio client, declared as a
   dependency** (`websockets>=15`, the floor `fastmcp` already imposes). Only
   `monitor/bidi.py` imports it, and a boundary test holds that. Selenium's
   per-call BiDi for site data is untouched. Cost if wrong: one module rewritten
   against another client; nothing else sees the library.
2. Claude, 2026-10-09: **ping every 20 s, give up after 20 s more.** Measured: a
   ping is not activity, so it costs the session nothing, and it is how a dead
   TCP path to the hub (a hub pod replaced under a Service) is noticed. It says
   nothing about the browser. The monitor dials `grid.url`, the in-cluster
   Service here; through an ingress, a 20 s ping also keeps an idle socket
   inside a proxy's read timeout — *unverified*, since nothing here goes through
   one. Cost if wrong: an ingress that cuts idle sockets sooner drops the socket,
   and it is opened again on the next look.
3. Claude, 2026-10-09: **a socket is a channel, never a liveness signal.**
   `session.ended` has three sources only: our own quit (`ended`), a call that
   found the session gone (`lost`), and the Grid's listing (`gone`). Every watch
   that ends has its socket closed by the monitor, so no zombie outlives its
   session. A socket that drops counts as one missed listing, no more. Cost if
   wrong: none found — the measurement is what forced it.
4. Claude, 2026-10-09: **gone is two listings, not one.** A watched session is
   gone when two listings that each show at least one node, both taken after the
   watch began, do not list it — or one, after its socket dropped. A listing that
   failed or shows no nodes (a hub restarting before its nodes register again;
   KEDA at zero, which only happens once our sessions have already been missed
   for its cooldown) says nothing. This replaces the collector's mark-then-undo
   (`_back`). Cost if wrong: a hub whose nodes re-register one by one over more
   than a tick could have a live recorded session marked ended; its recording is
   still filed if it arrives within `recording.wait` (600 s), and lost only if it
   does not.
5. Claude, 2026-10-09: **the note queue stays** (the programme's
   recommendation). Notes on disk remain the recordings' queue and survive a
   restart; only liveness moves to the monitor. At `start` the collector watches
   every owed note not yet ended, which is how a restart re-establishes what the
   monitor watches. Cost if wrong: none; the queue's doctrine is unchanged.
6. Claude, 2026-10-09: **a reconnect after a reap owes nothing of the old
   session.** A reap ends the old watch (`lost` when a call finds it, `gone` when
   the listing does). The reopened session is a new session, owed exactly what
   its replayed settings make it owe: a recording gets a new note and a new watch
   as today; capture is E3's to replay. A socket that drops while its session
   lives is opened again on the next look and re-sends its subscriptions by event
   name (ids are per connection). Cost if wrong: E3 finds it wants a capture
   buffer to span the reap, which is its call.
7. Claude, 2026-10-09: **recording owes liveness, not a socket.** A watch holds
   a socket only for a reason that names BiDi events (`events_for`); `recording`
   names none, so in E2 the monitor watches recorded sessions by the listing
   alone and holds no socket in production. R2 says a socket is held *only
   while* a session is owed — a condition this keeps — and the measurement says
   a socket would tell a recording nothing. The holder ships in E2, tested
   against a real local WebSocket server, and E3's `capture` is its first
   production use. Cost if wrong: if Dr K wants E2 to exercise the holder in
   production, a recorded session holds a subscription-free socket: one table
   entry and one more connection per recorded session.
8. Claude, 2026-10-09: **`call.finished` is declared, not emitted.** Its type and
   fields are fixed here so E3 and E4 cite one definition; whichever of them
   merges first emits it, once per call, at the recipe's host (`Workspaces.act`,
   the flow step, the bound write), and calls `Monitor.touch` there, which is
   what slides a watch's deadline (R3). Until then a deadline is the open plus
   the timeout, inert in E2 because nothing holds a socket. Cost if wrong: E3 and
   E4 disagree on the emission point; this ruling is what they cite.
9. Claude, 2026-10-09: **`outcome` is `ok`, `4xx` or `5xx`**, the status class
   `errors.py` already decides. E4's open question, answered here because the
   field is declared here; E4 may narrow it before anything emits it. Cost if
   wrong: a renamed value before first use.
10. Claude, 2026-10-09: **the timeout is seconds, on the workspace, and
    survives the session.** `grid_timeout` is read at every open and reopen
    (programme R4), stored on the workspace record, kept when the session ends
    (the `summary / idle` board shows `IDLE TIMEOUT`), and replaced by the next
    open. It is in `workspace://current`, `GET /browser` and the admin row; not
    in the `open_session` result, which R4 does not name. Unknown is `null` and
    is shown as nothing. A watch with no known timeout uses the Grid's default,
    300 s, for its deadline. Cost if wrong: an agent wanting the timeout at open
    reads one resource.
11. Claude, 2026-10-09, as drawn (Penpot file *Admin UI*, components
    `summary / live` and `summary / idle`, read for this ruling): **the card
    shows `idle timeout` in the browser group**, beside `version`, `id` and
    `node` — the session's facts, since under E1's ruling 8 the timeout is
    the session's — and **in seconds, naming no cause**: `300 s · read from
    the Grid node` on a live card, `300 s · no session is open` on an idle
    one. It is kept on the record after the session ends (ruling 10), which
    is why an idle card still has it. The idle board said *the Grid reaped
    it* until Dr K changed it to match on 2026-10-09: the record does not
    keep what ended a session (`end_browser`, the admin's End, a reap), and
    no field is added to keep it. The `CAPTURE` row on the same boards is
    E3's (it is the capture setting), and the `show` app's `context` view is
    left to E3, which owns that board. Cost if wrong: a string in `idle()`.
12. Claude, 2026-10-09: **a subscriber is a function run on the loop.** Each has
    a bounded queue (1 000) drained by a callback the loop schedules; overflow is
    dropped and counted (`Subscription.dropped`) and logged once a streak; a
    handler that raises is logged by exception type once a streak and the others
    are unaffected. No task per subscriber, so nothing on the bus waits forever
    (R3's doctrine). Publishing hands off with `call_soon_threadsafe` and never
    blocks the call. Cost if wrong: a consumer that needs to await does so in a
    task it starts itself, as the collector already does.
13. Claude, 2026-10-09: **no settings.** The tick (30 s, the Grid's own clean-up
    cadence), the two misses, the queue limit and the ping are constants in
    `monitor/`. Cost if wrong: a `monitor` config section later; the name is
    free (AGENTS.md's section-name rules allow it).
14. Claude, 2026-10-09: **events carry the session's `session_id`, and no
    subscriber logs it.** It is how the collector matches a note; R6's
    "browser" is read as this id plus the browser's kind. AGENTS.md's rule that
    a Grid id never reaches the operator's log covers every subscriber. Cost if
    wrong: an id in a log line, caught in review as it is today.
15. Claude, 2026-10-09: **every session is announced; only owed ones are
    watched.** `session.opened` and `session.ended` are published for every
    session this server opens or ends; the listing runs only while a watch
    exists, so a server with no recording owed asks the Grid nothing new
    (programme R8's "free when off" holds for the monitor too). `session.ended`
    is published at most once per `session_id`. Cost if wrong: none; events are
    free when nobody subscribes.
16. Claude, 2026-10-09, on Dr K's vocabulary of the same day: **the lifecycle
    events are `session.opened` and `session.ended`**, not the programme's
    `browser.*`, and their id field is `session_id` — the Grid's id the session
    holds, the name the code already gives it (`Workspace.session_id`,
    `Grid.quit(session_id)`). A watch is per session. `browser` stays the
    browser's kind (`chrome`, `firefox`). E3 and E4 cite these names. Cost if
    wrong: a rename across three event types before anything consumes them.
17. Claude, 2026-10-09, on E1's config split (its ruling 9): **`grid_timeout`
    is a field of the workspace record, never a setting.** E1 left the
    `session` section as *how a session opens* (`browser`, `width`, `height`,
    `page_load_timeout`, `script_timeout`), resolved through the cascade into
    the record's `settings`, which a reap replays into `open_session` as its
    arguments. The Grid's timeout is read, never set (programme R4): in
    `settings` it would be replayed as an argument `open_session` does not
    take, and in the `session` section it would be a setting that sets
    nothing. So it is `Workspace.grid_timeout`, beside `settings`, and the
    `grid_` prefix keeps it apart from the two `session.*_timeout` values,
    which are ours to set. Cost if wrong: if Dr K wants it among the
    session's settings, the reap's replay filters it out — one line.
18. Claude, 2026-10-09, on E1's ruling 8 (*session* is the live browser):
    **text about the timeout says *session*.** The resource description,
    the status schema, the skill line and the changelog say *a session in
    this workspace* / *a session*, not *this browser*; the card's label stays
    `idle timeout`, as the boards have it. Cost if wrong: a word in four
    places, and the `openapi.json` golden.
19. Claude, 2026-10-09: **E2 adds nothing to E1's `session` config section**,
    and ruling 13 stands. That section is how a session opens; the monitor is
    how one is watched, so if it ever needs settings they are a `monitor`
    section (a free name: no underscore, no environment variable of its
    own). Nothing else E1 renamed reaches E2's design: the admin's End
    (`DELETE /admin/workspaces/{key}/session`) goes through
    `Workspaces.end_browser`, so it announces `ended` like the caller's own;
    and the event names already follow E1's vocabulary (ruling 16). Cost if
    wrong: none; nothing is built on it.

## Goal

One place in the process knows which sessions this server opened, which have
ended and why, and which it still owes something; it reads each session's idle
timeout from the Grid and shows it on the workspace; and it says all of that as
typed, value-free events that the collector, the admin page and the next two
epics subscribe to — without ever touching a browser to find out.

## Non-goals

| Not built | Why |
|---|---|
| Capture: subscriptions, buffers, resources, the error hint | E3 (programme R8–R11) |
| `/metrics`, spans, emitting `call.finished` | E4, or E3 if it merges first (ruling 8) |
| Ending a session at its deadline | programme R3: the Grid reaps; later option |
| A Redis bus, a second replica holding sockets | programme R5: shaped for, not built |
| A socket for recorded sessions | ruling 7 |
| Answering `workspace://current`'s `live` from the monitor | Next round |
| Settings for the monitor | ruling 13 |

## Design

### 1. The package

```
kubed/selenium_flow/monitor/
  __init__.py     re-exports: Monitor, Watch, LocalBus, Bus, Subscription,
                  Event, SessionOpened, SessionEnded, CallFinished, EVENTS,
                  BidiSocket, BidiError
  events.py       the event types and the bus
  bidi.py         BidiSocket: one held BiDi connection
  monitor.py      Monitor: the watch registry, the listing, the sockets
```

`monitor/` is kernel: it imports no protocol library (`fastmcp`, `starlette`,
`mcp`, `uvicorn`), no `selenium`, and nothing from `mcp/`, `http/`, `routes`,
`server` or `spec`. It does not import `workspace/` or `core/` either: what it
needs from the Grid comes in as callables (`listing`, `socket_url`), so a test
drives it without a Grid. `websockets` is imported by `monitor/bidi.py` and
nowhere else.

### 2. The events and the bus (`monitor/events.py`)

Every event is a frozen dataclass with a `KIND` and these fields, and no others.
Every field is a `str`, `int`, `float`, `bool` or `None` — never a container,
so nothing that is a value can ride along (programme R6). A test enumerates
`EVENTS` and holds this.

| Kind | Type | Fields |
|---|---|---|
| `session.opened` | `SessionOpened` | `workspace: str`, `session_id: str` (the Grid's id), `browser: str` (`chrome`, `firefox`), `grid_timeout: int \| None` (s), `reopened: bool` (a silent reopen after a reap), `at: float` (epoch s) |
| `session.ended` | `SessionEnded` | `workspace: str`, `session_id: str`, `cause: "ended" \| "gone" \| "lost"`, `at: float` |
| `call.finished` | `CallFinished` (declared, not emitted: ruling 8) | `workspace: str`, `session_id: str` (`""` when the call had none), `browser: str`, `tool: str` (the capability name), `surface: "mcp" \| "http" \| "flow"`, `outcome: "ok" \| "4xx" \| "5xx"`, `started: float` (epoch s), `duration: float` (s) |

Causes: **`ended`** — this server quit it (`end_browser`, the admin's
`DELETE /admin/workspaces/{key}/session`, `open_session` replacing one, a
racing open's loser) and the Grid confirmed (a 404 counts, as `Grid.quit`
already rules). **`gone`** — the listing stopped showing a watched session
(ruling 4). **`lost`** — a call found it gone before the monitor did
(`resolve`'s reopen).

The bus:

```python
class Bus(Protocol):
    def publish(self, event: Event) -> None: ...
    def subscribe(self, kinds: str | Iterable[str], handler: Callable[[Event], None],
                  *, name: str, limit: int = QUEUE_LIMIT) -> Subscription: ...
```

`LocalBus` is the one implementation: `start()` binds the running loop,
`stop()` stops delivery. `publish` from any thread hands the event to the loop
(`call_soon_threadsafe`); on the loop it queues directly and the handler runs
after `publish` returns; before `start` it delivers inline in the caller's
thread (tests, and nothing in production: the lifespan starts the bus before
any request); after `stop` it drops. `subscribe` refuses a kind not in
`EVENTS`. Each `Subscription` has its `kinds`, its `handler`, a `name` for the
log, a bounded `deque` (`limit`, 1 000), and `dropped`. A handler is a plain
function, run on the loop, that must not block (ruling 12).
`Subscription.cancel()` removes it.

### 3. The watch registry (`monitor/monitor.py`)

```python
@dataclass
class Watch:
    session_id: str
    workspace: str
    browser: str
    owed: set[str]            # reasons: "recording" in E2; E3 adds "capture"
    since: float              # when the watch began: older listings say nothing
    touched: float            # last activity (the watch, or Monitor.touch)
    timeout: int | None       # the node's sessionTimeout, seconds
    misses: int = 0           # consecutive listings without it
    socket: BidiSocket | None = None
    dropped: bool = False     # its socket closed under it

    @property
    def deadline(self) -> float: return self.touched + (self.timeout or DEFAULT_TIMEOUT)
```

`Monitor(bus, *, listing, socket_url=None, events_for=None, connect=None,
on_bidi=None, clock=time.time, tick=LISTING_TICK)`:

| Method | From | Does |
|---|---|---|
| `opened(workspace, session_id, browser, grid_timeout, *, reopened=False)` | any thread | publishes `session.opened` |
| `ended(workspace, session_id, cause) -> bool` | any thread | publishes `session.ended` once per id (False if already), drops its watch and closes its socket |
| `watch(session_id, workspace, reason, *, browser="", grid_timeout=None)` | any thread | adds `reason` to the session's watch, creating it; ignored for an id already ended; starts the task |
| `release(session_id, reason)` | any thread | removes `reason`; a watch owing nothing is dropped and its socket closed |
| `touch(session_id, at=None)` | any thread | slides the deadline; a socket let go at the deadline is opened again on the next look |
| `watching(session_id) -> frozenset[str]` | loop | what it is owed, empty when unwatched |
| `socket_held(session_id) -> bool` | loop | whether a socket is open right now (E3's `capturing`) |
| `look()` | loop | one pass: listing, gone, deadlines, sockets (what tests drive) |
| `start()`, `stop()`, `running` | loop | the task's lifecycle (§8) |

Calls from worker threads (FastMCP's sync tools, Starlette's routes) are handed
to the loop the way the collector's are (`_post`): inline before `start`, by
`call_soon_threadsafe` after. The dedupe of `ended` is a lock-guarded ordered
set of the last 4 096 ids, checked in the caller's thread so two enders race
safely. The registry is process memory: one replica (AGENTS.md "Scaling").

`events_for: Mapping[str, tuple[str, ...]]` maps a reason to the BiDi events it
needs. A watch wants a socket when any reason it owes has an entry. E2 passes
none (ruling 7); E3 adds `"capture": (…)`. `connect` is the socket factory, a
test seam. Events a socket delivers go to `on_bidi(session_id, method, params)`,
`None` in E2.

### 4. The socket holder (`monitor/bidi.py`)

```python
class BidiSocket:
    def __init__(self, url, *, on_event=None, on_close=None, connect=None): ...
    async def open(self) -> None
    async def command(self, method, params=None, *, timeout=REPLY_TIMEOUT) -> dict
    async def subscribe(self, events: Sequence[str]) -> str       # the subscription id
    async def unsubscribe(self, subscription: str) -> None
    async def reopen(self) -> None     # open again, re-subscribe by event name
    async def close(self) -> None      # unsubscribe each, then close; never raises
    is_open: bool; dropped: bool; subscriptions: dict[str, tuple[str, ...]]
```

- **Connect**: `websockets.asyncio.client.connect(url, open_timeout=5,
  ping_interval=20, ping_timeout=20, max_size=16 MiB)`. The URL is
  `socket_url(session_id)`, which the server wires to `Grid.bidi_url` — derived
  from `grid.url`, never from the browser's `webSocketUrl` (research).
- **One reader task per open socket** dispatches a reply to the command waiting
  on its `id` (`type: success` → its `result`; `type: error` → `BidiError(error,
  message)`) and an event (`type: event`) to `on_event(method, params)`. When the
  socket closes for any reason the reader fails every waiting command with
  `ConnectionError` and, unless we closed it, calls `on_close()`.
- **Never an intercept.** `command` refuses `network.addIntercept` with a
  `ValueError` before anything is sent: an intercept belongs to the browser's
  session, survives the socket, and leaves every matching request hanging with
  nobody to answer (programme research). A test holds it.
- **A command that gets no reply in 5 s** raises `TimeoutError`; that is what a
  zombie socket does (measured).
- **Close** unsubscribes each held subscription (each bounded by the reply
  timeout), then closes the connection. Firefox replays its console buffer only
  to a socket whose predecessor unsubscribed (programme research); E3 relies on
  it.
- **Reopen** re-sends the subscriptions it held by event name, since an id
  belongs to its connection.

### 5. Liveness: the listing, moved out of the collector

```
            ┌──────────────── while any watch exists ───────────────┐
 watch() ──►│ every 30 s, or now when a socket drops or a touch      │
            │ comes:                                                 │
            │   listing() → (nodes, {session_id: timeout_s})         │ ── session.ended(gone)
            │   per watch: socket dropped → flagged                  │
            │              listed → misses = 0, timeout refreshed    │
            │              missed in a listing with nodes taken      │
            │              after `since` → misses += 1               │
            │              misses ≥ 2, or ≥ 1 after a drop → gone    │
            │   sockets: open where events are owed and before the   │
            │            deadline; close past it or when not owed    │
            └──────────── no watch: the task ends ───────────────────┘
```

- **`listing`** is `Grid.listing()`, a new method on `core/browser.py`'s `Grid`:
  one `GET /status`, returning how many nodes are registered and every running
  Grid session's id mapped to its node's `sessionTimeout` in seconds (or
  `None`). It never touches a session: a command such as
  `GET /session/{id}/url` is activity and would keep the browser alive forever
  (AGENTS.md). Run in a worker thread.
- **The deadline** (programme R3): a watch with a socket keeps it only until
  `touched + timeout`. Past it the monitor unsubscribes and closes the socket and
  keeps watching by the listing; the Grid's timer runs and reaps, and the listing
  reports it. A `touch` (ruling 8) brings the socket back on the next look. The
  task waits the tick or until the nearest deadline of a watch holding a socket,
  whichever is sooner, and wakes early on a dropped socket or a touch.
- **What leaves the collector**: `live=`, `_listing`, `_listing_failed`,
  `_listed`, `_gone`, `_back` and `_list`. The collector learns an end only from
  `session.ended` (any cause), through its existing `ended(grid_id)`. Its sweep
  reads `owed.ended is not None` where it asked `_gone`; a cut-off file is still
  filed only once it has been quiet a minute **and** its session has ended. The
  module docstring's paragraph *"Whether a browser is gone is read off the
  Grid's status"* becomes *"is the monitor's to say"*.
- **What the collector gains**: a `monitor` (anything with `watch` and
  `release`). `expect` watches the session for `"recording"` (a provisional
  `discard` note too: its deadline needs an end as much as a kept one does);
  `_load` watches each loaded note that is not done and not ended; `_forget`,
  once the note is gone from disk, releases it. The notes, their lock, the inbox
  watch and every filing rule stay exactly as they are (ruling 5).

### 6. The Grid's timeout: read, stored, shown

- **Read** by `Grid.session_timeout(session_id) -> int | None` (one
  `listing()`), called by `Workspaces.open_browser` right after the session opens
  and by `resolve`'s reopen after a reap — the two places a session is made. Any
  failure (`/status` down, the session not listed) is `None` and is logged at
  info without the id; it never fails the open.
- **Stored** on the workspace record: `Workspace.grid_timeout: int | None =
  None`, written by `remember(..., grid_timeout=)` in the same compare-and-set
  that binds the session, read back tolerantly by `from_json` (anything but a
  positive int is `None`). `detached()` keeps it.
- **Shown**:
  - `workspace://current` and `GET /browser`: `grid_timeout` (integer seconds,
    or `null`), and one sentence in the resource's description: *"grid_timeout
    is how many seconds the Grid lets a session in this workspace sit idle
    before it ends it; every call starts that clock again."* (ruling 18). The
    hand-written status response schema in `spec/schemas.py`
    (`RESPONSES["current_workspace"]`) gains the field.
  - The admin workspace row (`workspaces_payload`): `grid_timeout`, from the
    record. `WorkspaceRow.grid_timeout?: number | null` in `ui/src/lib/types.ts`.
  - `WorkspaceSummary.svelte`: the fact `idle timeout` in the `browser` group,
    after `node`, formatted by a new `idle(seconds, live)` in `lib/format.ts`
    (`300, true` → `300 s · read from the Grid node`, `300, false` → `300 s ·
    no session is open`, `null` → hidden). Boards `summary / live` and
    `summary / idle` (`IDLE TIMEOUT`, ruling 11).
  - The monitor's watch takes it at `watch` and refreshes it from every listing.

### 7. Who publishes, who consumes

**Publishers** — `Workspaces` (`workspace/workspaces.py`), given `monitor=`:

| Where | Publishes |
|---|---|
| `open_browser`, once the session is bound to the workspace | `opened(..., reopened=False)` |
| `resolve`, finding the held session gone | `ended(..., "lost")` for the old id, then `opened(..., reopened=True)` for the new one once bound |
| `end_browser`, after a confirmed quit | `ended(..., "ended")` (replaces `self.recordings.ended(target)`) |
| `remember`, quitting a racing open's loser | `ended(..., "ended")` |
| the monitor's own listing | `ended(..., "gone")` |

`Workspaces` built without a monitor (unit tests) publishes nothing.

**Consumers**, wired in `server.py`:

- **The recordings collector**: `bus.subscribe("session.ended", lambda e:
  collector.ended(e.session_id), name="recordings")`, and
  `Collector(..., monitor=monitor)`.
- **The admin broadcast**: `bus.subscribe(("session.opened", "session.ended"),
  lambda _e: broadcast.poke(), name="admin")`. An open page shows a session
  appear or end now, not a poll later. The two-second poll stays: it is what
  sees everything else (programme E2: *"keeps its poll"*).
- **Later**: E3 subscribes to `session.*` and adds `"capture"` to `events_for`;
  E4 subscribes to `call.finished` and `session.*`.

### 8. Lifecycle

```python
@asynccontextmanager
async def lifespan(_server):
    bus.start()
    await monitor.start()
    if collector is not None:
        await collector.start()      # watches what its notes still owe
    try:
        yield {}
    finally:
        if collector is not None:
            await collector.stop()
        await monitor.stop()         # closes every socket, ends the task
        await bus.stop()
```

The monitor's task runs only while a watch exists and ends with the last one
(AGENTS.md's bounded wait), exactly like the collector's. `stop` cancels it and
closes every held socket. A watch survives nothing: after a restart the
collector's notes re-create the recording watches (ruling 5), and E3 decides
what capture re-creates.

### 9. Failures

| Case | Answer |
|---|---|
| `/status` unreachable or erroring | the listing says nothing: no watch changes; logged once a streak by exception type; asked again next tick |
| A listing with no nodes | says nothing (ruling 4) |
| A session missing from one listing | one miss; gone at two (ruling 4) |
| The Grid's timeout cannot be read at open | `grid_timeout: null`, logged at info without the id; the open succeeds |
| Two nodes with different timeouts | each session carries its own node's |
| A socket cannot open | logged once a streak with the workspace name; tried again on the next look while owed and before the deadline; never fails a call |
| A socket drops while its session lives | flagged and looked at now; reopened and re-subscribed on that look while listed; if the listing misses it once more, gone |
| A command on a held socket gets no reply in 5 s | `TimeoutError` to the caller (E3); the listing, not the socket, decides the session's fate |
| A session ends and its socket stays open (measured) | the monitor closes it when the watch ends |
| `end_browser`'s quit fails | no `ended` event (as today); the listing finds it later if it is watched |
| A look raises unexpectedly | logged by type; the next look runs a tick later |
| A subscriber raises | logged once a streak by type; the event is dropped for it; other subscribers unaffected |
| A subscriber falls 1 000 events behind | the event is dropped for it and counted in `dropped`; logged once a streak |
| `publish` before `start` / after `stop` | delivered inline / dropped |
| `ended` twice for one id (a quit and a listing) | published once |
| `watch` for an id already ended | ignored: nothing left to owe |
| The process restarts | the bus and the watches are memory; the collector's notes re-create its watches at `start` |
| Shutdown with sockets held | each unsubscribed and closed |

### 10. Settings

None (ruling 13). Constants in `monitor/monitor.py`: `LISTING_TICK = 30.0`,
`MISSES = 2`, `DEFAULT_TIMEOUT = 300`, `ENDED_MEMORY = 4096`; in `events.py`
`QUEUE_LIMIT = 1000`; in `bidi.py` `OPEN_TIMEOUT = 5.0`, `REPLY_TIMEOUT = 5.0`,
`PING_INTERVAL = 20.0`, `MAX_MESSAGE = 16 * 2**20`.

### 11. AGENTS.md amendments (R2, R3), verbatim

In **Rules**, the BiDi sentence of *"Plain W3C WebDriver only"* — today
*"WebDriver BiDi is W3C and allowed, used **only** for site data, and
reattached per call through `Grid.bidi(session_id)`, which closes its socket on
exit."* — becomes:

> WebDriver BiDi is W3C and allowed in exactly two shapes. **Per call**, for
> site data, reattached through `Grid.bidi(session_id)`, which closes its socket
> on exit. **Held**, by the session monitor (`monitor/bidi.py`), only while a
> session is owed something that needs its events, and never with an intercept:
> a request matching an intercept hangs with nobody to answer. `websockets` is
> imported there and nowhere else. A held socket is a channel, never a liveness
> signal: measured, it survives its session's end and its hub still answers a
> ping, so the monitor closes it when the watch ends.

In **Refresh, not cleanup**, the last paragraph becomes:

> **One bounded wait is allowed:** a task that waits for something this server
> was told to expect, and ends when nothing is owed — the admin broadcast
> (while a page listens), the recordings collector (while a recording is owed)
> and the session monitor (while a session is watched). A loop that tidies is
> still not.
>
> **The Grid reaps; we let it.** A held subscription on a busy page would keep
> its browser alive forever (measured: 2.2× the timeout and counting), so a
> watched session carries a deadline: its last call plus its node's
> `sessionTimeout`, read from `/status`. At the deadline the monitor
> unsubscribes, closes the socket and lets the Grid's timer run. This server
> ends no session on a timer of its own.

In **Recordings**, the bullet *"The collector never sends the Grid a session
command…"* becomes:

> - **Nothing that watches sends the Grid a session command.** The monitor
>   learns whether a watched session still runs from one `GET /status` listing
>   per tick (`Grid.listing()`): a WebDriver command such as
>   `GET /session/{id}/url` counts as activity and would stop the Grid ever
>   reaping the browser. A session is gone when two listings with nodes, taken
>   after the watch began, miss it; a listing that fails or shows no nodes says
>   nothing. The collector hears an end as `session.ended`, and watches every
>   owed note at start.

A new section after **Refresh, not cleanup**:

> ### The session monitor and the bus
>
> `monitor/` knows which sessions this server opened, which ended and why, and
> which it still owes something. It says so on a bus: `session.opened`,
> `session.ended` (cause `ended`, `gone`, `lost`), and `call.finished`.
>
> - **Events carry no values**: workspace, the session's `session_id`, browser
>   kind, tool, surface, outcome, times — never an argument, a URL, a header, a
>   body or a secret. Every field is a scalar; `test_events_carry_no_values`
>   holds it. No subscriber logs the `session_id`.
> - **Publishing never blocks a call.** A subscriber is a function run on the
>   loop, with a bounded queue; one that falls behind loses events, counted.
> - **`/status` is the truth about liveness, the socket is not.** Gone is two
>   listings; a socket that drops is one miss.
> - **The registry and the bus are process memory**, like the per-session lock:
>   one replica.

In **Gotchas**, after *"The Grid's session timeout is not this repo's
setting."*: *"This server reads it per node from `/status` at every open and
reopen and reports it as `grid_timeout`; it never sets or assumes it."*

In the lifetime table (E1's **Two lifetimes: the session and the workspace**),
the row *How long a session's browser lives idle* reads, in its *Who owns it*
cell: *the Grid — `SE_NODE_SESSION_TIMEOUT` on the node, read from `/status`
and shown as `grid_timeout`*.

In **Scaling: one replica**, after the lock's paragraph: *"The session
monitor's watches and the bus are process memory too: a second replica would
watch nothing the first opened."*

### 12. Documentation

- **AGENTS.md**: §11.
- **README**: where it names `SE_NODE_SESSION_TIMEOUT`, one sentence — the
  server reads it from the Grid's `/status` and shows it as `grid_timeout`.
- **Skill**: `references/WORKSPACES.md` gains one line where it explains
  `workspace://current`: `grid_timeout` is how long a session may sit idle
  before the Grid ends it, and any call starts the clock again.
- **Wiki**: regenerated; the status page gains the field from the schema.
- **pyproject**: `websockets>=15`, with its reason beside it, as the others
  have; `kubed.selenium_flow.monitor` in the packages list.
- **CHANGELOG `[Unreleased]`**: *"`workspace://current` and the admin summary
  show how long the Grid lets a session sit idle (`grid_timeout`)."* The monitor
  itself is internal and earns no line.

### 13. Testing

Unit tests; no new integration flow (AGENTS.md: a flow only for something a
person would see break that no existing flow catches — and a reap takes five
minutes).

- **`tests/test_monitor_events.py`**: every event type's fields are scalars from
  the allowed set (`test_events_carry_no_values`); the kinds are the three;
  inline before `start`; on the loop, after `publish` returns; from a worker
  thread; dropped after `stop`; an unknown kind refused; overflow counted and
  logged once; a raising handler logged once by type and others still served;
  `cancel`.
- **`tests/test_monitor_bidi.py`**, against a real `websockets.asyncio.server`
  on localhost speaking enough BiDi: command and reply by id, error replies as
  `BidiError`, no reply times out, the intercept refused before anything is
  sent, events to `on_event`, `subscribe` returns and records the id, `close`
  unsubscribes first, a server-side close fails waiting commands and calls
  `on_close`, `reopen` re-subscribes by event name.
- **`tests/test_monitor.py`**, with a fake listing, a fake clock and a fake
  socket factory: one listing per look however many watches; a listed session
  resets misses and refreshes its timeout; two misses publish `gone` once and
  drop the watch; a failed listing, one with no nodes, and one older than the
  watch say nothing; `ended` publishes once and refuses a later `watch`;
  `release` of the last reason drops the watch; the task runs only while a watch
  exists and a watch before `start` is taken up by it; recording alone holds no
  socket; a reason in `events_for` opens and subscribes, the deadline closes it,
  a `touch` reopens it; a dropped socket is reopened while listed, and one miss
  after a drop is gone; a socket that cannot open is retried and logged once;
  ending and `stop` close sockets; calls from worker threads.
- **`tests/test_grid_listing.py`**: `listing()` from a `/status` payload (nodes,
  slots, `sessionTimeout`, bad timeouts), `session_timeout`, `bidi_url`.
- **`tests/test_recording_collector.py`**: the listing tests go (they are the
  monitor's now); the rest drive ends with `ended(grid_id)` where they cleared a
  fake listing; new: `expect` watches (a discard too), `start` watches what the
  notes still owe, a filed note releases its watch, a `session.ended` on the
  server's bus reaches the collector.
- **`tests/test_workspace_monitor.py`**: the record's `grid_timeout` from
  `Grid.session_timeout`, `None` when it raises; kept when the session ends;
  replaced on reopen; `describe` reports it; `opened`, `ended` and `lost`
  announced at each place in §7; nothing announced without a monitor; a stored
  timeout read back only when it is a positive int.
- **`tests/test_recording_open.py`**: the two `finished` assertions read the
  monitor's `ended` instead.
- **`tests/test_boundaries.py`**: `monitor` in the kernel walk and the
  selenium-free set; `test_only_the_monitor_holds_a_socket`, read off the source.
- **`tests/test_admin_events.py`**: a session opening or ending on the bus pokes
  the broadcast. **Goldens** regenerate for the new field (`openapi.json`,
  `admin-workspaces.json`); the tools goldens do not move. **UI**:
  `WorkspaceSummary` shows `idle timeout` in the browser group, live and idle,
  as the boards word it (idle: `no session is open`, no cause), and hides it
  when null; `idle()` formats.

## Verify first

Programme items 1–3 are E2's. Measured 2026-10-09 against the cluster Grid: hub
and node `4.48.0 (revision 27f5213)`, Chrome `152.0.7977.82`, KEDA, one session
per node; client `websockets` 17.2, `requests`, `selenium` 4.50.0. Every Grid
session opened was deleted, including on failure, and the Grid listed none
afterwards. The scripts were throwaway and are not in the repo.

1. **A second BiDi socket on one Grid session, beside the per-call one** —
   *measured, works.* Two held sockets (A, B) at once each answered
   `session.status` and `browsingContext.getTree`; `Grid.bidi` (Selenium's
   per-call socket) opened, answered and closed beside them; A answered again
   after. Chrome reports `"message": "already connected"` in `session.status`
   and serves both anyway.
2. **The reap closes a silent held socket** — *measured, it does not.* A
   subscription-free socket stayed open for 60 s after `DELETE /session/<id>`
   (no close frame, no error), and, held through a real reap, stayed open after
   the listing dropped the session. After the delete the hub answered a ping at
   once and a command got no reply in 10 s. Hence rulings 3 and 4.
   **A ping is not activity** — *measured*: a socket pinging every 20 s was
   reaped 330 s after its session opened, a silent one 331 s (timeout 300 s,
   clean-up every 30 s, the listing polled every 5 s).
   **A subscribed socket on a polling page is not reaped within 2× the
   timeout** — *measured, it is not*: subscribed to `log.entryAdded` on a page
   logging every 5 s (`setInterval`), the socket received an event every 5 s
   (the hundredth at 500 s) and the session was still listed 660 s after its
   socket opened, 2.2× its 300 s timeout, until the script deleted it. That is
   programme R3's reason for the deadline. Its socket, too, was still open 5 s
   after that delete, and the silent and pinged sockets were still open some
   350 s after their reaps when the script ended.
3. **`/status.sessionTimeout`** — *measured under KEDA*: a node scaled up from
   zero by a `POST /session` listed `sessionTimeout: 300000` and the new session
   in its slot as soon as the POST returned. *Unverified on standalone*
   (`docker compose up`; no Docker daemon in this pod): Grid source makes
   standalone a node, so it should report the same. Should it not, a
   standalone's `grid_timeout` is `null` and its watches use the 300 s default
   (ruling 10): nothing breaks, the card shows nothing. Dr K's
   `docker compose up` settles it: `curl -s localhost:4444/status | jq
   '.value.nodes[].sessionTimeout'`.

Also measured: `session.subscribe` returns `{"subscription": "<uuid>"}` on
Chrome 152 and `session.unsubscribe` takes it; an unknown method is an `error`
reply (`unknown command`). *Unverified*: Firefox's answers to the same (only
Chrome was asked for), whether connecting a silent socket is itself activity
(the reap timings above cannot tell 300 s after open from 300 s after connect),
and the ingress behaviour in ruling 2.

The design was also prototyped outside the repo before the plan was written:
`monitor/`, the collector and workspace changes and their tests, on the
pre-rename tree, passed the whole suite but for the two goldens the new field
moves. Once E1 was implemented the plan was applied, task by task and as
written, to a copy of E1's tree (`8d33c6e`): Python, UI, ruff and the wiki
check all pass, and only `openapi.json` (Task 5) and `admin-workspaces.json`
(Task 7) move. The plan's *Reconciled with E1* section has the counts.

## Next round

- **`workspace://current`'s `live`** still asks `Grid.is_alive`, a session
  command, so reading the status is activity. For a watched session the monitor
  could answer instead.
- Ending a watched session at its deadline ourselves (programme R3's later
  option).
- A Redis-backed bus and a lease for a second replica (programme R5).
- Firefox: the held socket's subscribe/unsubscribe and replay, measured when E3
  needs them.
