# Flows — save a sequence once, run it in one call

A flow is a list of tool calls saved under a name. `run_flow` replays the whole
list server-side, in order, in the browser you already have.

The saving is not speed on the wire. It is **you**: a twelve-step form is one
call and one decision instead of twelve of each, and it replays a sequence
somebody already got right instead of re-deriving the selectors every time.

## When not to save one

A sequence you will run once is cheaper as the tool calls themselves. Save a
flow when you, or another agent, will run it again — a login, a multi-page form,
a check you repeat on Chrome and then on Firefox.

## The loop

1. **Discover.** Drive it once by hand. Use `extract` to find each selector and
   confirm it matches exactly one element (`references/READING_PAGES.md`).
2. **Build.** Write each call down as a step. Anything that changes between runs
   becomes a parameter.
3. **Save.** `save_flow`, once. It validates every step against the live tool
   schemas, so a step that could not run is refused now rather than halfway
   through a form.
4. **Run.** `run_flow` forever after.

Read `flow://schema` (or call `flow_schema`) before writing one. It is derived
from the tools themselves, so it cannot describe a step that would not run.

## A step is a tool call

```json
{"tool": "write", "params": {"css": "#email", "text": "a@example.com"}}
```

`params` is **exactly** the arguments you would pass the tool directly. Nothing
is renamed and nothing is added. A step may also carry:

| Key | Does |
|---|---|
| `id` | a name for the step, shown in the report |
| `note` | a comment for the next reader |
| `onError` | `abort` (default) or `continue` past a failure |
| `return` | include this step's full result in the report |

To bound one slow step, set `wait_timeout` in its `params` — the same argument
the tool takes directly.

**Not steps:** `open_session` and `end_browser`. A flow runs in the browser the
caller already holds, which is what lets the same flow run on Firefox unedited.
Never put `session_id` in a step either; the run supplies it.

## Parameters: what varies between runs

Declare them as JSON Schema, and point a step at one with `value_from`. These
four fields are `save_flow`'s arguments:

```json
{
  "name": "sign-up",
  "description": "Create an account on the demo site",
  "parameters": {
    "type": "object",
    "required": ["email"],
    "properties": {"email": {"type": "string"}}
  },
  "steps": [
    {"tool": "navigate", "params": {"url": "https://demo.example.com/join"}},
    {"tool": "write", "params": {"css": "#email", "value_from": {"param": "email"}}},
    {"tool": "interact", "id": "submit",
     "params": {"action": "click", "css": "button[type=submit]"}},
    {"tool": "extract", "params": {"css": "h1"}, "return": true}
  ]
}
```

Then, every time after:

```
run_flow(name="sign-up", params={"email": "a@example.com"})
```

There is **no templating** — nothing like `{{email}}` is ever substituted into a
string. A value arrives through `value_from` or not at all, and `value_from`
names exactly one source. Only `write` takes one today.

Mark a parameter `"writeOnly": true` when the caller supplies it but it must not
come back out — it is typed as usual and hidden from the report. For a password,
do not use a parameter at all: bind a secret (`references/SECRETS.md`).

## Whose flows you see

This depends on your session name, and it is not guessable from any schema.

| You are | You save into | You can run |
|---|---|---|
| **named** (`?session=` / `X-Session-Key`) | your own library | yours, plus the shared library |
| **unnamed** | the shared `global` library | the shared library |

**`global` is shared with every session.** Every caller can read and run what is
in it, and every unnamed caller can overwrite or delete it — `save_flow` and
`delete_flow` act on the library you save into, and for an unnamed caller that
*is* `global`. Name your session before saving anything you would not want
another agent to replace.

Where a name exists in both, yours wins, and `list_flows` marks each entry
`shared: true` or `false` so you can tell which one will run. A named session
never changes the shared library; only an operator promotes a flow into it.

## Running one

Call `open_session` first — a flow never opens a browser. To run the same flow
on Firefox, `open_session(browser="firefox")` and run it again, unchanged.

The report has one line per step plus where the browser ended up:

```json
{"flow": "sign-up", "status": "ok", "steps_run": 4, "steps_total": 4,
 "steps": [{"n": 1, "tool": "navigate", "summary": "...", "ok": true}, "..."],
 "url": "https://demo.example.com/welcome", "title": "Welcome"}
```

It stops at the first failing step unless that step says `onError: continue`.
A failed run says which step stopped it, the error, and the page it was on — so
check that page with `extract` before deciding the selector is wrong.

Pass `verbose=true` for every step's full result, or mark the one step you care
about with `return: true` rather than reading them all.

`url_redacted: true` means the page it ended on carried a guarded value, so the
URL shown is scrubbed and is not a real address. Do not navigate back to it.

## The reads

| Resource | Tool | Gives |
|---|---|---|
| `flow://flows` | `list_flows` | every flow you can run: name, description, parameters, step count |
| `flow://flows/{name}` | `get_flow` | one flow with its steps |
| `flow://schema` | `flow_schema` | the document shape and every step tool's parameters |

Writes are tools only: `save_flow` creates or replaces, `delete_flow` removes,
`run_flow` runs.
