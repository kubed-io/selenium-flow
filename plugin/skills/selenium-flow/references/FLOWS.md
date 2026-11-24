# Flows

A flow is a list of tool calls saved under a name. `run_flow` replays it
server-side in the browser you hold, so a twelve-step form is one call. Save one
when you or another agent will run the sequence again: a sign-in, a multi-page
form, a check repeated on Chrome and Firefox. A sequence you run once is cheaper
as the calls themselves.

## Running one

```
run_flow(name="sign-up", params={"email": "a@example.com"})
```

Call `open_session` first: a flow never opens a browser. To run it on Firefox,
`open_session(browser="firefox")` and run it again, unchanged.

```json
{"flow": "sign-up", "status": "ok", "steps_run": 4, "steps_total": 4,
 "steps": [{"n": 1, "tool": "navigate", "summary": "...", "ok": true}, "..."],
 "url": "https://demo.example.com/welcome", "title": "Welcome"}
```

- A run returns the result of each step marked `return: true` and of no other.
  For a flow that marks none, such as one from the shared library, pass
  `verbose=true`.
- A step that produced a file carries its link whether or not it is marked.
- `url_redacted: true` means the last page's URL carried a guarded value and is
  scrubbed. Do not navigate to it.
- Cancelling the call stops the run at the next step, or at the next poll of a
  waiting `assert`.

## When a flow fails

Read `status` before you report success. A failed run is a result, not a tool
error: `status` is `failed`, and the report names the step, the error and the
page it was on, with a `hint`:

```json
{
  "status": "failed",
  "hint": {
    "read": "skill://selenium-flow/selenium-flow/references/FLOWS.md",
    "section": "when-a-flow-fails",
    "prompt": "repair_flow",
    "arguments": {"flow": "sign-in", "step": "4"}
  }
}
```

`read` is a resource to load as written, and `section` the heading in it for this
kind of failure. `prompt` names the one that helps: run it if your client offers it as
a command (`/selenium-flow:repair_flow`), otherwise tell somebody to pick it.

A run stops at the first failing step unless that step says `onError: continue`.
Check the page it stopped on with `extract` before you decide the selector is
wrong. Then it is one of two things:

- The page changed under the flow: a selector found nothing, or something it
  cannot use. `outline` that page. An entry that is `hidden`, `covered` or
  `disabled` is not missing: it needs a `hover`, a dismissed banner or another
  order. `save_flow` the fix and run it again.
- The flow ran somewhere it was not written for. Give it a first step that
  navigates, or an `assert` that stops the run and says where it must be.

## Building one

1. Drive it once by hand. `outline` gives each selector, checked to match one
   element (READING_PAGES.md). `outline` is not a step: a saved flow does not
   look around.
2. Write each call down as a step. What changes between runs becomes a
   parameter.
3. `save_flow` once, with `sites` and `paths`, so the next agent on that page is
   told the flow exists. It checks every step against the live tools and lists
   every problem before a browser moves.
4. `run_flow` from then on.

`flow://schema` has the document's shape and every step tool's arguments.

```json
{"tool": "write", "args": {"selector": {"css": "#email"}, "text": "a@example.com"}}
```

`args` are the tool's own arguments, `wait_timeout` included to bound one slow
step. `open_session`, `end_browser`, `await_control`, `outline`,
`network_body`, `run_flow`, `save_flow` and the resource tools are not steps,
and a step names no session.

A step that opens a popup leaves the session on it, so the steps after it act
there. To reach a tab the flow did not just open, use `tab` with
`action: switch` and an `id` or a `url`. A flow starts and ends on the session's
current tab.

## Parameters

Declare them as JSON Schema and write `${name}` where the value goes:

```json
{
  "name": "sign-up",
  "description": "Create an account on the demo site",
  "parameters": {
    "type": "object",
    "required": ["email"],
    "properties": {
      "email": {"type": "string"},
      "site": {"type": "string", "default": "https://demo.example.com"}
    }
  },
  "steps": [
    {"tool": "navigate", "args": {"url": "${site}/join"}},
    {"tool": "write", "args": {"selector": {"css": "#email"}, "text": "${email}"}},
    {"tool": "interact", "id": "submit",
     "args": {"action": "click", "selector": {"css": "button[type=submit]"}}},
    {"tool": "extract", "args": {"selector": {"css": "h1"}}, "return": true}
  ]
}
```

- A parameter is text, and `${name}` may sit anywhere in any argument:
  `"url": "${site}/orders/${id}"`.
- An argument that is exactly one `${name}` takes the field's own type, so
  `"wait_timeout": "${seconds}"` passes a number.
- A `default` is used when a run leaves the parameter out. A default does not
  satisfy `required`, so keep a parameter with one out of `required`.
- Substitution is one pass: a value you pass is never scanned again, so
  `params={"note": "${admin}"}` types that text. Write `$${` for a literal `${`,
  as a JavaScript template literal in a script needs.
- A `${name}` that is not declared is refused at save. A value that cannot become
  what the field wants is refused when the run reaches that step.
- A parameter is never secret: everything you pass may appear in the report, and
  `save_flow` refuses `writeOnly`. For a password, use a secret (SECRETS.md).

## Where a flow applies

`sites` and `paths` say where a flow is for. A call that moves the browser to a
page they match names the flow in its result's `flows_here`, and `open_session`
names the flows for the page it opens on. List development and live together:

```json
{"sites": ["localhost:8080", "example.com", "*.example.com"],
 "paths": ["/login", "/admin/login", "/#/auth/*"]}
```

The tool's argument descriptions give the matching rules. Beyond them:
`*.example.com` never matches `example.com` itself, so list both; `#!/` is the
same as `#/`; and an entry without `#` matches every screen, so `/` covers a whole
hash-routed app. Leave both out and the flow is never named. A run is never
refused for being elsewhere.

## Tags

`tags` group flows, such as `["auth:ldap", "smoke"]`. Before you set them, read
`workspace://current/tags`, the tags in use with how many flows carry each, and
reuse one. Make up a new tag only when none fits. A tag is saved lowercase, and
`:` or `/` in one is structure for a person, never split. Tags never decide where
a flow applies: `sites` and `paths` do.

## Say what must be true

A flow that carries on when the page is not what it expected reports a success it
has not earned. An `assert` step runs JavaScript that must return true:

```json
{"tool": "assert", "args": {"script": "return location.pathname.startsWith('/orders')",
  "message": "The Orders link did not open the orders page."}}
```

- Return a boolean: `return !!document.querySelector('#total')`, never the
  element. Any other value is refused.
- It asks again until true or `wait_timeout` passes, so it can follow a click.
  `wait_timeout: 0` asks once.
- A precondition needs `stable_for`, because a page about to redirect is true on
  the way past. `stable_for: 1` requires the answer to hold for a second. It must
  be shorter than `wait_timeout`.
- `message` becomes the failing step's error. Without one the error names only
  the page, never the script.
- A false assertion stops the run with `failed`. `onError: continue` is refused
  on an `assert`.
- `${name}` works inside the script.

A flow is responsible for being where it acts: start with `navigate`, or with an
`assert` that says where the browser must already be. `save_flow` warns when the
first step acts on whatever page is open. An `assert` can also say the flow
should not run:

```json
{"tool": "assert", "args": {"script": "return !!document.querySelector('#login-username')",
  "stable_for": 1, "message": "Already signed in - you don't need to run this flow."}}
```

Signed out, the form is there and stays, so the run goes on. Signed in, the app
redirects and the run stops with that sentence. For a page slow to decide, first
wait for either outcome, then ask the real question once:

```json
{"steps": [
  {"tool": "assert", "note": "wait for the page to commit to one state",
   "args": {"script": "return !!document.querySelector('#login-username') || document.querySelectorAll('div.navigation-header').length > 0",
     "wait_timeout": 30}},
  {"tool": "assert", "note": "then the precondition, asked once",
   "args": {"script": "return !document.querySelector('#login-username')",
     "wait_timeout": 0, "message": "Already signed in - you don't need to run this flow."}}
]}
```

A sign-in flow ends with an `assert` that proves you are in, then
`{"tool": "save_site_data", "args": {}}`, so a later browser comes back signed in
(SITE_DATA.md). The report never carries the saved data.

## How long a run may take

A run starts no step after 120 seconds. A flow that exists to wait, for an order
to ship or a build to finish, sets `timeout` beside `steps`:

```json
{
  "name": "wait-for-shipping",
  "timeout": 1200,
  "steps": [
    {"tool": "assert", "note": "shipping takes up to fifteen minutes",
     "args": {"script": "return document.querySelector('.status')?.textContent === 'Shipped'",
       "wait_timeout": 900}}
  ]
}
```

`timeout` bounds starting a step; a running step is bounded by its own
`wait_timeout`, so give the run more than its longest wait. A run that ran out
says `the run passed its 120s budget before this step`. If the flow was meant to
take that long, give it a `timeout`; if not, the step before is where it stuck.

## Whose flows you see

| You are | You save into | You can run |
|---|---|---|
| named | your workspace's library | yours and the shared library |
| stdio | the `stdio` library | its own and the shared library |
| unnamed | nowhere | the shared library |

`global` is the shared library: every workspace lists and runs it, and
`save_flow` and `delete_flow` refuse it. Only an admin moves a flow into
`global`. Saving a name you read from `global` writes a copy into your own
library. Where a name is in both, yours wins, and `flow://flows` marks each entry
`shared: true` or `false`. A flow name is 1 to 64 letters, digits, `.`, `-` and
`_`, starting with a letter or digit.
