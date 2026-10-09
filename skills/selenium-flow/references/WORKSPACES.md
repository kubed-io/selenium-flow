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
the person.

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

## Your library is your name too

The flow library and Files are the directory your workspace name
points at. A caller that names no workspace gets the shared `global` library,
which everyone may read and run and nobody may write — so naming yourself is
also how you get somewhere to save a flow.
