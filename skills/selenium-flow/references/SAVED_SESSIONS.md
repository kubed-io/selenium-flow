# Saved sessions — the server holds your browser

You are in this mode when `session://current` reports a non-null `key`.

```json
{"session_id": "a1b2...", "url": "https://example.com/", "live": true,
 "key": "named:desktop", "key_source": "named", "store": "memory"}
```

## What changes

**Omit `session_id` on every call.** The server resolves it from your key. You do
not need `open_session` at all — call `navigate`, `extract` or `screenshot` and a
browser is opened on first use.

```
navigate(url="https://example.com")     # opens one if you have none
extract(xpath="//h1")                   # same browser
close_session()                         # closes it, forgets the mapping
```

Passing `session_id` explicitly still works and always wins. Do that only when
you are deliberately driving a browser you got from somewhere else.

## What `key_source` tells you

| `key_source` | Means | Survives a reconnect |
|---|---|---|
| `named` | the client set `X-Session-Key` or `?session=<name>` | **yes** — same name, same browser |
| `transport` | the negotiated `Mcp-Session-Id` | no — a reconnect is a new browser |
| `stdio` | one process, one client | for the life of the process |

If `key_source` is `transport` and you are doing something long-running, be aware
a dropped connection starts a fresh browser. Nothing breaks; you just lose the
page you were on.

## The Grid can still take the browser away

Sessions are reaped after an idle timeout. You do not have to handle this: the
next call notices the browser is gone, opens a new one, and navigates back to the
last URL it recorded. It is invisible except in the server log.

What you *may* notice is lost in-page state — scroll position, an open dropdown,
an unsubmitted form, anything the URL does not capture. If a long pause is
coming before a step that depends on unsaved page state, do that step first.

## Sharing a browser on purpose

A named session is just a string. Two clients that set the same
`X-Session-Key` share one browser — useful for handing a logged-in session to a
second agent, and a footgun if two agents pick the same name by accident. Names
are per-deployment, so choose one that says who you are.

## Closing

`close_session()` with no arguments closes the browser and clears the mapping,
so the next call opens a fresh one. Call it when your task is done. If you want
to keep the login for a later run, just stop calling — the Grid will reap it on
its own schedule, and a named session will re-open at the same URL next time.
