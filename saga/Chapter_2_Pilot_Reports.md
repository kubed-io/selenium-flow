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

## Status: **OPEN — E17 left** — opened 2026-09-14, built 2026-09-14/15

It opened as planning only — nothing built until Dr K signed the forks off,
which is how Chapter 1 ran and why its reasoning survived the build. The forks
were answered the same day and four epics shipped behind them. Sections are
marked *recommended* until answered and *locked* once they are; the passes below
say who answered what, §F2.10 says what flying it afterwards proved wrong, and
§F2.11 is the measuring §F2.4 asked for before anything was promised.

**First pass, same day.** Dr K answered five of the six forks: nothing glides
unless asked (§F2.3), `press_key` is not an enum (§F2.1), and the epic order is
Dispatch's call (Part IV).

**Second pass, same day.** He closed the rest, and changed two of them. **There
is no `when`** — a flow that needs to branch is usually a flow on the wrong page,
so the answer is an `assert` step that stops the run with instructions (§F2.5,
§F2.6). **Only screenshots change**: they save by default, and every other file
keeps working exactly as it does (§F2.9). And `outline` is for the agent building
a flow, not a step inside one (§F2.8).

**Built, same day.** E12, E14, E15 and E16 shipped in five pull requests — `#25`
through `#30` — and the server was flown afterwards by two pilots, whose findings
are §F2.10 and are folded into the plan below.

**Built, the next day.** One pull request closed everything that was left except
E17 and E5: §F2.10's three faults, E16's two leftovers, and **E13 whole** — the
spike (§F2.11), the remembered pointer, `glide`, the nudge, and `drag`. The plan
below is ticked against it.

**Built since, in one breaking change.** E18, E19 and E20 landed together
because each one breaks the published contract and a caller should migrate once:
every session is named by its caller and no browser id is in the contract
(§F2.12), the HTTP surface is REST with paths declared per capability (§F2.13),
and `xpath` and `css` are one `selector` (§F2.14). The four questions §F2.12
held open were answered in the course of it and are recorded there.

**What is left.** One epic, the cluster half of E5, and a release.

1. **E17 — the admin UI's carried items.** Wants a Penpot pass before any code.
2. ~~**E5 — `ROUTE_PREFIX` as the global mount.**~~ **Built in #35**, but for
   its cluster-repo half, which has to ship with the release that carries it.
3. **The callers of the old contract.** The n8n workflows in this cluster and
   the flows on its NFS share both break the moment this ships — they are live
   data in another repo's deployment, not files in this one.
4. **A release, and it is the one that matters.** The last tag is still `v0.1.0`
   and everything since is only in `:latest`. This is a breaking change, so the
   version is what tells a caller which contract it is holding.

Three smaller things are deliberately still open, each recorded on its own line
in Part IV rather than hidden here: a flow used as a contract in `FLOWS.md`
(waiting on somebody using one that way), a kept file exercised from the admin
UI, and a `drag` flown against a sortable list on a real site rather than a
synthetic one.

**And a release.** Everything above is on `main` and in `:latest`; the last tag
is still `v0.1.0`, so nothing shipped today is in a version anybody can pin. The
next publish is a minor.

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
   with a distance threshold — sees a teleport. *Firefox measured later, in
   §F2.11: it does not behave this way.*
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
pointer that stays put (B's center went from y=430 to y=130 after a 300px
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
  knows why the condition matters; the server does not. Without one the failure
  names the page and asks for a message — **it does not quote the script**,
  which is author text that can carry a literal a report must not repeat, and
  which `SAFE_IN_SUMMARY` already keeps out of run summaries.
- **An assertion cannot be continued past.** `onError: continue` on an `assert`
  is refused when the flow is saved, and refused again when a hand-edited one
  runs — a run that carries on after a false assertion reports a success it did
  not earn, which is the whole failure this action exists to stop.
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
from `elementFromPoint` at its center), disabled, or outside the viewport.

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
- **`keep_file` is unchanged**, and keeping a file past the browser is still one
  explicit act — by an agent when asked, or from the admin UI's button.
- **The stored file's shape changed, on purpose, and `save_pdf` shares it.** An
  action that stores something used to return the Grid's own listing entry:
  `name`, `size`, `creationTime` and nothing to open. It now returns the
  descriptor every other file surface already used — `created` rather than
  `creationTime`, plus `content_type`, `image`, `kept`, `url` and
  `absolute_url`. One shape for a file everywhere, rather than the raw Grid
  entry in two places and the descriptor in the rest. It is a **breaking**
  change to `screenshot(save=true)` and `save_pdf`, which shipped in `v0.1.0`,
  and the changelog says so.

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

### §F2.12 — Decision (Dr K's): one session, always named, and no ids in the contract

Raised while reviewing #31, on the back of a finding about kept files: an HTTP
caller had no way to name the library it had just kept a file into. #31 took the
small fix — an explicit `session` argument, matching what `/files/*` already
takes. **Dr K's point is that the small fix keeps paying rent on a distinction
that has stopped making sense**, and over two passes his answer got simpler
rather than more elaborate. What follows is the simpler one.

**The rule.** One kind of session. It always has a **name**, and the **caller
supplies it** — this server never generates one and has no opinion about what a
good name looks like. `session_id` disappears from every tool, every request
body and every result. **The Grid's browser id is never exposed**, because
nothing outside needs it: it is already attached to the named session in the
background.

**What that is instead of.** Today there are two contracts. A caller with a key
is in *saved* mode and must not pass `session_id`; a caller without one is in
*stateless* mode and must pass it on every call. That split is described
everywhere as MCP-versus-HTTP, and it is not really about the transport at all —
the flow **library** already resolves the same way on both surfaces. Only the
**browser** does not.

**What it deletes.** This is the reason to do it, and it is a long list:

| Goes away | Why it existed |
|---|---|
| `resolve`'s two-mode branch | one contract per mode |
| `mode()`, and `describe`'s `guidance` and `pass_session_id` | telling a caller which contract it is under |
| `stateless_key()` and `store_key`'s special case | a keyless caller needed a key invented for it |
| **`resources.ShapeSessionId`**, and `test_shaping_does_not_leak_between_clients` | rewriting the advertised schema per request, because `session_id` was required in one mode and refused in the other |
| one of `STATELESS.md` / `SAVED_SESSIONS.md` | two ways of working, two references |
| `routes.py`'s separate session world | the HTTP surface never touched `SessionManager` |
| `session_id` on ~20 tool schemas and every HTTP body | the caller's handle on a browser |

An entire middleware and a whole reference disappear. The awkward pair of
paragraphs in `AGENTS.md` — "a client owns one session" and "a browser opened
through the HTTP surface gets no record" — collapse into one sentence.

**Durability is Redis, and in-memory is not meant to survive.** Worth stating
because it is a real change and it was raised as an objection: *today* a
stateless HTTP caller survives a pod restart, because the browser id is in its
own hand and the server holds nothing. Under this rule the name is the only
handle and the **store** is what resolves it, so a restart with an in-memory
store loses the mapping and orphans the browser on the Grid until it is reaped.
Dr K's answer: **that is what Redis is for.** A deployment that wants sessions
to survive a restart configures one; in-memory is a single-replica convenience
and always was.

*Measured, and deliberately not the chosen answer:* a browser **can** carry its
own session name, because a Selenium session takes arbitrary metadata. Verified
on the live Grid:

- A capability set at creation — `se:flowSession`, or any key with a colon in
  it — comes back from the hub's `/status` under
  `nodes[].slots[].session.capabilities`, beside `se:vnc` and `se:containerName`
  which `Grid.sessions()` already reads.
- An **unprefixed** key is refused outright: W3C requires a colon in an
  extension capability.
- A **reconnect sees none of it.** `ReattachDriver` skips `start_session`, so
  `driver.capabilities` is empty — zero entries. Only the session that opened
  the browser holds them. So the shape is *write at open, read from the hub*,
  which is one call for every browser at once and nothing at all on the
  per-call path.

So the name→browser mapping could be rebuilt by asking the Grid rather than the
store, and a restart would survive with no Redis. That is **not** proposed:
Redis is the answer to durability and it is the simpler one. It is recorded so
nobody rediscovers it as a surprise — and because it is independently useful
somewhere else. `grid://sessions` today lists browsers this server has no record
of and labels them as somebody else's; a name on the browser itself would let
the admin list and the Grid console agree about who owns what without consulting
the store.

**What breaks, and it is a published contract.**

1. **`open_session` stops returning a Grid id.** Every HTTP caller today keeps
   that id and passes it back. They would name themselves once instead — a URL
   or a header change, not a code change, but a change.
2. **A raw Grid id from somewhere else stops working.** `AGENTS.md` currently
   promises such an id is "taken on trust". That promise goes.
3. **`SAVED_SESSIONS=off` stops meaning anything**, since every session is a
   record. The switch has to go or change meaning.
4. **The admin UI correlates on the browser id** and would keep doing so
   internally, but what it *shows* a person becomes the name.

**All four answered, in the course of building it.** They are kept with their
answers because each one shaped the result. Q5 below was asked and answered in
the same breath and is kept for its reasoning, not as a question (Copilot, #32).

1. ~~**Does the MCP transport id still count as a name?**~~ **Answered: no.**
   Dr K's rule was to drop the generated names entirely, and a transport id is a
   generated name by another route. Every caller names itself; one that names
   nothing is refused with a message saying how. It does refuse clients that
   worked before, which is the price of there being one rule.
2. ~~**What does `open_session` answer with?**~~ **Answered: the name and the
   settings.** The Grid's id is dropped on the way out rather than never
   fetched — it is how the browser is reached, and not part of what a caller is
   told.
3. ~~**Is there a migration release?**~~ **Answered: a clean break.** A release
   that accepts `session_id` and warns is a branch, and removing branches was
   the point. The package is `0.x`, the callers are countable, and they migrate
   once — with E19 and E20 — rather than three times.
4. ~~**Two callers, one name, is sharing.**~~ **Answered: documented, not
   enforced.** It is true today and deliberate (§F1.2), and mandatory naming
   makes it easier to do by accident. That sentence is now in `AGENTS.md`, the
   README, the wiki and the skill rather than only here. Enforcing it needs a
   credential per session, which is its own piece of work.
5. ~~**Could the Grid replace the store outright, and Redis with it?**~~
   **Closed: no — half of it can, and the half that cannot is the half that
   matters.** Dr K's question, and the answer is *half*. **Capabilities are write-once**: W3C
   negotiates them at session creation and there is no update command. So the
   Grid can hold what never changes — the name, and the settings it opened
   with — and cannot hold the two things that do: the page the browser is on,
   which changes after every action, and the window size after a `resize`.

   And the deeper catch: while the browser is alive nothing needs a store,
   because the browser can be asked where it is. The record exists for **after
   the Grid reaps it** — and the capability dies with the browser, so at the
   one moment the last page is needed, both are gone. Losing that means a
   caller returning after an idle timeout gets a fresh browser on `about:blank`
   rather than the page it was on, which is the §F1 behaviour that made a reap
   invisible.

   A file-backed store was considered as the third option — `FLOW_DATA_DIR` is
   already durable and, in this deployment, already shared between replicas —
   and **recommended against**. Three reasons, in order of weight: `touch` runs
   on every browser action, which is a small keyed write with a sliding TTL and
   is precisely what Redis is for and what a network filesystem is not; TTL
   expiry would become a sweep this package owns, and `AGENTS.md` says in as
   many words not to add a scheduler; and two replicas writing one record over
   NFS needs locking or accepts last-write-wins. Redis is also not a dependency
   being *added* — it is an optional extra, the default is memory, and this
   cluster already runs one.

   **So: keep the store, and add the capability anyway.** Not as a consolation
   — it closes things the store cannot:

   - **The admin view reconciles with the Grid.** `grid://sessions` lists
     browsers this server has no record of and labels them as somebody else's.
     A name on the browser says whose they are, including ones opened by
     another replica.
   - **Orphans become recoverable.** After a store loss, browsers are stranded
     on the Grid with no way back. A name makes them findable again: the last
     page is gone, the browser is not. That makes memory mode markedly less bad
     without pretending to be Redis.
   - **The Grid console becomes readable** — `se:flowSession: helpdesk-duplo`
     rather than a hex id.

   It costs one `set_capability` in `Grid._options()`, at creation, with no
   ongoing cost. The store stays the system of record for what must outlive the
   browser; the capability is the browser saying who it belongs to.

**Not decided here.** This is Dr K's design, recorded so the next chapter starts
from it rather than from a review thread. It is a breaking contract change, it
touches the admin UI, the skill references, the middleware and the published
OpenAPI, and it wants its own chapter.

### §F2.13 — Decision (Dr K's): the HTTP surface is RESTful, and the session is who is calling

**What is true today.** Every browser action is `POST /browser/<action>` with
`session_id` in the body, and every flow verb is `POST /flows/<verb>` with the
library in the body: `get_flow` is `POST /flows/get`. The routes are generated
from the tool list, and a tool is a verb with arguments, so the HTTP surface
came out as RPC wearing URLs.

**Dr K's reading:** generating both surfaces from one list backfired on exactly
one axis. A tool and an endpoint should share *behaviour and schemas* — what
`actions.py` does, what a request body may contain, what comes back. They should
not share *shape*. A tool is `verb(args)`; a resource is a path plus a method,
and deriving the second from the first is what made `GET /flows/{name}` into
`POST /flows/get`.

**The rule.** Bodies and results stay derived from the tool schemas, so the two
surfaces still cannot drift in what they accept. The path and the method are
**declared**, one row per capability, in an explicit route table — and the
existing test that every tool has a route keeps holding against that table.

**The session is not in the path.** It was proposed there first, as
`/sessions/{name}/…`, and Dr K turned it down. A session is **who is calling**,
closer to a user than to a folder: it is the boundary of what a caller can see,
not a namespace a request picks. A path segment is chosen per request, so a
series of calls from one caller could land in three sessions by editing a URL,
and an implementor has nothing to hold the line with. A header is set once on
the client — exactly as the MCP URL names a session once, for the connection —
so one caller is one session by construction, and HTTP resolves a session the
same way `/mcp` does (E18's own goal).

Stated in its limits: with one bearer token, a header stops **accidents**, not
**attackers** — any caller can send any name. Real enforcement is a credential
per session, which is §F2.12 Q4 and not this.

- `X-Session-Key` header, else `?session=<name>`. The header is the normal case.
- **Both present is a 400**, not a precedence contest. A caller sending both has
  two ideas about who it is, and quietly picking one hides that.
- For the flow library, **no session means `global`** — the default the lookup
  cascade already falls back to, said out loud.
- For anything touching a browser, no session is a 400. There is no shared
  browser.

**The one place a session is in a path is the admin page**, and that is the
same rule seen from the other side: the token holder looking *across*
boundaries is the only role that addresses sessions as resources. So
`/admin/sessions/{key}/…` stays, and nothing else grows a session segment.

**The shape, as proposed** (session from the header throughout):

| Today | Becomes |
|---|---|
| `POST /flows/list` | `GET /flows` |
| `POST /flows/get` | `GET /flows/{name}` |
| `POST /flows/save` | `PUT /flows/{name}` — create-or-replace is what PUT means |
| `POST /flows/delete` | `DELETE /flows/{name}` |
| `POST /flows/run` | `POST /flows/{name}/runs` — a run is created, not fetched |
| `POST /flows/schema` | `GET /schemas/flow` — not under `/flows`, where `schema` would be a flow name |
| `POST /browser/open_session` | `POST /browser` — this session's browser, opened or picked up |
| `POST /browser/end_browser` | `DELETE /browser` — the session survives its browser |
| `session://current` | `GET /browser` |
| `POST /browser/interact` + `action` | `POST /browser/interact/{action}` — `/browser/interact/click` |
| `POST /browser/<action>` | `POST /browser/<action>`, with the session out of the body |

**Browser actions stay commands**, and that is deliberate. `click` and
`press_key` are not resources, and inventing one for each is worse REST than an
honest command on the one resource they act on — *this caller's browser*.

**Links.** A `PUT` or a created run answers with `Location`, and results carry a
small `links` object — a run report links its flow, a file its signed URL, which
it already has. HATEOAS as far as it helps a caller follow one link, and no
further: no HAL, no media-type negotiation.

**Why with E18 and not before it.** Both break the published contract, and every
HTTP caller — the n8n workflows in this cluster first — migrates once rather
than twice. Taking `session_id` out of every body is most of what E18 asks of
this surface anyway.

**Open, Dr K's:**

1. ~~**One tree or two for a session's flows?**~~ **Closed: one.** `GET
   /flows/{name}` with the session from the header; a session is never a path
   segment outside the admin page.
2. ~~**Does `outline`/`extract` become a `GET`?**~~ **Closed: no — every browser
   action is a POST, reads included.** Dr K's reason is the better one: the body
   of a request is then the same object as a tool's arguments and a flow step's
   `args`, one shape in three places. `extract` in particular takes enough
   options that a query string would be a worse home for them, and a GET that
   waits thirty seconds surprises caches and proxies besides.

### §F2.14 — Decision (Dr K's, recommended): `xpath` and `css` become one `selector`

**Proposed:** `interact(selector={"css": "button.go"})` instead of
`interact(css="button.go")`. A `Selector` model with two fields, exactly one of
which is given.

**Measured, on FastMCP 4.0.3,** before recommending it:

- The schema a client lists is **fully inlined** — no `$defs`, no `$ref` — so a
  client that cannot follow references sees a plain nested object.
- "Exactly one" as a model validator is refused with a message naming both
  keys, the same rule `browser.locator` gives today.
- A selector sent as a **JSON string** (`selector='{"css": "#a"}'`) is refused
  as "not a valid dictionary". Some clients stringify object arguments, so this
  needs the `BeforeValidator` coercion `tools.py` already applies to its enums.

**For it:**

- **They always mean one thing.** Every tool that takes one takes the other,
  for the same element, with the same rule. Today that rule is written out in
  about a dozen descriptions; it becomes one description on one model.
- **`drag` is where it pays most.** `xpath`/`css`/`to_xpath`/`to_css` become
  `selector` and `to`, and `to` is plainly the same kind of thing as `selector`
  — the `to_` prefix was a flat schema doing the work of a type.
- **`outline` can hand back what a step takes.** Its result can carry
  `selector: {"css": …}`, pasted into a step unchanged.

**Against, and why it does not decide it:** to a model, one level of nesting is
a small cost and not zero — flat arguments are the easiest shape there is. A
one-level object is well within what current models fill reliably, and the
grouping says *these two are one choice*, which is worth about what it costs.

**Where it does not fit:** `frame` also takes `index`, which is not a selector.
It stays beside `selector` rather than inside it.

**Mutually exclusive, or combined?** Dr K asked. There are three readings of
"both":

1. **Or — try css, fall back to xpath.** Refused. It is the self-healing locator,
   and `locator()` already says why not: a typo in one becomes a click on the
   element the other found, and a flow that silently used its fallback reports
   `ok` while it drifts. A broken selector should fail loudly and go to
   `repair_flow`.
2. **And — both must match the same element.** Refused. Twice the lookups to
   state one thing twice, and when they disagree the error has no good answer.
3. **Within — css finds a container, xpath searches inside it.** The one reading
   with real value, and it is still not two keys on one element: a single XPath
   already says `//form[@id='signup']//button[.='Save']`. If scoping ever earns
   a place it is a nested `within` selector.

**So exclusive.** The model validator keeps the rule `locator()` has today.

**It breaks every saved flow**, since a step's `args` are exactly a tool's
arguments. That is why it belongs in the same release as E18 and E19: one
migration for flows on disk, n8n workflows and HTTP bodies, not three.

### §F2.11 — Measured: Firefox interpolates, and a pointer drag is a real HTML5 drag on Chrome

§F2.4 said *verify in the pod before promising anything*, and Part I left
Firefox unmeasured. Both were done before E13 was written. Same method: the live
Grid, a `data:` page logging every event, one run per browser.

| | Chrome | Firefox |
|---|---|---|
| `pointermove` from `move_to_element` (WebDriver's 250ms default) | **1** | **13** |
| the same move at `duration=800` | **1**, in 0.82s | **44**, in 0.83s |
| the same move at `duration=16` — what a jump sends now | **1** | **1** |
| 24 small moves in one `perform()` | 24, in 0.41s | 24+, in ~0.5s |
| second hover of the element the pointer is on | **0** `mouseover` | **0** `mouseover` |
| the same, after a nudge away | 1 | 1 |
| pointer position after a reload | survives | survives |
| HTML5 drag from a pointer sequence | `dragstart` → `drag` ×13 → `dragenter` → `dragover` ×4 → **`drop`** → `dragend` | **nothing native at all** |

Three things fall out, and two of them are not what was expected.

**1. Interpolation is a function of the move's duration, and Firefox is the one
that honours it.** geckodriver spreads a long move over intermediate
`pointermove` events; ChromeDriver waits out the duration and delivers one. So
"a move is a teleport" is a *ChromeDriver* fact, not a WebDriver one.

It stops mattering once the server sets the duration, which it does: a jump is
one 16ms move and arrives as one event on both, and a glide is many small moves
and arrives as many on both. The two browsers agree because we stopped leaving
the number to them.

It does mean the honest thing to report is **what the server sent**. `glided`
says "this travelled in steps"; it is not a claim about what the page received.

**2. A pointer drag *is* an HTML5 drag on Chrome.** The folklore §F2.4 warned
about — pointer actions not firing native `dragstart`/`drop` — is out of date
there: the full native sequence fires, `drop` included, which means `drag` covers
`draggable=true` as well as the pointer-event libraries it was built for. **On
Firefox it fires nothing native**: the same sequence moves the pointer and the
page's drag handlers never hear about it. So the sentence the tool description
owes an agent is not "this cannot do HTML5 drag" but "this does HTML5 drag on
Chrome and not on Firefox", which is what it says.

**3. The hover that does nothing is both browsers.** §F2.10's fault was measured
on Chrome; it reproduces identically on Firefox, and the nudge fixes it on both.
So does everything else E13 promises — glide, the click that leaves the pointer
on what it clicked, and a `by_x` drag that moves a range slider from 0 to 86.

### §F2.10 — Flying it again: what the second sortie found

`v0.1.0` plus everything above went to the Grid, and two pilots flew it — the
agent whose first report opened this chapter, on a real app, and Dispatch, on
`selenium.dev`. Both reports are here because they change the plan below rather
than merely praising it.

**What the tools bought.** `outline` replaced three hand-written DOM dumps with
one call, and caught a fact the pilot had previously got right only by luck: a
*Sign in* button disabled until both fields validate. `assert` turned a selector
trick into stated intent. The hint even changed section — from *when a flow
fails* to *say what must be true* — once the failure was an assertion rather
than a selector. And the failure message the pilot singled out is the one this
chapter was written around: *"It exists, but ul.menu-content is hidden… a script
cannot open one either, because synthetic events do not set :hover."* A dead end
converted into the answer.

**Three faults, and the first is the serious one.**

**1. `assert` polls until true, so it latches onto a transient.** Poll-until-true
means *eventually* true, which is right after a click and wrong for a
precondition. The pilot's guard passed **while signed out**: navigating to `/`,
the authenticated shell paints for a moment before the auth guard redirects, and
the poll caught that window. Worse, §F2.5's own example teaches the racy shape —
`wait_timeout: 5` on "is the login form here", which a flickering form satisfies.

The pattern that works is *settle, then ask once*: poll for either terminal
state, then ask the real question with `wait_timeout: 0`.

```yaml
- tool: assert   # wait for the page to commit to one state or the other
  args: {script: "return !!document.querySelector('#login-username') ||
                  document.querySelectorAll('div.navigation-header').length > 0",
         wait_timeout: 30}
- tool: assert   # then the precondition, asked once
  args: {script: "return !document.querySelector('#login-username')",
         wait_timeout: 0, message: Already signed in.}
```

So: **`stable_for`** — the answer must hold for N milliseconds, not merely
occur — and the documented pattern above beside it. Without one of the two, a
guard that looks right is wrong on exactly the pages people write guards for.

**2. `hover` reports success when it did nothing.** If the pointer is already
inside the target, no `mouseover` fires and the step still returns `ok`. It bit
the pilot twice. Measured here on a `:hover` menu: the first hover fired one
`mouseover`, the second fired **none**, and both were reported as successes.

And the obvious verification does not work. `document.querySelectorAll(':hover')`
came back **empty** in the same breath as `getComputedStyle` reporting the menu
`display: block` — the state is applying and the selector will not confess to
it. So the fix is not to check afterwards; it is to **nudge**: when the pointer
is already within the target, move it away first, so the move it is asked for
is a move. That is E13's pointer work arriving early, and it removes the failure
mode rather than reporting it.

**3. `outline` says what blocks an element, never what reveals it.**
`blocked_by: ul.menu-content` names what is in the way; the caller needs what to
hover. The click error tries, and on a real page produced
`//li[normalize-space()="HelpDeskHelpDeskAdd TicketHistoryFeature Requests"]` —
concatenated descendant text, brittle and unreadable. Two fixes, one rule: a
**`revealed_by`** field carrying the trigger's checked selector, in the map as
well as the message; and **no text XPath for an element with element children**,
because that text is its descendants' and not its own.

Related, from the same flight: `outline` skipped the trigger entirely, because
it is an `<a>` with no `href`. Defensible by accessibility semantics and useless
in practice — it gated half the nav. `[aria-expanded]` belongs in the
interactive set, and a bare `<a>` probably does too.

**And one Dispatch found on `selenium.dev`:** the advice says *hover whatever
reveals it*, and that menu is **click-toggled**. Hovering the element it named
did nothing; clicking the toggle opened it. The page said so all along —
`outline` reports `expanded: false` there, because it reads `aria-expanded`. The
probe has the evidence and ignores it when writing the sentence. When a trigger
carries `aria-expanded`, say **click**; say hover only when it does not.

**Carried, minor:** `screenshot` returns `absolute_url`, but `kept: false` — the
link dies with the browser and a durable one still needs `keep_file`. That is
§F2.9's decision working as designed; what is missing is the tool saying so.

---

## Part III — Carried from Chapter 1

| Item | From | This chapter |
|---|---|---|
| **E5** — `ROUTE_PREFIX` becomes the global mount | §F1.11 | **Built** (#35). The cluster-repo half ships with the release. |
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
- [x] Flown: a menu opened from the tool list alone on `selenium.dev` — the
      schema's enums and `outline` were enough, and no `execute_script`

### E14 — Cross-check: `assert`, and failures that say what to do

- [x] `assert` tool + `POST /browser/assert`, taking `script`, `message` and the
      ordinary `wait_timeout` (§F2.5)
- [x] Boolean only — anything else refused, naming what came back
- [x] Evaluated again until true or the timeout passes; nothing sleeps
- [x] The keyword alias in one place — `assert` on both surfaces, `assert_` as the
      method — read by `flowrun` and by the surface test
- [x] A failed assertion is a 400, and the step's `error` is the author's message
- [x] A `hint` on every failed run, its `read` URI chosen from what failed (§F2.6)
- [x] `SKILL.md` and `FLOWS.md`: *a flow owns where it starts*; `assert`, its
      boolean rule and the `$${` escape; the login guard
- [ ] `FLOWS.md`: flows as contracts, once someone has used one that way
- [x] `save_flow` warns when the first step neither navigates, carries a `url`,
      nor asserts — a `warnings` list on the save result, never a refusal
- [x] Per-step `url` in the non-verbose report when it changes (§F2.7)
- [x] Tests through `run()`, not the helper: the wrong-page flow now fails with
      its message, the signed-in login flow fails inside its `wait_timeout`, and
      breaking the assertion on purpose turns both red
- [x] Flown: a route-changing flow with an `assert` on a live site, and the login
      guard on a real app by the other pilot — which is how §F2.10's first fault
      was found
- [x] **`stable_for`**: the answer must hold, not merely occur — poll-until-true
      latches onto a transient and passed a signed-out guard (§F2.10). **In
      seconds**, not the milliseconds this line first said: `wait_timeout` is
      seconds and two units on one call is a trap. A `stable_for` longer than
      the `wait_timeout` is refused, naming the unit, so the misreading fails
      loudly instead of never passing. And the failure tells *never true* apart
      from *would not stay true*, because the fixes are opposite
- [x] `FLOWS.md`: the guard example takes `stable_for`, settle-then-ask-once is
      documented beside it, and the page says which shape belongs after a click
      and which before one. The action itself has been driven against the live Grid —
      true, false with and without a message, a non-boolean, and an element that
      appears 2.5s late, which it waited 2.4s for

### E16 — Ground handling: screenshots, and two file odds and ends

- [x] `screenshot(save=...)` defaults to `true`; the result carries the file entry
      and signed URL (§F2.9)
- [x] A failed save returns the image with a note, never an error
- [x] **Changed:** changelog line — screenshots now appear in the session's files
- [x] `upload_file` accepts a kept file by name — `kept=`, a fourth source
- [x] `open_session(fresh=true)` — drops the remembered page, keeps the browser
      and window. MCP only: the HTTP surface has no flow session to inherit
      from, so the argument is stripped from the published contract rather than
      accepted and ignored
- [x] Flown: a screenshot saved itself and came back with a working signed link,
      opened from the result
- [ ] Flown: one kept from the admin UI, which is the half not yet exercised
- [x] Every unkept file entry carries `keep_with` — `keep_file("shot.png")`.
      The tool description has said it since #28 and a pilot still handed
      somebody a dying link, because what it read was the result (§F2.10)

### E15 — The sectional chart: the probe, its hints, and `outline`

- [x] One usability probe in JavaScript, identical on both browsers (§F2.8)
- [x] Timeouts on an *existing* element carry the reason and the next move: hover,
      `scroll_to`, cover named, disabled, zero-size
- [x] `outline` tool + endpoint: scoped, `text`-filtered, limited, interactive-only
      by default
- [x] `outline` outside the runnable set — `save_flow` refuses it as a step and
      says what it is for
- [x] Prompts from markdown files, the `skills-mcp` format: frontmatter arguments,
      `{{ placeholders }}`, strict loading, a test that loads every shipped one
- [x] `repair_flow` and `build_flow`, and the run hint carries `read`, `prompt`
      and `arguments`, chosen from what actually failed (§F2.6)
- [ ] Flown: a flow broken on purpose — a renamed button — repaired from
      `/repair_flow` alone
- [x] Selectors: CSS where a unique attribute exists, XPath by text otherwise, each
      verified to match exactly one element
- [x] A test per reason, and one asserting both scripts are built from the same
      helper — the error and the map cannot drift because there is one copy of
      the reasoning, not two that agree
- [x] Flown: the probe and `outline` driven against a live browser — a hidden
      submenu link comes back with its selector, `reason: hidden` and
      `blocked_by: ul.menu-content`; two links with identical text get distinct
      selectors; disabled, off-screen, zero-size and covered all reported
- [x] Flown by an agent end to end: a hidden nav item found and opened with
      `outline` and `interact` alone, no `execute_script` — on a menu that turned
      out to be click-toggled, which is the next item
- [x] **Say click when the trigger says click.** `open_with` on the entry and
      the right verb in the failure sentence, from `aria-expanded` and
      `aria-controls` (§F2.10)
- [x] **`revealed_by`** on a hidden entry — the trigger's checked selector, in the
      map as well as in the failure message (§F2.10)
- [x] **No text XPath unless the text is the element's own**: refused for a
      container of several children, kept for a single wrapper chain, which is
      what `<button><span>Save</span></button>` is
- [x] `[aria-expanded]`, `[aria-controls]`, `[aria-haspopup]` and `a:not([href])`
      in the interactive set: the trigger gating half a nav was skipped for
      having no `href`

### E13 — Stick and rudder: the pointer

- [x] **Spike first:** the Part I measurements on **Firefox**, and HTML5 native
      drag vs pointer-event drag in Chrome — both done, both surprising, §F2.11
- [x] Pointer position recorded per Grid session after every move, reset on
      `open_session` and on `end_browser`, in Redis wherever the session store
      is — `pointer.py`, sharing the session store's own switches (§F2.3)
- [x] Every pointer gesture moves first, then acts; `click` keeps WebDriver's
      element click so a covered target is still refused. A move that cannot be
      sent costs the remembered position and never the gesture
- [x] `glide` on `interact`, default `false`; unknown start → a jump, and the
      result says so in `glide_note`
- [x] Destination from `getBoundingClientRect` at move time, correct after a scroll
- [x] `drag` tool + `POST /browser/drag`, element-to-element and by offset, `glide`
      default `true`; a destination outside the window stops at its edge and
      says so (§F2.4)
- [x] A test page logging `pointermove`: a glide delivers many events, a jump one;
      after a click the pointer is on the clicked element; a covered click is
      still refused. All of it in `tests/test_pointer.py`, marked `integration`
      and run against the live Grid
- [x] Flown: a range slider driven from 0 past 50 by `drag(by_x=100)`, on Chrome
      **and** on Firefox — pointer-event drag works on both
- [x] Flown: a `draggable=true` drop target receiving the whole native sequence
      — **Chrome only**, which is what §F2.11 measured. Firefox fires nothing
      native from a pointer drag, and this line said the opposite for a while
      (Copilot, #31): a box ticked for a browser the measurement two sections
      above it had already ruled out
- [ ] Flown: a sortable list on a *real site*. The pages driven so far are
      synthetic, which proves the events and not the libraries
- [x] **Nudge before hovering.** A hover onto a target the pointer is already
      inside fires no `mouseover` and still reports `ok` — measured, twice
      (§F2.10). The move steps aside first, preferring a point still inside the
      element's parent so a nudge cannot close the flyout it is about to hover,
      and the result says `nudged`

### E17 — The tower: the admin UI's carried items

- [ ] The detached-browser state, designed in Penpot first (§F1.36)
- [ ] Keyboard reaches the flow list and its rows
- [ ] `If-Match` on flow saves against `flows.revision`, 409 on conflict, and the
      editor says what happened

### E18 — One session, always named (§F2.12) — **built**

Dr K's design, recorded rather than planned: §F2.12 is the work of this entry,
and the boxes are what it implies rather than a commitment. **The four
questions in §F2.12 were answered in the course of building it**, and are
recorded there with their answers.

- [x] Every session has a name and the **caller** supplies it; nothing here
      generates one
- [x] `session_id` is gone from every tool schema, every request body and every
      result
- [x] The Grid's browser id is never exposed; it stays attached to the named
      session in the background
- [x] `routes.py` resolves a session the way `/mcp` does — one path, and the
      HTTP surface inherits the reopen-after-reap it has never had
- [x] `resources.ShapeSessionId` and one of the two session references are
      deleted, not adapted
- [x] `SAVED_SESSIONS` is resolved: gone, or given a meaning that still holds
- [x] Written down: a session name is a credential, the bearer token is all that
      guards it, and sharing one is now easy to do by accident
- [ ] The browser carries its session name as a capability (`se:flowSession`),
      so the admin view can reconcile with the Grid and an orphaned browser can
      be found again. Augments the store; does not replace it (§F2.12 Q5).
      **Not built** — it is additive, and nothing else waits on it

### E19 — A RESTful HTTP surface (§F2.13) — **built**

Built with E18 or not at all: both break the published HTTP contract, and a
caller should migrate once.

- [x] Paths and methods come from a declared route table, not from tool names;
      bodies and results stay derived from the tool schemas
- [x] The session is the `X-Session-Key` header or `?session=`, never a path
      segment and never a body field; both at once is a 400
- [x] HTTP resolves a session through the same code `/mcp` does
- [x] No session on a library route means `global`; on a browser route, a 400
- [x] `interact`'s action is a path segment: `POST /browser/interact/click`
- [ ] `PUT` and a created run answer with `Location`; results carry `links`.
      **Not built** — the shapes are right without it, and HATEOAS earns its
      place only once something follows a link
- [x] `/admin/sessions/{key}/…` stays — the admin view is the one role that
      looks across sessions
- [x] The OpenAPI spec, the wiki and the skill references are regenerated from
      the new table
- [ ] The n8n workflows in this cluster are migrated. **Not done** — they live
      in a database, not in this repo, and they break the moment this ships

### E20 — One `selector` (§F2.14) — **built**

In the E18/E19 release: it breaks saved flows, and they migrate once.

- [x] A `Selector` model — `xpath` or `css`, exactly one — replaces the two flat
      arguments on every tool that takes them
- [x] `drag` takes `selector` and `to`; `frame` keeps `index` beside `selector`
- [x] A selector sent as a JSON string is coerced, like the enums are
- [ ] `outline` returns `selector` in the shape a step takes. **Not built** —
      it still returns `xpath`/`css` flat, which is a smaller change than it
      looks and wants its own pass over the probe
- [x] The integration flows and every example in the skill and the wiki are
      migrated
- [ ] The flows on the cluster's NFS share are migrated. **Not done** — they are
      live data in another repo's deployment, and they break the moment this
      ships

### E5 — The approach plate (carried, built in #35)

As written in Chapter 1, and ticked there with what changed in the building.

### Found in review, not built: the JSON handlers block the event loop

Copilot caught `/ready` dialling the Grid with synchronous `requests` inside an
async handler, where a Grid that has gone away blocks the loop until it times
out and takes `/health` down with it. That one is fixed, in a thread.

**The same is true of every `/browser`, `/flows` and `/files` handler**:
`_answer` calls straight into `actions`, which is Selenium and therefore
blocking, on the event loop. One slow page stalls every other request in the
process, probes included. The admin handlers already use `run_in_threadpool`
and are the shape to copy. Not touched here because it is not this change's
fault and the fix belongs with its own tests — but it is the next thing worth
doing, and it is why `/ready` alone is not enough to survive a Grid outage.

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
