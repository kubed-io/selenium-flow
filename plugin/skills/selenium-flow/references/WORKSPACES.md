# Workspaces

The caller names its workspace; the server never invents a name. A workspace
is the named, persistent box: it holds your flows, files, saved site data, page
history and settings, and it lasts. A session is the live browser open in it. A
workspace has at most one, `open_session` starts it, `end_browser` or the
Grid's reaper ends it, and an idle workspace has none.

Name your workspace one of these ways, whichever your client can set:

| How | Looks like | Use it when |
|---|---|---|
| A URL parameter | `…/mcp?workspace=research-bot` | you configure the server by URL — the usual case |
| A header | `Sf-Workspace: research-bot` | an operator pins one workspace to one credential |
| A Claude.ai connector | a static request header `X-Workspace: research-bot` | the connector can only send allowlisted header names. `X-Workspace` is an alias of `Sf-Workspace`, and `Sf-Workspace` wins when both are sent. A connector's header is the same for every member of the organisation, so they all share one workspace and one browser. |

A header and `?workspace=` together is refused (`X-Workspace` counts as a
header), so send one. A name is letters, digits, dots, dashes and underscores,
starting with a letter or digit, 64 characters at most. Case counts: `Desk` and
`desk` are two workspaces. `stdio` and `global` are reserved. Over **stdio** the
name is `stdio` and you need do nothing.

`workspace://current` reports what you are holding:

```json
{"workspace": "research-bot", "named_by": "query", "browser": "chrome",
 "url": "https://example.com/", "live": true, "in_frame": false,
 "window": "1400x900"}
```

`live` says whether a session is open. `show(workspace://current)` draws it for
the person. `grid_timeout` is how long a session may sit idle before the Grid
ends it; any call starts the clock again. The server keeps a browser alive for a
while after your last call (15 minutes by default), so a pause of a few minutes
costs nothing. `capture` says whether the session was opened with
`capture=true`, `capturing` whether it is recording now (false means paused
until the next call), and `captured` how many console entries and requests are
kept.

```
open_session(width=1400, height=900)     # once
navigate(url="https://example.com")      # acts on your workspace's browser
extract(selector={"xpath": "//h1"})
end_browser()                            # no arguments
```

To use the browser someone else is driving, use the same workspace name. A name
is all that separates two callers: anyone who can call this server can name
your workspace and drive your browser.

## The same name finds the same workspace

After a reconnect, a client restart or a new process a week later, the same name
leads back to the same workspace: the settings you passed are remembered (the
rest are read afresh), and `open_session()` returns to the page you left. A
workspace expires after a day unused by default; the next call that names it
starts a new one.

## Choosing the browser

`open_session` takes a small set of named options. Each is a choice the server
already knows how to treat, so reach for them before anything else:

| Option | Does | Next `open_session()` |
|---|---|---|
| `browser` | `chrome` (default) or `firefox` | keeps it if you passed it |
| `version` | the browser version to ask the Grid for, such as `"152"` | keeps it if you passed it, unless you switch browser |
| `width`, `height` | the window | keeps them if you passed them |
| `page_load_timeout`, `script_timeout` | bounds on a hanging page or script | keeps them if you passed them |
| `insecure` | `true` accepts a self-signed certificate; that browser gets no saved site data and does not delete what was saved | keeps it if you passed it |
| `headless` | draws nothing on the node's screen, which is quicker; no recording, no live view | keeps it if you passed it |
| `record` | films the browser | **asks again** |
| `capture` | keeps the console and network | **asks again** |

Choose `headless` for speed, or when nobody will watch; leave it off for real
sites, whose bot checks spot it more easily. `record=true` on a headless browser
is refused: pass `headless=false` to turn it off and record.

What you leave out comes from the server's and your connection's defaults at
each open, so a change there reaches you at your next `open_session()`.

`version="152"` matches any 152 build the Grid offers, `"152.0.7977.82"` only
that one. The result's `version` is the one that actually ran, and
`workspace://current` says it too. If no node offers the version the open fails
after the Grid's own wait
(`skill://selenium-flow/references/TROUBLESHOOTING.md`, *No Grid node took
this browser*).

`version="latest"` forgets a remembered version, and so does switching browser.
Name a version in the same call as the switch to keep one. A version passed
without a browser stays with the browser it was passed to.

For anything no option covers there is `capabilities`, the last resort; it is
described in `skill://selenium-flow/references/TROUBLESHOOTING.md`, *Sending a
capability no option covers*.

## The Grid can still take the session away

The Grid ends a browser that sits idle too long, and a Grid node can go away
under one. The next call notices, opens a new browser with the same settings,
and navigates back to the last URL it recorded. Its result says so once, in `reopened`: `cause` is `reaped` (idle past
the keep-alive window) or `lost` (ended while you were working), and `message`
says what did not survive. If that call fails, its error says it instead. Check
the page before you go on.

The new browser also gets back what you saved with `save_site_data`, so you are
still signed in, and the result says so in `site_data`
(`skill://selenium-flow/references/SITE_DATA.md`). In-page state the URL does
not capture is gone: scroll position, an open dropdown, an unsubmitted form.

## Ending a session

`end_browser()` quits the browser and does not end your workspace. The workspace
keeps the settings you passed and the page you were on, so `open_session()` with
no arguments later returns to that page; anything you did not pass comes from
the current defaults.

The session's downloads, `session://downloads`, go with the browser, so
`keep_file` anything you still need first. Screenshots and prints land in your
workspace's own files, `workspace://files/screenshots` and `workspace://files`,
from the moment they are taken.

## Recording a browser

`open_session(record=true)` films the browser from that call until it ends;
reopening without `record` does not record. The video shows up under
`workspace://files/recordings` shortly after the browser ends or is reaped;
`keep_file` it to keep it. Ask for it only when a person will watch.

## When a person takes control

A person can take control of your workspace's browser from the admin's live
view, to sign in, solve a captcha or fix something by hand. While they hold it,
every call that would change the browser is refused, and you wait for them to
hand it back.

A refusal is one sentence (over HTTP, a 409):

> kelly has control of this browser since 14:02 UTC. Call await_control (POST /browser/await-control), which returns when they hand back.

If they have only asked so far, it reads `kelly asked for control of this
browser at 14:02 UTC. …` and ends the same way. A person the server has no name
for is "a person". Do not retry the refused call, and do not reach for another
tool to get round it — every tool that acts is refused the same way. Call
`await_control()`.

Every successful MCP result says it too, while it lasts: a `control` field,
`{"state", "who", "since", "hint"}`, with the `hint` as a line of text. A call
that fails for another reason carries the `hint` as text after its error.

| `state` | Means | Do |
|---|---|---|
| `requested` | a person asked while this call ran; the browser is theirs once it ends | call `await_control` next |
| `held` | a person has it now; this call was a read, which still answers | call `await_control` before anything that acts |
| `handed_back` | `control of this browser was handed back at 14:20 UTC`, since your last result; said once | call `await_control` for what they did, or look at the page before carrying on |

Reads still answer: `extract`, `outline`, `screenshot`, `print`,
`network_body`, every resource, the flow library and `keep_file`. A read is
refused when it would move the page or the window — a `url` the browser is not
on, because that navigates, and a `screenshot` with `width`, `height` or
`full_page`, because that resizes. `execute_script` and `assert` run your
JavaScript, so they count as acting; so do `open_session` and `end_browser`.

`await_control()` takes no arguments and returns when they hand back. A
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
| `visited` | every page they reached, oldest first: the last whole, earlier ones origin, path and hash route; at most 100, with `dropped` counting what went before. Absent when their pages could not be followed, and `message` says so |
| `outline` | the current page's elements, as `outline` lists them |
| `ended` | true when the browser ended while they had it: there is no page, call `open_session` |
| `message` | the whole of it in one sentence |

`page_error` stands in for `page` and `outline` when the page could not be read.
Read the summary before you carry on: you are rarely where you left off. The
person may have signed in, moved a step further on, or gone to another site.

A flow stops at a refused step, whatever its `onError` says, and
`await_control` is not a step: call it yourself, then decide from its summary
whether to run the flow again.

## Your library is your name too

Your flow library and Files belong to your workspace name. A caller that names
no workspace gets the shared `global` library, which everyone may read and run
and nobody may write, so you need a name before you can save a flow
(`skill://selenium-flow/references/FLOWS.md`).
