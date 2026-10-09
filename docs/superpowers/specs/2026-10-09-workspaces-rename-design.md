# Workspaces: the rename

**Status: DESIGNED 2026-10-09, branch `issue-60-workspaces` (#60). Plan:
`docs/superpowers/plans/2026-10-09-workspaces-rename.md`.** Epic E1 of the
programme `2026-10-09-workspaces-and-observability-design.md`, which carries
the research; this spec cites it as `programme R<n>`. Penpot: already renamed
(every page, component and text; the browser's sessionStorage strings kept).

## Brief

Dr K, 2026-10-09: *"They are actually workspaces. We recently changed the header
to be x-workspace which is the correct and well known header. We do need to
change everywhere in the ui, docs, design, tooling everywhere that says
'session' and make it 'workspace'… The workspace is literally just a
'namespace' in this app."* Programme R1.

Dr K, later the same day: *"We should generally consider a 'session' as when
the browser is actively open… not fully remove the word 'session'… It's more
like 'open_session', then the session is running in the workspace, like it's one
to one session to workspace. The workspace is the outer box and the session is
the thing holding and controlling the 'grid session'."*

So two words, two things:

- A **workspace** is the outer box: named by its caller, persistent, holding
  flows, files, site data, history and the settings it opens browsers with.
- A **session** is the live browser open in a workspace: at most one at a time,
  started by `open_session`, ended by `end_browser` (or the Grid's reaper), and
  it holds and controls the Grid's session (`session_id`). An idle workspace has
  no session.

## Research that shaped it

An inventory on 2026-10-09 (this branch's base, `6e925d5`) found our meaning
of *session* on about 1 270 lines of `kubed/`, 1 980 of `tests/`, 575 of
`ui/src`, 145 of `skills/`, 406 of `wiki/`, and in README, AGENTS.md,
CONTRIBUTING.md, `scripts/` and `prompts/`. What matters beyond the count:

- **Three other sessions share the word** and keep it: the Grid's browser
  session (`session_id`, `/session/<id>`, `Grid.sessions()`, `session_count`,
  `SE_NODE_SESSION_TIMEOUT`), the MCP transport session (`Mcp-Session-Id`,
  `Context.session_id`, `mcp.session.id`), and the browser's sessionStorage
  (site data's `"session"` key, `session_storage`, "Session storage", the
  "session" cookie expiry).
- **Persisted shapes**: the record's `session_id` field and site data's
  `"session"` key are serialised into Redis; both mean the other sessions and
  stay, so no stored record changes shape.
- **Things that outlive a deploy**:
  - Redis keys under the default prefix `selenium-flow:session:` (they expire
    within `SESSION_TTL`, a day by default).
  - `DATA_DIR/sessions/<name>/`, which is not ephemeral: flows, kept files,
    screenshots, recordings and the collector's `.pending` queue.
  - Saved flows whose `upload_file` steps name `session://files/…`.
  - Deployments setting `SESSION_*` env, which the lenient env layer would
    silently drop after the rename.
- **Names a client already holds**: the `X-Session-Key` header, `?session=`,
  `session://` URIs, `/admin/sessions` paths, the wiki pages `Sessions` and
  `current_session`.
- **Kept on purpose**: the tool names `open_session` and `end_browser` (the
  programme's later epics write `open_session(capture=true)`), and the
  capability title "Open browser session", which means the Grid's.

## Rulings

Programme R1 binds the vocabulary. The open items from #60, and what the
inventory raised, are decided here; Dr K reviews them on the PR.

1. Claude, 2026-10-09: **no aliases.** `X-Session-Key` and `?session=` stop
   naming a workspace (spec 2026-10-02: no compatibility while there is one
   user). They are **refused**, not ignored: a request carrying either is a 400
   that names the new spelling, so a client wired the old way finds out on its
   first call rather than as "name your workspace". Cost if wrong: one config
   line per client, said by the error.
2. Claude, 2026-10-09: **retired names refuse to boot**, as `FLOW_DATA_DIR`
   does: the env vars and file keys that moved to `workspace` (ruling 9) stop
   the boot naming the new name.
   Cost if wrong: a deploy that sets them stops until renamed, which is the
   point.
3. Claude, 2026-10-09: **the data directory moves itself, once.** At boot,
   `DATA_DIR/sessions/` becomes `DATA_DIR/workspaces/` with one rename when
   `workspaces/` does not exist. Both present stops the boot naming both. Cost
   if wrong: none expected; a rename on one filesystem is atomic.
4. Claude, 2026-10-09: **Redis keys expire.** The default prefix becomes
   `selenium-flow:workspace:`; records under the old prefix age out within the
   TTL ("everything is ephemeral"), and a deployment with an explicit
   `REDIS_PREFIX` keeps its keys. Cost if wrong: one sign-in per workspace,
   restored by `save_site_data` after.
5. Claude, 2026-10-09: **an old URI says where it went.** A `session://` URI
   given to any tool (`upload_file`, `keep_file`, `show`, `read_resource`) is
   refused with the `workspace://` spelling, so a saved flow names its own fix.
   Saved flows are not rewritten. Cost if wrong: a flow edit per old step.
6. Claude, 2026-10-09: **result fields follow the word**: `session` → `workspace`
   in every result, listing and payload (`open_session`, `end_browser`,
   `workspace://current`, flows, files, secrets, admin, `/ready`, `/info`).
7. Claude, 2026-10-09: **the wiki moves pages**: `Sessions` → `Workspaces`,
   `current_session` → `current_workspace`.
8. Dr K, 2026-10-09: **session stays, for the live browser.** Everything about
   the named box says workspace; everything about the open browser in it says
   session (see Brief). Text about whether a browser is open says "session"
   ("no session is open in this workspace").
9. Claude, 2026-10-09: **the config section splits along that line.**
   `workspace.store` and `workspace.ttl` (how workspaces are kept; env
   `WORKSPACE_STORE`, `WORKSPACE_TTL`) leave the `session` section, which keeps
   `browser`, `width`, `height`, `page_load_timeout` and `script_timeout` (how a
   session opens; `SESSION_*` env names unchanged). Ruling 2's refusal narrows to
   what moved: `SESSION_STORE`, `SESSION_TTL`, and `session.store`/`session.ttl`
   in the file. Cost if wrong: a later split back is the same small change.
10. Claude, 2026-10-09: **the admin's End ends a session, not a workspace**:
    `DELETE /admin/workspaces/{key}/session`, route name `admin_end_session`.
    A workspace is never deleted, only expires. Cost if wrong: one path.

## Goal

The thing a caller names, and every word about it on every surface, is a
*workspace*; the live browser open in it is its *session*; nothing a running
deployment holds is lost; and *session* means nothing else of ours.

## Non-goals

| Not built | Why |
|---|---|
| New behaviour of any kind | E1 is a rename; E2 onward build on it |
| Renaming `open_session`, `end_browser` | Kept by the programme; their results change keys |
| Aliases for the old names | Ruling 1 |
| Rewriting saved flows | Ruling 5 |
| Editing the saga or shipped specs | The record is the record |

## Design

### 1. Vocabulary

| Before | After |
|---|---|
| `X-Session-Key`, `?session=` | `X-Workspace`, `?workspace=` (refused, ruling 1) |
| `session://current\|files\|site-data` | `workspace://…` |
| config `session.store`, `session.ttl` (`SESSION_STORE`, `SESSION_TTL`) | `workspace.store`, `workspace.ttl` (`WORKSPACE_STORE`, `WORKSPACE_TTL`); the rest of `session.*` stays (ruling 9) |
| `REDIS_PREFIX` default `selenium-flow:session:` | `selenium-flow:workspace:` |
| `/admin/sessions…` | `/admin/workspaces…`; End is `DELETE /admin/workspaces/{key}/session` (ruling 10) |
| `DATA_DIR/sessions/<name>/` | `DATA_DIR/workspaces/<name>/` |
| package `session/`, `sessions.py` | `workspace/`, `workspaces.py` |
| `SessionManager`, `SessionRecord`, `SessionStore`, `SessionLayout` | `Workspaces`, `Workspace`, `WorkspaceStore`, `WorkspaceLayout` |
| `SessionSettings` (one class) | `WorkspaceSettings` (store, ttl) and `SessionSettings` (browser, size, timeouts) |
| `valid_session_name`, `GLOBAL_SESSION`, `STDIO_SESSION`, `RESERVED_SESSIONS`, `SESSIONS_DIR` | `valid_workspace_name`, `GLOBAL_WORKSPACE`, `STDIO_WORKSPACE`, `RESERVED_WORKSPACES`, `WORKSPACES_DIR` |
| result keys `session`, `sessions` | `workspace`, `workspaces` |
| operationId `currentSession`, schema `SessionStatus` | `currentWorkspace`, `WorkspaceStatus` |
| `references/SESSIONS.md`, wiki `Sessions`, `current_session` | `WORKSPACES.md`, `Workspaces`, `current_workspace` |
| UI `Session*` components, `#/sessions/…`, "Sessions" | `Workspace*`, `#/workspaces/…`, "Workspaces" |

**Kept** (the global constraint every task checks against): *session*
meaning the live browser in a workspace (ruling 8: `open_session`, "no session
is open", the `session` config section's browser settings, `admin_end_session`,
`DELETE …/session`); `session_id`
and every Grid use; `Context.session_id`, `Mcp-Session-Id`, `mcp.session.id`;
sessionStorage and the site-data `"session"` key; `open_session`,
`end_browser`, "Open browser session"; pytest's `scope="session"`,
`start_new_session`, `requests.Session`.

### 2. The refusals

| Given | Answer |
|---|---|
| `?session=` on a request | 400: "`?session=` is now `?workspace=`: rename it in the URL" |
| `X-Session-Key` header | 400: "`X-Session-Key` is now `X-Workspace`: rename the header" |
| `SESSION_STORE` or `SESSION_TTL` in env, non-blank | boot stops: "SESSION_TTL is now WORKSPACE_TTL: workspace settings moved out of session" (every one named) |
| `session.store` or `session.ttl` in the config file | boot stops: "the config file's `session.store` and `session.ttl` are now `workspace.store` and `workspace.ttl`" |
| `session://…` to a tool | 400: "`session://…` is now `workspace://…`" with the URI rewritten |
| `DATA_DIR/sessions/` and `DATA_DIR/workspaces/` both | boot stops naming both: merge by hand |

### 3. The move

`flows/store.from_settings` runs before anything reads the data directory. It
renames `sessions/` to `workspaces/` when the former is a directory (a symlink
to one is renamed as the link) and the latter is absent, logs the move once,
and lets an `OSError` stop the boot as an unreadable data directory already
does. The old-layout
check and the recording overlap check use `WORKSPACES_DIR`.

### 4. Documentation

AGENTS.md: every section explaining sessions, the design-record pointer moved
from `saga/` to `docs/superpowers/` with the saga named deprecated, and the
sentence "a session name is now the entire credential for driving that
browser" removed. README (within its 25 000-byte budget), CONTRIBUTING, the
skill, prompts, wiki (generated pages regenerated, hand-written ones and notes
rewritten), and one BREAKING changelog line.

### 5. Testing

The renames are proven by the existing suite and goldens: identifiers first
with goldens unchanged (behaviour identical), then each wire layer with its
goldens regenerated and read. New tests for each refusal in §2 and the move in
§3. Final audit: `git grep -n -i -w -E 'sessions?'` over the repo shows only
the kept meanings.

## Verify first

Nothing external; the inventory is the verification. The plan's final task runs
the audit grep and the full suites (Python, UI tests, `svelte-check`, eslint,
ruff, the wiki check).

## Next round

- The cluster repo's `components/mcp` sets `SESSION_*`: rename alongside the
  image bump (ruling 2 refuses the old names, so the deploy will say so).
- E2 onward, in the new vocabulary.
