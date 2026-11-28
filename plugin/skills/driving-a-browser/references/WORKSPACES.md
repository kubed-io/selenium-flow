# Workspaces

A workspace is the named box that keeps your flows, files, site data, page
history and the settings you passed. A session is a live browser in it, started
by `open_session` and ended by `end_browser`, an admin or the Grid's reaper. A
workspace holds a list of sessions: yours is named after you, and the first is
its main one (below). The server never invents a name: the caller names the
workspace.

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
- A name is an address, not a credential: anyone with your subject who knows it
  drives your session. A caller with another subject gets a 403 on it and can
  open its own session in the workspace. A connector's header is the same for
  every member of the organisation, so they share the workspace, and a browser
  only when they share a subject.

## Sessions in a workspace

A call that names no session acts in your own, named after you.
`open_session` opens it, or joins it when its browser is live. To work in
another session you opened, pass `session`: `open_session(session="tester")`,
then the same `session` on every call, its state at `session://tester/…`. A
blank `session` is none. The first session in the list is main, and that
changes nothing about what it can do. An idle session you didn't open, such as
an old `default`, isn't yours: open your own, and it becomes main. A name
another caller holds is refused with "that session is held by another caller"
(403), so pick another name. Callers with the same subject share a session and
take turns. On a live session, `fresh=true` replaces the browser for a clean
start, with saved site data back, and `restore_site_data=false` with it starts
as a new user.

| Fact | Holds |
|---|---|
| Name | letters, digits, dots, dashes and underscores like a workspace's; your own is your username, else your token's subject, `admin` for the server token, and `default` when nothing names you; `current`, `downloads`, `console`, `network`, `files` and `site-data` are reserved |
| Contents | its own cookies, storage, downloads, capture and window; it starts empty and nothing is copied from another session |
| Shared | the workspace's files, flows, config and history; each history entry names its session |
| Life | the first in the list lasts as the workspace does, `workspace.ttl` after its last use (for ever in a kept workspace); any other `session.sub_ttl` (default 3600 seconds) after its last use; then it is gone and a tool call naming it is `NoSession`; reading `session://{name}` says not found |
| Opening | only `open_session` opens a browser; a typo is refused and opens nothing |
| Callers | the server cannot tell callers on one connection apart: callers with the same subject share a session and take turns; a subagent shares its main agent's session, or names its own |
| Pin | `?session=tester` on the MCP URL, or an `Sf-Session` header, fixes a connection to a session; a call naming another is refused |
| Reading | `session://{name}` is that session as `workspace://current` is yours; `/console`, `/network`, `/network/{id}`, `/site-data`, `/site-data/{site}` and `/downloads` are its own; a result's `see` and `uri` already point at the session the call acted in |
| Subagents | a subagent shares its main agent's connection, so `workspace://…` is the main agent's session; it reads its own at `session://<name>/…`, and in Claude Code only when its `tools:` lists `ReadMcpResourceTool` and `ToolSearch`; one that shares its main agent's session never calls `end_browser` on it |

A flow runs in one session: the one its `run_flow` names. A person's control
covers one session, so `await_control` takes the `session` a refused call
named (`references/CONTROL.md`).

## What `workspace://current` says

```json
{"workspace": "research-bot", "named_by": "query", "session": "research-bot-agent",
 "main": true, "browser": "chrome",
 "url": "https://example.com/", "live": true, "in_frame": false,
 "window": "1400x900",
 "tabs": [{"tab": "t1", "url": "https://example.com/", "title": "Example", "current": true}],
 "sessions": [{"name": "research-bot-agent", "main": true, "live": true,
               "last_used": 1764240000, "url": "https://example.com/",
               "uri": "session://research-bot-agent", "yours": true}]}
```

| Field | Means |
|---|---|
| `session`, `main` | your own session's name, and whether it heads the workspace's list |
| `sessions` | every session in list order, each `{name, main, live, last_used, url, uri, yours}`; `yours` means your subject holds it, so you may use it, and another session's `url` is its origin, path and hash route |
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

## A result says `main_moved`

Another session opened while yours headed the list idle, and took the head. Your
next result says so once, as `main_moved: {from, to, at}` beside `main: false`.
Nothing else changed: carry on in your own session.

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
