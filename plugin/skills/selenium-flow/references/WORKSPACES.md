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

The default session is the one a call reaches when it names none. A
sub-session is another Grid browser in the same workspace, opened with
`open_session(session="tester")` and reached by passing the same `session` to
any tool or resource name.

| Fact | Holds |
|---|---|
| Name | letters, digits, dots, dashes and underscores like a workspace's; `default` is the workspace's own; `current`, `downloads`, `console`, `network`, `files` and `site-data` are reserved |
| Contents | its own cookies, storage, downloads, capture and window; it starts empty and nothing is copied from another session |
| Shared | the workspace's files, flows, config and history; each history entry names its session |
| Life | `session.sub_ttl` (default 3600 seconds) after its last use; then it is gone and a call naming it is `NoSession` |
| Opening | only `open_session` opens a browser; a typo is refused and opens nothing |
| Callers | callers that name the same session are one caller and take turns; a main agent gives each subagent its own name |
| Pin | `?session=tester` on the MCP URL, or an `Sf-Session` header, fixes a connection to a session; a call naming another is refused |
| Reading | `session://{name}` is that session as `workspace://current` is yours; `session://{name}/console`, `/network` and `/downloads` are its own |

A flow runs in one session: the one its `run_flow` names. A person's control
covers one session, so `await_control` takes the `session` a refused call
named.

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
| `config`, `config_url` | the workspace's own settings, each with its `value` and `source` (`default` or `workspace`); the admin UI's page for them |

The server keeps a browser alive for 15 minutes after your last call by default,
so a pause of a few minutes costs nothing.

## Managing a workspace

A workspace can have a config, `workspace.yaml` in its folder. A workspace with
one is kept: it never expires. `workspace://current/config` shows each setting,
its value and whether the file set it. Today it holds one setting:

| Setting | Means |
|---|---|
| `flows.global` | which shared flows this workspace is offered: rows of tags joined by commas; a flow carrying every tag of any row passes. Unset offers them all |

Only an admin changes the config. You never can: no tool writes it, and that is
the point, since it is the boundary a person set for you. When a person wants
it changed, write the new YAML for them and guide them, in this order:

1. **The admin UI.** Give them `config_url` from `workspace://current`, which
   opens this workspace's Config tab, and the YAML to paste into its editor:

   ```yaml
   flows:
     global:
     - shop,login
     - reports
   ```

2. **By hand, when there is no admin UI** (no `config_url`, as on stdio). The
   file is `<data dir>/workspaces/<workspace>/workspace.yaml` on the server, and
   the next call reads it. Give them the whole file:

   ```yaml
   flows:
     global:
     - shop,login
   ```

3. **With selenium-flow itself.** If the server's admin token is held as a
   secret (an operator names it, for example `admin` with the key `token`), you
   can drive the admin UI in your own browser: open the admin page, `write` the
   secret into the token field (`#token`), open `config_url`, and paste the
   YAML into the editor. Ask the person before you do, and show them this YAML
   first:

   ```yaml
   flows:
     global:
     - shop,login
   ```

A config that does not parse or validate refuses every call that names the
workspace, `workspace://current` included, with one sentence (over HTTP, a 409):

> this workspace's config is invalid: `colour` is not a setting workspace.yaml holds: it holds flows.global. An admin fixes it in the admin UI: https://flows.example.com/#/workspaces/research-bot/config

Nothing works until it is fixed. Tell the person the reason and the link, and
offer the corrected YAML.

## Browser options that are not obvious

`open_session`'s description lists its options and which ones it remembers.
These are the traps:

- `version="152"` matches any 152 build the Grid offers, `"152.0.7977.82"` only
  that one. The result's `version` is the one that ran. If no node offers it,
  the open fails after the Grid's own wait (TROUBLESHOOTING.md, *No Grid node
  took this browser*).
- Switching browser forgets a remembered version, and so does
  `version="latest"`. Pass a version in the same call as the switch to keep
  one. A version passed without a browser stays with the browser it was passed
  to.
- `record=true` on a headless browser is refused: pass `headless=false` with it.
- `insecure=true` is remembered for the workspace, and an insecure browser gets
  no saved site data. Use it only for a site with a self-signed certificate.
- For anything no option covers there is `capabilities`, the last resort
  (TROUBLESHOOTING.md, *Sending a capability no option covers*).

## A result says `reopened`

The Grid ends a browser idle too long, and a node can go away under one. The next
call opens a new browser with the same settings, navigates back to the last URL
it recorded, and restores saved site data (SITE_DATA.md). Its result says so
once, in `reopened`: `cause` is `reaped` (idle past the keep-alive) or `lost`
(ended while you were working), and `message` says what did not survive. If that
call fails, its error says it instead.

What only the old page held is gone: scroll position, an open dropdown, an
unsubmitted form. Check the page and redo any unsaved step. Until your next call
after a reap, `workspace://current` shows `live: false`.

## Ending a session

`end_browser()` quits the browser and keeps the workspace: its settings and its
page, so `open_session()` later returns there. The browser's downloads
(`session://downloads`) go with it, so `keep_file` what you need first.
Screenshots and prints are in the workspace's files from the moment they are
taken.

`open_session(record=true)` films the browser until it ends. The video appears
under `workspace://files/recordings` shortly after; `keep_file` it to keep it.
Ask for a recording only when a person will watch it.

## When a person takes control

A person can take your browser from the admin's live view, to sign in, solve a
captcha or fix something by hand. While they hold it, every call that would
change the browser is refused with one sentence (over HTTP, a 409):

> alex has control of this browser since 14:02 UTC. Call await_control (POST /browser/await-control), which returns when they hand back.

If they have only asked, it reads `alex asked for control of this browser at
14:02 UTC. …` and ends the same way. A person the server has no name for is "a
person". Do not retry the refused call or try another tool: every tool that acts
is refused the same way. Call `await_control()`.

While control lasts, every successful MCP result carries a `control` field,
`{"state", "who", "since", "hint"}`. A call that fails for another reason carries
the `hint` as text after its error.

| `state` | Means | Do |
|---|---|---|
| `requested` | a person asked while this call ran; the browser is theirs once it ends | call `await_control` next |
| `held` | a person has it now; this call was a read, which still answers | call `await_control` before anything that acts |
| `handed_back` | `control of this browser was handed back at 14:20 UTC`, since your last result; said once | call `await_control` for what they did, or look at the page first |

Reads still answer: `extract`, `outline`, `screenshot`, `print`,
`network_body`, every resource, the flow library and `keep_file`. A read that
would move the page or the window is refused: a `url` the browser is not on, or a
`screenshot` with `width`, `height` or `full_page`. `execute_script`, `assert`,
`open_session` and `end_browser` count as acting.

`await_control()` returns when they hand back, or at once with nobody in control
(`control: null`, or a hand-back you had not been told of). A client that runs
MCP background tasks waits as long as it takes. Any other gets an answer after
about 90 seconds with `handed_back: false` and `still in alex's control; call again.`
Call it again until it hands back.

What it returns that needs reading:

| Field | Holds |
|---|---|
| `handed_back` | false at the 90-second cap, with nobody in control, or when the server is stopping (`message` says to call again once it is back) |
| `visited` | every page they reached, oldest first: the last whole, earlier ones as origin, path and hash route; at most 100, with `dropped` counting the rest. Absent when their pages could not be followed |
| `ended` | the browser ended while they had it: call `open_session` |
| `page_error` | stands in for `page` and `outline` when the page could not be read |

Read the summary before you carry on. The person may have signed in, moved
further on or gone to another site.

A flow stops at a refused step, whatever its `onError` says, and `await_control`
is not a step: call it yourself, then decide from its summary whether to run the
flow again.
