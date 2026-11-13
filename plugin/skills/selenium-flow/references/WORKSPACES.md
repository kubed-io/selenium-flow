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
| A header | `Sf-Workspace: research-bot` | an operator pins one workspace to one credential |

**Sending both is an error**, not a contest one of them wins. Two names is two
ideas about who is calling, and quietly picking one hides that from whoever
wired it up.

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
are back where you were: the settings you passed are remembered (the rest are read afresh), and when you call `open_session()`,
the page you left.

A workspace expires after a day unused. Nothing removes one before that, and the
name reappears the moment you call again, because the name comes from your own
URL or header rather than from anything stored.

## Choosing the browser

`open_session` takes a small set of named options. Each is a choice the server
already knows how to treat, so reach for them before anything else:

| Option | Does | Next `open_session()` |
|---|---|---|
| `browser` | `chrome` (default) or `firefox` | keeps it if you passed it |
| `version` | the browser version to ask the Grid for, such as `"152"` | keeps it if you passed it, unless you switch browser |
| `width`, `height` | the window | keeps them if you passed them |
| `page_load_timeout`, `script_timeout` | bounds on a hanging page or script | keeps them if you passed them |
| `insecure` | `true` accepts a self-signed certificate; that browser gets no saved site data | keeps it if you passed it |
| `headless` | draws nothing on the node's screen, which is quicker; no recording, no live view | keeps it if you passed it |
| `record` | films the browser | **asks again** |
| `capture` | keeps the console and network | **asks again** |

Choose `headless` for speed, or when nobody will watch; leave it off for real
sites, whose bot checks spot it more easily. `record=true` on a headless browser
is refused: pass `headless=false` to turn it off and record.

What you leave out comes from the server's and your connection's defaults at
each open, so a change there reaches you at your next `open_session()`. A version
passed without a browser keeps the browser it ran on.

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

**It does not end your workspace.** The workspace keeps the settings you passed
and the page you were on, so `open_session()` with no arguments later returns to
that page; anything you did not pass comes from the current defaults.

What *is* gone is the session's downloads, `session://downloads`. The Grid keeps that store per
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

A person can take control of your workspace's browser from the admin's live
view — to sign in, solve a captcha, fix something by hand. While they hold it the
browser is theirs: **every call that would change it is refused**, and you wait
for them to hand it back.

**A refusal is one sentence** (over HTTP, a 409):

> kelly has control of this browser since 14:02 UTC. Call await_control (POST /browser/await-control), which returns when they hand back.

If they have only asked so far, it reads `kelly asked for control of this
browser at 14:02 UTC. …` and ends the same way. A person the server has no name
for is "a person". Do not retry the refused call, and do not reach for another
tool to get round it — every tool that acts is refused the same way. Call
`await_control()`.

**Every successful MCP result says it too**, while it lasts: a `control` field,
`{"state", "who", "since", "hint"}`, with the `hint` as a line of text. A call
that fails for another reason carries the `hint` as text after its error.

| `state` | Means | Do |
|---|---|---|
| `requested` | a person asked while this call ran; the browser is theirs once it ends | call `await_control` next |
| `held` | a person has it now; this call was a read, which still answers | call `await_control` before anything that acts |
| `handed_back` | `control of this browser was handed back at 14:20 UTC`, since your last result; said once | call `await_control` for what they did, or look at the page before carrying on |

**Reads still answer**: `extract`, `outline`, `screenshot`, `print`,
`network_body`, every resource, the flow library and `keep_file`. A read is
refused when it would move the page or the window — a `url` the browser is not
on, because that navigates, and a `screenshot` with `width`, `height` or
`full_page`, because that resizes. `execute_script` and `assert` run your
JavaScript, so they count as acting; so do `open_session` and `end_browser`.

**`await_control()` takes no arguments and returns when they hand back.** A
client that runs MCP background tasks gets one, which waits as long as it takes.
Any other gets a blocking call that answers after about 90 seconds with
`handed_back: false` and `still in kelly's control; call again.` — so call it
again, until it hands back. With nobody in control it answers at once:
`control: null`, or the hand-back you had not been told of yet.

What it hands back is what they did:

| Field | Holds |
|---|---|
| `handed_back` | true when they handed back; false at the cap, with nobody in control, or when the server is stopping (`message` says to call again once it is back; a stop more often just drops the call) |
| `control` | who had it, `since`, `until`, and `held_for` in seconds |
| `page` | the URL and title the browser is on now |
| `visited` | every page they reached, oldest first: the last whole, earlier ones origin and path; at most 100, with `dropped` counting what went before. Absent when their pages could not be followed, and `message` says so |
| `outline` | the current page's elements, as `outline` lists them |
| `ended` | true when the browser ended while they had it: there is no page, call `open_session` |
| `message` | the whole of it in one sentence |

`page_error` stands in for `page` and `outline` when the page could not be read.
**Read the summary before you carry on**: you are rarely where you left off —
signed in now, a step further on, or on another site.

**A flow stops at a refused step**, whatever its `onError` says. A flow cannot
wait for a person and `await_control` is not a step: call it yourself, then
decide from its summary whether to run the flow again.

## Your library is your name too

The flow library and Files are the directory your workspace name
points at. A caller that names no workspace gets the shared `global` library,
which everyone may read and run and nobody may write — so naming yourself is
also how you get somewhere to save a flow.
