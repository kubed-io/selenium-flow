# Chapter 1 — The Flight Plan

> Flight log, **SELENIUM-FLOW**, Dispatch.
>
> Here is the field as it stands. We have an airfield — Selenium Grid — with a
> handful of aircraft on it, and a tower that will talk to any of them. We have
> built a very good radio. Any pilot who can raise us can taxi, take off, turn,
> descend and land, on Chrome or on Firefox, and we will read back exactly what
> the aircraft did. Two radios, in fact, on the same frequency: one for agents
> who speak MCP and one for anything that can POST JSON, and a test that fails
> if the two ever start saying different things.
>
> That radio works. It has been flown daily, it has found and fixed its own bugs
> in the air, and the parts of it that were wrong were wrong loudly.
>
> **And every single turn requires a radio call.**
>
> Bank left — *roger, banking left*. Descend to four thousand — *roger,
> descending*. Every instruction is a separate transmission, and between each
> one the pilot has to think about what to say next, which for our pilots is the
> expensive part: they are language models, and thinking costs more than flying.
> A twelve-step approach is twelve calls, twelve readbacks and twelve rounds of
> deliberation, to fly a route that has not changed since the last time anyone
> flew it.
>
> This chapter is about **filing a flight plan**. One document, filed once,
> flown on one clearance. The pilot says *"execute the approach"* and Dispatch
> flies the whole thing and reports back at the threshold.
>
> Chapter 1 is exposition and design. **We build nothing until the plan is
> signed off.** What follows is what exists, what we are adding, what we have
> decided, and the forks that are still Dr K's to close.

---

## Status: **OPEN** — 2026-09-10

Design and planning only. No code has been written for this feature.

**Second pass, same day.** Dr K answered the four blocking forks and added the
`global` session; the sections below carry the answers and say where they
overruled the first draft. §F1.6 and §F1.7 were also researched against prior art
at his instruction rather than invented — see *Prior art* at the head of Part II.

**Third pass, 2026-09-11.** Part III — the secrets system — added from Dr K's
design, and §F1.7 **revised**: no templating anywhere, structural `valueFrom`
references instead.

Part III's two load-bearing claims were checked against a live cluster from
inside a pod rather than reasoned about: token-only Kubernetes access works with
no SDK, and there is **no way to list a Secret's key names without pulling its
values** (§F1.20). A third finding came out of re-reading our own shipped code:
`write` returns the value it just typed, which would have handed every bound
secret straight back to the model (§F1.25).

Everything in Parts II and III is **locked or recommended**; what remains open is
listed under *Open questions*, and none of it blocks E2.

---

## Part I — Exposition: what is already flying

This part exists so a reader picking the repo up cold does not have to
reconstruct it from the source. It is a summary, not a spec — `AGENTS.md`
carries the rules, and this restates only what Part II builds on.

### The shape of the thing

`selenium-flow` is an MCP server that drives real browsers on a Selenium Grid.
It is deployed as a component *of* the Selenium app in the cluster repo
(`apps/selenium/components/mcp`) rather than as an app of its own, because a
browser driver with no Grid is furniture.

Four facts define everything else:

1. **The server holds no browser.** A browser lives on the Grid; the caller
   carries its id. That is why this pod can restart, scale to zero or be
   replaced mid-task without anyone losing a session.
2. **Every capability is exposed twice** — an MCP tool and an HTTP endpoint,
   one to one, both thin wrappers over `actions.py`. `tests/test_surfaces.py`
   fails if the two sets ever diverge.
3. **A flow session is the thing; a browser is something it holds.** The
   session is a record in a store (Redis in the cluster) keyed on the caller:
   browser choice, window size, last page, and the id of a browser *if it
   currently has one*. Detached is an ordinary state.
4. **The client's capabilities decide the rendering, not the registration.**
   One server object serves every client; middleware filters the tool listing
   per request. A resource gets a mirroring tool for clients that cannot read
   resources, and that tool is hidden from clients that can.

### What a caller can do today

Fourteen actions: `open_session`, `end_browser`, `navigate`, `interact`,
`frame`, `resize`, `dialog`, `upload_file`, `write`, `press_key`, `extract`,
`execute_script`, `screenshot`, `save_pdf`. Plus three mirror tools that are
not actions (`current_session`, `session_files`, `selenium_flow_skill`), an
admin UI over HTTP, signed file URLs, an embedded skill, and a generated
OpenAPI document that the wiki is rendered from.

### The two session modes

Whether a caller must pass `session_id` depends on whether the server can
identify it, and the advertised schema is rewritten per request to match:

| Mode | When | `session_id` |
|---|---|---|
| **saved** | the caller has a stable key | must NOT be passed |
| **stateless** | it has none | required on every call |

A key comes from an `X-Session-Key` header, else `?session=<name>` on the MCP
URL, else the negotiated `Mcp-Session-Id`, else stdio's constant. Nothing is
ever invented — that rule is the scar tissue from `Context.session_id`, which
returns a fresh UUID rather than failing and once leaked one browser per tool
call.

**This distinction is load-bearing for Part II.** Two of the three key sources
are stable across reconnects and restarts; one is not.

### Files, today

Files are the Grid's. `se:downloadsEnabled` gives each browser a per-session
directory on the node it runs on, the Grid serves it at
`/session/<id>/se/files`, and it is **deleted with the browser**. We list it,
read from it, and hand out signed URLs into it. `screenshot(save=True)` and
`save_pdf` push files *into* it through the browser's own download path.

`AGENTS.md` is explicit that a store of our own would duplicate that lifecycle
and get it subtly wrong. That reasoning is correct for *ephemeral* files and is
about to acquire an exception — see §F1.10.

---

## Part II — The doctrine

### Prior art, because Dr K asked before we invented anything

Three formats are worth knowing about. Two of them describe browser flows and
one describes exactly the thing we are building — a saved sequence of MCP tool
calls, exposed as one tool.

**[Chrome DevTools Recorder / `@puppeteer/replay`](https://github.com/puppeteer/replay)**
— the closest thing to a standard for browser flows, and Chrome ships the
recorder that writes it. Read from `src/Schema.ts` rather than the docs:

- A `UserFlow` is `{title, timeout?, selectorAttribute?, steps: Step[]}`.
- `Step` is a **discriminated union on `type`**, exactly Dr K's instinct. Types:
  `click`, `doubleClick`, `hover`, `change`, `keyDown`, `keyUp`, `scroll`,
  `navigate`, `close`, `setViewport`, `emulateNetworkConditions`,
  `waitForElement`, `waitForExpression`, `customStep`.
- Shared fields come from interface inheritance — `BaseStep {type, timeout?,
  assertedEvents?}` → `StepWithTarget {target?}` → `StepWithFrame {frame?}` →
  `StepWithSelectors {selectors}`.
- **Steps are flat**: the arguments sit beside `type`, not under a key. The one
  exception is `customStep`, which nests them under `parameters` — the escape
  hatch is the only part that needed the room.
- **There are no variables and no parameterisation at all.** Confirmed in both
  the reference docs and the schema source.
- Two ideas worth stealing outright: `selectors` is an **array of alternatives**
  ("it's recommended that the implementation tries out all of the alternative
  selectors to improve reliability of the replay as some selectors might get
  outdated over time"), and `assertedEvents` declares that a step is expected to
  navigate. Both are answers to the problem a *saved* flow has and a live agent
  does not: locators rot between runs.

**[Selenium IDE `.side`](https://www.selenium.dev/selenium-ide/)** — JSON, a flat
`{command, target, targets, value}` per step, and it is the one browser format
that **does** have variables: `store` writes one and `${name}` reads it back.
The `targets` array is the same alternative-selector idea as Chrome's.

**[ToolHive vMCP composite tools](https://docs.stacklok.com/toolhive/guides-vmcp/composite-tools)**
— not a browser format at all, but structurally our exact problem: define a
multi-step workflow over MCP tools and expose it as one tool. Its shape:

```yaml
name: my_workflow
description: A multi-step workflow
parameters:            # literally JSON Schema
  type: object
  properties: {required_param: {type: string}}
  required: [required_param]
steps:
- id: step_name        # a unique id, because other steps refer to it
  tool: backend_tool
  arguments:           # NESTED under their own key
    arg1: '{{.params.input}}'
  timeout: '30s'
  onError: {action: abort}      # abort | continue | retry
```

with `{{.steps.<id>.output.<field>}}` for reading an earlier step's result, and
an `output:` block for shaping what the whole run returns.

**The two browser formats are flat and the workflow format is nested, and the
reason they differ is the whole answer to §F1.6.** Chrome's recordings are
*recorded* — no parameters, no ids, no step referring to another. ToolHive's are
*authored*, parameterised, and chained, so every step needs an `id`, an
`onError`, a `timeout` and a place to put arguments that cannot collide with any
of them.

Ours are authored. Ours want parameters. Ours want chaining, eventually.

### §F1.1 — Decision (locked): a flow is one clearance, and the saving is mostly not where it looks

The obvious pitch for batching is "fewer round trips to the browser". That
pitch is **wrong here**, and getting it right is what makes the rest of the
design honest.

`Grid.reconnect` does not talk to the Grid. `ReattachDriver` deliberately skips
`start_session`, so binding to a running browser is local object construction —
no HTTP, no handshake. Batching saves nothing there, and the WebDriver commands
each step sends are the same commands either way.

Here is what a twelve-step task actually costs today, per step:

| Per step, today | Why |
|---|---|
| **One model inference** | the agent must decide what to call next |
| **One MCP round trip** | client → server, and back |
| **One `is_alive` GET to the Grid** | `sessions.resolve` validates the stored record |
| **One store read + write** | `resolve` reads it, `touch` writes the new URL |
| Its own WebDriver commands | unavoidable, and unchanged by any of this |

Filed as a flow, the whole run costs **one** of each of the first four, and the
same WebDriver traffic. The dominant term is the first one — a model turn is
orders of magnitude more expensive in both latency and tokens than everything
below it — and it is the term that collapses hardest, from twelve to one.

So the doctrine:

> **A flow is a clearance, not a macro.** Its value is that the pilot stops
> deliberating between turns. Everything else it saves is real and secondary.

The corollary matters for scope: a flow whose steps a model still has to reason
about between calls has no reason to exist. **A flow must be runnable without
the model in the loop**, which is what forces §F1.7 (parameters) and §F1.8
(what a run returns) to be answered properly rather than deferred.

### §F1.2 — Decision (locked): a session owns its flows, and everything unnamed shares `global`

Flows are stored per session, and the session's *name* is the directory. The
first draft of this section made that a hard requirement and refused to serve
flows to a caller with no name. **Dr K overruled it, and the override is
better:** an unnamed caller gets a reserved session called `global`.

That dissolves the problem the hard rule existed to solve. Here is why the rule
was there:

| Key source | Stable across a reconnect? | Directory |
|---|---|---|
| `named:<name>` — header or `?session=` | yes, the client chose it | `<name>/` |
| `mcp:<transport id>` | **no** — new on every reconnect | `global/` |
| `stdio` | constant, but shared by every stdio caller | `global/` |
| none (stateless) | there is no key at all | `global/` |

A directory per *transport* key would have filled the disk with folders keyed on
ids that never come back — the flows inside them unreachable forever. Routing
all three unnamed cases to one shared `global` gets rid of that entirely: there
is exactly one directory for everyone who has not named themselves, it is stable,
and it is useful rather than merely harmless. Flows always work, and there is no
error path to explain.

So:

- **`global` is a reserved session name.** `?session=global` is legal and lands
  in the same place, which is consistent rather than a special case.
- **`global` is shared and unauthenticated by session.** Anyone who can reach
  this server without naming a session can list, run, overwrite and delete what
  is in there. In a homelab behind one bearer token that is fine; it must be
  *said*, in the skill and in the admin UI, because "shared" is not guessable
  from the tool schema.
- **Naming yourself is how an agent gets a private library.** `?session=` stops
  being an ergonomic nicety and becomes the thing that owns your flows and your
  kept files.

**Every session reads `global`; only an admin writes to it (Dr K, closing
question #3).** The resolution rules:

- **Reads** are your own session's flows *plus* `global`. Your own wins on a name
  collision, so a session can shadow a shared flow without disturbing it.
- **Writes** — `save_flow`, `delete_flow` — only ever touch **your own** session.
  An agent cannot publish to the shared library, deliberately.
- **Promotion is an admin action in the UI.** Click a flow, make it global. That
  makes `global` a *curated* library rather than a shared scratchpad, which is
  the difference between a shared login flow you can trust and one anybody
  overwrote last Tuesday.

**One asymmetry to be aware of rather than fix:** a caller with no session name
*is* `global`, so it writes there directly, while a named session needs an admin
to promote. That is not inconsistent so much as unavoidable — the unnamed caller
has no other folder to write to — but it does mean **anonymous callers can write
to the shared library and named ones cannot.** In a homelab behind one bearer
token that is an acceptable trade for "flows always work with no setup". It is
written down here so nobody discovers it later and thinks it was an accident.

**The HTTP surface stays explicit**, as it already is everywhere else: `/flows/*`
takes the session name as a parameter, defaulting to `global`. That is not a
special case, it is the contract `/browser/*` already has applied to a new noun.

### §F1.3 — Decision (locked): one directory, two subdirectories, one per session

```
$FLOW_DATA_DIR/
  global/                     # everything that did not name itself (§F1.2)
    flows/
      cookie-banner.yaml
    files/
  research-bot/               # ?session=research-bot
    flows/
      login.yaml
      weekly-report.yaml
    files/
      report-2026-09-10.pdf
  form-filler/
    flows/
      onboarding.yaml
    files/
```

- **`FLOW_DATA_DIR`** is the one new env var. **Unset means the feature is off**,
  and deliberately not a fallback to a temp directory. The point is not
  durability — §F1.12 accepts an `emptyDir` that a redeploy wipes — it is that
  *the operator chose where this lives*. A silent fallback to `/tmp` would put
  flows on a 64Mi volume alongside the upload staging area, where they would
  disappear for reasons nobody could trace back to a config decision they never
  made.
- The session directory is created lazily, on first write.
- `flows/` holds one document per flow, named `<flow>.yaml`. YAML rather than
  JSON because a person edits these by hand and in the admin UI — see §F1.14,
  which settled the format after this section was first written.
- `files/` holds kept files, byte for byte as they came off the Grid.

Both halves live under one root because they are the same idea: *this session's
durable things*. One env var, one volume, one backup, and one thing to point at
WebDAV later.

### §F1.4 — Decision (locked): names are validated, never sanitised

Both the session name and the flow name become path segments, and both come
from a caller — the session name off a URL query parameter that anyone who can
reach the port can write. `?session=../../etc` is the obvious attack and it is
not the only one.

**Reject, do not slug.** A rejected name is a 400 the caller fixes in one try.
A silently rewritten name produces a flow saved somewhere the caller will never
look for it again, which is the failure this feature exists to prevent.

The rule: a name matches `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, and must not be
`.` or `..`. Applied at the boundary, in one function, used by every surface —
same shape as `auth.py` and `errors.py`, which are each the single place their
question is answered.

This also constrains §F1.2's error message: an unnamed session and an
*invalidly* named one are different failures and must say different things.

### §F1.5 — Decision (locked): reads are resources, writes are tools

This is Dr K's direct question — *"is there some better MCP practice for not
overdoing the tools with just CRUD, or do I go full CRUD?"* — and this repo
already answered it for a different noun.

The established pattern is `files.py`: a listing resource, a
read-one-by-name resource, and a **mirror tool** that returns the same listing
for clients with no resource support, hidden from clients that have it by
`HideMirrorTools`. Apply it verbatim:

| Verb | Surface | Visible to a resource-capable client? |
|---|---|---|
| list | `flow://flows` resource + `list_flows` mirror tool | resource only |
| get one | `flow://flows/{name}` resource + `get_flow` mirror tool | resource only |
| save (create-or-update) | `save_flow` tool | **yes** |
| delete | `delete_flow` tool | **yes** |
| run | `run_flow` tool | **yes** |

So a client like Claude sees **three** new tools, not five, and reads the
library for free out of resources without spending a call. A client like n8n,
which has no notion of resources, sees five and loses nothing.

That is the answer: **you do not avoid CRUD by merging verbs into one
overloaded tool — you avoid it by putting the reads where reads belong.** The
mirror machinery for doing that already exists here and is already tested.

`save_flow` is deliberately create-or-update on one verb, as Dr K specified:
same name updates, new name creates. A separate `create` that fails on conflict
would buy nothing an agent can use — it does not know what already exists until
it lists, and if it listed, it knows.

### §F1.6 — Decision (recommended, was OPEN): nested `{tool, params}`, a discriminated union, derived from the tool schemas

Dr K's instinct was *"each step is a tool call with an extra discriminator"*,
sketched as `action: extract` / `params: ...`, with Pydantic models spread into
kwargs so the types survive. That instinct is right and the prior art confirms
it — with two corrections and one important consequence.

**Nested, not flat.** Chrome's recorder is flat; ToolHive's workflows are nested;
and §*Prior art* above explains the split. Flat works when a step is recorded and
never refers to anything. The moment a step needs an `id` (so a later step can
read its output), an `onError`, a `timeout` or a `return` marker, every one of
those words is stolen from the namespace the tool's own arguments live in. We
would collide immediately and permanently:

```jsonc
// flat — collides on day one
{"tool": "interact", "action": "click", "xpath": "//button"}
//                    ^^^^^^ interact, frame and dialog ALL take a param
//                           literally called `action`
```

```jsonc
// nested — the two namespaces never touch
{
  "id": "submit",
  "tool": "interact",
  "params": {"action": "click", "xpath": "//button[@type='submit']"},
  "onError": "abort"
}
```

**`tool`, not `action`, as the discriminator.** Dr K wrote `action:`, and the
repo's own vocabulary agrees — `actions.py` holds them and `AGENTS.md` calls
them actions. But `{"action": "interact", "params": {"action": "click"}}` is a
document with the word `action` at two nesting levels meaning two different
things, and it will be misread by people and models alike. `tool` is also what
the caller actually experiences: these are the tool names in `tools/list`.
(ToolHive uses `tool` + `arguments`; `params` is Dr K's word and shorter, so:
`tool` + `params`.)

**The consequence, and it is the load-bearing one: the step models are
*derived*, not written.**

`AGENTS.md`'s central rule is that `actions.py` is the only place behaviour
lives, and that the type hints in `tools.py` *are* the tool schema — FastMCP
builds the JSON schema from the signature. Hand-writing a second set of Pydantic
models for the same parameters would create exactly the drift this repo is built
to prevent: add an argument to `write`, forget the step model, and a flow that
should work fails validation for no visible reason.

**There is direct precedent for the fix.** `openapi.py` already does it — the
HTTP request schemas in the published spec are *"the MCP tool schemas verbatim,
which FastMCP derives from the signatures in `tools.py`"*. The same move works
here: build the per-tool params models with `pydantic.create_model()` from the
schemas FastMCP already has, and assemble them into a discriminated union on
`tool`. One source of truth, three renderings — the tool schema, the OpenAPI
request body, and the flow step.

That also hands Dr K the thing he asked for at the end of §F1.7 for free: **the
whole flow document schema becomes publishable**, as `flow://schema`, so a model
can be handed the exact shape it is expected to produce rather than inferring it
from a docstring.

Step-level keys, then, all of which the nested shape has room for and the flat
one does not:

| Key | Chapter | Meaning |
|---|---|---|
| `tool` | 1 | which action — the discriminator |
| `params` | 1 | that action's own arguments, validated against its derived model |
| `id` | 1 | a name for this step, unique in the flow; needed for reports now and chaining later |
| `onError` | 1 | `abort` (default) \| `continue` — ToolHive's word, better than the `optional: true` this chapter first proposed |
| `return` | 1 | include this step's full result in the run report (§F1.8) |
| `note` | 1 | a human comment, so a twelve-step flow is readable |
| ~~`timeout`~~ | — | **withdrawn in E3.** Nothing could honour it: a Selenium call blocks, so a wall-clock bound cannot interrupt one, and every action that *can* wait already takes `wait_timeout` in its own params — which is validated against that tool's schema and is the real per-step bound. A key that parses and then does nothing is worse than one that is refused. |
| `saveAs` | 2 | bind this step's output into the variable bag (§F1.7) |

**Two ideas from Chrome's schema deliberately deferred, and worth writing down
so they are not lost:** `selectors` as an *array of alternatives* tried in order,
and `assertedEvents` declaring that a step is expected to navigate. Both exist
because a saved flow has a problem a live agent does not — **its XPaths rot
between runs, and it has no model in the loop to notice.** §F1.1's doctrine is
precisely that we removed the thing that would have adapted. That makes locator
fallback the single most valuable Chapter 2 feature, and `params.xpath` should
therefore be specified now as *either a string or a list of strings*, so adding
it later is not a breaking change.

### §F1.7 — Decision (locked, REVISED 2026-09-11): parameters are JSON Schema, and every reference is structural — there is no templating

**This section originally specified `{{name}}` substitution. Dr K withdrew it:**

> *"Let's make the entire workflow structural — no handlebar replacements or
> templating. We get a param like a secret and use `valueFrom.param` or
> `valueFrom.secret` or `valueFrom.config`. Structural means we don't use
> templating in our object and make structural references instead."*

That is the better design, and it retires an argument rather than winning it.

**The declaration is unchanged and still right.** A flow's `parameters` is
literally a JSON Schema object, taken from ToolHive's shape (see *Prior art*) —
so it is not a bespoke dialect that has to be described to a model, it *is* the
description, and `list_flows` can hand it over verbatim.

**What changes is how a step reaches a value.** Not by interpolating text, but
by naming a source in its own field:

```yaml
name: login
description: Log in to the admin panel
parameters:
  type: object
  properties:
    email: {type: string, description: the account to log in as}
  required: [email]
steps:
- tool: navigate
  params: {url: https://example.com/login}

- id: fill-email
  tool: write
  params:
    css: "#email"
    value_from: {param: email}

- id: fill-password
  tool: write
  params:
    css: "#password"
    value_from:
      secret: {name: nextcloud-admin, key: password}

- tool: interact
  params: {action: click, css: "button[type=submit]"}
```

The rules:

- **`value_from` is an ordinary parameter of the action**, so a step's `params`
  is *exactly* the arguments of the call, with no exception — and a step is
  literally the call a caller would make directly.

  **Corrected twice on 2026-09-11.** The first draft made `valueFrom` a
  step-level map of parameter-name → source. Dr K cut the map:

  > *"You have that extra level of `text` that was unnecessary. Remember that
  > k8s has `value` and `valueFrom` on the same level as mutually exclusive
  > keys."*

  …and then cut the step-level key itself:

  > *"For the tools to be symmetric the valueFrom can be nested under params
  > since it is specific only to write."*

  Both are right, and the second is the stronger claim. Kubernetes shapes an env
  var as a **thing that already has a name**, with `value` and `valueFrom` as
  mutually exclusive siblings; the name is never repeated inside `valueFrom`.
  Here the *action* is the named thing and it declares which argument a
  `value_from` fills, so nothing repeats a name — and because it is a parameter
  rather than a step key, the step form and the direct-call form stop being two
  shapes at all:

  ```python
  write(css="#password", value_from={"secret": {...}})              # direct
  ```
  ```yaml
  {tool: write, params: {css: "#password", value_from: {secret: {...}}}}
  ```

  It is spelled `value_from`, not `valueFrom`, because it is a parameter and
  every other parameter on this surface is snake_case. The *step* keys stay
  camelCase (`onError`) — those are ours; parameters are the tool's.

  **What it costs**, stated so it is a decision rather than an oversight: a
  value can only reach an argument an action has *chosen to open*, and only
  `write` has (§F1.28). Taking `navigate`'s `url` from a flow parameter is not
  expressible today. That is deliberately a one-line change per action — a
  `value_from` parameter plus an entry in `FILLS` — so it is a decision
  deferred, not a door closed.
- **A source is exactly one of three**: `param` (a name from this flow's
  `parameters`), `secret` (`{name, key}`), or `config` (`{name, key}`, when
  ConfigMaps land — §F1.31). Two is refused, zero is refused.
- **A value may not be given twice.** Giving the action's value argument *and* a
  `value_from` is refused at save time, not silently resolved in one direction —
  exactly what Kubernetes means by `value` and `valueFrom` being mutually
  exclusive.
- **References resolve to whole values, never fragments.** There is no way to
  express "the URL is `https://` plus this param plus `/login`". If a flow needs
  a composed value, the composition is the caller's job and it passes the
  finished string as a parameter.

That last rule is the one that costs something, and it is worth being explicit
that it is a deliberate trade rather than an oversight. Templating buys string
composition; structure buys everything else:

| | Templating (`{{name}}`) | Structural (`valueFrom`) |
|---|---|---|
| Compose a string from parts | yes | **no** |
| Validate a reference at **save** time | no — it is text until it runs | **yes**, against the parameter list |
| Say *what kind* of thing is being referenced | no | **yes** — param, secret or config |
| Bind a secret without it ever being a string in the document | no | **yes** |
| Collide with the payload | **yes** — see below | no |

The last row was very nearly a shipped bug. The prior draft chose `{{name}}`
over `${name}` specifically because `execute_script` steps carry JavaScript, and
JavaScript template literals are `` `${...}` `` — so `$`-substitution would have
silently mangled a script we handed the browser. Structural references have no
such failure mode **because nothing scans the payload at all.** A whole class of
bug stops existing rather than being avoided, which is the strongest kind of
simplification.

`writeOnly: true` on a parameter (this section's original proposal) survives and
still means "supplied but not echoed back". But see §F1.29: for anything
genuinely secret, `value_from.secret` is strictly better, because a `writeOnly`
parameter still has to be *supplied* — which means a model held it.

### §F1.8 — Decision (locked by E3): what a run returns

A twelve-step flow whose steps include three `extract` calls on `//body` returns
an enormous amount of text if it returns everything. Returning nothing makes the
run unverifiable. Both are bad, and this decides whether the feature is actually
cheaper than the twelve calls it replaces.

Proposal — compact by default:

```json
{
  "flow": "login",
  "status": "ok",
  "steps_run": 3,
  "steps": [
    {"n": 1, "tool": "write",    "ok": true, "summary": "wrote 18 chars to //input[@name='email']"},
    {"n": 2, "tool": "write",    "ok": true, "summary": "wrote 12 chars to //input[@name='password']"},
    {"n": 3, "tool": "interact", "ok": true, "summary": "clicked //button[@type='submit']"}
  ],
  "url": "https://example.com/dashboard",
  "title": "Dashboard",
  "result": { "...": "the last step's full result" }
}
```

With `verbose=true` returning every step's full result, and a per-step
`"return": true` marking a step whose output the caller actually wants — the
`extract` in the middle of the flow that *is* the answer.

**On failure**, the run stops and returns everything up to and including the
failed step, with the full error, the URL the browser was on, and the step
number. A flow that fails at step nine and reports only "failed" would send the
agent straight back to twelve individual calls to find out why, which loses more
than the flow ever saved.

**Built as recommended**, and one thing was learned doing it: a step's result
has to be *cleaned* rather than merely selected. `screenshot` returns a megabyte
of base64, so a run that returned three of them would cost more than the twelve
calls it replaced — the `image` field is dropped from every report, and
`screenshot(save=True)` plus the file list is the way to look at one.

The recommendation, for the record: compact, plus `return`-marked steps, plus a
`verbose` escape hatch; stop on the first error, with `onError: continue` for
steps that
are allowed to fail — the cookie banner that is only sometimes there, which is a
real and very common case. (§F1.6 settled the spelling: `onError`, from
ToolHive, rather than the `optional: true` this section first proposed.)

Note the one asymmetry worth keeping: **`status` is about the run, `ok` is about
the step.** A run with a failed `onError: continue` step is `"status": "ok"` with
an `"ok": false` step inside it, and both facts are visible without reading
prose.

### §F1.9 — Decision (locked): `open_session` and `end_browser` are not steps

A flow runs against **the browser the caller already has**, resolved exactly the
way every other tool resolves it.

This is what makes Dr K's cross-browser testing case work:

```
open_session(browser="chrome");   run_flow("checkout");  end_browser()
open_session(browser="firefox");  run_flow("checkout");  end_browser()
```

The same flow, unedited, on both. If the flow could open its own browser, the
browser choice would be baked into the saved document and that comparison would
require two nearly-identical flows that drift apart.

It also keeps a whole class of bug out: a flow that opens a session while the
caller holds one would end the caller's browser mid-task, because
`open_session` ends the one you are holding by design.

Lifecycle is the pilot's; the route is the plan's.

### §F1.10 — Decision (locked): one file list, a `kept` flag, and `keep_file` flips it

Dr K's call, and it is simpler than what this chapter first proposed. The first
draft wanted two listings — Grid-backed ephemeral files and disk-backed kept
ones — on the grounds that different lifetimes deserve different surfaces. That
was wrong in the way that adds work: it makes the caller ask two questions and
join the answers.

**One list. Every file the session has, with a property saying whether it
survives.**

```json
{
  "component": "fileGrid",
  "session": "research-bot",
  "files": [
    {"name": "export.csv",   "kept": false, "size": 20481, "url": "..."},
    {"name": "invoice.pdf",  "kept": true,  "size": 88213, "url": "..."}
  ]
}
```

- **`keep_file(name)`** takes a name and nothing else. It does not care whether
  the file came from a screenshot, a `save_pdf`, or something the site
  downloaded — it copies the bytes out of the Grid onto disk and flips `kept` to
  true. One verb, one argument, no type-awareness anywhere.
- **No `keep=` parameter on `screenshot` or `save_pdf`.** Dr K chose the tool,
  and it is the right call for a reason worth recording: `screenshot` already has
  `save`, meaning "put it in the session's files". A `keep` beside a `save` would
  be two similar words for two different lifetimes and every caller including us
  would get it wrong. `keep_file` after the fact also matches how you actually
  work — you rarely know a screenshot was worth keeping until you have looked
  at it.
- **The admin UI gets a small overlay icon** on each file marking ephemeral
  versus kept. Nothing more; the property is already in the payload the file grid
  renders.

The consequences that need building rather than deciding:

- **The listing is a union with two different keys.** Ephemeral files are the
  Grid's and are keyed by *browser id*; kept files are ours and are keyed by
  *session name*. So `session_files` needs the session name as well, and — this
  is the good part — **it keeps working after the browser is gone**, returning
  the kept files alone. Today an ended browser means an empty list.
- **A name can exist in both places.** The same `report.pdf` downloaded twice, or
  kept and then re-downloaded. The kept one wins in the listing and carries
  `kept: true`; `keep_file` on an already-kept name overwrites, which is the same
  create-or-update rule `save_flow` uses (§F1.5). Nothing silently duplicates.
- **The file URL route needs to serve both.** `/files/{browser id}/{name}` exists
  and reads from the Grid. Kept files need a sibling keyed by session name,
  signed through `links.py` and **not** a second signing implementation.
- **`upload_file(path=...)` already reaches the server's filesystem**, so a kept
  file is re-uploadable by path. Download an export in one session, attach it to
  a form in another. That falls out for free and is a good reason to build this
  at all.
- **A file made during a flow is tagged with the flow's name** (Dr K, closing
  question #9). Flows produce files exactly the way anything else does —
  `screenshot` and `save_pdf` already write into the session, and a flow calling
  them changes nothing about that. The tag is one extra field on the listing
  entry, and it is what makes "which run produced this?" answerable when a
  nightly flow has left thirty screenshots behind.
- **Nothing collects kept files.** That is what "kept" means. The repo's standing
  rule is no schedulers, so the admin UI shows total size per session and a
  person decides. See open question #5.

### §F1.11 — **OPEN**: `ROUTE_PREFIX` becomes a global prefix, and `/browser` stops moving

Dr K's proposal, and I agree with it. Today `ROUTE_PREFIX` renames the browser
endpoints — `/browser` is a *variable*, while `/mcp`, `/admin` and `/files` are
fixed. That is backwards: nobody wants the browser endpoints called something
else, and everybody eventually wants the whole server mounted under a path.

Proposed:

| | Today | Proposed |
|---|---|---|
| `ROUTE_PREFIX` means | what to call the browser endpoints | where the **whole server** is mounted |
| Browser endpoints | `$ROUTE_PREFIX/*` | `$ROUTE_PREFIX/browser/*` — fixed |
| MCP | `/mcp` | `$ROUTE_PREFIX/mcp` |
| Admin, files, flows | `/admin`, `/files` | `$ROUTE_PREFIX/admin`, `/files`, `/flows` |
| `/health`, `/openapi.*` | root | **stays at root** — see below |

`/health` and `/openapi.json` stay unprefixed on purpose. They are how a kubelet
and a load balancer find out whether this process is alive, and making that
depend on a configurable path is how a readiness probe silently 404s after a
config change. The cluster's probes point at `/openapi.json` today.

**This is a breaking change with a live deployment attached, and the sequencing
is the risk, not the code.** `mcp.env` currently sets `ROUTE_PREFIX=/browser`.
Under the new meaning that would serve everything at `/browser/browser/*`,
`/browser/mcp`, `/browser/admin` — the MCP URL every client is configured with
would 404, including the one Dr K is using.

So the rollout is ordered and must be written into the PR description:

1. Ship the code with `ROUTE_PREFIX` defaulting to **empty**.
2. In the **same** change, delete `ROUTE_PREFIX=/browser` from `mcp.env`.
3. Bump `newTag` and `kubectl up` — the ConfigMap hash rolls the pods, so both
   land together.

**Verify before building** (do not assume): FastMCP's `run(transport="http")`
takes a `path` for the MCP endpoint, and Starlette custom routes are registered
with literal paths. Whether the MCP endpoint can be moved cleanly, and whether
the admin UI's own asset and API paths are all relative, are two things to check
in the pod rather than reason about. The admin page is served from `static/` and
fetches `/admin/sessions` and `/admin/events` — if any of those are absolute in
the HTML or JS, they break under a prefix and that is exactly the kind of thing
that looks fine in tests and 404s in the browser.

**Second prize, if prefixing `/mcp` turns out to be awkward:** keep the ingress
`StripPrefix` doing the work, and make `ROUTE_PREFIX` global for everything
except `/mcp`. Worse, but honest, and better than a half-prefixed server.

### §F1.12 — Decision (locked): the codebase sees a directory; the installer decides what is behind it

This chapter first argued for a PVC and spent a page on what it forecloses. Dr K
cut that off, correctly: **it is not the codebase's business.**

> *"It's just some folder. When it is implemented and installed we leave that
> totally up to the installer."*

So the contract is one env var pointing at a directory, and nothing in the code
knows or cares what is mounted there. A laptop points it at `~/selenium-flows`.
A cluster points it at whatever it likes. The three obvious choices are all
valid and none of them is ours to make:

| Backing | Who it suits | What it costs |
|---|---|---|
| A host directory | a MacBook, a `docker compose up` | nothing |
| `emptyDir` | **this cluster** | lost on pod restart |
| A PVC | someone who wants flows to outlive a redeploy | pins the pod to a node; forecloses `replicas > 1` until RWX |

**For Dr K's cluster: `emptyDir`.** Still ephemeral, but on a *much* longer clock
than a browser session — it survives every reaped browser, every ended session
and every restart of the Grid, and dies only when the pod does. And it collects
no junk, which is the other half of the reason: nothing accumulates across
deploys, so the "a session used once leaves a directory forever" problem this
chapter worried about simply does not arise.

**The honest consequence, stated once so nobody is surprised: a redeploy wipes
saved flows.** That is a real cost and it should shape how much anyone invests in
a flow until the backend below exists. It also means the deployment note in
`mcp.env` has to say so, in the same voice as the `SESSION_TTL` comment already
there.

**The backend is pluggable, and that is the part that is the codebase's
business.** Dr K's stated direction:

> *"later on I want a webdav storage backend instead of filesystem. So basically
> file store as local or webdav. Then we could use nextcloud."*

That is a `FileStore` protocol with a `local` implementation now and a `webdav`
one later — the identical shape to `SessionStore`'s `memory`/`redis` split, which
is already in `store.py` and already proves the pattern works here. It costs
nothing to write it that way the first time and it is expensive to retrofit.

Two things that protocol must get right on day one, because they are what a
WebDAV backend will break if they are assumed:

- **No path arithmetic outside the store.** Every caller asks the store for "the
  flows of session X" and gets documents back. The moment something elsewhere
  builds a path with `/`, the WebDAV backend has to reimplement it.
- **No assumption that reads are cheap or local.** A listing over WebDAV is a
  network round trip. The flow listing resource should return names and
  descriptions (§F1.5), never the full documents, precisely so this stays true.

And it is a genuinely good destination: flows in Nextcloud means they are
versioned, shared, browsable and backed up by something that already does all
four — the same argument every other `nextcloud-*` project in this fleet makes.

### §F1.33 — Decision (locked): a flow declares where it applies, and the page you are on decides what you see

Dr K's, and it solves a problem the feature is about to have rather than one it
has: a library of thirty flows across six sites, where twenty-nine of them are
noise on any given page.

> *"Since the browser always has context on some URL we are currently on, we can
> put a glob-like list of URLs the flow works on. Then list_flows can filter by
> relevant flows. The context of what flows are available is the site itself."*

A flow gains an optional `urls`:

```yaml
name: login
description: Log in to the admin panel
urls:
- https://nextcloud.example.com/*
steps: [...]
```

- **Globs over the whole URL**, matched with `fnmatch`. `*` on its own means
  every site, which is the escape hatch for a genuinely generic flow.
- **No `urls` at all means everywhere**, so every flow saved before this
  existed keeps appearing. Absent and `*` behave identically, deliberately.
- **A URL with no path is matched as though it ended in `/`**, so
  `https://host` matches the pattern `https://host/*` rather than mysteriously
  not doing.

**This is a filter, not a leash, and the distinction has to survive contact with
the next person who reads both features.** A secret's `_allowed_urls` (§F1.27)
is a *security control*: origin-exact, substring matching explicitly rejected,
failing closed when it cannot be parsed. A flow's `urls` is a *discovery
convenience*: glob, fails **open** — when in doubt the flow is listed — and
worth nothing as a defence, because a caller can name any flow it likes.

They look similar and must never be merged. Merging them would either make
discovery annoyingly strict or, far worse, make the secret leash a glob.

**Running is not gated on it.** A flow's first step is very often `navigate`,
so at the moment a run starts the browser is frequently on `about:blank` or on
the page you came from — gating the run on the pattern would refuse exactly the
common case. `urls` says where a flow is *relevant*, not where it is *permitted*.
See open question #14.

### §F1.34 — Decision (locked): the filtered view is its own resource, because MCP has no filter

Dr K asked directly: *"do resources have a filter?"*

**No.** A resource is identified by its URI and nothing else; there is no query
or parameter mechanism in the protocol. The one variable part is a **resource
template** (RFC 6570), which parameterises *path segments* — which is how
`flow://flows/{name}` already works. So a filtered view has to be a different
URI, exactly as Dr K guessed.

The obvious spelling has a collision worth catching before it ships:

> `flow://flows/current` would be ambiguous with `flow://flows/{name}` — it is
> also the URI of a flow that somebody named `current`.

Reserving the name would be a rule nobody can see from the outside. So:

| URI | What |
|---|---|
| `flow://here` | the flows that apply to the page the browser is on |
| `flow://flows` | every flow this session can run |
| `flow://flows/{name}` | one flow, unchanged |

`flow://here` cannot collide with a flow name, and it reads as what it is.

**The tool takes a flag instead**, because a tool *can* have parameters:
`list_flows(everywhere=False)`. The default is the current page, which is Dr K's
call and the right one — the common question is "what can I do *here*".

**The current URL comes from the session record, not from the Grid.** It is what
the last action reported, it is already kept current by `sessions.touch`, and
reading it costs nothing. A resource read must never open a browser or spend a
round trip, and this one does neither. The cost is that a page which navigated
itself since the last action is not reflected — an acceptable trade for a
listing, and stated so nobody assumes otherwise.

### §F1.35 — Decision (locked): the session status says when the page has flows

The other half of Dr K's idea, and the one that makes the feature discoverable
rather than merely filterable:

> *"When the URL changes or the browser session starts, there can be a hint
> about the specific URL having flows."*

`session://current` — which the skill already tells an agent to read first —
gains `flows_here`: the **names** of the flows that apply to the page it is on.
Names only, not descriptions: it is a pointer into `flow://here`, not a
duplicate of it.

That makes the flow library announce itself at exactly the moment it is useful,
without an agent having to think to ask. It also gives the skill a much better
opening move than "list your flows": *read the status, and if `flows_here` is
not empty, one of them is probably the task you were given.*

**The listing has to be cheap for this to be free**, because the status resource
is read often and a summary today reads every flow file in the session. So the
flow store gains the same short-TTL catalogue cache the secrets catalogue
already has (§F1.23) — cache the listing, never the documents.

### §F1.13 — Decision (locked): selector strategy is a per-step, mutually exclusive choice — and it lands in the tools first

Dr K, answering what had been open question #8:

> *"We should simply expose a mutually exclusive option for how the selector is —
> choose between xpath, css selectors, or whatever else there is — then we can
> decide each step how it chooses to select."*

This supersedes this chapter's earlier idea of `xpath` as a list of *fallback*
alternatives. It is a different and better axis: not "try these until one works"
but "this step selects **by** css, that one **by** xpath".

`actions.py` currently hardcodes `By.XPATH`. Selenium offers eight strategies —
`ID`, `NAME`, `CLASS_NAME`, `TAG_NAME`, `CSS_SELECTOR`, `XPATH`, `LINK_TEXT`,
`PARTIAL_LINK_TEXT` — and `CSS_SELECTOR` plus `ID` cover most of what the other
six do more legibly.

**Shape: mutually exclusive keys, exactly one required.**

```jsonc
{"tool": "interact", "params": {"action": "click", "xpath": "//button[@type='submit']"}}
{"tool": "interact", "params": {"action": "click", "css":   "button[type=submit]"}}
```

rather than a `{"by": "css", "value": "..."}` pair. The keys name themselves, a
model writes them without being told the enum, and `xpath` keeps working exactly
as it does today — which matters, because every existing caller, every wiki page
and the whole skill are written in terms of it.

**The consequence that decides the sequencing:** §F1.6 made step params
**derived** from the tool schemas. So a selector choice that existed only in
flows would need the derivation to grow an exception — and the derivation is the
one thing keeping a single source of truth. Therefore:

> **This lands in the tools first, as its own PR, and flows inherit it for free.**

That is not a detour. It is independently the most useful small change available
here: every caller gets CSS selectors, `session://current` guidance gets simpler,
and the flow feature then costs nothing extra to support it. It touches eight
tool signatures, `actions.py`'s locator helper, the OpenAPI request schemas
(derived, so automatic), the wiki (generated) and the skill (written).

### §F1.14 — Decision (locked): stored as YAML, served as JSON, edited as YAML

Dr K asked for *"a simple yaml editor"* in the admin UI for now, with a real
editor later. That settles the on-disk format too, and the answer is cheap:
**PyYAML is already a dependency** — `routes.py` imports it to serve
`/openapi.yaml`.

- **On disk: `<flow>.yaml`.** Hand-editable, comment-able, and it matches the
  house style every other configuration file in this fleet is written in.
- **Over the API: JSON objects**, because that is what a tool call is made of and
  what an MCP client speaks. `yaml.safe_load` on read, `yaml.safe_dump` on write;
  the document is a dict either way and neither surface knows the difference.
- **In the admin UI: the YAML text**, edited directly.

**One honest caveat to document:** `safe_dump` does not preserve comments, so a
flow a human annotated in the editor loses those annotations the next time an
agent calls `save_flow` on it. That is a real cost of letting both edit the same
file. It is acceptable because the `note` field on a step (§F1.6) is the
*structured* place for a comment and survives everything — which is most of why
that field exists.

### §F1.15 — Decision (locked): rationale moves here; `AGENTS.md` keeps the rules

`AGENTS.md` is 525 lines and roughly half of it is *why*. It is good writing and
most of it should not be in an operating manual: the `Context.session_id` war
story, the spec-first argument that was considered and rejected, the six-of-six
Firefox measurement, the history of `grid://sessions` being removed.

This is Penpot's §D4.2 applied here — **documentation rots in the direction of
its author** — and the fix is the same: give the reasoning somewhere else to
live.

The split:

- **`AGENTS.md` keeps** every rule, every invariant, every "do not do this", each
  with the one sentence of *why* that makes it stick, and a citation into this
  saga for the full story.
- **The saga takes** the narratives, the measurements, the rejected
  alternatives, and the decision history.

**The discipline that keeps this from doing damage:** a war story is often the
only reason a rule survives contact with someone who thinks it looks silly. So
the sentence that stays must carry the *consequence*, not just the instruction.
"Never key on `Context.session_id`" is a rule someone will break. "Never key on
`Context.session_id` — it returns a fresh UUID instead of failing, and once
leaked one browser per tool call" is not.

Do this **after** the feature lands, as its own PR. Thinning the manual and
changing the thing it documents in one change makes both unreviewable.

### §F1.16 — Decision (locked): the skill gets `references/FLOWS.md`

`SKILL.md` is an index and stays one. Flows get one row in its routing table and
one new reference, because three tests already enforce the shape: every
`references/...` path named in the index must exist, every file on disk must be
linked from the index, and an unlinked file is never lazily loaded so it may as
well not ship.

`FLOWS.md` has to teach the loop, which is the actual feature and is not obvious
from the tool schemas:

1. **Discover** — drive it once by hand, using `extract` to find the XPaths.
2. **Build** — write the steps down, with `{{params}}` for anything that varies.
3. **Save** — `save_flow`, once.
4. **Run** — `run_flow` forever after, without re-deriving any of it.

Plus: that flows need a named session and why; that the browser is the caller's,
not the flow's; how a failed run reports where it stopped; and the honest
guidance on when *not* to file one — a one-off sequence you will never repeat is
cheaper as three tool calls than as a saved document.

---

## Part III — Sealed orders: the secrets system

Every pilot flies with a locked pouch. They carry it, they hand it to the right
desk at the right airfield, and they never open it. That is the whole design.

Chapter 1 so far builds a flight plan that a machine flies. **This part is what
makes flying it worth doing.** A saved flow that logs in is only useful if the
password can get into the form — and today the only way is for the model to hold
it, which means it is in the transcript, in the tool call, and in whatever the
host logs.

Numbered `§F1.17` onward, continuing Part II's series.

### §F1.17 — Doctrine (locked): the credential never enters the conversation

The point is not encryption. Nothing here encrypts anything, and saying
otherwise would be the dishonest version of this feature.

The point is **removing the value from the places a value should never be**: the
model's context, the tool-call arguments, the transcript, the flow document on
disk, and the server's own logs. The agent learns that a secret named
`nextcloud-admin` has a key named `password`. It never learns the password. It
binds one to a field by *name*, and the substitution happens inside the server,
one layer below anything that can talk.

**The threat this actually defends against is prompt injection**, and it is
worth naming because it is not hypothetical for a browser agent. A page can
contain text addressed to the model. An agent that holds a credential can be
talked into typing it somewhere; an agent that holds only the *name* of one can
be talked into asking for it, and §F1.27 is what refuses.

The corollary is the strongest argument for Parts II and III together:

> **A reviewed flow with a binding is safer than an agent improvising**, because
> a human decided which secret goes into which field on which URL, once, and
> every later run replays that decision instead of re-making it.

### §F1.18 — Decision (locked): a secret is a directory of files

Dr K's shape, and it is exactly right:

```
<secrets dir>/
  nextcloud-admin/          # the secret's name
    username                # a key
    password                # another key
  github/
    token
```

This is precisely how Kubernetes projects a Secret into a pod, which is the
point: **the same code reads a k8s mount and a folder somebody made on a
laptop.** No adapter, no k8s in the picture at all unless there is one.

`SECRETS_DIRS` is a **PATH-like list**, colon-separated:

```
SECRETS_DIRS=/var/run/secrets/kubernetes.io/serviceaccount/..data:/etc/selenium-flow/secrets
```

so a deployment can point at the service account's automounted directory *and*
its own mounted secrets, and a laptop can point at whatever it likes.

Rules that fall out and should be written down before they are discovered:

- **First match wins**, like `PATH`. Two directories offering `nextcloud-admin`
  resolve to the earlier one; the listing says which directory each came from,
  because "why am I getting the wrong password" is otherwise unanswerable.
- **One level deep, always.** A directory inside a secret directory is not a
  nested secret and is ignored. Kubernetes mounts are exactly one level, and
  recursing would invent a shape nothing else produces.
- **Dotfiles are skipped.** A k8s projected volume is full of them —
  `..data`, `..2026_09_10_23_02_50`, all symlinks — and a real one is
  `.dockerconfigjson`. Skipping every name starting with `.` loses that one
  key and avoids listing the machinery, which is the right trade: a docker
  config is not something to type into a form.
- **Unreadable is absent**, the same rule the flow store already follows. A
  permissions error on one secret must not take out the catalogue.

### §F1.19 — Decision (locked): filesystem metadata rides in reserved keys

A secret needs more than keys — a description, and the URLs it may be used on
(§F1.27). On the k8s side those are labels and annotations. A directory has no
such place, so two reserved filenames carry them:

```
nextcloud-admin/
  username
  password
  _description        # "Admin login for the homelab Nextcloud"
  _allowed_urls       # https://nextcloud.example.com  (one per line)
```

Reserved names begin with `_`, are **excluded from the key list**, and are never
bindable. The prefix is chosen because a k8s Secret key cannot begin with `_`
under its own validation rules, so nothing that arrives from a real k8s mount
can collide with one.

### §F1.20 — Decision (locked): Kubernetes is a *source*, not a dependency — verified

Dr K asked whether this could work without the k8s SDK. It can, and it was
tested from a pod rather than reasoned about.

Everything needed is already mounted or in the environment:

| What | Where |
|---|---|
| token | `/var/run/secrets/kubernetes.io/serviceaccount/token` |
| CA | `.../ca.crt` |
| namespace | `.../namespace` |
| API address | `KUBERNETES_SERVICE_HOST` / `KUBERNETES_SERVICE_PORT` |

A bearer token, a CA file and `requests` — **already a dependency** — is the
whole client. Confirmed live against this cluster's API.

Two findings from that test that change the design:

**1. The token rotates, so it must be re-read on every call.** The mount here
showed `..data -> ..2026_09_10_23_02_50`, a directory stamped hours after the
pod started: the projected token had been swapped underneath. Reading it once at
boot is the classic version of this bug, and it fails hours later with a 401 that
looks like an RBAC problem.

**2. There is no way to list key names without pulling values.** The metadata
projection works —

```
Accept: application/json;as=PartialObjectMetadataList;v=v1;g=meta.k8s.io
```

— and returns `apiVersion`, `kind`, `metadata` and **nothing else**, so it
carries no `data` and therefore no key names. Key names live only in `data`,
alongside the values.

So the honest design is: **one label-selected list call, projected to names and
key names immediately, values dropped without ever being stored, logged or
returned.** The metadata projection is not used, because the opt-in label
(§F1.21) already bounds the set and the projection cannot answer the question we
are asking. That is a real property of the API, not a shortcut — worth recording
so nobody re-litigates it.

**RBAC is a Role and a RoleBinding in the cluster repo**, granting `get` and
`list` on `secrets` and `configmaps` in one namespace. Note plainly what that
means: **the pod can read every secret in its namespace.** Kubernetes RBAC
cannot restrict `list` by label, so the label selector is hygiene, not a
boundary. If that is too much authority, the answer is a dedicated namespace,
and it is the operator's call.

### §F1.21 — Decision (locked): only Secrets and ConfigMaps, and only labelled ones

**Two kinds, ever.** No Pods, no Deployments, no CRDs, no `list` on anything
else. This is not a Kubernetes client that happens to read secrets; it is a
secret source that happens to speak to Kubernetes. If a future need argues for a
third kind, that is a different feature with a different threat model.

**And only those carrying the opt-in label:**

```yaml
metadata:
  labels:
    selenium-flow.kubed.io/expose: "true"
```

Without it, every secret in the namespace — database passwords, TLS keys,
registry credentials — would appear in a catalogue a browser agent can read the
names of. An operator opting a secret in one line at a time is the correct
default, and the noise argument alone would justify it: this cluster's `build`
namespace has 24 secrets and roughly two are things anyone would type into a
form.

### §F1.22 — Decision (locked): scoping by label, and the filesystem is global

```yaml
labels:
  selenium-flow.kubed.io/expose: "true"
  selenium-flow.kubed.io/session: research-bot     # optional
```

- **With a session label**, the secret appears only in that session's catalogue.
- **Without one**, it is visible to every session — the same "global" idea flows
  already use (§F1.2), and the same word.
- **Filesystem secrets are always global.** A directory carries no labels and
  inventing a scoping convention for it would mean two mechanisms doing one job.
  Dr K called this correctly.

### §F1.23 — Decision (locked): cache the catalogue, never the values

The catalogue — names, keys, descriptions, allowed URLs — is cached with a short
TTL, because it is read on every listing and changes rarely.

**A value is never cached.** It is read at the moment it is bound and dropped
when the keystroke is sent. That is what keeps a rotated credential from being
served from memory after it stopped being valid, and it means the process holds
a secret for the duration of one action rather than for its lifetime.

A stale catalogue is harmless by construction: the worst case is a bind that
fails with "no such secret", which is a clear error and a refresh away.

### §F1.24 — Decision (locked): no tool ever returns a value — and the honest limit

**The hard rule.** `list_secrets` returns name, keys, description, allowed URLs
and source. There is no tool that returns a value, there is no debug flag that
returns a value, and there is no admin endpoint that returns a value.

Now the part that must be said out loud, because a security feature that
overstates itself is worse than none:

> **Once a secret has been typed into a page, `execute_script` can read it
> back.** `document.querySelector('#password').value` is one call, and this
> server cannot tell that from any other script.

**A second limit, found in review and worth the same honesty.** The leash is
checked by reading the page and then typing — two operations, not one. Nothing
makes them atomic, so a *second* caller sharing the same browser session could
navigate it between the check and the keystroke, and the value would land on a
page that was never approved.

It is a narrow window and it requires an attacker who can already drive your
browser session — at which point they can navigate it anywhere regardless. A
lock would not close it either: the browser is on the Grid, and another client
holding the same session id can move it whatever this process does. So it is
recorded as a known limit rather than defended against badly, and it is an
argument for one session per caller (§F1.2) rather than for machinery here.

So the guarantee is precise and limited: **the value never passes through the
model on its way in.** It is not sealed off from a determined agent afterwards.
The mitigations that do exist are §F1.27 (a secret can only be used on URLs its
owner allowed) and the flow itself (a reviewed sequence with no model in the
loop between steps). This is the same honesty §F1.7 applies to `writeOnly`:
a marker, not encryption.

And it is never *evidence*. Three places decided whether a page was safe to
remember by comparing a URL with its scrubbed form, which is the same question
as "did the marker appear" — and a secret whose value is exactly `<hidden>`
scrubs to itself, so all three called the credential URL clean. The question is
whether the value is in the text; `flowrun.taints` asks that, and the three
callers ask it instead of inferring.

### §F1.25 — Decision (locked): `write` currently returns what it typed, and that leak must close first

The single most important implementation note in this Part, and it is in code
that already shipped:

```python
# actions.py, write()
value = element.get_attribute("value")
...
return {"value": value, **browser.page_state(driver)}
```

`write` reads the field back and returns it, deliberately — "so you can confirm
the text actually landed" — and that is a genuinely good feature for ordinary
text. **Bind a secret into it and the tool response hands the model the
password**, defeating the entire system on the very call that was meant to
protect it.

So, before any binding ships:

- When a value came from a secret, `write` returns **`"value": null`** and a
  `"value_from"` field naming the secret and key that were used.
- The read-back is not merely omitted from the response — it is not performed,
  so the value never exists in a local variable that a traceback could carry
  into a log.

This is not a caveat to document. It is the first thing E9 builds, and there is
a test for it before there is a feature.

### §F1.26 — Decision (locked): the binding is structural, and `<secret>.<key>` cannot work

The shorthand is tempting and is genuinely impossible. Kubernetes key names
routinely contain dots — **checked against this cluster**, whose own secrets
carry `tls.crt`, `tls.key`, `config.yaml` and `.dockerconfigjson`. So
`codeserver-tls.tls.crt` has no unambiguous split point, and no parsing rule
recovers one. Name and key are two fields, always.

Following §F1.7, a binding is the same structural reference everything else
uses. In a **flow step**:

```yaml
- tool: write
  params:
    css: "#password"
    value_from:
      secret: {name: nextcloud-admin, key: password}
```

and on a **direct tool call**, where there is no flow and so no `param` source
to choose between:

```python
write(css="#password", value_from={"secret": {"name": "nextcloud-admin",
                                              "key": "password"}})
```

- **The two are the same shape**, which they were not in the first two drafts:
  `value_from` is a parameter of `write` on both surfaces. It names a source,
  and which argument it fills is `write`'s own business (§F1.7).
- **Exactly one of `text` or `value_from`**, enforced at the boundary with the
  idiom `browser.locator` already uses for `xpath`/`css` — both is refused
  rather than resolved.
- The nested object is a real schema, not a blob: FastMCP derives it from a
  typed model, so a model filling it in is told the shape, and §F1.6's step
  derivation gets it for free.

### §F1.27 — Decision (locked): allowed URLs are enforced, and matched by origin

Dr K listed this as optional metadata. **It is the control that makes the rest
worth having**, and it should be enforced rather than displayed.

A secret may declare where it may be used. At bind time the browser's *current*
URL is checked against that list, and a mismatch refuses the write.

- **Matched by origin** — scheme, host and port — never by substring.
  `https://nextcloud.example.com.evil.com` must not match
  `nextcloud.example.com`, and a substring check is exactly how that gets
  through.
- **The check is on the page the browser is on**, after any `url` navigation the
  call performs, because that is where the keystroke actually lands.
- **No declaration means no restriction**, which is the pragmatic default for a
  homelab. The listing shows which secrets are unrestricted, so the gap is
  visible rather than assumed.

This is what stops an injected page talking an agent into typing the Nextcloud
admin password into a form on a site that just asked it to.

### §F1.28 — Decision (locked): only `write` binds

Not `execute_script` — a script is arbitrary code and a bindable parameter there
is a value-exfiltration API with extra steps. Not `navigate`, because a secret
in a URL lands in browser history, the referrer header, and this server's own
session record, which is stored in Redis. Not `press_key`, which has no value to
carry. Not `upload_file` yet, though a credentials file is a plausible later
case and the refusal should say "not yet" rather than "never".

Dr K's instinct that `write` is the only consumer holds up under exactly this
kind of enumeration, which is why it is written down as a list of refusals
rather than as a single yes.

### §F1.29 — Decision (locked): binding supersedes `writeOnly` parameters for real secrets

§F1.7 gave a flow `parameters` with `writeOnly: true` for values a caller
supplies but should not see echoed. That is still right for values the caller
genuinely owns.

For anything actually secret, **the binding is strictly stronger**: a
`writeOnly` parameter still has to be *supplied*, which means the model held it
and put it in a tool call. A binding is never supplied at all.

So the guidance, and it belongs in the skill: **a parameter is for what varies
between runs; a binding is for what must not be seen.** An email address is a
parameter. Its password is a binding.

### §F1.30 — Decision (locked): the use is audited, the value is not

Every bind records: the flow, the step, the secret name, the key, the URL it was
used on, and whether it was allowed. Never the value.

That is cheap, it is the record an operator wants after something goes wrong,
and the admin event stream already exists to carry it. A refused bind is the
more interesting event of the two and must be recorded loudest — it is the
signal that something tried to use a credential somewhere it should not.

Two things the implementation had to learn, both the same mistake:

**The identifiers are read off the catalogue's entry, not off the request.** The
line says which secret was *resolved*, spelled the way its source spells it,
rather than echoing the string a caller asked with. More accurate, and it also
means nothing in the audit line descends from the caller-supplied `value_from` —
which is what a scanner reads as the credential itself, and it is not wrong to.

**A rejected permission line is rebuilt, never echoed with the bad part removed.**
`_allowed_urls` refuses a line carrying userinfo or a path, and `/secrets`
publishes which line was refused so an operator can fix it. Taking the userinfo
out and printing the rest published `?token=…` — so the branch that refuses a
line *for carrying a credential* handed it straight back. What is shown is
assembled from the scheme, host and port; what went missing is named, never
quoted.

### §F1.31 — Decision (locked): the surfaces, and ConfigMaps later

- **`secret://secrets`** — the catalogue, as a resource.
- **`list_secrets`** — the mirror tool, hidden from clients that read resources,
  exactly like `session_files` and `current_session` (§F1.5).
- **`GET /secrets`** — the HTTP half, because the catalogue is a capability and
  the repo's rule admits no exceptions for capabilities.

That is the entire tool surface: **one read, and a parameter on `write`.** Dr K
called it, and it holds: nothing else ever needs to name a secret.

**ConfigMaps are the same machinery with the value visible** — same catalogue,
same label, same binding, plus a `config_map`/`config_key` pair and a listing
that may show values because there is nothing to protect. Deliberately **not in
this chapter**: the catalogue and the binding have to be right first, and
"values are visible for this kind and not that one" is the sort of branch that
should be added deliberately rather than at the same time as the thing it
branches from.

### §F1.32 — Decision (locked): the skill teaches one loop, end to end

`references/SECRETS.md`, and one row in `SKILL.md`. It has to teach the whole
arc, because no single tool description can:

1. **Discover the secret** — `list_secrets`, read the names and keys. You will
   never see a value, and you do not need one.
2. **Discover the selectors** — drive the login page by hand once, `extract` to
   find the field selectors (§F1.13 now offers `css` as well as `xpath`).
3. **Build the flow** — steps with the selectors, and a `value_from` naming the
   secret on the password step instead of a value.
4. **Save it** — `save_flow`, once.
5. **Run it** — `run_flow`, forever after, with no credential in any transcript.

Plus the rule from §F1.29 in one line — parameters for what varies, bindings for
what must not be seen — and the honest limit from §F1.24, because an agent that
believes a secret is unreadable after it has been typed will reason badly about
what it can safely do next.

---

## Part IV — The plan

**E0 first.** It is the only piece that is useful on its own, it is small, and
§F1.13 explains why the flow work needs it underneath rather than beside it.
After that, E1→E2→E3 is the spine; E4 needs E1; E5 is independent and can land
any time.

**E7→E9 is the secrets arm** (Part III), independent of the flow arm until they
meet: E7 and E8 are a catalogue nothing consumes yet, and E9 is the binding that
makes a saved login flow possible. E9 depends on E3 only because a bound secret
*in a flow* needs flows to run — the `write` parameter itself works the day it
ships.

E6 and E10 are documentation and finish last.

**One PR per epic, in this order.** Not one PR for the feature — E2 and E3 alone
touch the tool surface, the HTTP surface, the OpenAPI responses, the wiki and the
skill, and a single PR containing all of it would be unreviewable.

### E0 — Pre-flight: selectors other than XPath — **DONE** (#9)

The low-hanging fruit, independently valuable, and a prerequisite rather than a
detour — step params are *derived* from tool params (§F1.6), so a selector choice
that existed only in flows would need an exception in the one mechanism keeping a
single source of truth (§F1.13).

- [x] One locator helper in `actions.py`, replacing the hardcoded `By.XPATH`
- [x] Mutually exclusive `xpath` / `css` params on every element-addressing tool,
      exactly one required; `xpath` behaves precisely as it does today
- [x] A clear error when both or neither are given — this is the one new way to
      get a call wrong, so it must name the fix
- [x] ~~`id` as a third key~~ — **not built.** `css="#foo"` already is it, and
      the argument against the other five applies: two strategies to choose
      between is a schema, eight is a quiz.
- [x] Wiki regenerates; `SKILL.md` and `references/INTERACTION.md` gain the
      choice; `CHANGELOG.md` gets one line — this one users *do* notice

### E1 — The hangar: storage and naming — **code done**, deploy pending

- [x] `FlowStore` protocol + `local` implementation, shaped like `SessionStore`'s
      `memory`/`redis` split so `webdav` lands later without a retrofit (§F1.12)
- [x] **No path arithmetic outside the store, and no assumption reads are local**
      — the two things a WebDAV backend breaks if they are assumed (§F1.12)
- [x] YAML on disk, dicts in the API — `yaml.safe_load`/`safe_dump`, PyYAML is
      already a dependency (§F1.14)
- [x] `FLOW_DATA_DIR` env var + `--flow-data-dir` flag; unset means the feature
      is off, with a log line saying so (§F1.3)
- [x] Session-name resolution: `named:` → its own directory, everything else →
      `global`; `global` reserved (§F1.2)
- [x] One name-validation function used by every surface; reject, never slug
      (§F1.4)
- [x] Tests: traversal on both names, a name of `..`, unset-dir behaviour, all
      three unnamed key sources landing in `global`, lazy directory creation
- [ ] **Cluster repo:** `emptyDir` volume + `FLOW_DATA_DIR` in `mcp.env`, with a
      comment saying plainly that **a redeploy wipes saved flows** (§F1.12).
      **Deliberately held back until E2/E3 land** — mounting a volume for a
      feature with no tools on it deploys dead configuration, and it is a
      change to a different repository besides.

### E2 — The flight plan: the document and its CRUD — **DONE**

- [x] Step models **derived** from the FastMCP tool schemas via
      `pydantic.create_model()`, assembled into a discriminated union on `tool` —
      the move `openapi.py` already makes for request bodies (§F1.6)
- [x] Flow document: `name`, `description`, `parameters` (JSON Schema), `steps`
- [x] Step keys: `tool`, `params`, `id`, `onError`, `return`, `note`. `timeout`
      was **withdrawn** (§F1.6) — `wait_timeout` in a step's own params is the
      per-step bound — and `valueFrom` was withdrawn too: it is a *parameter*,
      `value_from`, so `params` is exactly the call's arguments (§F1.7).
- [x] `value_from` validated at **save** time: a source that is not exactly one
      of param/secret/config, an undeclared parameter, a value also given
      literally, or an action that does not offer the parameter at all — all
      refused then, not at step nine of a run (§F1.7)
- [x] `flow://flows` and `flow://flows/{name}` resources; the listing returns
      names, descriptions and `parameters` only — **never full documents**, so
      the WebDAV backend stays viable (§F1.5, §F1.12)
- [x] **Reads merge your session with `global`, your own winning on a collision;
      writes only ever touch your own** (§F1.2)
- [x] `list_flows` / `get_flow` mirror tools, into the existing `HideMirrorTools`
      set (§F1.5)
- [x] `flow://schema` — the derived document schema, published so a model gets
      the exact shape it must produce (§F1.6)
- [x] `save_flow` (create-or-update) and `delete_flow` tools
- [x] `/flows/*` HTTP endpoints, session name explicit, defaulting to `global`
- [x] **A separate `FLOW_ENDPOINTS` table and its own test.** `/flows` is a layer
      *above* `/browser`, not more of it, so `test_surfaces.py`'s `EXPECTED` set
      stays exactly as it is (question #7)
- [x] The `/flows` half of `openapi.py`, written by hand like every response
      shape, with a test holding the published path list against the one the
      server binds — the guard the multipart upload schema did not have until
      after it had already drifted
- [x] **Validate on save, not on run.** Unknown tool, unknown param, missing
      required param. Discovering at step nine that step ten was never going to
      work is the worst version of this feature.

### E3 — The clearance: running one — **DONE**

- [x] `run_flow`, dispatching every step against **one** resolved browser
      (§F1.1); its `/flows/run` endpoint.
      **Deviation from this plan, recorded:** it lives in `flowrun.py`, not
      `actions.py`. Running a flow is not a browser action — it is an
      orchestration *over* them that has to read the flow store — and putting it
      in `actions.py` would make the behaviour layer depend on the storage
      layer, which is the one direction `AGENTS.md` does not allow. It stays out
      of `ENDPOINTS` for the same reason `/flows` does (question #7).
- [x] Required-parameter check **before step one**, against the flow's JSON
      Schema (§F1.7)
- [x] Structural resolution of each step's `valueFrom` into the call's kwargs —
      **no string scanning anywhere**, so a payload can never collide with a
      reference (§F1.7)
- [x] `writeOnly: true` params kept out of the run report and the logs, and
      documented as a marker rather than encryption (§F1.7)
- [x] Result shape per §F1.8: compact by default, `return`-marked steps in full,
      `verbose` escape hatch
- [x] `onError: abort` (default) / `continue`; a failed run reports the step id,
      the step number, the error, and the URL the browser was on
- [x] An overall run timeout, so a filed plan cannot hold a Grid slot forever
- [x] `run_flow` annotated `destructive: true` — it can click anything
- [x] Tests: mid-flow failure, missing param, unknown tool, `onError: continue`,
      a `writeOnly` value absent from the report, and one asserting that a
      `${...}` payload and a `{{...}}` one both arrive byte for byte — the
      substitution test became a *no-substitution* test when §F1.7 went
      structural, which is the stronger assertion
- [x] A secret reference **refuses** rather than typing nothing, so a login flow
      cannot report success having left the password field empty (E9 fills it in)

### E4 — The hold: kept files

- [ ] `keep_file(name)` tool + endpoint — name only, type-agnostic (§F1.10)
- [ ] **One** file listing, unioned across the Grid and the store, every entry
      carrying `kept` (§F1.10)
- [ ] The listing works **after the browser is gone**, returning kept files alone
- [ ] Files produced during a run carry the **flow's name** as a tag (§F1.10)
- [ ] Name collisions: the kept file wins, `keep_file` overwrites — the same
      create-or-update rule `save_flow` uses
- [ ] A signed URL route for kept files keyed by session name, through
      `links.py` — no second signing implementation
- [ ] Admin UI: an overlay icon marking ephemeral versus kept, total kept size

### E5 — The approach plate: `ROUTE_PREFIX` goes global

Independent of everything above.

- [ ] `ROUTE_PREFIX` becomes the whole-server mount point; `/browser` fixed
      (§F1.11)
- [ ] `/health` and `/openapi.*` stay at root
- [ ] **Verify in the pod first**, not in tests: that FastMCP's `path` moves the
      MCP endpoint cleanly, and that no admin asset or `fetch` uses an absolute
      path
- [ ] `openapi.py`'s `servers:` block reflects the prefix
- [ ] **Cluster repo, same change:** delete `ROUTE_PREFIX=/browser` from
      `mcp.env`; decide whether to drop the ingress `StripPrefix` in favour of
      `ROUTE_PREFIX=/flow`, which would finally make the served paths and the
      public URL agree

### E6 — Ground school: the admin UI and the documentation

- [ ] Admin UI: every session directory listed, with its flows and its kept-file
      size, and a **delete** for a directory whose session is finished with
      (question #5)
- [ ] Admin UI: a **simple YAML editor** for one flow, and the **promote to
      `global`** action — the only way anything reaches the shared library
      (§F1.2, §F1.14). A richer editor is a later chapter.
- [ ] `skills/selenium-flow/references/FLOWS.md` + its row in `SKILL.md` (§F1.16)
- [ ] Say in the skill that **`global` is shared and readable by every session**
      — not guessable from a tool schema (§F1.2)
- [ ] `README.md` — flows in the feature list and the env var table, as
      advertisement not explanation
- [ ] `wiki/` regenerates from the spec; hand-written guidance goes in
      `wiki/notes/run_flow.notes.md` (the `.notes.md` suffix is load-bearing)
- [ ] `CHANGELOG.md` `[Unreleased]` — one short line per user-visible thing, per
      PR, or `pr.yml` fails the gate
- [ ] **Separate, last PR:** the `AGENTS.md` thinning (§F1.15)

### E7 — The pouch: secrets from the filesystem — **DONE**

The whole feature for someone with no Kubernetes, and the foundation for E8.

- [x] `secrets.py`: a `SecretSource` protocol and a `FilesystemSource`, shaped
      like `FlowStore` so a second source slots in (§F1.18)
- [x] `SECRETS_DIRS`, PATH-like and colon-separated; unset means no filesystem
      secrets, which is not an error
- [x] Directory per secret, file per key, **one level deep**; dotfiles skipped;
      an unreadable secret is absent rather than fatal (§F1.18)
- [x] First match wins across directories, and the listing names the source each
      secret came from (§F1.18)
- [x] Reserved `_description` and `_allowed_urls` keys, excluded from the key
      list and never bindable (§F1.19)
- [x] A `Catalogue` merging sources, answering "what may this session see" —
      filesystem entries are global (§F1.22)
- [x] Short-TTL cache of the catalogue; **no value is ever cached** (§F1.23)
- [x] Tests: a k8s-shaped mount read from a temp dir including the `..data`
      symlink layout, precedence across two dirs, a reserved key absent from the
      key list, one unreadable secret not taking out the catalogue

### E8 — The other airfield: secrets from Kubernetes

Optional, additive, dependency-free — §F1.20 was verified against this cluster's
API from inside a pod rather than reasoned about.

- [ ] `KubernetesSource`: bearer token + CA + `requests`, no SDK
- [ ] **Re-read the token on every call** — it rotates in the projected volume,
      and reading it once at boot fails hours later as a 401 that looks like RBAC
- [ ] Enabled by the service account being present, so it is the "hidden feature
      that works in Kubernetes" Dr K described; one env var forces it off
- [ ] `Secret` and `ConfigMap` only. Nothing else, ever (§F1.21)
- [ ] Label-selected: `selenium-flow.kubed.io/expose: "true"` required,
      `.../session` scopes to one session (§F1.21, §F1.22)
- [ ] One list call, projected to names and key names immediately, values
      dropped — the metadata projection carries no `data` and so cannot answer
      this, which is why it is not used (§F1.20)
- [ ] `_description` / `_allowed_urls` come from annotations on this side
- [ ] Tests against recorded API responses, plus one that fails if any verb or
      kind beyond get/list on secrets and configmaps is ever requested
- [ ] **Cluster repo:** Role + RoleBinding, with a comment saying plainly that
      this grants read of every secret in the namespace (§F1.20)

### E11 — Local knowledge: flows that know where they apply

- [ ] `urls` on a flow document: a list of globs, validated at save time —
      strings only, and a pattern that is not a string is refused (§F1.33)
- [ ] Matching helper: `fnmatch` over the whole URL, a path-less URL treated as
      ending in `/`, absent-or-`*` meaning everywhere
- [ ] `flow://here` resource — **not** `flow://flows/current`, which collides
      with a flow named `current` (§F1.34)
- [ ] `list_flows(everywhere=False)`, defaulting to the current page; with no
      current URL there is nothing to filter by, so show everything rather than
      nothing
- [ ] `/flows/list` takes the same flag, and a `url` override so an HTTP caller
      with no session can ask "what applies to this page"
- [ ] `flows_here` on `session://current` and `current_session` (§F1.35)
- [ ] Short-TTL cache on the flow listing, so the status resource stays cheap —
      cache the listing, never the documents
- [ ] The skill's opening move becomes "read the status; if `flows_here` is not
      empty, one of them is probably your task"
- [ ] Tests: a glob matching and not matching, `*`, absent, a path-less URL, the
      no-current-URL fallback, and one asserting `flow://here` and a flow named
      `current` do not collide
- [ ] **A test that the two URL mechanisms stay apart**: a secret's leash is
      origin-exact and fails closed, a flow's `urls` is a glob and fails open.
      They will look mergeable to somebody one day (§F1.33)

### E9 — Handing over the pouch: the binding — **DONE**

- [x] **First, and with a test before the feature: close the `write` read-back.**
      A bound write returns `"value": null` plus a `"value_from"` naming what was
      used, and does not perform the read at all (§F1.25)
- [x] A typed `ValueFrom` model — exactly one of `param`, `secret`, `config` —
      shared by the tool parameter and the flow step key (§F1.7, §F1.26)
- [x] `value_from` on `write`, mutually exclusive with `text`, refused at the
      boundary with the `browser.locator` idiom (§F1.26)
- [x] Allowed-URL enforcement, **matched by origin**, against the page the
      browser is actually on at the moment of the write (§F1.27).
      **Consequence found while building:** a step that binds a secret may not
      also carry `url`. A step that navigates first would have its leash checked
      against the page it is leaving, and a redirect would defeat even that —
      so navigation is its own step, refused at save time with that reasoning.
- [x] Refusals with reasons for `execute_script`, `navigate` and `press_key`;
      `upload_file` says "not yet" rather than "never" (§F1.28)
- [x] Audit events: flow, step, secret, key, URL, allowed — never a value, and a
      refused bind logged loudest (§F1.30)
- [x] `secret://secrets` resource, `list_secrets` mirror tool, `GET /secrets`
      (§F1.31) — **shipped with E7**: the catalogue is the half that stands on
      its own, and it is what a skill can teach before any binding exists
- [x] Tests: a bound value never appears in a tool result, a run report, a saved
      flow or a log record; an origin-suffix attack is refused; a bind on a
      disallowed URL is refused before any keystroke is sent

### E10 — Ground school II: the secrets skill

- [ ] `skills/selenium-flow/references/SECRETS.md` + its row in `SKILL.md`,
      teaching discover → selectors → build → save → run (§F1.32)
- [ ] The rule from §F1.29 in one line: parameters for what varies, bindings for
      what must not be seen
- [ ] The honest limit from §F1.24 stated plainly — a secret typed into a page
      can be read back off it, so a bound flow is not a sandbox
- [ ] `README.md` and the env var table gain `SECRETS_DIRS`
- [ ] `CHANGELOG.md` — one line, and this one users very much notice

---

## Open questions

Dr K closed seven of these on 2026-09-10. Kept with their answers, because the
answer is the useful part.

1. ~~**§F1.6 — flat or nested steps?**~~ **Closed:** nested `{tool, params}`, on
   the evidence in *Prior art* — every authored, parameterised format is nested.
2. ~~**§F1.7 — parameters in Chapter 1?**~~ **Closed, then revised.** Yes, and
   `parameters` is JSON Schema — but Dr K withdrew the `{{name}}` substitution
   on 2026-09-11 in favour of **structural `valueFrom` references**. See the
   revised §F1.7: it retires the templating question rather than answering it,
   and takes the `$`-in-JavaScript collision with it.
3. ~~**Does a named session see `global`?**~~ **Closed: yes, every session reads
   it.** Writes stay in your own session, and **promotion to `global` is an admin
   action in the UI** — which makes the shared library curated rather than a
   scratchpad. See §F1.2 for the one asymmetry it leaves.
4. ~~**Can a flow call another flow?**~~ **Closed: no, and not as a limitation.**
   Dr K: *"we just don't expose running another one — the idea is another tool
   like n8n can now compose at a higher level. This is more like filling out a
   form or clicking through a wizard."* That is the scope statement this feature
   needed: **a flow is a wizard, not a program.** Composition is a job for
   something that already does it well, and n8n can POST to `/flows/run`.
5. ~~**Does anything collect a session directory?**~~ **Closed: no reaper, an
   admin delete.** The admin UI lists every directory and an orphan gets deleted
   by a person. On `emptyDir` the question is close to moot — a new pod simply
   has no flows, and *the app should not know they ever existed*. It returns if
   the backend becomes WebDAV or NFS, and that is an installer's problem.
6. ~~**Does the admin UI get a flow editor?**~~ **Closed: a simple YAML editor
   now**, a real one eventually. This also settled the on-disk format (§F1.14).
7. ~~**Does `run_flow` go in `ENDPOINTS` or its own table?**~~ **Closed: its
   own.** Dr K: *"/flow/run — this better shows the /browser are the simple
   actions for browser and that /flow is a separate thing above it."* So
   `test_surfaces.py`'s `EXPECTED` is untouched and flows get their own contract
   test. **Naming to confirm:** the original brief said `/flows` (plural, beside
   `/files`); the answer above wrote `/flow`. E2 assumes **`/flows`** — say if
   you want the singular.
8. ~~**Locator fallback?**~~ **Closed, and reshaped into something better:** not
   fallback alternatives but a **mutually exclusive choice of selector strategy
   per step** — §F1.13, and it lands in the tools first as E0.
9. ~~**Where do files from a flow go?**~~ **Closed: into the session, exactly as
   `screenshot` and `save_pdf` already put them.** No special path. They are
   **tagged with the flow's name** so a nightly run's thirty screenshots are
   attributable.

10. **Should a session be able to *create* a secret?** Deliberately not
    proposed. Everything in Part III is read-only, and an agent that can write a
    secret can write one whose `_allowed_urls` it chose. If the need appears it
    is an admin-UI action, like promoting a flow to `global` (§F1.2), never a
    tool. Recommend **no**, and say so in the skill so it does not read as an
    oversight.
11. **Does `list_secrets` reveal too much by itself?** Names and keys are a map
    of what exists. It is already behind the server's bearer token, and the
    alternative is an agent that cannot discover what it may bind. Recommend
    accepting it, with §F1.21's opt-in label as the real control — a secret
    nobody exposed is not in the catalogue at all.
12. ~~**What about an `_allowed_urls` entry carrying a path?**~~ **Closed in
    E7, the hard way.** The recommendation here was to *refuse* such a line
    rather than trim it — and the first implementation trimmed it anyway, along
    with treating a declaration that parsed to nothing as no declaration at all.
    Review caught both. A path-carrying line is refused, one bad line
    invalidates the whole declaration, and a secret whose leash does not parse
    is usable **nowhere** rather than everywhere. The rejected lines are
    published in the listing so an operator can see why.
13. **Does `saveAs` survive the structural change?** §F1.7's Chapter 2 idea was
    a step binding its output into a variable bag for `{{...}}` to read. With no
    templating, the natural spelling is a fourth source — `valueFrom: {step:
    {id: extract-token, field: text}}` — which is *better*, because it is
    checkable at save time against the step ids in the same document. Recommend
    that shape when Chapter 2 gets there.

14. **Should `urls` ever gate a *run*, not just a listing?** §F1.33 says no,
    because a flow's first step is usually `navigate` and the browser is often
    on `about:blank` when the run starts — gating would refuse the common case.
    A softer version exists: warn when a flow declares `urls`, the current page
    matches none of them, **and** its first step does not navigate. That is a
    real smell and a cheap check. Recommend: not in Chapter 1, and only if
    running the wrong flow on the wrong page turns out to happen.

**Still genuinely open:** §F1.8 (what a run returns — a recommendation is on the
table, no objection yet) and §F1.11 (the `ROUTE_PREFIX` rollout, which has a live
deployment attached). Plus the Chapter 2 list: step-output references (#13),
locator *fallback* on top of E0's strategy choice, ConfigMaps (§F1.31), and a
real flow editor.

---

## What this chapter is not doing

Named so nobody has to ask:

- **No recording.** A "record my clicks into a flow" mode is the obvious next
  idea and it is a much bigger one — it needs the browser to report events back,
  which is a different architecture. The discovery loop in §F1.16 is the manual
  version and it works today.
- **No conditionals, loops or branching.** A flow is a straight line. The moment
  it grows an `if`, it is a programming language with no debugger and we should
  have used `execute_script`.
- **No scheduling.** Nothing here runs a flow on a timer. That is n8n's job, and
  n8n can already POST to `/flows/run`.
- **No templating, in anything, ever.** §F1.7 is structural references only. A
  flow cannot compose a string from parts; if a value needs composing, the
  caller composes it and passes it as a parameter.
- **No flow calling another flow.** Closed as question #4: a flow is a wizard,
  not a program. Compose them in something built to compose.
- **No secret writing, and no ConfigMaps.** Part III is a read-only catalogue
  and one binding. Creating a secret is question #10; ConfigMaps are designed in
  §F1.31 and deliberately deferred.
- **No sandbox.** §F1.24 is explicit: a secret typed into a page can be read
  back off it with `execute_script`. This removes the credential from the
  conversation; it does not contain it afterwards.
- **No flow calling another flow.** Closed as question #4: a flow is a wizard,
  not a program. Compose them in something built to compose — n8n can POST to
  `/flows/run`.

---

> **Next:** §F1.11 is the last fork with a deployment attached — confirm the
> `ROUTE_PREFIX` rollout order — and E1 starts. Everything else is written down.
>
> The aircraft are fine. It is the paperwork we are fixing. 🛫
