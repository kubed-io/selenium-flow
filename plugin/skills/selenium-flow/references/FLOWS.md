# Flows

A flow is a list of tool calls saved under a name. `run_flow` replays the whole
list server-side, in order, in the browser you already have: a twelve-step form
is one call, and the selectors are not worked out again each time. Save a flow
when you or another agent will run the sequence again, such as a login, a
multi-page form or a check you repeat on Chrome and then on Firefox. A sequence
you will run once is cheaper as the tool calls themselves.

## The loop

1. **Discover.** Drive it once by hand. `outline` gives each selector, already
   checked to match exactly one element
   (`skill://selenium-flow/references/READING_PAGES.md`). A saved flow does not
   look around: `outline` is not a step.
2. **Build.** Write each call down as a step. Anything that changes between runs
   becomes a parameter.
3. **Save.** `save_flow`, once. It checks every step against the live tool
   schemas and refuses all the mistakes at once, before a browser moves. Give it
   `sites` and `paths` too, so the next agent on that page is told it exists.
4. **Run.** `run_flow` from then on.

`flow://schema` is the document's shape and every step tool's parameters.

`show(flow://flows/<name>)` draws one flow; `show(flow://flows)` the scroller.

## A step is a tool call

```json
{"tool": "write", "args": {"selector": {"css": "#email"}, "text": "a@example.com"}}
```

`args` is exactly the arguments you would pass the tool directly. A step may
also carry:

| Key | Does |
|---|---|
| `id` | a name for the step, shown in the report |
| `note` | a comment for the next reader |
| `onError` | `abort` (default) or `continue` past a failure |
| `return` | include this step's full result in the report |

To bound one slow step, set `wait_timeout` in its `args` — the same argument
the tool takes directly.

**Not steps:** `open_session`, `end_browser`, `await_control`, `outline`,
`network_body`, `run_flow`, `save_flow` and the resource tools. A flow runs in
the browser the caller already holds, which is what lets the same flow run on
Firefox unedited, and a step names no `session_id`.

## How long a run may take

A run starts no step after 120 seconds. A flow that exists to wait, for an
order to ship or a build to finish, says so with `timeout` beside `steps`;
`save_flow` takes it the same way:

```yaml
name: wait-for-shipping
timeout: 1200        # seconds for the whole run
steps:
  - tool: assert
    args:
      script: return document.querySelector('.status')?.textContent === 'Shipped'
      wait_timeout: 900
    note: shipping takes up to fifteen minutes
```

`timeout` bounds starting a step; a step already running is bounded by its own
`wait_timeout`, so give the run more than its longest wait. A run that ran out
says `the run passed its 120s budget before this step`: if the flow was meant to
take that long, give it a `timeout`; if not, the step before is where it got
stuck.

## Parameters: what varies between runs

Declare them as JSON Schema and write `${name}` where the value goes. The
fields below are `save_flow`'s arguments, with `sites`, `paths` and `timeout`
left out:

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
    {"tool": "navigate", "args": {"url": "https://demo.example.com/join"}},
    {"tool": "write", "args": {"selector": {"css": "#email"}, "text": "${email}"}},
    {"tool": "interact", "id": "submit",
     "args": {"action": "click", "selector": {"css": "button[type=submit]"}}},
    {"tool": "extract", "args": {"selector": {"css": "h1"}}, "return": true}
  ]
}
```

Then, every time after:

```
run_flow(name="sign-up", params={"email": "a@example.com"})
```

A parameter is text. `${name}` may sit anywhere in any argument of any step,
including in the middle of a longer string:

```yaml
- tool: navigate
  args:
    url: ${site}/orders/${id}
```

An argument that is exactly one `${name}` takes the value as the field's own
type, so a numeric parameter works where a tool wants a number:

```yaml
- tool: assert
  args:
    script: return document.querySelector('#done') !== null
    wait_timeout: ${seconds}
```

The placeholder is accepted when the flow is saved, and a value that cannot
become what the field wants is refused when the run reaches that step.

Substitution is a single pass: a value you pass is never re-scanned, so
`params={"note": "${admin}"}` types that text and resolves nothing. Write `$${`
for a literal `${`, as a JavaScript template literal in a `script` needs. A
`${name}` that is not a declared parameter is refused when the flow is saved.

A parameter's `default` is used when a run leaves it out. A default does not
satisfy `required`, so keep a parameter with a default out of `required`:

```json
{
  "type": "object",
  "required": ["email"],
  "properties": {
    "email": {"type": "string"},
    "site": {"type": "string", "default": "https://demo.example.com"}
  }
}
```

`run_flow(name="sign-up", params={"email": "a@example.com"})` then navigates to
the demo site without being told to.

A parameter is never secret: `save_flow` refuses `writeOnly`, and everything you
pass may appear in the report. For a password, name a secret instead of using a
parameter (`skill://selenium-flow/references/SECRETS.md`).

## Where a flow applies

`sites` and `paths` say where a flow is for. A call that moves the browser to a
different host, port, path or hash route they match names the flow in its result's
`flows_here`, and `open_session` names the flows for the page it opens on. The
same flow can serve development and live, so list both:

```yaml
name: login
description: Sign in to the admin panel with an email and password
sites:
- localhost:8080
- example.com
paths:
- /login
- /admin/login
steps: [...]
```

```yaml
name: penpot-login
description: Sign in to Penpot with an email and password
sites: [penpot.example]
paths: ["/#/auth/*"]
steps: [...]
```

- A site is a host with an optional port: `example.com`, `*.example.com`,
  `localhost:8080`. With a port it matches only that port; without one, any. The
  scheme is ignored. `*.example.com` matches subdomains, never `example.com`
  itself: list both to cover both.
- A path is exact, with `*` for one segment and `**` for any depth:
  `/login`, `/apps/*/settings`. The query is ignored, and so is any fragment that
  is not a route. For an app that routes after `#`, add the route: `/#/auth/*`,
  `/app/#/settings/**`; `#!/` is the same as `#/`. An entry without `#` matches
  every screen, so `/` covers a whole hash-routed app.

Leave one out to match any; leave both out and the flow is never named in
`flows_here`. They are for finding a flow: a run is never refused for being
elsewhere.

## Say what must be true

A flow that carries on when the page is not what it expected reports a success it
has not earned. `assert` runs JavaScript that must come back true:

```yaml
- tool: assert
  args:
    script: return location.pathname.startsWith('/orders')
    message: The Orders link did not open the orders page.
```

- **Return a boolean.** `return !!document.querySelector('#total')`, never the
  element itself. Any other value is refused.
- **It asks again** until the answer is true or `wait_timeout` passes, so an
  assertion straight after a click does not have to know how long a route change
  takes. `wait_timeout: 0` asks once.
- **`stable_for` guards.** Asking until true is right after a click and wrong
  for a precondition, because a page that is about to redirect is true on the way
  past. `stable_for: 1` requires the answer to still be true one second later,
  or the clock restarts. Both it and `wait_timeout` are seconds, and `stable_for`
  must be shorter than `wait_timeout`.
- **`message` is what the reader sees.** In a flow it becomes the failing step's
  error. Without one the failure names only the page it was false on, and the
  script is never repeated back.
- **A false assertion fails the step**, so the run stops there and reports
  `failed`. `onError: continue` is refused on an assert, at save time and when a
  hand-edited flow runs.
- `${name}` works inside the script.

A flow is responsible for being where it acts. Start with `navigate`, or start
with an `assert` that says where you must already be: `save_flow` warns when the
first step acts on whatever page the browser is on. An `assert` can also say a
flow should not run at all:

```yaml
- tool: assert
  args:
    script: return !!document.querySelector('#login-username')
    stable_for: 1
    message: Already signed in - you don't need to run this flow.
```

Signed out, the login form is there and stays there, so the flow runs. Signed
in, the app redirects and the run stops with that sentence.

For a page that takes a while to decide, settle and then ask once: wait for
either outcome, then ask the real question with `wait_timeout: 0`.

```yaml
- tool: assert   # wait for the page to commit to one state or the other
  args:
    script: >-
      return !!document.querySelector('#login-username') ||
        document.querySelectorAll('div.navigation-header').length > 0
    wait_timeout: 30
- tool: assert   # then the precondition, asked once
  args:
    script: return !document.querySelector('#login-username')
    wait_timeout: 0
    message: Already signed in - you don't need to run this flow.
```

## Keeping a sign-in

A login flow ends with `save_site_data`, after a step that proves you are in:

```yaml
- tool: assert
  args:
    script: return !document.querySelector('#login-username')
    stable_for: 1
    message: Still on the login form.
- tool: save_site_data
```

A run's report never carries the saved data
(`skill://selenium-flow/references/SITE_DATA.md`).

## When a flow fails

Read `status` before you report success. A failed run is an ordinary result,
not a tool error: the call succeeds and its `status` says `failed`. It names the
step, what went wrong and the page it was on, and carries a `hint`:

```json
{
  "status": "failed",
  "hint": {
    "read": "skill://selenium-flow/references/FLOWS.md",
    "section": "when-a-flow-fails",
    "prompt": "repair_flow",
    "arguments": {"flow": "sign-in", "step": "4"}
  }
}
```

- `read` is a resource to load, exactly as written, and `section` is the heading
  in it that covers this kind of failure. With the skill switched off there is no
  `read`.
- `prompt` is for a person. `repair_flow` walks a page and fixes the flow
  against what is there now; you cannot pick it yourself, but you can tell
  somebody to.

Two failures, two fixes:

**The page changed under the flow.** A selector found nothing, or found
something that cannot be used. Go to that page, `outline` it, and read what the
entries say: an element that is `hidden`, `covered` or `disabled` is not a
missing element, it needs a `hover`, a dismissed banner or a different order.
Then `save_flow` the fix and run it again.

**The flow was run somewhere it was not written for.** Give it a first step that
navigates, or an `assert` that stops the run and says where it must be.

## Whose flows you see

| You are | You save into | You can run |
|---|---|---|
| named (`?workspace=` / `Sf-Workspace`) | your own library | yours, plus the shared library |
| stdio | its own `stdio` library | its own, plus the shared library |
| unnamed | nowhere | the shared library |

`global` is the shared library. Every workspace lists and runs what is in it,
and `save_flow` and `delete_flow` refuse it, so name your workspace before you
save anything. Only an operator moves a flow into `global`, from the admin UI.
A flow name is 1 to 64 letters, digits, `.`, `-` and `_`, starting with a letter
or digit. Saving a name you read from `global` writes a copy into your own
library. Where a name exists in both, yours wins, and `flow://flows` marks each
entry `shared: true` or `false`.

## Running one

Call `open_session` first: a flow never opens a browser. To run the same flow
on Firefox, `open_session(browser="firefox")` and run it again, unchanged.

The report has one line per step plus where the browser ended up:

```json
{"flow": "sign-up", "status": "ok", "steps_run": 4, "steps_total": 4,
 "steps": [{"n": 1, "tool": "navigate", "summary": "...", "ok": true}, "..."],
 "url": "https://demo.example.com/welcome", "title": "Welcome"}
```

A step that produced a file (a screenshot, a PDF) carries that file's link in
its line whether or not it is marked `return: true`.

A long run reports progress while it works, a step at a time. Cancel the call
and the run stops, at the next step or at the next poll of an `assert` that is
waiting.

It stops at the first failing step unless that step says `onError: continue`. A
failed run says which step stopped it, the error and the page it was on, so
check that page with `extract` before deciding the selector is wrong.

A run returns the result of the steps marked `return: true`, any number of them,
and nothing else carries one. A flow whose point is its final `extract` needs
that flag on the extract. For a flow that marks none, such as one from the
shared library that you cannot edit, pass `verbose=true` and read every step.

`url_redacted: true` means the page it ended on carried a guarded value, so the
URL shown is scrubbed and is not a real address. Do not navigate back to it.

## The reads

| Resource | Gives |
|---|---|
| `flow://flows` | every flow you can run: name, description, parameters, step count |
| `flow://flows/{name}` | one flow with its steps |
| `flow://schema` | the document shape and every step tool's parameters |

Writes are tools only: `save_flow` creates or replaces, `delete_flow` removes,
`run_flow` runs.
