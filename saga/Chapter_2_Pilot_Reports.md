# Chapter 2 — Pilot Reports

> Flight log, **SELENIUM-FLOW**, Dispatch.
>
> Chapter 1 closed on a warning from the tower door: *one aircraft flying
> beautifully is a demo, and the second one is when you find out what you
> actually built.* Two days after the version was cut, the second one turned up.
>
> An agent nobody on this field had briefed flew a real sortie — a login, a
> collapsible sidebar, an Angular route change, a saved flow built and debugged
> and flown again — and when it landed Dr K asked it for a **PIREP**: the pilot
> report, the thing a pilot files so the next one does not fly into the same
> weather.
>
> It filed a good one. It liked the radio. It said our error messages were the
> best part of the aircraft, that the flight-plan manual taught it the whole
> model in one read, and that it wrote a correct eight-step plan on the first
> attempt.
>
> And it reported two things no test on this field could have found. It flew a
> plan that came back **8/8, status ok — on the wrong page.** And it spent a long
> stretch of the sortie building, by hand and badly, a control **the aircraft
> already had**, because nothing on the panel told it the control was there.
>
> This chapter is about reading pilot reports and fixing the cockpit, not the
> engine. The engine flies.

---

## Status: **OPEN — planning** — opened 2026-09-14

Planning only. **Nothing is built until Dr K signs the forks off**, which is
how Chapter 1 ran and why its reasoning survived the build. Sections are marked
*recommended* until answered and *locked* once they are.

**First pass, same day.** Dr K answered five of the six forks: nothing glides
unless asked (§F2.3), `press_key` is not an enum (§F2.1), and the epic order is
Dispatch's call (Part IV).

**Second pass, same day.** He closed the rest, and changed two of them. **There
is no `when`** — a flow that needs to branch is usually a flow on the wrong page,
so the answer is an `assert` step that stops the run with instructions (§F2.5,
§F2.6). **Only screenshots change**: they save by default, and every other file
keeps working exactly as it does (§F2.9). And `outline` is for the agent building
a flow, not a step inside one (§F2.8).

What this chapter was planned from:

- **The first pilot report** — an agent flying `v0.1.0` against a real
  single-page app, asked afterwards what it liked, what cost it time, and what it
  wanted. What mattered in it is in Part I, and quoted where its own words decide
  something.
- **What Chapter 1 carried forward** — E5, E8, E11, the admin UI's three open
  items, `upload_file` and kept files, and the two refusals Chapter 1 expected to
  be asked about again. Both were asked about within two days. Part III.
- **Measurements taken while planning**, against the live Grid — Part I.
- **Prior art**: Playwright MCP and `angiejones/mcp-selenium`, read for how they
  solved the same problems before anything here was invented (§F2.1, §F2.8).

---

## Part I — Exposition: what the second aircraft found

### What the pilot liked — do not regress these

- **The error messages.** *"no clickable element matched … The browser is at
  '…/tickets/add'; if that is not the page you expected, the wait is not the
  problem."* Naming the page and pre-empting the wrong conclusion saved it a
  wrong diagnosis outright. It asked for the pattern to reach every tool; §F2.8
  does that.
- **`FLOWS.md`**, which taught the whole flow model in one read — including the
  two lines (*open_session is not a step*, *a flow runs in the browser you already
  hold*) exactly where it would otherwise have gone wrong.
- **`flow://schema` derived from the real tools**, so `save_flow` validation was
  trustworthy rather than advisory.
- **The server holding the session.** It never passed a `session_id` once.
- **The invisible wait.** Every element tool waits for its element in the
  background and continues the moment it appears — no sleeps, no guessed
  durations. The whole flow leaned on it without the pilot having to think
  about it once.
- **`screenshot` → `keep_file` → a signed URL** as the way to hand an image to a
  human. §F2.9 shortens it; it does not replace it.

### A control nobody can find does not exist

The agent needed a flyout menu that opens on `:hover`. `interact` has had
`action="hover"` since `v0.0.2`, and the tool description names it. The agent
read that description and still wrote an `execute_script` that called `.click()`
on the menu's parent — which opened it, sometimes, and let it close again,
sometimes. It shipped a flow that passed twice and was quietly unreliable, and
only found `hover` after asking why. Its own diagnosis:

> *"When I plan, I plan against types. A bare string reads as an open-ended
> field, so I never went back to prose to enumerate options — I reached for the
> escape hatch instead."*

The schema says `"action": {"type": "string"}`. The valid values live only in
the description. A model scanning schemas to plan sees an open field — and a
typo like `mouseover` passes `save_flow`, the one place this server otherwise
catches everything, and fails at run time instead.

Two more things pushed it the wrong way. `execute_script` describes itself as
*"the escape hatch for anything the other tools do not cover"*, which invites
exactly the conclusion it reached. And nothing says **hover state persists
between calls** — the load-bearing fact that makes hover-then-click a usable
two-call pattern.

The server's log agrees: the one `interact` failure that afternoon is a 30s
timeout on a link inside a menu whose ancestor was `display: none`.

### Green on the wrong page

The flow's last step was `extract` on `.content-header-title`, marked
`return: true`. The click before it returns as soon as the click is delivered;
an Angular route change lands a few frames later. The previous page *also* had a
`.content-header-title`, so the extract matched it instantly, and the run
reported eight passing steps while sitting on `/tickets/add` instead of the page
it meant to reach. The agent caught it only because it happened to read the
returned text.

That is the worst class of defect this server can have: **a silent wrong
answer**. A loud failure costs a turn. A confident green sends the next ten turns
in the wrong direction. There is no primitive today that lets an author say
*what must be true* after a step, and the non-verbose run report shows one line
per step and only the final URL — so a navigation that did not happen mid-flow is
invisible.

### What else cost it time

- **Finding one selector took three hand-written DOM dumps** through
  `execute_script`: enumerate the links, walk each parent chain for
  `display:none`, find the toggle. Each returned far more than it needed. (§F2.8)
- **No drag.** Its only fallback was synthesising events in script, which
  pointer-threshold and HTML5 drag libraries both ignore. (§F2.4)
- **"Log in, unless already logged in"** written as three login steps marked
  `onError: continue` — three red steps on a successful run, and about 45s of
  timeouts on the signed-in path. (§F2.6)
- **`extract` used as a wait**, because it has `wait_timeout`. **Not a gap, and
  not changing:** every tool that addresses an element already waits for it
  through its own `wait_timeout`, which is exactly what that step relied on.
- **`screenshot(save=true)` did not return a URL**, so handing a screenshot to a
  human took a `keep_file` and a resource read. (§F2.9)
- **A declared `default` is applied, and nothing says so**; it tested that
  empirically. **`ready: false` from a scaled-to-zero Grid** read as a broken
  Grid. **`open_session()` restoring the last URL** got in the way of testing a
  login flow from a clean start. (§F2.9)

### Measured: a hover is a jump, and a click does not move the pointer

Dr K asked whether moving to an element already traces a path from where the
pointer is to where it is going, ending in a hover. **It does not.** Measured on
the live Grid (Selenium 4.49.0, Chrome) with a page that logs every
`pointermove`:

| What was sent | `pointermove` events | Wall time |
|---|---|---|
| `interact(hover)` from A (50,50) to B (630,430) | **1**, at B | — |
| one element move with `duration=800` | **1**, at B | 0.85s |
| 24 small viewport moves in one `perform()` | **24**, evenly spaced | 0.46s |
| a relative `+10,+10` move after reloading the page | lands at (60,60) | — |

And, because Dr K proposed that a click should tell us where the pointer is —
*a person has to be over a button to click it*:

| What was sent | Pointer afterwards | On a button covered by an overlay |
|---|---|---|
| `element.click()` — what `interact(click)` does today | **still at A** | refused: `ElementClickInterceptedException` |
| a pointer-action click on the element | at B | **clicks the overlay, silently** |
| either, on a button 3000px below the fold | — | both scroll to it and click it |

What falls out, and §F2.3 rests on all of it:

1. **ChromeDriver waits out a move's duration and delivers one event.** Anything
   watching intermediate movement — a sortable list, a slider, a drag library
   with a distance threshold — sees a teleport. *Firefox not yet measured.*
2. **A glide is cheap.** Many small moves in one action sequence are one round
   trip and deliver every step.
3. **The pointer's position survives navigation.** Input state belongs to the
   browser, not the page.
4. **WebDriver cannot tell you where the pointer is** — but nothing moves a
   WebDriver pointer except WebDriver, so whoever sends every move can remember.
5. **Today's click leaves the pointer wherever it was.** So "a click orients the
   pointer" is not true yet, and the obvious way to make it true — click with
   the pointer — trades the one loud refusal WebDriver gives us for a silent
   click on the wrong element.

---

## Part II — The doctrine

### §F2.1 — Decision (locked): a closed set of values lives in the schema, and an open one does not pretend to be closed

Every argument checked against a fixed list becomes a `Literal` in the tool
signature, so FastMCP emits an `enum` and `flow://schema` inherits it. The report
named three; the sweep found four:

| Tool | Argument | Validated against |
|---|---|---|
| `interact` | `action` | `MOUSE_ACTIONS` |
| `dialog` | `action` | `DIALOG_ACTIONS` |
| `frame` | `action` | `FRAME_ACTIONS` |
| `open_session` | `browser` | `BROWSERS` |

**The rule, which is Chapter 1's rule again:** the `Literal` is built *from* the
tuple the action layer validates against — `Literal[MOUSE_ACTIONS]` — so the
schema and the check cannot disagree. A test walks every tool schema and fails
if an argument validated against a module-level tuple is emitted as a bare
string. That test is what stops the next one. **The schema lists the lowercase
spelling and every surface still accepts any case.** These were plain strings the
action layer lowercased, so `Hover` worked over MCP; a bare `Literal` would have
started refusing it before the tool ran. Each choice is lowercased *before* the
enum check, and a test calls the real tools with `Hover` to keep it that way.

**`press_key.key` is the deliberate exception**, and the test names it.

*Why there were 73.* `KEYS` is every uppercase attribute of Selenium's `Keys`
class, and that class is 60 keys under 73 names: `LEFT` and `ARROW_LEFT`,
`BACK_SPACE` and `BACKSPACE`, four names for Meta (`COMMAND`, `LEFT_COMMAND`,
`META`, `LEFT_META`), three for Alt. An enum of it would advertise thirteen
duplicates as if they were choices.

*What the prior art does.* Both **Playwright MCP** and **mcp-selenium** type
`key` as a plain string and teach by example. Playwright's is the richer
contract: *"Name of the key to press or a character to generate, such as
`ArrowLeft` or `a`"* — DOM `KeyboardEvent.key` names, any single character, and
combinations like `Control+a` and `Shift+Tab`. Ours accepts none of the last
three.

*Decided.* `key` stays a string, because the set it should accept is open: any
character is a key. What changes is what it accepts and how it teaches:

- **DOM key names are accepted** beside Selenium's — `ArrowLeft`, `Enter`,
  `Escape`, `PageDown`. That is the spelling a model has seen most, from browsers
  and from Playwright.
- **A single character is a key.** `a`, `/`, `?`.
- **Combinations**, `Control+a` — held with key-down and key-up, not typed.
  Select-all and copy are ordinary tasks and today they need a script.
- The description shows the common keys and a combination; the error lists the
  60 canonical names, not 73 with their aliases.

### §F2.2 — Decision (recommended): the escape hatch points back before it accepts the job

`execute_script` is the most seductive tool in the set, and reaching for it is
almost always a sign something cheaper exists. Its description stops calling
itself the escape hatch for anything uncovered and sends the reader back first —
naming `interact`'s actions, `assert` and `outline`, and saying plainly that a
CSS `:hover` menu **cannot** be opened by script, because a synthetic event does
not set `:hover`.

Two more lines, each where an agent actually reads:

- `interact`: *"hover leaves the pointer there, so a :hover menu stays open for
  the next call."*
- `SKILL.md` gets a **capability matrix** — one row per tool, its actions and key
  arguments, on one screen. The pilot read `FLOWS.md` and never opened
  `INTERACTION.md`, *"because I didn't think I had an interaction question — I
  thought I had a 'this menu won't open' question."* A router by task misses a
  reader who has misclassified their task. A table of capabilities does not.

A `list_capabilities` tool was considered and rejected: it helps only a reader
who already suspects something is missing. The enum helps one who does not.

### §F2.3 — Decision (locked): the server remembers the pointer; moves jump unless asked to glide; a click puts the pointer where a person's would be

Dr K's idea — **the caller names only the destination; the server knows the
start and plots the path** — shaped by two of his answers: *do not glide by
default*, and *make glide an option*.

**`glide` is an argument, default `false`.** On `interact` it applies to every
gesture that moves the pointer — `click`, `double_click`, `right_click`,
`hover` — and is ignored by `scroll_to`. On `drag` it defaults to `true`, for
the reason §F2.4 gives. A jump is today's behaviour, so nothing that works today
changes; an agent asks for a glide when an interface needs one, and the
description says which interfaces those are: sliders, sortable lists, drag
thresholds, menus that track direction.

No caller ever supplies a path or coordinates. A model reasoning about pixels is
spending its expensive part on the cheap part.

**Every pointer gesture moves the pointer first.** Today a click is WebDriver's
element click, which leaves the pointer wherever it was (Part I, fact 5). It
becomes *move to the element — jump or glide — then the element click.* That
gives Dr K's property — after a click, the pointer is on what was clicked — and
keeps the element click deliberately, because it is the one that refuses a
covered target loudly; a pointer-action click would silently click the overlay.
If the move itself uncovers something that lands on the target, the element click
still refuses, and §F2.8's hint names what is in the way.

**Where the pointer is** — recorded after every move this server sends, in
viewport coordinates, with the rest of the session state, so it survives in Redis
the way the window size does. Keyed by the Grid's session id, so a stateless
caller's browser has one too. Reset on `open_session`, because a new browser
starts at (0,0). Gestures that do not move the pointer — `write`, `press_key` —
leave it alone.

**When it is not known**, a glide falls back to a jump and the result says so.
Never a guessed start: a path from the wrong origin crosses the wrong elements,
which is worse than no path.

**Where it is going** is read at the moment of the move with
`getBoundingClientRect`, never cached — scrolling moves every element under a
pointer that stays put (B's centre went from y=430 to y=130 after a 300px
scroll).

**The path** is a straight, eased line, with a step count from distance — about
one step per 20px, clamped — and a frame-length pause between steps, in one
`perform()`.

**Known limit:** a straight line is not how a person leaves a flyout. Moving
diagonally from a menu item to its submenu can cross the sibling below it. The fix
is a waypoint, and it is **not** proposed until a real site needs one; hover the
parent, hover the child, then click, already works.

**Not this:** glide exists for interfaces that need intermediate events to work.
Jitter, randomised human timing and anything aimed at looking less like
automation to a detector are out of scope, and the code says so.

### §F2.4 — Decision (recommended): `drag` is its own tool

Chapter 1 grouped five gestures into `interact` because *they take identical
arguments and differ only in what is sent.* Drag breaks that premise — it needs a
source **and** a destination — so by the same doctrine it is its own tool.
Playwright MCP reached the same shape (`browser_drag` with a start and an end
target).

```
drag(css=".card:nth-child(2)", to_css=".column.done")       # element to element
drag(css="input[type=range]", by_x=120)                       # element by an offset
```

- The source is addressed like every other element: `xpath` **or** `css`.
- The destination is **exactly one of** `to_xpath`, `to_css`, or a `by_x`/`by_y`
  offset, refused otherwise with the `browser.locator` idiom.
- Press, move, release, with a short hold after the press, because several drag
  libraries arm only after a delay or a distance.
- **`glide` defaults to `true` here.** Incremental movement is most of what a
  drag is for; the pilot's words were *"if you add only one thing here, make it
  incremental movement."* `glide=false` is there for the rare target that wants
  the jump.

**Verify in the pod before promising anything.** Pointer actions drive
*pointer-event* drag — sliders, `dnd-kit`, SortableJS's fallback, canvases — and
have a long history of **not** firing native HTML5 `dragstart`/`drop` in Chrome.
If that is still true, the description says so in one sentence, because an agent
that finds out by failing reaches for `execute_script`, which is worse at HTML5
drag, not better.

### §F2.5 — Decision (locked): `assert` — JavaScript that must come back true

The wrong-page green and the guarded login looked like two missing features.
Dr K's reading is that they are one, and it is not a conditional: **a flow needs
a way to say what must be true, and to fail with an explanation when it is not.**
Every test framework calls that `assert`, and so does this one.

**Its shape is `execute_script` with one difference: the answer must be a
boolean.** This section first proposed a vocabulary of condition keys — `url`,
`exists`, `absent`, `text`. Dr K replaced it with an expression, which is both
smaller and longer-reaching: cookies, query parameters, computed styles, counts,
storage, anything the page can be asked. One thing to learn instead of four keys
with their own semantics, and the advanced case is the same tool rather than a
new one.

```yaml
- tool: assert
  args:
    script: return location.pathname.startsWith('/extensions/helpdesk-feature-requests')
    message: The sidebar link did not open Feature Requests - check the menu still has it.
```

A failed run then reads:

```json
{"flow": "open-feature-requests", "status": "failed", "steps_run": 3,
 "steps": [{"step": 3, "tool": "assert", "ok": false,
            "error": "The sidebar link did not open Feature Requests - check the menu still has it.",
            "url": "https://app.example.com/tickets/add"}]}
```

- **Boolean only.** Anything else is refused as a mistake in the assertion,
  naming what came back. Truthiness is how an assertion passes by accident:
  `return document.querySelector('#x')` looks right and is right by luck — an
  element is truthy, `null` is falsy — until the expression returns `0`, `""` or
  `[]` and says the opposite of what its author meant.
- **It waits the way everything else waits.** The expression is evaluated, and
  if it is false it is evaluated again until it is true or `wait_timeout` passes
  — the same explicit wait every element tool already uses, so an assertion after
  a click needs no knowledge of how many frames a route change takes.
  `wait_timeout: 0` checks once. **Nothing sleeps.**
- **`message` is the author's sentence** to whoever reads the failure. The author
  knows why the condition matters; the server does not.
- **A failed assertion is an error, not a new status.** Dr K: it rides with the
  error, as the message beside it. The step fails the way any step fails — the
  run reports `failed`, the step's `error` is the author's message — and no third
  run status is invented. The `stopped` status this chapter proposed is withdrawn.
- **`${name}` substitution works**, and a JavaScript template literal still needs
  `$${` — the escape §F1.41 added exists for exactly this argument.
- **The keyword.** `assert` cannot name a Python method, so the tool and the
  route say `assert` and the method is `assert_`, through one alias in
  `routes.py` that `flowrun` and the surface test both read.

### §F2.6 — Decision (locked): no `when` — a flow owns where it starts, and stops when it is not there

Chapter 1 wrote *"No conditionals, loops or branching,"* and the first pilot
report asked for one within two days: *log in, unless already logged in*, written
as three steps marked `onError: continue` — three red steps on a successful run,
and ~45s of timeouts. Dr K holds the line, and gives the reason it should be held:

> **Conditionals open branching, and weird edge cases where we are dealing with
> an agent simply on the wrong page, trying to make the flow dynamic enough to be
> at the right place.**

A flow that copes quietly with being in the wrong place is a flow that passes on
the wrong page. So the missing thing is not control flow. It is two other things,
and both have better fixes.

**A documentation problem: nobody told the author that being in the right place
is the flow's job.** A flow can start with `navigate` — that is what makes it
runnable from anywhere — and the skill never says to. `SKILL.md` and `FLOWS.md`
gain the rule, stated once: *a flow is responsible for being where it acts. Start
with `navigate`, or start with an `assert` that stops the run with instructions
when the browser is somewhere else.* And `save_flow` nudges: a flow whose first
step neither navigates, carries a `url`, nor asserts is saved with a **warning**
in the result. A warning rather than a refusal, because some flows really are
meant to run on whatever page you hold. That answers Chapter 1's question #14
with a cheaper check than the one it proposed.

**An error-handling problem: a flow that cannot proceed had no way to say why.**
That is `assert`, and the login flow becomes a straight line again:

```yaml
steps:
- tool: navigate
  args: {url: "${site}/login"}
- tool: assert
  args:
    script: return !!document.querySelector('#login-username')
    wait_timeout: 5
    message: Already signed in — you don't need to run this flow.
- tool: write
  args: {css: "#login-username", text: "${user}"}
```

Signed out, the assertion holds and the login runs. Signed in, the app redirects,
the field never appears, and the run comes back in five seconds with one failed
step whose error is that sentence — instead of `ok` with three red steps after
forty-five.

**The hint — telling an agent how to fix what failed.** Every run that fails
carries a `hint` whose `read` is a `skill://` URI into the section of the skill
that covers that way of failing. The server already does this once — a
session error points at `SAVED_SESSIONS.md` or `STATELESS.md` — and a URI costs
nothing until it is read. The kind is decided from what actually failed:

| How the run failed | The hinted section says |
|---|---|
| an `assert`, with the author's `message` | do what the message says; the flow itself is fine |
| an `assert`, with no message | the expression, and the page it was false on; write a message |
| any other step, because an element is gone | the page changed under the flow — `outline` it, find the new selector, `save_flow` |
| anything else | the error text is the diagnosis; the usual troubleshooting |

**The hint names a prompt too — the half a person picks.** A `skill://`
resource reaches the agent, which reads it when it decides to. An MCP **prompt**
is the other primitive: a template a *person* picks in their client — Claude Code
lists them as slash commands — and fills in before the model sees anything. An
agent cannot invoke one, but it can tell a person to pick `repair_flow`, and a
person who reads the failed run in the admin UI can pick it directly. So the hint
carries both:

```json
"hint": {"read": "skill://selenium-flow/references/FLOWS.md#the-page-changed",
         "prompt": "repair_flow", "arguments": {"flow": "login", "step": "3"}}
```

The prompts follow the pattern `skills-mcp` already runs in production, rather
than a new one: **one markdown file per prompt**, YAML frontmatter declaring its
arguments with descriptions, required flags and defaults, and a body with
`{{ placeholders }}` — double braces, which matters more here than there, because
these bodies are full of flow YAML whose references are `${name}`. Loading is
strict the same way: a placeholder with no declared argument is an error, not a
blank, and a test loads every shipped prompt so a broken one fails CI instead of
vanishing at startup. They ship in the wheel beside the skill. One server, one
pack, so no pack prefix on the names.

The loader is small, and it is **copied, not imported**: `selenium-flow` does not
take a dependency on another server's package for sixty lines. The contract — the
file format, the placeholder syntax, the strictness — is what must not drift, and
a comment at the head of each loader names the other.

Two prompts, both about flows, both built on this chapter's tools:

| Prompt | Arguments | What it has the model do |
|---|---|---|
| `repair_flow` | `flow`, `step` (optional), `symptom` (optional) | read the flow and its last run, `navigate` to where the failing step acts, `outline` the page, find what moved, `save_flow` the fix, `run_flow` it again |
| `build_flow` | `task`, `url` | walk the task by hand with `outline` and the action tools, then save it as a flow that **starts where it acts** and **asserts what must be true** after every step that navigates |

A third is a candidate once someone uses the pattern for real: `flow_contract` —
write an acceptance flow for a page that does not exist yet, from a description of
what it must do (contracts, below).

**What this opens: flows as contracts.** An `assert` makes a flow a test in the
ordinary sense — it states what a page must be and fails when it is not. That
makes a real way of working possible: one agent writes a flow against a design,
with assertions, as the **acceptance contract** for a frontend; another agent
builds the frontend; `run_flow` coming back `ok` is the handover, and a failed
`assert` carrying the author's message is exactly the part the builder has not
done yet. The failing step's `error` is what makes that readable by n8n or CI.
Running a
*suite* of flows is not proposed — n8n can loop over `/flows/run` — but nothing
here should make it harder later.

### §F2.7 — Decision (recommended): the run report says where each step went

In the non-verbose report, a step's summary carries `url` **when it differs from
the previous step's**. Silence means the page did not change. The smallest
addition that makes a navigation which did not happen visible, and the cheapest
defence left when an author forgets an `assert`.

### §F2.8 — Decision (locked; the selector rule recommended): one probe for *can this element be used*, and two readers — the error and `outline`

**Yes, `outline` is a tool, and yes, it is the one that hands out selectors.**
Today an agent finds a selector by reading HTML — `extract` on a container, or an
`execute_script` DOM dump — and working one out. `outline` answers that question
directly, with the selector ready to paste and whether the element can actually
be used. `extract` goes back to its job: reading *content*.

**It is not a flow step.** Dr K: `outline` is a meta tool — for the agent
*building* a flow, repairing one after a hint sends it there, or working out the
inputs to run one. A saved flow never needs to discover its own selectors; it
already has them. So it sits outside the runnable set beside `open_session` and
`end_browser`, for a different reason: those are lifecycle, this is discovery.
It stays an HTTP endpoint, because a person building a flow from n8n needs to
discover selectors as much as an agent does.

**The prior art — Playwright MCP's `browser_snapshot`.** It returns the page's
accessibility tree as indented YAML, and gives every node a **ref**:

```
- navigation "Primary" [ref=e4]:
  - link "Deep Target Link" [ref=e17]
→ browser_click { target: "e17" }
```

Every acting tool takes a ref as its target. Refs are *"valid until the page
changes"*, and Playwright's guidance is to prefer them over selectors because they
point at exactly the element the model just saw. It also ships `browser_find` — a
text or regex search over the snapshot returning only matching nodes, because a
whole snapshot of a large page is expensive — and, behind a testing flag,
`browser_generate_locator`, which turns a ref into a durable locator for a test
file. And by default it attaches a fresh snapshot to **every** action's response,
which is most of why its token cost per task is high.

**What we take, and what we do not.**

- **Selectors, not refs.** A ref dies with the page. That is fine for a
  conversation and useless in a saved flow, which is the thing this server exists
  to make. Playwright needed a second tool to turn a ref into something durable;
  we hand out the durable thing to begin with. It also keeps Chapter 1's two
  addressing strategies at two — a `ref` would be a third on every tool.
- **A text filter, not a separate find tool.** `outline(text="Feature Requests")`
  is `browser_find`'s value for one argument.
- **On demand, never attached** to other tools' results.
- **Script, not CDP**, so it is identical on Chrome and Firefox.

```
outline(css="nav")
→ [{role: "button", name: "HelpDesk", css: "nav .menu-toggle", visible: true, expanded: false},
   {role: "link", name: "Feature Requests", xpath: "//nav//a[normalize-space()='Feature Requests']",
    visible: false, hidden_by: "ul.menu-content"}]
```

Role from the element and its ARIA attributes, name from its accessible text, one
selector, and the probe's answer. Scoped by `xpath`/`css`, filtered by `text`,
bounded by a limit, interactive elements only by default.

**One selector per element, and the key says which kind** — CSS when the element
has an id, a `data-testid` or an otherwise unique attribute; XPath by visible text
when it does not, because CSS cannot match text. Emitting both doubles the tokens
for a choice the agent rarely needs to make. Every selector is checked to match
exactly one element before it is returned.

**The probe** — one piece of JavaScript, because Chapter 1's most repeated lesson
is that a rule written twice drifts. Per element: visible or not, and if not, why
— `display:none` on a named ancestor, zero size, covered by another element (named,
from `elementFromPoint` at its centre), disabled, or outside the viewport.

**Reader one — the failure message.** When a wait for a usable element times out
and the element *exists*, the error extends the house pattern with the reason and
the next move:

> *no clickable element matched `a[href$="…"]` within 30s. It exists, but its
> ancestor `<ul class="menu-content">` is `display:none` — it may open on hover.
> Try `interact(action="hover")` on the menu first.*

Covered → name the cover. Off-screen → `scroll_to`. Disabled or zero-size → say
so. An intercepted click (§F2.3) gets the same treatment.

**Reader two — `outline`.** The same answer, asked before acting instead of after
failing.

### §F2.9 — Decision (locked): screenshots are saved by default; every other file stays exactly as it is

Dr K cannot see screenshots in the admin UI, and an agent does not always show
one, because showing it is never strictly necessary. The `save` option is why: it
makes the agent decide, per screenshot, whether a person will want to see it, and
the agent's answer is usually *no*.

**Second pass: change screenshots, and only screenshots.** The first draft
generalised this into one lifecycle for every file. Dr K pulled it back —
screenshots are what he wants to see, and downloads, PDFs and keeping all work
today — so nothing else moves:

- **`screenshot` saves by default.** `save` stays and its default becomes `true`,
  so the image lands in the session's files beside anything downloaded and shows
  up in the admin UI without the agent deciding anything. `save=false` remains for
  a caller that wants the image and no file.
- **The result carries the file entry and its signed URL**, so handing a human a
  link is one call. It still returns the image for the agent to see.
- **A save that fails does not fail the screenshot.** A page whose policy blocks
  the download still returns its image, with a note that it was not saved.
- **`save_pdf`, downloads and `keep_file` are unchanged.** Keeping a file past the
  browser is still one explicit act — by an agent when asked, or from the admin
  UI's button.

Not breaking: a flow that says `save: true` means what it always meant, and a
caller that never passed `save` now gets a file, in a session that is ephemeral
anyway. A **Changed** line in the changelog, not a **BREAKING** one.

Checked while planning, because saving on every screenshot makes it
load-bearing: Chrome's *"this site is trying to download multiple files"* prompt —
the kind of bubble §F1.37 found eating input — is already disabled in the session
preferences, and Firefox's equivalent too.

**The rest of the small things:**

- **`FLOWS.md` says a declared `default` is applied.** It has been since #23.
- **`ready: false` under KEDA** — a line in `TROUBLESHOOTING.md` and in
  `open_session`'s error path: a scaled-to-zero Grid reports no nodes at idle and
  scales on request.
- **`open_session(fresh=true)`** skips restoring the previous URL, for testing a
  login flow from a known start. Auth state already resets; only the URL carries.
- **`upload_file` takes a kept file by name** — carried from §F1.41.

---

## Part III — Carried from Chapter 1

| Item | From | This chapter |
|---|---|---|
| **E5** — `ROUTE_PREFIX` becomes the global mount | §F1.11 | Still owed, still independent. Slot it wherever a PR is quiet. |
| **E8** — secrets from Kubernetes | §F1.20–22 | Deferred. The filesystem source serves the one install. |
| **E11** — flows that know where they apply | §F1.33–35 | **In part**: §F2.5's `url` condition uses E11's glob matcher, so the matcher lands in E14 and the rest of E11 follows cheaply. |
| The detached-browser state in the admin UI | §F1.36 | E17. |
| An accessibility pass over the admin page | Chapter 1 close | E17. |
| The YAML editor's blind PUT → `If-Match`, 409 | §F1.40 | E17 — it has a server half. |
| `upload_file` has no handle on a kept file | §F1.41 | E16. |
| A step reading another step's output | Q#13 | **Still refused.** |
| Control flow | "not doing" | **Still refused.** No `when`; an `assert` stops the run instead (§F2.6). |
| Locator *fallback* on top of the strategy choice | Q#8, §F1.13 | Deferred; `outline`'s checked selectors should need less of it. |
| ConfigMaps as a secret source | §F1.31 | Deferred with E8. |
| A real flow editor | Q#6 | Deferred. |
| Warn when a flow's `urls` match nothing and it does not navigate | Q#14 | **Answered more cheaply**: `save_flow` warns when a flow neither navigates, carries a `url`, nor asserts first (§F2.6). |

---

## Part IV — The plan

Epics continue Chapter 1's numbering, so `E5` means one thing in both chapters.
**One PR per epic.** And per §F1.39, the last box of every epic is the same: *an
agent has flown it against the live Grid on a real page* — not the suite passing.

**The order, Dispatch's call:** E12 → E14 → E16 → E15 → E13 → E17, with E5
anywhere.

- **E12 first** — cheapest, and it alone would have prevented the hover detour.
- **E14 second** — silent wrong answers are the worst class this server has.
- **E16 third** — small, and it is the one Dr K feels every day in the admin UI.
- **E15 before E13** — the pointer epic changes how every click moves, and the
  probe is what turns that change's failures into readable ones.

### E12 — The checklist: capabilities an agent can find

- [x] `Literal` enums built from the validating tuples: `interact.action`,
      `dialog.action`, `frame.action`, `open_session.browser` (§F2.1)
- [x] A test that fails if any tuple-validated argument is a bare `string` — in the
      MCP schema **and** `flow://schema` — with `press_key.key` its one named
      exemption
- [x] `save_flow` refuses `action: mouseover` at save time, listing the valid ones
- [x] Python 3.10: `Literal[TUPLE]` subscripted with a tuple, and the emitted
      schema asserted, not assumed — green on 3.10 through 3.14, from a full
      matrix dispatched on the branch, since a pull request only runs 3.14
- [x] `press_key`: DOM key names, single characters, `Control+a` combinations; the
      error lists 60 canonical names
- [x] `execute_script` points back; `interact` says hover persists (§F2.2)
- [x] Capability matrix in `SKILL.md`
- [x] `FLOWS.md` documents `default`; `TROUBLESHOOTING.md` documents KEDA's
      `ready: false` (§F2.9)
- [x] The wiki regenerates from the new descriptions
- [ ] Flown: a fresh agent, given only the tools, asked to open a hover menu

### E14 — Cross-check: `assert`, and failures that say what to do

- [x] `assert` tool + `POST /browser/assert`, taking `script`, `message` and the
      ordinary `wait_timeout` (§F2.5)
- [x] Boolean only — anything else refused, naming what came back
- [x] Evaluated again until true or the timeout passes; nothing sleeps
- [x] The keyword alias in one place — `assert` on both surfaces, `assert_` as the
      method — read by `flowrun` and by the surface test
- [x] A failed assertion is a 400, and the step's `error` is the author's message
- [ ] A `hint` on every failed run, its `read` URI chosen from what failed (§F2.6)
- [x] `SKILL.md` and `FLOWS.md`: *a flow owns where it starts*; `assert`, its
      boolean rule and the `$${` escape; the login guard
- [ ] `FLOWS.md`: flows as contracts, once someone has used one that way
- [ ] `save_flow` warns when the first step neither navigates, carries a `url`,
      nor asserts
- [ ] Per-step `url` in the non-verbose report when it changes (§F2.7)
- [x] Tests through `run()`, not the helper: the wrong-page flow now fails with
      its message, the signed-in login flow fails inside its `wait_timeout`, and
      breaking the assertion on purpose turns both red
- [ ] Flown: a route-changing flow on a real single-page app, and the login flow
      on both paths. The action itself has been driven against the live Grid —
      true, false with and without a message, a non-boolean, and an element that
      appears 2.5s late, which it waited 2.4s for

### E16 — Ground handling: screenshots, and two file odds and ends

- [ ] `screenshot(save=...)` defaults to `true`; the result carries the file entry
      and signed URL (§F2.9)
- [ ] A failed save returns the image with a note, never an error
- [ ] **Changed:** changelog line — screenshots now appear in the session's files
- [ ] `upload_file` accepts a kept file by name
- [ ] `open_session(fresh=true)`
- [ ] Flown: screenshots from a flow appear in the admin UI without anyone asking,
      and one is kept from there

### E15 — The sectional chart: the probe, its hints, and `outline`

- [ ] One usability probe in JavaScript, identical on both browsers (§F2.8)
- [ ] Timeouts on an *existing* element carry the reason and the next move: hover,
      `scroll_to`, cover named, disabled, zero-size
- [ ] `outline` tool + endpoint: scoped, `text`-filtered, limited, interactive-only
      by default
- [ ] `outline` outside the runnable set — `save_flow` refuses it as a step and
      says what it is for
- [ ] Prompts from markdown files, the `skills-mcp` format: frontmatter arguments,
      `{{ placeholders }}`, strict loading, a test that loads every shipped one
- [ ] `repair_flow` and `build_flow`, and the run hint gains `prompt` and
      `arguments` (§F2.6)
- [ ] Flown: a flow broken on purpose — a renamed button — repaired from
      `/repair_flow` alone
- [ ] Selectors: CSS where a unique attribute exists, XPath by text otherwise, each
      verified to match exactly one element
- [ ] Tests on fixture pages for each reason, and one asserting the error and
      `outline` agree about the same element, so the two readers cannot drift
- [ ] Flown: find and open a hover-menu link with `outline` alone, no
      `execute_script`

### E13 — Stick and rudder: the pointer

- [ ] **Spike first:** the Part I measurements on **Firefox**, and HTML5 native
      drag vs pointer-event drag in Chrome
- [ ] Pointer position recorded per Grid session after every move, reset on
      `open_session`, carried in the session store (§F2.3)
- [ ] Every pointer gesture moves first, then acts; `click` keeps WebDriver's
      element click so a covered target is still refused
- [ ] `glide` on `interact`, default `false`; unknown start → a jump, and the
      result says so
- [ ] Destination from `getBoundingClientRect` at move time, correct after a scroll
- [ ] `drag` tool + `POST /browser/drag`, element-to-element and by offset, `glide`
      default `true` (§F2.4)
- [ ] A test page logging `pointermove`: a glide delivers many events, a jump one;
      after a click the pointer is on the clicked element; a covered click is
      still refused
- [ ] Flown: a sortable list and a range slider on real pages

### E17 — The tower: the admin UI's carried items

- [ ] The detached-browser state, designed in Penpot first (§F1.36)
- [ ] Keyboard reaches the flow list and its rows
- [ ] `If-Match` on flow saves against `flows.revision`, 409 on conflict, and the
      editor says what happened

### E5 — The approach plate (carried, unchanged)

As written in Chapter 1. Independent.

---

## Open questions

1. ~~**Does `click` glide?**~~ **Closed: nothing glides unless asked.** `glide` is
   an option, default `false` except on `drag`. And Dr K's addition: a click
   leaves the pointer on what it clicked — built as *move, then element click*, so
   an overlay is still refused (§F2.3).
2. ~~**`when`?**~~ **Closed: no.** A conditional is usually an agent on the wrong
   page. A flow owns where it starts, and `assert` stops a run with the author's
   instructions and a hint (§F2.5, §F2.6).
3. ~~**Does saving imply keeping?**~~ **Closed, and narrowed:** only screenshots
   change — `save` defaults to `true`. Downloads, PDFs and `keep_file` are
   untouched (§F2.9).
4. ~~**`outline`?**~~ **Closed: yes — a discovery tool, not a flow step.** Still
   recommended for the build: one selector per element, CSS when a unique
   attribute exists, XPath by text otherwise, each verified unique (§F2.8).
5. ~~**Epic order.**~~ **Closed: Dispatch's call** — E12 → E14 → E16 → E15 → E13 →
   E17, E5 anywhere.
6. ~~**Is `press_key.key` an enum?**~~ **Closed: no.** 73 names are 60 keys and 13
   aliases, and any character is a key. It stays a string, as in Playwright MCP
   and mcp-selenium, and learns DOM names, characters and combinations (§F2.1).

**Raised by the second pass:**

7. ~~**`assert` or `test`?**~~ **Closed: `assert`**, taking a JavaScript
   expression that must return a boolean (§F2.5). The keyword is why the tool and
   the route say `assert` while the method is `assert_`, through one alias.
8. ~~**A third run status?**~~ **Closed: no.** A failed assertion is an error
   carrying the author's message. `failed` already says the run did not finish,
   and the failing step's `error` says why.
10. **Does `assert` also take `xpath`/`css` as shorthand for "this exists"?**
   Recommend **not yet**: `return !!document.querySelector('#x')` covers it, and a
   second way to say one thing is how two mechanisms start drifting. Revisit if
   authors keep writing that line.
9. **Which prompts ship first?** Recommend **`repair_flow` and `build_flow`**, with
   `flow_contract` held until the contract way of working has been tried once
   (§F2.6).

## What this chapter is not doing

- **No coordinates from the caller**, except a drag's offset from an element. The
  server does the geometry.
- **No refs.** Two addressing strategies, and both survive into a saved flow.
- **No detection evasion.** Glide is for UIs that need movement to work.
- **No `when`, no `else`, no loops, no step outputs feeding later steps.** A flow
  is a straight line that can stop (§F2.6).
- **No recording.** Still a different architecture.
- **No CDP.** WebDriver plus script, so both browsers behave alike.
- **No vision.** `outline` reads the DOM; nobody infers clicks from a screenshot.

---

> **Dr K, reading the PIREP on the tower stairs:** *"The pilot didn't complain
> about the engine. It complained that the switch it needed was on the panel and
> it couldn't see it, and that the instruments said 'on course' while it was over
> the wrong field. Those are cockpit problems. Fix the cockpit — and then hand the
> next pilot a clean aircraft and see what their report says."*

---

Sources / cross-links:
- [Chapter 1 — The Flight Plan](Chapter_1_The_Flight_Plan.md), especially §F1.13
  (selector strategy), §F1.33–35 (E11), §F1.37 (prompts that eat input), §F1.39
  and §F1.41 (what flying taught).
- [Playwright MCP](https://github.com/microsoft/playwright-mcp) and its
  [snapshots guide](https://playwright.dev/mcp/snapshots) — `browser_snapshot`,
  refs, `browser_find`, `browser_generate_locator`, `browser_press_key`.
- `kubed-io/skills-mcp` — `prompts.py` and `prompts/`, the prompt file format and
  loader §F2.6 copies.
- [mcp-selenium](https://github.com/angiejones/mcp-selenium) — `interact` with a
  string action, `press_key` with a string key.
- Pointer and click measurements: 2026-09-14, the live Grid, Selenium 4.49.0,
  Chrome, `data:` pages logging `pointermove` and clicks.
