# Stateless — you own the session

You are in this mode when `session://current` reports `"key": null`, or when any
call fails with *"session_id is required: this request carries no stable session
to key on"*.

It is also the **only** mode on the `/browser/*` HTTP endpoints, whatever the MCP
side is doing. Those endpoints take a session in and give one back, always.

## The contract

1. `open_session` → keep the `session_id` it returns.
2. Pass that `session_id` to **every** later call.
3. `close_session(session_id=...)` when done, including after a failure.

```
open_session(url="https://example.com", width=1280, height=800)
  -> {"session_id": "a1b2c3...", "url": "...", "title": "...", ...}

extract(session_id="a1b2c3...", xpath="//h1")
click(session_id="a1b2c3...", xpath="//button[contains(., 'Next')]")
close_session(session_id="a1b2c3...")
```

Losing the id strands a browser until the Grid reaps it. Treat it as the one
piece of state you must not drop.

## Why you might be here

- A client that cannot set a header or a URL parameter, so the server has nothing
  stable to key on.
- The HTTP endpoints, by design.
- `SAVED_SESSIONS=false` on the server.

If you control the client config and want the easier mode, add
`?session=<name>` to the MCP URL or an `X-Session-Key` header, and read
`references/SAVED_SESSIONS.md` instead.

## Passing a session between steps

This is the point of the stateless shape: the session id is portable. An n8n
workflow opens a browser in one node and threads `session_id` through the rest:

```
Open      POST /browser/open      {"width":1280,"height":800}   -> session_id
Navigate  POST /browser/navigate  {"session_id":"...", "url":"..."}
Write     POST /browser/write     {"session_id":"...", "xpath":"...", "text":"..."}
Close     POST /browser/close     {"session_id":"..."}
```

Wire the close step so it runs on **both** the success and failure paths.

## Recovering a dead session

Nothing refreshes it for you here — that only happens when the server holds the
mapping. If a call returns an invalid-session error, the id is dead:

1. `open_session` for a new one.
2. Navigate back to where you were.
3. Carry on with the new id.

Do not retry the old id; it will not come back.
