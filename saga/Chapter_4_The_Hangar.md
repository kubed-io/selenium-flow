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

## Status: **PLANNING** — opened 2026-09-24

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
