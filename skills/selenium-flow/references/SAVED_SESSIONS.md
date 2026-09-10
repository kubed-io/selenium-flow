# Saved sessions — the server holds your browser

You are in this mode when `session://current` reports `"mode": "saved"`.

```json
{"mode": "saved", "session_id": "a1b2...", "url": "https://example.com/",
 "live": true, "in_frame": false, "key": "named:desktop",
 "key_source": "named", "pass_session_id": false}
```

## Two rules, and they are absolute

**1. Call `open_session` first.** Nothing opens a browser implicitly. It is the
only place a browser is created and the only place its settings — window size,
timeouts — can be chosen, so it is not done behind your back.

**2. Never pass `session_id`.** The server knows which browser is yours. The
tools do not even advertise the argument in this mode, and passing one anyway is
an error: an id from somewhere else is either a mistake or a browser you do not
own.

```
open_session(width=1400, height=900)     # once
navigate(url="https://example.com")      # no session_id, ever
extract(xpath="//h1")
end_browser()                            # no arguments
```

If you need a browser someone *else* opened, do not pass its id — have both
callers use the same session name instead. Sharing is by name, deliberately, so
there is exactly one way to do it.

## What `key_source` tells you

| `key_source` | Means | Survives a reconnect |
|---|---|---|
| `named` | the client set `X-Session-Key` or `?session=` | **yes** — same name, same browser |
| `transport` | the negotiated `Mcp-Session-Id` | no — a reconnect is a new browser |
| `stdio` | one process, one client | for the life of the process |

If `key_source` is `transport` and the task is long, a dropped connection means
your next `open_session` gets a fresh browser. Nothing breaks; you just lose the
page you were on.

## The Grid can still take the browser away

Sessions are reaped after an idle timeout. You do not have to handle it: the
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
same browser at the same page. Nothing you do removes a session: it expires
after a day unused, and a named one reappears the moment you call again, because
the name comes from your own URL or header rather than from anything stored.

What *is* gone is the browser's files. The Grid keeps a file store per browser
and deletes it with the browser, so fetch anything you still need first.
