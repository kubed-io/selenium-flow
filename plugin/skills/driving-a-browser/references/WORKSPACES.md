# Workspaces

A workspace is the named box that keeps your flows, files, site data, page
history and the settings you passed. A session is a live browser in it, started
by `open_session` and ended by `end_browser`, an admin or the Grid's reaper. The
workspace has a default session and may have sub-sessions (below). The server never invents a name: the caller names the workspace.

## Naming your workspace

| How | Looks like | When |
|---|---|---|
| URL parameter | `…/mcp?workspace=research-bot` | the usual case |
| Header | `Sf-Workspace: research-bot` | an operator pins one workspace to one credential |
| Claude.ai connector | static header `X-Workspace: research-bot` | the connector sends only allowlisted header names |

- Send one. A header and `?workspace=` together are refused, and `X-Workspace`
  counts as a header. When both headers are sent, `Sf-Workspace` wins.
- A name is letters, digits, dots, dashes and underscores, starting with a letter
  or digit, 64 characters at most. Case counts: `Desk` and `desk` are two
  workspaces. `stdio` and `global` are reserved.
- Over stdio the name is `stdio`, set for you.
- The same name always finds the same workspace, after a reconnect, a client
  restart or a week. A workspace expires after a day unused by default; the next
  call that names it starts a new one.
- A name is an address, not a credential: anyone who can call this server and
  knows it drives your browser. To share a browser, share the name. A connector's
  header is the same for every member of the organisation, so they all share one
  workspace and one browser.

## Sessions in a workspace

The default session is the one a call reaches when it names none; a caller
with no token subject (the static token, stdio) always does. A caller whose
token has a subject that does not own the default gets its own sub-session
instead, labelled by its username, made on its first call and opened with `open_session`. A
sub-session is another Grid browser in the same workspace, opened with
`open_session(session="tester")` and reached by passing the same `session` to
every tool call, and its state read at `session://tester/…`. A blank `session`
is none: your own.

| Fact | Holds |
|---|---|
| Name | letters, digits, dots, dashes and underscores like a workspace's; `default` is the workspace's own; `current`, `downloads`, `console`, `network`, `files` and `site-data` are reserved |
| Contents | its own cookies, storage, downloads, capture and window; it starts empty and nothing is copied from another session |
| Shared | the workspace's files, flows, config and history; each history entry names its session |
| Life | `session.sub_ttl` (default 3600 seconds) after its last use; then it is gone and a tool call naming it is `NoSession`; reading `session://{name}` says not found |
| Opening | only `open_session` opens a browser; a typo is refused and opens nothing |
| Callers | the server cannot tell callers on one connection apart: callers that name the same session are one caller and take turns; a main agent gives each subagent its own name |
| Pin | `?session=tester` on the MCP URL, or an `Sf-Session` header, fixes a connection to a session; a call naming another is refused |
| Reading | `session://{name}` is that session as `workspace://current` is yours; `/console`, `/network`, `/network/{id}`, `/site-data`, `/site-data/{site}` and `/downloads` are its own; a result's `see` and `uri` already point at the session the call acted in |
| Subagents | a subagent shares its main agent's connection, so `workspace://…` is the main agent's session; it reads its own at `session://<name>/…`, and in Claude Code only when its `tools:` lists `ReadMcpResourceTool` and `ToolSearch` |

A flow runs in one session: the one its `run_flow` names. A person's control
covers one session, so `await_control` takes the `session` a refused call
named (`references/CONTROL.md`).

## What `workspace://current` says

```json
{"workspace": "research-bot", "named_by": "query", "browser": "chrome",
 "url": "https://example.com/", "live": true, "in_frame": false,
 "window": "1400x900",
 "tabs": [{"tab": "t1", "url": "https://example.com/", "title": "Example", "current": true}]}
```

| Field | Means |
|---|---|
| `live` | a session is open |
| `in_frame` | a `frame` switch is in force |
| `tabs` | each tab with its `tab` id, `url`, `title` and whether it is `current`; reading it never follows a popup |
| `version` | the browser version that ran |
| `grid_timeout` | how long the session may sit idle before the Grid ends it; any call restarts the clock |
| `capture`, `capturing`, `captured` | opened with `capture=true`; recording now (false means paused until the next call); how many console entries and requests are kept |
| `flows_here` | the saved flows that apply to this page |
| `config`, `config_url` | the workspace's own settings, each with its `value` and `source` (`default` or `workspace`); the admin UI's page for them; changing them is in the `using-the-admin-ui` skill, row "A workspace's config" |

The server keeps a browser alive for 15 minutes after your last call by default,
so a pause of a few minutes costs nothing.

## Browser options that are not obvious

`open_session(browser="firefox")` switches browser and ends the one you hold. To
hold Chrome and Firefox at once, use two sessions, or two workspace names. Downloads go with an
ended browser, so `keep_file` one first (`references/FILES.md`).

`open_session`'s description lists its options and which ones it remembers.
These are the traps:

- `version="152"` matches any 152 build the Grid offers, `"152.0.7977.82"` only
  that one. The result's `version` is the one that ran. If no node offers it,
  the open fails after the Grid's own wait (`references/TROUBLESHOOTING.md`, *No Grid node
  took this browser*).
- Switching browser forgets a remembered version, and so does
  `version="latest"`. Pass a version in the same call as the switch to keep
  one. A version passed without a browser stays with the browser it was passed
  to.
- `record=true` on a headless browser is refused: pass `headless=false` with it.
- `insecure=true` is remembered for the workspace, and an insecure browser gets
  no saved site data. Use it only for a site with a self-signed certificate.
- For anything no option covers there is `capabilities`, the last resort
  (`references/TROUBLESHOOTING.md`, *Sending a capability no option covers*).

## A result says `reopened`

The Grid ends a browser idle too long, and a node can go away under one. The next
call opens a new browser with the same settings, navigates back to the last URL
it recorded, and restores saved site data
(the `signing-in` skill, row "Staying signed in across browsers"). Its result says so
once, in `reopened`: `cause` is `reaped` (idle past the keep-alive) or `lost`
(ended while you were working), and `message` says what did not survive. If that
call fails, its error says it instead.

What only the old page held is gone: scroll position, an open dropdown, an
unsubmitted form. Check the page and redo any unsaved step. Until your next call
after a reap, your session's context shows `live: false`.
