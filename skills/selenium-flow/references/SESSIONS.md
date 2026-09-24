# Sessions — name yourself, and the browser is yours

Every session here is **named by whoever calls**. The server never invents a
name, and there is no browser id in the contract at all: you say who you are,
and the browser that belongs to that name is the one you drive.

Name your session one of two ways, whichever your client can set:

| How | Looks like | Use it when |
|---|---|---|
| A URL parameter | `…/mcp?session=research-bot` | you configure the server by URL — the usual case |
| A header | `X-Session-Key: research-bot` | an operator pins one session to one credential |

**Sending both is an error**, not a contest one of them wins. Two names is two
ideas about who is calling, and quietly picking one hides that from whoever
wired it up.

Over **stdio** there is nothing to read, and one process serves one client, so
the name is `stdio` and you need do nothing.

`session://current` reports what you are holding:

```json
{"session": "research-bot", "named_by": "query", "browser": "chrome",
 "url": "https://example.com/", "live": true, "in_frame": false,
 "window": "1400x900"}
```

## Two rules, and they are absolute

**1. Call `open_session` first.** Nothing opens a browser implicitly. It is the
only place a browser is created and the only place its settings — window size,
timeouts — can be chosen, so it is not done behind your back.

**2. There is no `session_id` to pass.** No tool takes one and no result
carries one. The Grid's own id is how a browser is reached, not something you
hold, so there is nothing to keep and nothing to get wrong.

```
open_session(width=1400, height=900)     # once
navigate(url="https://example.com")      # about your session, because of your name
extract(selector={"xpath": "//h1"})
end_browser()                            # no arguments
```

If you need the browser someone *else* is driving, use the same session name.
Sharing is by name, deliberately, so there is exactly one way to do it — and
worth saying plainly: **a name is all that separates two callers.** The bearer
token is the only thing guarding it, so anyone who can call this server can
name your session and drive your browser.

## The same name always finds the same session

That is the whole point of choosing it yourself. A reconnect, a client restart,
a new process a week later — call with `?session=research-bot` again and you
are back where you were, on the browser you had, at the page you left.

A session expires after a day unused. Nothing removes one before that, and the
name reappears the moment you call again, because the name comes from your own
URL or header rather than from anything stored.

## The Grid can still take the browser away

Browsers are reaped after an idle timeout. You do not have to handle it: the
next call notices the browser is gone, reopens one **with the same settings**,
and navigates back to the last URL it recorded. It is invisible except in the
server log.

What a refresh cannot restore is in-page state the URL does not capture —
scroll position, an open dropdown, an unsubmitted form. If a long pause is
coming before a step that depends on unsaved state, do that step first.

## Ending a browser

`end_browser()` with no arguments quits the browser. Call it when you are done,
including after a failure — an abandoned browser holds one of the Grid's few
slots until it times out.

**It does not end your session.** The session keeps the browser choice and the
page you were on, so `open_session()` with no arguments later comes back on the
same browser at the same page.

What *is* gone is the browser's downloads. The Grid keeps that store per
browser and deletes it with the browser, so `keep_file` anything you still
need first. Screenshots and prints are never at risk this way — they land in
your session's own files, `session://files/screenshots` and `session://files`,
from the moment they are taken.

## Your library is your name too

The flow library and Files are the directory your session name
points at. A caller that names no session gets the shared `global` library,
which everyone may read and run and nobody may write — so naming yourself is
also how you get somewhere to save a flow.
