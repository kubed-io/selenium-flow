# Workspaces — name yourself, and the box is yours

Every workspace here is **named by whoever calls**. The server never invents a
name, and there is no browser id in the contract at all: you say who you are,
and the workspace that belongs to that name is the one you work in.

**A workspace is the named, persistent box. A session is the live browser open
in it.** The workspace holds your flows, files, saved site data, page history
and settings, and it lasts. A session is the browser you are driving right now:
a workspace has at most one, `open_session` starts it, `end_browser` or the
Grid's reaper ends it, and an idle workspace simply has none. Ending a session
never costs you the workspace.

Name your workspace one of two ways, whichever your client can set:

| How | Looks like | Use it when |
|---|---|---|
| A URL parameter | `…/mcp?workspace=research-bot` | you configure the server by URL — the usual case |
| A header | `X-Workspace: research-bot` | an operator pins one workspace to one credential, or a client may only send approved headers, such as Claude.ai custom connectors |

**Sending both is an error**, not a contest one of them wins. Two names is two
ideas about who is calling, and quietly picking one hides that from whoever
wired it up. The old spellings, `?session=` and `X-Session-Key`, are refused
with a message that gives the new one.

Over **stdio** there is nothing to read, and one process serves one client, so
the name is `stdio` and you need do nothing.

`workspace://current` reports what you are holding:

```json
{"workspace": "research-bot", "named_by": "query", "browser": "chrome",
 "url": "https://example.com/", "live": true, "in_frame": false,
 "window": "1400x900"}
```

`live` says whether a session is open. `show(workspace://current)` draws it for
the person. `grid_timeout` is how long a session may sit idle before the Grid ends it; any call starts the clock again.
`capture` says whether the session was opened with `capture=true`, `capturing`
whether it is recording now (false means paused until the next call), and
`captured` how many console entries and requests are kept.

## Two rules, and they are absolute

**1. Call `open_session` first.** Nothing opens a browser implicitly. It is the
only place a session is started and the only place its settings — window size,
timeouts — can be chosen, so it is not done behind your back.

**2. There is no session id to pass.** No tool takes one and no result
carries one. The Grid's own id is how a browser is reached, not something you
hold, so there is nothing to keep and nothing to get wrong.

```
open_session(width=1400, height=900)     # once
navigate(url="https://example.com")      # about your workspace, because of your name
extract(selector={"xpath": "//h1"})
end_browser()                            # no arguments
```

If you need the browser someone *else* is driving, use the same workspace name.
Sharing is by name, deliberately, so there is exactly one way to do it — and
worth saying plainly: **a name is all that separates two callers.** The bearer
token is the only thing guarding it, so anyone who can call this server can
name your workspace and drive your browser.

## The same name always finds the same workspace

That is the whole point of choosing it yourself. A reconnect, a client restart,
a new process a week later — call with `?workspace=research-bot` again and you
are back where you were: the same settings, and when you call `open_session()`,
the page you left.

A workspace expires after a day unused. Nothing removes one before that, and the
name reappears the moment you call again, because the name comes from your own
URL or header rather than from anything stored.

## Choosing the browser

`open_session` takes a small set of named options. Each is a choice the server
already knows how to treat, so reach for them before anything else:

| Option | Does | Next `open_session()` |
|---|---|---|
| `browser` | `chrome` (default) or `firefox` | keeps it |
| `version` | the browser version to ask the Grid for, such as `"152"` | keeps it, unless you switch browser |
| `width`, `height` | the window | keeps them |
| `page_load_timeout`, `script_timeout` | bounds on a hanging page or script | keeps them |
| `insecure` | `true` accepts a self-signed certificate; that browser gets no saved site data | keeps it |
| `record` | films the browser | **asks again** |
| `capture` | keeps the console and network | **asks again** |

**A version is a request, not a promise.** `version="152"` matches any 152
build the Grid offers, `"152.0.7977.82"` only that one. The result's `version`
is the one that actually ran, and `workspace://current` says it too: read it
rather than assume. The Grid cannot fetch a version it does not have, so if no
node offers it the open fails after the Grid's own wait
(`skill://selenium-flow/references/TROUBLESHOOTING.md`, *No Grid node took
this browser*).

`version="latest"` forgets a remembered version, and so does switching browser:
Chrome's 152 means nothing to Firefox. Name a version in the same call as the
switch to keep one.

For anything no option covers there is `capabilities`, the last resort; it is
described in `skill://selenium-flow/references/TROUBLESHOOTING.md`, *Sending a
capability no option covers*.

## The Grid can still take the session away

Browsers are reaped after an idle timeout. You do not have to handle it: the
next call notices the session is gone, opens a new one **with the same
settings**, and navigates back to the last URL it recorded. It is invisible
except in the server log.

A refresh restores what you saved with `save_site_data` too — cookies and every
site's storage, so you are still signed in — and the first successful result
after the reopen says so in `site_data`
(`skill://selenium-flow/references/SITE_DATA.md`). What it cannot
restore is in-page state the URL does not capture — scroll position, an open
dropdown, an unsubmitted form — and anything you never saved. If a long pause is
coming before a step that depends on unsaved state, do that step first.

## Ending a session

`end_browser()` with no arguments quits the browser. Call it when you are done,
including after a failure — an abandoned session holds one of the Grid's few
slots until it times out.

**It does not end your workspace.** The workspace keeps the browser choice and
the page you were on, so `open_session()` with no arguments later comes back on
the same browser at the same page.

What *is* gone is the session's downloads. The Grid keeps that store per
browser and deletes it with the browser, so `keep_file` anything you still
need first. Screenshots and prints are never at risk this way — they land in
your workspace's own files, `workspace://files/screenshots` and `workspace://files`,
from the moment they are taken.

## Recording a browser

`open_session(record=true)` films the browser from that call until it ends. It is
never inherited: reopening without `record` does not record. The video shows up
under `workspace://files/recordings` shortly after the browser ends (or is
reaped); `keep_file` it to keep it. It costs the Grid, so ask for it only when a
person will watch.

## When a person takes control

A person can take control of your workspace's browser from the admin page. While
one has, every tool result for your workspace ends with a notice that begins
with who has control (a name, or "a person") and `took control of this browser`: someone is at the keyboard of
this browser and may be typing in it right now. Stop, and ask the user before
your next action, because a click or a keystroke of yours lands on top of theirs.

When the last of them leaves, the next result says `control of this browser was
handed back`, once. The page may have changed under you, so look at it again,
with `extract` or `outline`, before carrying on from what you remember.

## Your library is your name too

The flow library and Files are the directory your workspace name
points at. A caller that names no workspace gets the shared `global` library,
which everyone may read and run and nobody may write — so naming yourself is
also how you get somewhere to save a flow.
