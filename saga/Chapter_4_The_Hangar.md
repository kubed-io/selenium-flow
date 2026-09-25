# Chapter 4 — The Hangar

> Flight log, **SELENIUM-FLOW**, Dispatch.
>
> Every aircraft on this field comes back with film in its camera. Since #40
> the film goes straight into the hangar, which was the right fix for the
> reason it was made. But the hangar has one door and no shelves. Dr K walked
> in to see what the pilots had been looking at and found 109 photographs,
> each with a pin on it and a bin next to it, and no way to clear the lot.
>
> This chapter gives the hangar shelves: film on one, what the browser brought
> back on another, and what somebody decided to keep on a third.

---

## Status: **MERGED (#41, 2026-09-25), not released** — opened, planned, drawn and built 2026-09-24

The chapter stays open until Dr K releases it; chapters close with releases.
The admin-page overhaul (Part IV) is being planned inside it.

Planned in chat (§F4.1–§F4.10), drawn in Penpot (§F4.11), then built from
`docs/superpowers/plans/2026-09-24-the-hangar.md` by subagent-driven
development: a fresh implementer per task, a spec-and-quality review after
each, a whole-branch review at the end. Part III is ticked against the build;
§F4.13 is what building it decided that the spec had not.

Planned first, drawn in Penpot second, then built. Nothing is built until Dr K
approves the drawing (§F1.40: *the design follows the app, except where Dr K
deliberately decides otherwise*).

What it is planned from:

- **Dr K's brief**, 2026-09-24: the pin on a screenshot or print no longer
  means anything, deleting a session's files one by one is too slow, and a
  proposal for separate Downloads, Screenshots and kept-files sections, each with a
  clear that fits what it holds.
- **The live admin UI**, signed in with the `admin` secret and read with
  `save=false` screenshots so the reading added nothing to the pile (Part I).
- **The Penpot file *Admin UI***, compared board by board with what is
  deployed (Part I).

---

## Part I — What the hangar looks like

### The pile

Measured on the deployed server, 2026-09-24:

| Session | Files | Of which PNG | Shown as kept |
|---|---|---|---|
| `claudecode` | 109 | 109 | 109 |
| `kf-extension-workspace` | 13 | 13 | 13 |

**Nothing on the field is a download.** The bubble and the ➕ keep button,
which the file tile was designed around (§F1.10, §F1.40), are not drawn on a
single tile in real use. Every tile is a pin with a bin beside it, because
since §F3.8 every screenshot and print is written straight into the kept
files.

That has two costs. The pin says *somebody chose to keep this*, and nobody
did. And **Clear downloads** is the only bulk action on the page, while the
files that actually accumulate are the ones it cannot reach: on `claudecode`
its confirm says *"There is nothing downloaded to clear"* above 109 files.
Clearing them means 109 bins and 109 confirms.

### Where the drawing has drifted

The Penpot file was last brought level with the code in §F1.40. Since then:

1. **A print is drawn as a download.** `login-report.pdf` carries the bubble
   and the ➕. Since #40 a print is kept from the start.
2. **Clear downloads is drawn as a plain button.** It is `danger` in the code.
3. **The confirm dialogs differ.** The real modal has a head, a body and a foot
   with dividers, a fixed title (`Clear downloads`) and a labelled Cancel; the
   drawing's Cancel is blank and its title counts the files.
4. **Session facts are stacked** label over value in the real page, and drawn
   as two columns. The real `last page` has a rule under it.
5. **The idle state was never drawn** (carried since §F1.36, E17). What the
   field actually shows is a *reaped* browser whose record still names it: an
   `idle` pill, a BROWSER block holding only the id, and both buttons enabled.
6. **The lightbox exists and is not drawn.** Clicking a thumbnail opens it,
   with the name, Download and Close, and no way to step to the next file.

Also seen, not chased: the Flows pill on `claudecode` read **1** and then
**0 / "No flows yet"** on one load.

### What the storage allows

- `FLOW_DATA_DIR` is an **NFS share** in the cluster, so the screenshots
  already in each session's `files/` survive a deploy.
- **A screenshot can be moved, not copied.** Keeping a download has to be a
  copy because the Grid has no per-file delete (§F1.10). A screenshot and a
  kept file both live in our own directory, so keeping a screenshot can take
  it out of the screenshots.

---

## Part II — The doctrine

### §F4.1 — Decision (Dr K's): three sections, and no delete-all

The one file grid becomes three sections: **Downloads**, **Screenshots** and
**Files** (§F4.2). Each clears in the way that fits what it holds:

| Section | Holds | Keep | Delete |
|---|---|---|---|
| Downloads | what the site downloaded | one at a time, into Files | the whole set (as today) |
| Screenshots | every `screenshot` | one at a time, into Files | the whole set |
| Files | prints, and anything kept | — | one at a time |

This **replaces** the delete-all-files button Dr K asked for first. Clearing
the screenshots is the bulk delete for the pile that actually grows, and Files
gets no delete-all on purpose: everything in it is there because somebody
chose it, or because it was a print a person asked for.

### §F4.2 — Decision (Dr K's): the third section is **Files**

Dispatch recommended *Kept*, the word `keep_file` already uses, and Dr K
overruled it the same minute: **"files" is clearer.** So the page reads
Downloads, Screenshots, Files. The verb stays: you *keep* a download or a
screenshot, and keeping puts it in Files. It is also the directory it already
lives in, `<session>/files/`.

### §F4.3 — Decision (Dr K's): newest first, and the lightbox follows the grid

Every section lists newest first, so the latest activity is at the top without
scrolling. The lightbox steps in the grid's order: → and **Next** go to the
tile after this one, which is older. ← → step, Esc closes, and it stops at
either end rather than wrapping.

### §F4.4 — Decision (Dr K's): the screenshots already on disk are moved by hand, once

No migration code, under §F3.7. After the deploy, each session's existing
`*.png` in `files/` is moved into `screenshots/` in the pod, once, with the
list shown to Dr K first. `*.png` is the rule because `files/` alone cannot
tell a screenshot from a kept download.

### §F4.5 — Decision (Dr K's): three accordions

*Amended by §F4.8: the three sit in a Files tab, and Flows moved to a tab of its own.*

**Downloads, Screenshots, Files**, then Flows (Dr K's order), each the existing section: title,
count pill, and its own action in the header. Everything is visible at once,
and the open/closed state each one already remembers comes for free. Tabs and
a filmstrip were weighed: tabs hide two of the three sets, and a strip of 109
cannot be taken in at a glance.

### §F4.6 — Decision (Dr K's): `session://files` is a folder, and the sections are paths

Dr K, reading the first draft of this part: *"why not something like
session://files/screenshots — this sort of seems like what it was designed
for, being all pathlike"*, and then: *"session://files shows the files and two
folders for screenshots and downloads."*

| URI | Is |
|---|---|
| `session://files` | the Files section's files, and the two folders beneath it |
| `session://files/{name}` | one file in Files — the template that already exists |
| `session://files/screenshots` | the screenshots |
| `session://files/screenshots/{name}` | one screenshot |
| `session://files/downloads` | what the browser downloaded |
| `session://files/downloads/{name}` | one download |

**This supersedes §F1.10's one list**, and the reason it can is the reason
§F1.10 existed: that ruling refused two listings because a caller had to ask
twice and join the answers. A folder that lists its subfolders, with a count on
each, costs a caller nothing it does not want, and a path is a shape every
model already knows. The draft this replaced kept one list and added a
`section` field, which then needed a `section=` argument on `keep_file` for a
name that was in two sections at once. **In a path, the section is part of the
address**, so a name only has to be unique inside its folder and the argument
never exists.

Measured before it was recommended, against FastMCP 4.0.9 (the pinned major):
the two folders are static resources and win over `session://files/{name}`;
`{name}` never matches a `/`, so `session://files/a/b` is not found rather than
misread; `%20` and a literal space resolve alike. **One collision is real**: a
file in Files named exactly `screenshots` or `downloads` could not be
addressed, so those two names are reserved there, and one arriving lands as
`downloads (1)`, the same rule every other clash follows.

### §F4.7 — Decision (Dr K's): what an agent sees

- **Storage.** `screenshot` writes to `<session>/screenshots/`; `print` to
  `<session>/files/`; downloads stay in the Grid's store. A clash inside a
  folder lands as `name (1)`, as it does today.
- **Reading.** `session://files` answers
  `{files: [...], folders: [{name, uri, count}, ...]}`, the downloads folder
  saying when there is no browser to hold any. The two folders answer the same
  entry shape. Every entry carries its own **`uri`**; the `kept` flag goes,
  because the folder is what says it. `session_files`, the tool only an
  MCP-App host is shown, returns all three at once so the app can draw them.
- **Keeping takes a URI.** `keep_file("session://files/screenshots/shot.png")`
  **moves** a screenshot into Files, landing as `shot (1).png` rather than
  overwriting anything, and answers with where it landed. A download is
  **copied**, replacing a same-named file, as today; a URI already in Files
  answers with that file. Each entry's `keep_with` spells its call. The tool's
  `idempotentHint` becomes **false**: a second keep of a moved screenshot finds
  nothing, and the hint is a promise (AGENTS.md).
- **`upload_file(kept=name)` becomes `upload_file(file=uri)`.** "Kept" no
  longer names anything, and any file then uploads — a screenshot straight into
  a page without keeping it first.
- **Unchanged:** no agent tool clears or deletes anything, and REST's `/files`
  tree mirrors these paths one to one.

### §F4.8 — Decision (Dr K's): Files and Flows are tabs

Dr K: *"I don't like scrolling to see them and when i do the files make it
noisy. Tabs will cure this right up and give plenty of space for the flows and
the files will have the page to itself for the three rows."* On `claudecode`
the 109 tiles put Flows some 4,000px down the page.

- **The session card stays above the tabs**: it describes the session, not
  either tab.
- **Files, then Flows, and Files is the default.** The tab is in the hash —
  `#/sessions/claudecode/flows` — the way the page already routes, so Back
  works, a link can land on Flows, and a reload stays put.
- **A tab's label carries its count** ("Files 111" is the same total the
  sessions list shows), drawn with the `tab` component the Sessions / Grid
  console tabs already use.
- **The three rows keep their carets**: free, and an empty Downloads row folds
  away.
- The event stream still refreshes the tab that is not showing, so switching
  never reveals a stale one.

This retires §F1.36's reason for the accordions — *"which is what keeps this
page one column"* — without undoing it: the page is still one column, it is
simply only ever one tab's column.

### §F4.9 — Decision (Dr K's): the admin API and the Files tab

**The admin API mirrors the paths** (§F4.6), behind the server token:

| Call | Does |
|---|---|
| `GET /admin/sessions/{key}/files` | all three sections and the session header, one call |
| `DELETE …/files/downloads` | clears the downloads — was `DELETE …/files` |
| `DELETE …/files/screenshots` | clears the screenshots |
| `POST …/files/{screenshots,downloads}/{name}/keep` | keeps one |
| `DELETE …/files/{name}` | deletes one in Files |

There is **no delete-all on Files and no per-screenshot delete**, on purpose.
The sessions list says the non-zero counts: "109 screenshots · 2 files".

**The three rows**, inside the Files tab (§F4.8):

| Row | Header action | Empty |
|---|---|---|
| Downloads | **Clear downloads**, disabled unless a browser is *live* | "No downloads." / with no live browser: "No browser — downloads go with it." |
| Screenshots | **Clear screenshots**, disabled when empty | "No screenshots yet." |
| Files | — | "Nothing here yet — prints land here, and anything you keep." |

Clear downloads used to key off *attached*, which left it enabled on a reaped
browser; it keys off *live* now.

**Tiles lose the status marks.** The row says what a file is, so the bubble and
the pin in the right corner go. One action stays, top left, a real button shown
on hover (always, on touch): **📌 keep** on Downloads and Screenshots, **🗑
delete** on Files. §F1.40 took the pin away as an *action* because nothing
unpins, and that reasoning does not reach this: the pin never sits on a tile as
a state, it sends the tile to Files, so there is nothing a second click could
be expected to undo. A kept screenshot leaves its row (a move); a kept download
stays and also appears in Files (a copy).

**The lightbox steps.** Within the row it was opened from; the head is name ·
**12 / 109** · ‹ › · the row's action · Download · Close; ← → and Esc; the
buttons disable at either end. **Keep inside the lightbox moves on to the next
screenshot**, so 109 can be triaged without closing it — delete in Files does
the same after its confirm. A file with nothing to preview shows its glyph and
a Download rather than being skipped, so the count stays true.

**Confirms**, all the existing modal: *Clear screenshots* says how many, that
Files is untouched, and lists the names, with a **Delete 109 screenshots**
button — a bulk delete shows what it takes. *Clear downloads* is unchanged but
for "— copy in Files stays". *Delete a file* says Files.

**The MCP app** draws the same three rows, read-only; the lightbox's stepping
works there too, since only its actions need a credential.

### §F4.10 — Decision (Dr K's): a Secrets tab, read-only, with who uses each

Dr K: *"we never included the secrets … these would be read only anyway …
secrets are global so they would sit next to sessions."* Checked, and true: a
filesystem secret is visible to every session (§F1.22); scoping waits for E8.

- **Top tabs: Sessions | Secrets | Grid console.**
- **One card per secret**, by name: description, keys, where it is allowed,
  and which source and directory it came from. **Never a value** — the server
  has none to send. A broken leash says *unusable until fixed* with its reason,
  and an unrestricted secret says *any site*, both on the `warn` pill.
- **Used by**: every stored flow whose steps name it, with the steps, linking
  to `#/sessions/{key}/flows/{flow}` — a new deep link that opens the Flows
  tab on that flow. A shared flow belongs to no one session, so it is listed
  with 🌐 and not linked. It is a scan of stored flows, because a step's
  `args.secret` already carries the reference; no new bookkeeping.
- **Named by a flow, not defined** — flows that reference a secret the
  catalogue does not have. They fail at run time, so this is the most useful
  line the page can show an operator.
- **What it cannot show:** a session typing a secret by hand with `write`.
  Nothing records that, and recording it would be logging secret use — its own
  decision, not a side effect of a page.
- **`GET /admin/secrets`**: the catalogue, each entry's uses, and the undefined
  names. Token-gated, and no tool, because the admin surface is never tools
  (AGENTS.md). Fetched when the tab opens rather than streamed: secrets change
  rarely.

Same pull request and Penpot pass as the files work (the one-PR rule).

### §F4.11 — Drawn: one page per view, one library for all of them

Drawn in Penpot on 2026-09-24, on Dr K's rule for the file itself: **a page is
one interactive view or workflow**, and every page instances one library.

| Page | Holds | Its prototype starts at |
|---|---|---|
| **Components** | every main component, in labelled groups | — |
| **Sessions** | sign in, the sessions list (live, idle, detached), the Grid console | sign in |
| **Session · Files** | the three rows, keep, the lightbox stepping, three confirms, the no-browser state | `files`, and `files-no-browser` |
| **Session · Flows** | the flows panel under its own tab, every state §F1.40 drew | `flows` |
| **Secrets** | the catalogue, a broken leash, backlinks, a name no secret answers to | `secrets` |

**The library grew from 14 components to 37**, and the growth is the point:
the atoms were components already, and everything larger — the session card,
a row's head, the tab bars — was copy-paste per screen, which is exactly what
§F1.40 found drifting. Now `nav`, `session-tabs`, `toolbar`, `summary`,
`session-card`, `row-head`, `file / view|keep|delete`, `modal / confirm`,
`lightbox / screenshot|file`, `btn / on-dark`, `secret-card / ok|warn|undefined`
and `used-by / session|shared` are components, and the pages are compositions
of them. The old `file` component, with three marks stacked on it and toggled
per instance, is gone.

The drift Part I listed is corrected on the way: a print is in Files, Clear
downloads is `danger`, the confirm is the real modal, facts are stacked, the
idle and detached states are drawn, and the lightbox exists.

Decided while drawing: the lightbox's steps are words, **‹ Prev** and
**Next ›** — a bare chevron on a translucent button all but vanished — and its
colours are the literals `app.css` itself uses, because the CSS has no custom
property for them.

**What Penpot taught, measured on 2.17.2:**

- **A prototype cannot leave its page.** A link to a board on another page is
  accepted and its destination reads back `null`. So each page is its own
  prototype, and a link that would cross — a sessions card into Files, a
  backlink into Flows — is drawn and not wired.
- **A shape cannot move between pages**, and neither can a component be made
  from one on a page that is not open: both calls return without error and do
  nothing. Carrying a screen to a new page is: make it a component with its
  page open, instance it on the other page, detach.
- **A link on a layer *inside* an instance is silently dropped.** A link on the
  instance itself sticks, so a tile links from its root, and the 📌 and 🗑 on a
  tile are transparent hotspots drawn over them.
- `insertChild(i)` counts the shape being moved, so it lands one slot early;
  `resize()` pins both dimensions, so a width change needs height set back to
  auto; an empty string is not valid text.

Checked mechanically before review, as §F1.40 requires: no main component
carries a link, no link on any page is dead, every board on every page is
reachable from that page's start, and `File.validate()` is clean. Versions
*before hangar restructure (Chapter 4)* and *Chapter 4 design — for Dr K's
review* bracket the work.

**Second pass, the same day — Dr K: *"it was quite hard to click around."*** The
first pass had been checked for *reachability*, and reachability was not the
question. Walked link by link, the Files page had dead ends on every result
screen, a lightbox chain that stopped at 3 of 6, tiles that did nothing, and
five stray flows Penpot had made on its own for boards a link started from.
The Flows page carried a slip from the original drawing — the secret step's
un-pick sat on the row next to the picked one — and a move nothing could undo.

Rebuilt as **stories**, each playable end to end: browse a row through its
lightbox, keep a screenshot (a move, then the 5-item list), keep a download (a
copy), clear, delete, pick, move and move back. Keep and delete are shown on
one file each, so every result screen stays true of what came before it; the
other tiles draw those buttons without wiring them. Every result screen
carries a yellow *Prototype* note saying what just happened, and a click on it,
the logo or the current tab starts over. Each page has a guide board beside its
start.

The check that belongs in the method from now on, beside reachability: **no
dead ends, and no flow on a page that nobody named.**

### §F4.12 — Decision (Dr K's): a Redis that is configured and unusable stops the boot

Found by another agent on 2026-09-24, and confirmed in the code: on 21 Sep the
pod started while Redis was refusing connections, `redis_client` pinged once,
logged a warning and handed back nothing, and `from_env` kept session records
**in memory for the life of the process** — three days, with nothing but one
log line saying so. The docstring's reasoning, *"a mapping that resolves
locally beats a server that will not start"*, was the mistake: a server that
will not start is restarted by Kubernetes until Redis answers, while a server
that started on the wrong store is never corrected at all.

So: **Redis configured and unreachable, or configured with the `redis`
package missing, or a `SESSION_STORE` naming no known backend, is a startup
error with the reason in it.** Memory is used only when nothing asked for
Redis. The pointer store shares the same connection and the same rule.

The deploy that ships this chapter restarts the pod, which puts the live server
back on Redis db 2; no separate restart (Dr K's call).

### §F4.13 — What building it decided

Rulings taken during the build, each recorded when it was made:

- **The admin's keep never opens a browser.** `files.keep` takes the browser id
  from the admin (`attached_id`), and an agent's keep of a download reads from
  the browser it holds — neither ever reopens a reaped one, which could not hold
  the download anyway.
- **A screenshot keep moves the file or changes nothing.** If removing the
  original fails, or a concurrent keep or clear got there first, the copy just
  made in Files is removed and the call fails.
- **`keep_file` is `destructive`** — a kept download replaces a same-named file
  in Files, and AGENTS.md's honest-annotations rule outranks the extra prompt.
- **The lightbox ignores keys while a confirm is open above it**, so Esc backs
  out of "delete this?" without closing the lightbox.
- **An empty Downloads row *can* fold** (its caret); it does not fold itself.
  §F4.8's wording allowed either — Dr K to say if it should.
- **A name a flow uses that no secret answers to is shown even when a hand edit
  made it a `${…}` reference**: such a flow really fails, so the card is true.

**Found by the reviews, not the tests.** The page's tests are string
assertions, so every UI task was reviewed by hand-tracing the JavaScript: a
deep-linked flow opening in the session you had just left, a bulk confirm that
listed one session's files while deleting another's, a lightbox refresh that
stranded the next session on "Loading…", an Esc that closed two overlays.
The whole-branch review found the new integration flow could never pass
(`stable_for` is seconds, not milliseconds — the plan's own mistake) and that
its XPath matched the grid, not the tile. None of that fails a unit test.

**Flown before the PR**, 2026-09-24: this branch's server run in the pod
against the real Grid, a session seeded with four screenshots, a print and a
download, and its admin page driven from a Grid browser — lightbox stepping
with the arrow keys, Keep moving on to the next screenshot (3 / 4 → 3 / 3),
Clear screenshots leaving Files untouched, a download kept as a copy through
the tile's 📌 (hover, then click, as the integration flow does), the Secrets
tab with its backlink landing on the flow. 44 requests, no errors.

---

## Part III — The plan, ticked against the build

- [x] Two file folders in the store (§F4.7)
- [x] Three sections addressed by path; keep moves a screenshot, copies a download (§F4.6, §F4.7)
- [x] `session://files` a folder; `keep_file(uri)`; REST mirror; spec (§F4.6)
- [x] Screenshots keep into `screenshots/`; `upload_file(file=uri)` (§F4.7)
- [x] Admin API: per-section listing, clears, keep from either, counts, signed screenshot route (§F4.9)
- [x] `GET /admin/secrets` with backlinks and undefined names (§F4.10)
- [x] Shared components: one action per tile, three read-only rows, a stepping lightbox (§F4.9)
- [x] Session page: Files | Flows tabs in the hash, three rows, confirms (§F4.5, §F4.8)
- [x] Lightbox keep and delete; keep moves on (§F4.3, §F4.9)
- [x] Secrets tab (§F4.10)
- [x] Docs, skill, wiki, changelog, and one new integration flow
- [x] Redis configured and unusable stops the boot (§F4.12)
- [x] After the deploy: the one-off move of each session's existing `files/*.png` into `screenshots/`, list shown to Dr K first (§F4.4) — done 2026-09-25 on the branch image (`file-sections`, c011e67): claudecode 142, kf-extension-workspace 17, no clashes, no file named `downloads` or `screenshots`; every file in Files was a PNG

---

## Part IV — The overhaul: the admin page on a framework

**Status: BUILT, in one pull request** — taken up and built 2026-09-25.

Open question 1, taken up 2026-09-25 after #41 merged. **Dr K's brief:** a full
refactor of the admin UI and the static content onto a frontend framework. It
must look and work exactly the same. Small, lightweight, simple and secure; no
Angular-sized framework; **npm and a build are fine** (which lifts the
question's old "no build step" constraint); component-based; a small build;
ideally *less* code than today because the framework does the heavy lifting.
Vue is familiar and used elsewhere in the stack, but alternatives were welcome.
**No abandoned projects — active maintenance only.**

Planned through the superpowers brainstorming flow; nothing is built until Dr K
approves the spec.

### What is there, and what any answer must keep

#### The surface

| File | Lines | What it is |
|---|---|---|
| `static/admin.html` | 1,580 | The whole admin page: markup plus one inline script of ~100 functions |
| `static/components.js` | 348 | The component library (`SF.*`), shared with the MCP App |
| `static/app.css` | 477 | One stylesheet for both surfaces |
| `static/app.html` | 57 | The MCP App shell: renders one `SF` component from a tool result |

`admin.py`'s `page()` inlines the CSS and the component library into each page
by string substitution (`__CSS__`, `__COMPONENTS__`). The admin page is served
unminified: 147 KB, **45 KB gzipped**, on every visit.

The pain is concrete. Live updates are hand-written with four sequence
counters (`filesSeq`, `flowsSeq`, `flowDocSeq`, `secretsSeq`), `gone(key)`
guards and a `filesSettled()` barrier (Chapter 4's Keep race), and 30
`innerHTML` assignments that are each only as safe as the `esc()` call someone
remembered to put in them.

#### The constraint that decides the field: the MCP App CSP

`components.js` also runs inside an MCP App, in a sandboxed iframe whose CSP the
*host* writes. The MCP Apps spec (SEP-1865, stable 2026-01-26), "Restrictive
Default":

```
default-src 'none';
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline';
```

Inline scripts are allowed; **`unsafe-eval` is not, and a host may only make
this stricter.** Any library that compiles templates in the browser (`new
Function`) cannot run there: petite-vue, standard Alpine, and Vue's full
(runtime-compiler) build are out on this alone. Precompiled components — Vue
SFCs through Vite, Svelte, Solid, Preact JSX, Lit — are fine, and a single
inlined bundle is exactly what the host accepts.

#### Found while looking: the MCP App has never started

`app.html` imports the SDK from
`https://unpkg.com/@modelcontextprotocol/ext-apps/dist/index.js`. That path
**404s** on both 1.7.5 and 2.0.0 — the browser entry is
`dist/src/app-with-deps.js` (the package's `./app-with-deps` export). Every
host shows "This host could not start the app". The URL is also unpinned, so it
floats to whatever major ships (2.0.0 did on 2026-09-24). Bundling the SDK from
npm at a pinned version fixes both, and drops `https://unpkg.com` from the
app's CSP.

#### What tests the page today

158 tests (`tests/test_admin_ui.py`, `tests/test_files_and_admin.py`) grep the
page source for function names and call counts. A rewrite invalidates nearly
all of them by construction; they test the implementation, not the page. The
behaviour net is 3 integration flows (`tests/integration/flows/*.yaml`) that
the server runs against its own admin page — the only tests that would notice
the page working differently.

#### Maintenance (GitHub, measured 2026-09-25, window = last 12 months)

| Project | Stars | Releases | Latest | Verdict |
|---|---|---|---|---|
| vuejs/core | 54k | 52 | 3.6.0-rc.9 (09-18) | active |
| sveltejs/svelte | 88k | 100+ | 5.57.1 (09-18) | active |
| preactjs/preact (+signals) | 39k (4.5k) | 21 (72) | 11.0.0-rc.2 (09-08) | active |
| solidjs/solid | 36k | 72 | 2.0.0-rc.9 (09-18) | active, 2.0 imminent |
| lit/lit | 22k | 8 | 3.3.3 (05-14) | slower; 722 open issues |
| alpinejs/alpine | 32k | 21 | 3.17.4 (09-21) | active |
| vuejs/petite-vue | 9.7k | 0 | — | **abandoned** (last push 2024-07) |
| developit/htm | 9k | 0 | — | **abandoned** (last push 2024-02) |
| vanjs-org/van | 4.4k | 1 | 1.6.1 (07-16) | near-dormant (7 commits) |
| dy/sprae | 211 | 17 | 13.9.1 | active, one maintainer |

Supporting pieces: Vite 8 and Vitest 5 are very active. The
`@testing-library` adapters for Vue and Preact have had no release in over a
year; their live replacements are `@vue/test-utils` and Vitest browser mode
(`vitest-browser-svelte`, `vitest-browser-vue`, both released 2026-09).
`vite-plugin-singlefile` has had no release in 12 months — not needed, since
`page()` already inlines.

#### Size (measured 2026-09-25)

One identical component in each — session cards from data, a filter box, a
picked row, a count, an empty state — built with Vite 8 for production,
runtime included:

| Framework | min | gzip |
|---|---|---|
| Solid 1.9.15 | 13.3 KB | **5.4 KB** |
| Lit 3.3.3 | 18.1 KB | 7.1 KB |
| Preact 10.29 + signals 2.11 | 21.8 KB | 8.5 KB |
| Svelte 5.57.1 | 37.2 KB | 14.4 KB |
| Vue 3.6.0-rc.9, Vapor | 55.7 KB | 20.8 KB |
| Alpine CSP 3.17.4 | 71.3 KB | 23.1 KB |
| Vue 3.5.43 | 64.1 KB | 24.9 KB |

For scale: today's admin page is 45 KB gzipped with no framework at all,
because nothing is minified.

### §F4.14 — Decision (Dr K's): Svelte 5

Dr K, 2026-09-25: *"i'm interested in svelte. Sounds fun let's do it."* Chosen
over Vue 3 (familiar, but the largest runtime, and 3.6 still a release
candidate), Preact + signals (JSX; a thin router and testing ecosystem), Solid
(2.0 at rc.9, so a migration within months) and Lit (more boilerplate, the
slowest release cadence). What carried it: the least code for this kind of page
(`{#each}`, `{#if}`, `bind:`, scoped styles and transitions built in), compiled so
it runs under the MCP App's no-`eval` CSP, text escaped by default with
`{@html}` the one greppable opt-out, 14 KB, and the most active of the five.

### §F4.15 — Decision (Dr K's): the npm build is separate, and its output is never committed

Dr K, 2026-09-25: *"do not ever commit the generated stuff. We ignore the
generated output folder … Ideally the pip install does not know or care about
nodejs, it just collects static stuff. The npm build would be separate so python
stays pure."* Also: most frameworks have a `public/` folder of truly static
files merged into the build output, and `build/` might serve as the output.

**`build/` cannot be the output**, measured in a throwaway package: setuptools
prunes `build/` from the sdist, so `python -m build` — what `package.yml` runs,
sdist first and the wheel from it — fails with `package directory 'build/ui'
does not exist`, while a direct `pip install .` still works and hides it. The
repo-root `static/` fails differently: as a mapped package directory it makes
`pip install` error when the UI has not been built, which breaks "pip does not
care about Node".

What passes every case: the Vite output lands **inside the package**,
`kubed/selenium_flow/http/static/`, gitignored, and is collected by a
package-data glob on `kubed.selenium_flow.http`. Unbuilt, `pip install` and
`python -m build` both succeed with no UI; built, the files are in the sdist and
the wheel; the tree stays clean either way.

**`build/` asked about again, and measured again** (Dr K: *"can you reuse
"build" as the output dir? … build/ui or build/static"*). It can be shared: one
line, `[tool.distutils.build] build-base = "build/python"`, moves setuptools'
own scratch into `build/python`, so only that is pruned and `build/ui` reaches
the sdist and the wheel. But as a mapped package directory it must *exist*:
unbuilt, `pip install` and `python -m build` both fail. §F4.17 makes the UI
optional, which that cannot be — so the output stays inside the package.

### §F4.16 — Decision (Dr K's): a pure refactor

Dr K, 2026-09-25: *"This is pure refactor, all current functionality must be
maintained. No adding anything new either. Focus purely on making our purely
static UI become svelte."* With one allowance: *"if any little ui elements can
get a stylish boost from using this framework, hopefully svelte can make it look
nicer too"* — structure and behaviour unchanged. The spec is
`docs/superpowers/specs/2026-09-25-svelte-admin-ui-design.md`; the plan follows
it in `docs/superpowers/plans/` once Dr K approves.

### §F4.17 — Decision (Dr K's): the UI is optional

Dr K, 2026-09-25: *"I want to maintain the ability to use the mcp server without
the UI … if you don't build, the static html endpoint would be like a default
"Selenium Flow" title only … the full build will include that so it gets
bundled."* So a `pip install` from source is the whole server: every tool, the
REST API, the admin API. Without a UI build the admin URL answers with a page
that says **Selenium Flow** and nothing else, and the MCP App is not offered
(the tools behave as with `APPS_ENABLED=false`, and still return their data).
The image and the release wheel are built with the UI. This replaces the spec's
earlier "503 with the build command".

Dr K approved the spec the same day (*"so far the spec looks good, let's get
the plan going"*), including its three deliberate differences.

### §F4.18 — What building it decided

Rulings taken during the build, each recorded when it was made:

- **The lightbox's own `onclose` goes stale across an `await`.** A viewer
  closed mid-Keep read its live prop after the request settled and closed the
  *next* viewer instead of itself; the fix keeps exactly one viewer mounted, so
  there is nothing stale left to read.
- **A shell comment swallowed its own placeholders.** `page()` filled
  `__CSS__`/`__JS__` wherever they appeared, including inside an HTML comment
  in the shell, which blanked the page it produced; it now strips shell
  comments before it substitutes, once, with tests guarding the seam.
- **A composite `{#each}` key with no separator can collide.** A secret's
  used-by rows were keyed on `flow + session` with nothing between them, so
  `'ab' + 'c'` equalled `'a' + 'bc'`, sticking a pane on "Loading…" forever;
  every keyed list in `ui/src` was swept for the same fault, and it was the
  only one.
- **An accordion's `data-open` used to flip before its slide finished**, so
  closing one visibly snapped shut mid-animation. It now follows the *visible*
  state — true the instant it opens, false only once the closing slide ends —
  so the attribute, `aria-expanded` and the screen agree.
- **Cancelling a modal mid-confirm fired its `onclosed` twice.** A local
  "already closed" flag makes every callback after the first a no-op, which is
  what the page already looked like it did.
- **The console iframe does not remount on a tab switch.** It mounts once, at
  boot, and is only hidden or shown from then on — the same iframe the page
  has always kept, never a fresh one.
- **The MCP App's SDK is pinned at 2.0.0.** npm's `latest` sits on a newer
  release line; a patch behind is the cost until Dependabot catches up.
- **End browser stays disabled until a freshly opened session's row confirms
  it is attached**, rather than briefly showing the previous session's button.
- **Clear downloads stays disabled for the whole span of a files load**, even
  if a live push arrives partway through it.
- **Tab buttons carry `role="tablist"`/`role="tab"` with `aria-selected`** —
  the top tabs and the Files | Flows subtabs alike — so the state is valid
  ARIA; the cost is a screen reader now announcing "tab" where it used to
  announce "button".
- **An Edit whose re-read lands after the session was left opens nowhere.**
  Today it opened over whichever session had since taken the screen, with
  Save then refused; that was a stale-session artefact the refactor did not
  have to keep.
- **The shell renders one top pane at a time** — Sessions or Secrets — while
  the **Grid console stays mounted and hidden** instead of being torn down
  and rebuilt, so its iframe keeps its place across tab switches.
- **The live badge's pulsing dot is appended to the global `app.css`**, not a
  component's scoped styles — a scoped `@keyframes` block would put a
  `svelte-*` class on every element in that component. Today's rules are
  untouched.

---

## Open questions

1. **A frontend framework for the admin page?** Dr K, 2026-09-24: *"there is no
   frontend framework … wouldn't it be easier to use vue.js or something — a bit
   more modern than a giant single admin.html"* — filed for later, deliberately
   not this chapter. What is known: FastMCP offers nothing for an admin page (its
   only UI is MCP Apps, which `app.html` uses to draw a tool result in a client);
   the page is static files served by Starlette. The constraints any answer must
   keep: no build step (the repo ships one Python image), and `components.js`
   still has to run inside the MCP app. That points at a no-build framework from
   a CDN — petite-vue or Alpine.js (templates in the HTML), or Preact + htm (ES
   modules) — over Vue with a bundler. The pain it would remove is concrete:
   most of Task 8's review was hand-tracing `gone(key)` and sequence-number race
   guards that a reactive store makes structural. A spike first: port the Files
   tab in one of them and compare.

   **Taken up in Part IV** (2026-09-25): npm is fine after all, the CDN
   candidates fail the MCP App CSP or the maintenance bar, and the answer is
   Svelte 5 (§F4.14).
