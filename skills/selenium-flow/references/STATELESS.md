# Stateless — you own the session

You are in this mode when `session://current` reports `"mode": "stateless"`, or
when a call fails with *"session_id is required"*.

It is also the **only** mode on the `/browser/*` HTTP endpoints, whatever the
MCP side is doing. Those endpoints take a session in and give one back, always.

## The contract

1. `open_session` → keep the `session_id` it returns.
2. Pass that `session_id` to **every** later call. The tools advertise it as
   required here, so this is visible in the schema rather than something you
   discover by failing.
3. `end_browser(session_id=...)` when done, including after a failure.

```
open_session(url="https://example.com", width=1280, height=800)
  -> {"session_id": "a1b2c3...", "settings": {"width": 1280, "height": 800}}

extract(session_id="a1b2c3...", xpath="//h1")
interact(session_id="a1b2c3...", action="click", xpath="//button")
end_browser(session_id="a1b2c3...")
```

Losing the id strands a browser until the Grid reaps it. Treat it as the one
piece of state you must not drop.

`end_browser` ends the browser, not the flow session — the same as in saved
mode. Here the distinction rarely shows, because without a key there is nothing
to look the session up by afterwards: the id you were holding is what made it
yours.

## Why you might be here

- A client that cannot set a header or a URL parameter, so the server has
  nothing stable to key on.
- The HTTP endpoints, by design.
- `SAVED_SESSIONS=false` on the server.

If you control the client config and want the easier mode, add
`?session=<name>` to the MCP URL or an `X-Session-Key` header, then read
`references/SAVED_SESSIONS.md` instead.

## Passing a session between steps

This is the point of the stateless shape: the id is portable. An n8n workflow
opens a browser in one node and threads `session_id` through the rest.

```
Open      POST /browser/open      {"width":1280,"height":800}   -> session_id
Navigate  POST /browser/navigate  {"session_id":"...", "url":"..."}
Write     POST /browser/write     {"session_id":"...", "xpath":"...", "text":"..."}
End       POST /browser/end       {"session_id":"..."}
```

Wire the end step so it runs on **both** the success and failure paths.

`POST /browser/close` is the old name for the same thing and still works, so an
existing workflow does not need editing.

## Recovering a dead session

Nothing refreshes it for you here — that only happens when the server holds the
mapping. If a call returns an invalid-session error, that id is dead:

1. `open_session` for a new one.
2. Navigate back to where you were.
3. Carry on with the new id.

Do not retry the old id; it will not come back.
