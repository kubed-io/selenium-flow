# Recordings: a video of a browser's whole life

**Status: BUILT on branch recordings (PR to follow); Task 9 — Dr K's live check — pending. Plan:
`docs/superpowers/plans/2026-10-08-recordings.md`. Penpot: *Admin UI* → page
*Session · Files*, flow Recordings (boards `recordings-*`,
`lightbox-recording-*`, `overlay-clear-recordings`).

## Brief

Dr K, 2026-10-08: *"Selenium screen recordings."* Opt-in on `open_session`, so
the whole browser is recorded and the recording ends when the browser does — one
lifecycle, no extra tools. Off by default. A new **Recordings** row under
Screenshots in the admin UI, with the same rules as screenshots: ephemeral unless
kept into Files. `session://current` says whether the session is recorded. A
forgotten recording times out with the browser.

And, once the research was in: *"I do not want to rely on selenium and this mcp
sharing a folder"* → then, having seen why there is no other way: **this app
does not move recordings across machines.** However the operator does it — the
same NFS share mounted on the nodes, rclone to Nextcloud's WebDAV, a local
folder — recordings arrive in one inbox directory, and selenium-flow files them
from there. *"Then we take the responsibility off of this app."*

## Research that shaped it

### How Selenium records (docker-selenium source, verified 2026-10-08)

- **The recorder is already inside every node image.** Since `4.27.0-20241225`
  `selenium/node-*` and `standalone-*` are built on the video base: ffmpeg,
  rclone and the recorder service ship in the browser container. It is **off by
  default** in node images (`SE_RECORD_VIDEO=false`) and **switched on per
  session by the `se:recordVideo: true` capability**, which wins over the env
  default either way (README; `Video/video_service.py::get_video_filename`).
  Opt-in per session works from **`4.45.0-20260606`** (#3131, with #3147 fixing
  4.44's record-everything bug). The cluster runs `4.48.0-20260905`.
- **What it records:** ffmpeg `x11grab` of the node's whole Xvfb display —
  1920×1080 by default, browser chrome included, not just the page — at 15 fps,
  H.264 in **fragmented MP4** (`frag_keyframe+empty_moov`), capped near 1 Mbit/s
  (≈7.5 MB a minute at worst, far less for a still page). A fragmented file plays
  in a `<video>` even if it was cut off.
- **When:** the recorder listens on the Grid's event bus. `session-created` with
  the capability starts ffmpeg; `session-closed` — reason `QUIT_COMMAND` for our
  `end_browser`, `TIMEOUT` for a reap — stops it (`q`, ≤10 s, then SIGTERM) and
  finalises the file. **A recording is one Grid session**: it cannot start or
  stop mid-session, and a reopen after a reap is a new file.
- **The file name:** with the default `SE_VIDEO_FILE_NAME=auto`,
  `<se:videoName or se:name>_<gridSessionId>.mp4`, or `<gridSessionId>.mp4` with
  neither, under `VIDEO_FOLDER` (`/videos`); `SE_VIDEO_SESSION_SUBFOLDER=true`
  puts it in `/videos/<gridSessionId>/`. The name is passed through
  `[^a-zA-Z0-9-_]` → removed, so `.` is stripped: `a.b` and `ab` collide. **The
  Grid id survives every variant**, and it is unique per browser.
- **Getting it out:** there is **no Grid endpoint that serves a recording**. Port
  9000 is a readiness probe; the Grid UI's camera icon is live noVNC;
  `/session/{id}/se/files` is the browser's downloads directory only and is gone
  when the session ends — before the recording is even finalised. The image's
  only built-in export is `rclone copy <file> $SE_UPLOAD_DESTINATION_PREFIX`
  after finalising, to any of rclone's ~70 backends.
- **A capability with nothing behind it is silently ignored.** A Grid whose
  nodes predate 4.45, or that records nowhere anyone reads, accepts
  `se:recordVideo` and says nothing.
- Headless browsers cannot be recorded (we never run headless), and nodes must
  keep `SE_NODE_MAX_SESSIONS=1` or recordings share one display.

### How a finished recording is noticed (researched 2026-10-08)

Dr K turned down the first draft's lazy collection — a recording found only when
somebody happened to look — and asked for something proactive and async.

- **Filesystem events exist, and do not see NFS.** `watchfiles` (Rust `notify`;
  what uvicorn's reload uses) and `watchdog` wrap inotify / FSEvents / kqueue.
  `watchfiles.awatch()` is async (anyio), recursive, stops on an `anyio.Event`,
  and with `yield_on_timeout` wakes on a timer when nothing changes. But
  **inotify only reports changes made through the local mount**: a file an NFS
  peer writes — the node pods, here — raises no event (well documented;
  `plex-nfs-watchdog` exists because of it). The same goes for SMB and most FUSE
  mounts. `watchfiles` answers this with `force_polling` / `poll_delay_ms` on the
  same iterator.
- **The recorder leaves a completion marker.** Its ffmpeg (`-movflags
  frag_keyframe+empty_moov+default_base_moof`, no `skip_trailer`) ends a
  cleanly closed file with an `mfra` box whose **last 16 bytes are an `mfro`
  box**: `00 00 00 10`, `mfro`, `00 00 00 00`, then the `mfra` size
  (`libavformat/movenc.c`, `mov_write_trailer` → `mov_write_mfra_tag`, FFmpeg
  8.0). A file still being recorded, or still being copied — rclone, NFS,
  Nextcloud — cannot end that way. Only a file whose ffmpeg was killed (the
  recorder's SIGTERM fallback after 10 s) has none.
- **The recorder writes in place.** `video_service.py` records straight to the
  final `.mp4` name, so on a shared volume the file exists and grows for the
  whole session. A keyframe — hence a write — comes at least every ~17 s
  (x264's default 250-frame GOP at 15 fps), so a recording file is never idle
  for a minute while its browser lives.
- **What others do.** Selenoid records to a temporary name and renames it to
  `.mp4` when the session closes — its docs: the link *"will work only after
  session has finished"*. docker-selenium's recorder is event-driven off the
  Grid's ZeroMQ bus, checks integrity, then feeds an async upload queue. rclone
  (without `--inplace`) and Nextcloud write `*.partial` / `.part` and rename.
  BrowserStack and Sauce process asynchronously and clients poll the session
  API. Everyone has either an atomic rename or an explicit completion signal,
  and a queue between the two sides.
- **The admin page already listens.** `Broadcast` (`http/admin/sessions.py`)
  pushes the session list over SSE, `files_rev` included, from a task that runs
  only while a page is connected, and `invalidate()` + a nudge make the next
  tick immediate.

### What was ruled out

| Option | Why not |
|---|---|
| Pull the file in `end_browser` | Unreachable (inside the node container, no endpoint) and not finished yet — our quit is what finalises it |
| WebDriver BiDi `browsingContext.startScreencast` | Chrome: *"not implemented"* (chromium-bidi). Firefox: shipped in 154 but fails until 156 (bug 2066782); the cluster's image ships 155.0.1 |
| A WebDAV upload endpoint on selenium-flow | Works, but makes this app own one transport out of endlessly many, and cannot work in stdio. Dr K: the drive is the operator's business |
| Server-side frames (screenshot polling) | 3–5 fps slideshow, viewport only, blank during dialogs and long waits, fights the per-session lock |
| CDP `Page.startScreencast` | CDP is not allowed (AGENTS.md) |
| Collect lazily when the recordings are read | Dr K: not proactive; a recording nobody looks at is never filed |
| Wait for the file inside `end_browser` | Blocks the caller for seconds, and a reap has no caller |
| Subscribe to the Grid's ZeroMQ bus for `session-closed` | Says when the browser ended, not when the file arrived; adds `pyzmq` and a network dependency on Grid internals |
| Keep the queue in the session store | Durable only with Redis; the notes on disk survive a restart in both modes, and recording already requires `DATA_DIR` |

## Rulings

Dr K, 2026-10-08:

1. **Selenium records; the operator delivers; selenium-flow files.** The
   recorder in the node images makes the video. Getting it into the inbox is the
   operator's, documented in the README's *Recording* section with worked
   examples. selenium-flow never receives an upload and never mounts the Grid.
2. **Opt-in on `open_session(record=true)`; one recording per browser.** Off by
   default. Explicit only — no query parameter, no header, no config default,
   like `insecure` (§F3.8).
3. **A reap replays it; an explicit `open_session` asks again.** A silent reopen
   after the Grid reaps the browser keeps recording — the caller never ended it.
   A bare `open_session()` after `end_browser` does **not** inherit `record`,
   unlike every other setting: video is costly, and *"turned off when the browser
   is ended"* is the model.
4. **`recording.enabled` gates it.** Off: `open_session(record=true)` is a 400
   saying recording is not set up on this server. It exists because the Grid
   silently ignores the capability, so only the operator can say it is wired up.
5. **Only a filed recording is shown.** Dr K: *"I don't want a UI for the dump
   of the recordings. I only want to see the recordings in the session view once
   they are properly moved into the session."* The Recordings row, the folder
   resource and the counts hold finished files and nothing else — no in-progress,
   finishing or missing tiles, and no view of the inbox. (This replaces an
   earlier answer that drew a ● REC tile in the row.) That a browser *is* being
   recorded is session state, so the session card carries a ● REC pill and
   `session://current` says `recording: true`.
6. **A data root with two homes in it** — a breaking change, made now:
   `data.dir` replaces `flow.data_dir`; sessions live under `sessions/`, and the
   default inbox is `recordings/`, the same name as the folder in each session.

   ```
   $DATA_DIR/
     sessions/<name>/{flows,files,screenshots,recordings}
     recordings/                     ← recording.dir's default: the inbox
   ```

   `recording.dir` is its own setting, so an operator who already has a folder
   the Grid writes to points at it instead.

7. **Same rules as screenshots.** Keep moves a recording into Files; the row
   clears in bulk; no per-recording delete.
8. **A collector files recordings as they arrive** — proactive and async, not
   lazy. The notes on disk are the queue; one asyncio task per process is the
   engine; it runs only while a recording is owed. A file is complete when it
   ends in a valid `mfro` box. Filing nudges the admin broadcast, so an open
   page shows the recording within a second.
9. **`watchfiles`, events or polling chosen automatically.** Native events on a
   local disk; polling on a network filesystem, where events cannot see the
   other side's writes. `recording.watch` overrides the choice.

## Design

### 1. Data layout and configuration

`data.dir` (`DATA_DIR`, `--data-dir`) is the server's data root. Unset still
turns off flows, kept files, screenshots — and now recordings.
`SessionLayout`'s root becomes `data.dir/sessions`; nothing else about how a
session's paths are built changes, so the link and traversal rules stand as they
are.

The `recording` section:

| Setting | Env | Default | Meaning |
|---|---|---|---|
| `recording.enabled` | `RECORDING_ENABLED` | `false` | The operator says the Grid's recordings reach the inbox |
| `recording.dir` | `RECORDING_DIR` | `$DATA_DIR/recordings` | The inbox. Set it independently to point at a folder the operator already has — any path, inside the data dir or not |
| `recording.wait` | `RECORDING_WAIT` | `600` (s, ≥ 30) | How long after a browser ends to wait for its file |
| `recording.watch` | `RECORDING_WATCH` | `auto` | `auto`, `events` or `poll`. `auto` polls when the inbox is on a network filesystem (`nfs`, `nfs4`, `cifs`, `smb3`, `9p`, `ceph`, `glusterfs`, `fuse.*`, read from `/proc/self/mountinfo`), and uses events otherwise, including where there is no `/proc` |
| `recording.poll` | `RECORDING_POLL` | `1000` (ms, ≥ 200) | The poll interval, when polling |

**Boot refuses, with the reason in the message** (§F4.12: configured and
unusable stops the boot):

- `recording.enabled` without `data.dir` — a recording needs somewhere to be
  filed.
- `recording.enabled` and the inbox is missing, or not readable *and* writable
  by this process — collecting a recording is a move out of it.
- `FLOW_DATA_DIR` in the environment, or `flow:` in the config file — *"renamed
  to `DATA_DIR`; sessions now live under `DATA_DIR/sessions`"*. The env layer is
  lenient by design (AGENTS.md), and here that would boot with flows silently
  off; a retired name is the one exception worth naming.
- **An old layout:** a directory at the top of `data.dir` that is a valid
  session name and holds `flows`, `files` or `screenshots`. Its message lists
  them and says to move them into `sessions/`. No migration code (§F4.4): the
  move is done once, by hand.

### 2. Opening a recorded browser

- **`open_session(record: bool | None = None)`**, docstring written for the
  model: records the browser's whole life as a video; ends with the browser;
  the file appears under `session://files/recordings` shortly after; costs the
  Grid CPU, so only when a person will watch it.
- `record` joins `SETTINGS` as explicit-only (`None, None, _as_flag`) and is
  stored in `record.settings`, so `resolve`'s reopen-after-reap replays it with
  everything else. `open_browser` **drops `record` from `previous`** before the
  cascade (ruling 3). The HTTP route already passes every `SETTINGS` key, so
  `POST /browser {"record": true}` comes free.
- `Grid._options(browser, insecure, record, name)` adds
  `se:recordVideo: true` and `se:videoName: <session name>` when recording. The
  name is only for a person browsing the inbox; nothing matches on it.
- Right after the browser opens, the server writes the **note** (§3) and hands
  the Grid id to the collector. The Grid id lives on our disk and in the inbox
  only; no result, resource or admin payload carries it (AGENTS.md).
- The `open_session` result gains `recording: true|false`.
- `record=true` with `recording.enabled` off → **400**:
  *"Recording is not set up on this server (recording.enabled is off). See
  the README's Recording section."*

### 3. The collector

```
open_session(record=true) ─► note on disk ─► collector.expect(gridId)    session card: ● REC
end_browser               ─► note stamped ─► collector.ended(gridId)
                                    │
    ┌───────────────────────────────┴──────────────────────────────────────┐
    │ while anything is owed:                                               │
    │   async for changes in awatch(inbox, events | polling,                │
    │                               yield_on_timeout, 30 s timer):          │
    │     a file whose name holds an owed Grid id changed                   │
    │        → ends in a valid mfro?   yes → file it                        │
    │        → no mfro, unchanged 60 s, browser gone?  → file it as it is   │
    │     timer → read the Grid's /status listing once (never a session);   │
    │             drop notes past ended + recording.wait, with a warning    │
    │     filed → note deleted, admin broadcast nudged                      │
    │   nothing owed → stop_event set, the task ends                        │
    └───────────────────────────────────────────────────────────────────────┘
```

**The queue** is the notes, `sessions/<name>/recordings/.pending/<gridId>.json`
→ `{"opened": <ms>, "ended": <ms> | null, "browser": "chrome"}`. They are on disk,
so they survive a restart whichever session store is in use, and recording
already requires `DATA_DIR`.

**The engine** is one `Collector` per process (`recordings/collector.py`, under
the boundary rules: no protocol imports, no `selenium`). It keeps a
`gridId → expectation` map mirroring the notes, and one asyncio task.

- **Bounded.** The task starts with the first expectation and ends when the map
  is empty — the shape `Broadcast` already has, running only while a page
  listens. At startup the map is rebuilt from the notes and the task resumes if
  anything is owed. AGENTS.md's *"do not add a scheduler"* was written against
  cleanup loops; it is amended to allow **a bounded wait for something this
  server was told to expect**, and nothing else.
- **Fed from the edges.** `open_session` and `end_browser` run in worker threads
  (FastMCP sync tools, Starlette routes), so they write the note and hand the
  Grid id to the loop thread-safely. The collector owns no browser and takes no
  session lock.
- **Watching.** `watchfiles.awatch(inbox, recursive=True)`, with
  `force_polling` and `poll_delay_ms` from `recording.watch` and
  `recording.poll`, `stop_event` for the end, and `yield_on_timeout` with a
  30 s `rust_timeout` for the timer. A change is matched by Grid id anywhere in
  its path below the inbox: the recorder's per-session subfolder, rclone
  prefixes and Nextcloud paths all keep it. Names ending `.partial` or `.part`
  are skipped — a transport that renames is finishing.
- **Complete** means the last 16 bytes are `00 00 00 10 'mfro' 00 00 00 00` and
  the `mfra` size they name is no larger than the file, with `mfra` at that
  offset. Reading 16 bytes, plus 4 to confirm, costs nothing.
- **Cut off** — no `mfro`, unchanged for 60 s, and the Grid says the browser is
  gone — is filed as it is: a fragmented MP4 plays up to where it stopped. While
  the browser lives, the file cannot sit unchanged for 60 s (a keyframe is
  written at least every ~17 s), so this never files a recording early.
- **Filing** moves the file, in a worker thread, into
  `sessions/<name>/recordings/rec-<YYYYMMDD-HHMM>.mp4` (UTC, from the note's
  `opened`; a clash lands as `name (1)`, the rule every folder follows),
  deletes the note, logs one line, and calls the admin broadcast's
  `invalidate()` and nudge. Across filesystems a move is a copy and an unlink.
- **Reaps need no caller.** The recorder finishes a reaped browser's file on its
  own, and the change is seen like any other. Once per tick the collector reads
  the Grid's `/status` listing and never touches a session (a command such as
  `GET /session/{id}/url` counts as activity and would stop the Grid reaping the
  browser), so that a file that never comes has a deadline to miss. A listing
  taken before a browser opened cannot mark it gone, and a failed sweep is
  retried a tick later.
- **Missing** after `ended + recording.wait`: the note is deleted and one
  warning is logged — *"recording for session S (opened 14:03) never reached
  RECORDING_DIR; see the README's Recording section"*. Nothing is shown: the row
  holds files, and there is no file. A file that turns up later is not claimed.
- **The inbox is never cleaned by us.** A file no note claims — from another
  server, or one that arrived after its note was dropped — is left where it is.
  The operator owns the inbox.
- **Reads are pure.** Listing recordings reads the folder and nothing else.
  `session://current` (and `GET /browser`) gains `recording: true|false` — true
  when the browser it holds was opened with `record`, read from the stored
  settings.
- **Typical timing:** our quit, then up to 10 s for ffmpeg to close the file,
  then the operator's transport, then at most `recording.poll` for the change to
  be seen. On a shared volume the recording is in the row within about 5–15 s of
  the browser ending.

### 4. The Recordings folder on every surface

It is the Screenshots folder's twin (§F4.6–§F4.9), and every surface gets it in
the same commit:

| Surface | Adds |
|---|---|
| Resources | `session://files/recordings`, `session://files/recordings/{name}`; `session://files` lists a third folder with its count |
| Entry | an ordinary file entry (`name, uri, size, created, content_type: video/mp4, url, keep_with`). Notes are never listed |
| Reading one | answers its **entry** as JSON — the signed `url` to watch it — not the bytes: a model cannot watch a 40 MB video, and a resource read would base64 it |
| `keep_file` | `keep_file("session://files/recordings/<name>")` moves it into Files, like a screenshot |
| `upload_file` | any recording, by URI, like any file |
| REST | `GET /files/recordings`, `PUT /files/recordings/{name}/kept` |
| Admin API | `GET …/files` returns `recordings[]`; `DELETE …/files/recordings` clears; `POST …/files/recordings/{name}/keep` |
| Signed link | `/recordings/{session}/{name}`, served from disk with **Range** support so a player can seek |
| `show` | `show("session://files/recordings")` draws the row; playback in the app is best effort |
| Reserved | `recordings` joins `screenshots` and `downloads` as a name Files cannot hold |

**Clear recordings** deletes the files in the folder, and only them. Notes are
not files and are untouched, so a recording still on its way lands after a
clear.

**Serving a video.** Every type but a raster image or a PDF is served with
`Content-Security-Policy: sandbox`, and the sandboxed origin is what broke
screenshot links for extensions. `video/mp4` gets the image treatment: no
sandbox, `nosniff`, a no-script CSP that still lets the media document load
itself. The exact header is measured in a real browser in the plan.

### 5. Admin UI (drawn in Penpot)

- **Files tab rows:** Downloads, Screenshots, **Recordings**, Files.
- **Recordings row:** count pill; **Clear recordings** (`danger`), disabled
  when empty. Empty: *"No recordings yet."*
- **Tiles**, newest first, one kind: 🎬 on the thumb with a ▶ overlay,
  `rec-20261008-1403.mp4`, *"38.2 MB · 3m ago"*, 📌 keep on hover — the
  screenshot tile with a different glyph.
- **Lightbox:** `<video controls autoplay>`; it steps through the row
  (‹ Prev, Next ›, ← →, Esc) like screenshots, and **Keep moves on to the
  next**.
- **Session card:** a **● REC** pill beside *live* while the browser is
  recorded.
- **Sessions list:** counts read *"6 screenshots · 2 recordings · 2 files"*.
- **Confirm — Clear recordings:** how many, that Files is untouched, and their
  names; **Delete 2 recordings**.

**Drawn** in Penpot, 2026-10-08, on the *Session · Files* page, as one playable
flow, **Recordings**, starting at `recordings`:

| Board | Shows |
|---|---|
| `recordings` | the Files tab with the Recordings row between Screenshots and Files; ● REC on the session card |
| `lightbox-recording-1`, `-2` | the video lightbox stepping through the row |
| `lightbox-recording-after-keep` | Keep inside the lightbox moved on to the remaining recording, *1 / 1* |
| `recordings-kept` | the newest recording moved into Files |
| `overlay-clear-recordings` | the confirm |
| `recordings-cleared` | the empty row, *"No recordings yet."* |
| `proto-guide-recordings` | how to play it |

New library components on *Components*: `pill / rec`, `file / video`,
`file / video-keep` and `lightbox / recording`, the last drawing a 1280×900
browser window inside the recorder's 1920×1080 screen, because that is what a
recording shows. Checked as §F4.11 requires: every board reachable from the
start, no dead ends, no flow nobody named, `File.validate()` clean. Version
*Recordings design (spec 2026-10-08) — for Dr K's review*.

### 6. Failures

| Case | Answer |
|---|---|
| `record=true`, recording off | 400, message above |
| `record=true`, no data dir | cannot happen: `recording.enabled` without `data.dir` does not boot |
| Note cannot be written | the browser stays open; the result says `recording: true` and carries `recording_error` with the reason, as `file_error` does for a screenshot |
| Inbox unreadable while watching | logged once; the collector keeps its map and retries on the next tick |
| Move fails (permissions, disk) | the note stays; logged; tried again on the next change or tick |
| File never arrives | the note is dropped after `recording.wait` with one warning logged; nothing is shown |
| The collector's task raises | logged; it restarts from the notes on the next `expect` or tick, and on boot |
| The server restarts mid-recording | the notes rebuild the map at startup; a file that finished meanwhile is found by the first poll or a startup scan of the inbox |

### 7. Documentation

- **README — *Recording*:** what records (the node images' recorder,
  ≥ 4.45), how to switch it on (`RECORDING_ENABLED`), and the one rule — *make
  the Grid's recordings arrive in `RECORDING_DIR`*. Three worked examples: a
  shared volume mounted at the nodes' `/videos`; rclone to a WebDAV such as
  Nextcloud whose folder is the same storage (`SE_UPLOAD_DESTINATION_PREFIX` +
  `RCLONE_CONFIG_*`); and a local folder for stdio or compose. Both sides need
  write access to the inbox. Nodes keep `SE_NODE_MAX_SESSIONS=1`.
- **README — `DATA_DIR`:** the new layout, and the one-time move.
- **docker-compose.yaml:** mounts `./data/recordings` at the standalone container's
  `/videos` and sets `DATA_DIR` and `RECORDING_ENABLED`, so `docker compose up`
  records out of the box.
- **AGENTS.md:** the layout, and the recording rules — the Grid id stays on
  disk, the collector is the only background task that waits on the
  filesystem, and the inbox is the operator's. *"Refresh, not cleanup"* gains
  one sentence: a bounded wait for something the server was told to expect
  (the admin broadcast, the collector) is allowed; a loop that tidies is not.
- **pyproject:** `watchfiles` as a dependency, with the reason beside it, as
  the others have.
- **SKILL.md:** one line in the session reference about `record`.
- **CHANGELOG `[Unreleased]`:** *"`open_session(record=true)` records the
  browser; recordings appear under `session://files/recordings` and in the admin
  Files tab."* and *"**Breaking:** `FLOW_DATA_DIR` is now `DATA_DIR`, and
  session folders live under `DATA_DIR/sessions/`."*

### 8. Two terms

- **inbox** — `recording.dir`: where the operator's transport drops files. Not
  "uploads": `upload_file` already means putting a file into a page, and
  `uploads` is a legal session name.
- **note** — the `.pending/<gridId>.json` that claims a recording for a session.
- **collector** — the one task that waits for owed recordings and files them.

### 9. Testing

Unit tests, no new integration flow: recording needs a recorder behind the
Grid, and AGENTS.md's rule is to add a flow only for something a person would
see break that no existing flow catches.

- The cascade: `record` not inherited by an explicit open, replayed by a reap.
- `_options`: `se:recordVideo` and `se:videoName` only when recording.
- `mfro` detection on crafted bytes: complete, truncated, a `mfro` that names
  a wrong size, a file shorter than 16 bytes.
- The collector, with a temp inbox, polling at 200 ms, `FakeGrid` and a fake
  clock for the deadlines: nothing listed while the file grows; filed the moment
  it ends in `mfro`; a cut-off file filed only once the browser is gone; a match
  two directories down; `.partial` ignored until renamed; a note dropped (and
  logged) after `wait`; the task ending when nothing is owed and resuming from
  notes after a restart; the broadcast nudged on filing; a move across filesystems; two sessions never claim
  each other's files (`a.b` vs `ab`).
- Keep, clear (notes untouched), the reserved name, URIs, resource reads
  answering the entry.
- Config: the three refusals, the retired names, the old-layout check.
- Signed route: Range answers `206`; the video CSP.
- Goldens regenerate (tools on/off, OpenAPI, admin sessions);
  `test_surfaces.py` and `test_every_action_declares_a_response_shape` cover
  the new field.
- UI: the Recordings row and its empty state, the lightbox's video, the REC
  pill on the session card, the counts text.

### 10. Verify first in the plan

These were researched, not run. Task 1 measures them before anything depends on
them:

1. `docker compose up` with `selenium/standalone-chromium` and `./data/recordings`
   on `/videos`: does `se:recordVideo` record a standalone session, or does it
   need `SE_VIDEO_RECORD_STANDALONE=true`? What exact file name lands?
   Not run here (no Docker daemon in the build pod); requested from Dr K: recording is opt-in in the compose file (a bind-mount folder Docker creates is root's, and the image runs as 65534), so first `mkdir -p data/recordings && chmod -R 777 data` and uncomment `DATA_DIR`, `RECORDING_ENABLED`, both volumes and the Grid's `SE_VIDEO_EVENT_DRIVEN=false` and `SE_VIDEO_RECORD_STANDALONE=true` (there in case the standalone image needs them; polling mode, since a standalone container may not expose the event bus the event-driven recorder listens on); then `docker compose up --build`, `POST /browser?session=compose {"record": true}` and `DELETE /browser?session=compose`, and watch ./data/sessions/compose/recordings/.
2. Starlette `FileResponse` honours `Range` at the pinned version (believed
   since 0.39; the floor is 0.48).
3. The CSP a top-level `video/mp4` document needs to play in Chrome and
   Firefox without a sandbox.
   Measured in Task 9 against the deployed server. Task 6 ships
   `default-src 'none'; media-src 'self'; style-src 'unsafe-inline'` for
   `video/*`, which is what a top-level media document needs to load itself.
4. The recorder's timing: seconds from our quit to a file ending in `mfro`.
5. A real recording from the node image ends in `mfro` — the source says so;
   the plan looks at actual bytes.
   Items 4 and 5 requested from Dr K (2026-10-08): the live inbox listing, a recording's last
   16 bytes, and the quit-to-mfro timing — commands in the plan's Task 1, Steps 2–3.
6. `watchfiles` has a wheel for every Python the matrix runs (3.10–3.14) and
   for the image's platform, and its polling sees a file another container
   writes to a shared Docker volume.
   The wheel half is settled (below); the shared-Docker-volume polling half is still open.

**Results (2026-10-08):**

2. Settled: Starlette 1.7.0 `FileResponse` answers `Range: bytes=10-19` with
   `206` and `Content-Range: bytes 10-19/2560` (measured with `TestClient`).
6. Settled for wheels: watchfiles 1.3.0 ships `cp310-abi3` wheels (manylinux
   x86_64/aarch64, macOS) — one wheel covers 3.10–3.14.

### Out of scope

- WebDriver BiDi screencast — revisit when chromium-bidi implements it and the
  Firefox image passes 156.
- A recording's duration or poster frame — no ffmpeg in this image; the browser
  reads duration itself in the lightbox.
- Retention or a size cap for recordings — like screenshots, they stay until
  kept or cleared.
- Moving files across machines in any form (ruling 1).
