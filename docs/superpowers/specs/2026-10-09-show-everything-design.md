# show draws every resource

**Status: DESIGNED 2026-10-09 by Claude, unattended; open for Dr K on issue #63.**
Epic E7 of the programme spec
(`2026-10-09-workspaces-and-observability-design.md`, cited as *programme
R<n>*). Plan: `docs/superpowers/plans/2026-10-09-show-everything.md`. Assumes
E1 (#60) merged: every URI below is the renamed one. Penpot: file *Admin UI*,
page *App · show*, boards `site-data`, `site`, `file`, `document`, and
`context` (shared with E3); flow *App*. Read, never edited.

## Brief

Programme R17 (Dr K, 2026-10-09): *every URI the server serves — every listed
resource and every template — has a view in `show`'s table, and a test holds
it.* A single file is drawn from its listing entry, never its bytes; markdown
and the flow schema are drawn as a document; `MAX_SHOWN` still applies. The
issue adds: a screenshot as an image, a recording as a player, a download or
kept file as a card with Open; `site-data` and `site-data/{site}` as a
drill-down pair with httpOnly values `•••`; the `files` and `folder` views
drill into one file; the skill's `show` line and the wiki note. Out: E3's
capture views, fullscreen for the player.

Left open by the issue: a screenshot from its signed link or inline; what a
document does with a relative link.

## Research that shaped it

Checked against the branch on 2026-10-09, with probes run outside the
worktree.

**The inventory, enumerated for real.** A FastMCP `Client` against
`SeleniumMCP` with `data.dir`, `secrets.dirs` and `recording.enabled` set, and
again with nothing set: the listing is the same either way (a section that is
off still lists its resource and refuses the read). With the UI built, one more
resource appears, the app shell.

| # | URI form | Listed as | MIME | View today |
|---|---|---|---|---|
| 1 | `workspace://current` | resource | json | `context` |
| 2 | `workspace://site-data` | resource | json | — |
| 3 | `workspace://site-data/{site}` | template | json | — |
| 4 | `workspace://files` | resource | json | `files` |
| 5 | `workspace://files/screenshots` | resource | json | `folder` |
| 6 | `workspace://files/recordings` | resource | json | `folder` |
| 7 | `workspace://files/downloads` | resource | json | `folder` |
| 8 | `workspace://files/{name}` | template | octet-stream (json for a video) | — |
| 9 | `workspace://files/screenshots/{name}` | template | octet-stream | — |
| 10 | `workspace://files/recordings/{name}` | template | json (the entry) | — |
| 11 | `workspace://files/downloads/{name}` | template | octet-stream | — |
| 12 | `flow://flows` | resource | json | `flows` |
| 13 | `flow://flows/{name}` | template | json | `flow` |
| 14 | `flow://schema` | resource | json | — |
| 15 | `secret://secrets` | resource | json | `secrets` |
| 16 | `skill://selenium-flow/SKILL.md` | resource | markdown | — |
| 17 | `skill://selenium-flow/_manifest` | resource | json | — |
| 18–25 | `skill://selenium-flow/references/{CONFIGURATION,FLOWS,INTERACTION,READING_PAGES,SECRETS,SITE_DATA,TROUBLESHOOTING,WORKSPACES}.md` | resources | markdown | — |
| 26 | `ui://selenium-flow/component` | resource, only when the UI is built | `text/html;profile=mcp-app` | the shell itself |

So **19 listed resources and 6 templates** (20 listed with the UI built). Eight
forms have a view; **17 do not**: `site-data`, `site-data/{site}`, the four
single-file templates, `flow://schema`, and the ten skill files.

**Sizes, against `MAX_SHOWN` (100 000).** The largest document is
`references/FLOWS.md` at 15 080 characters (15 812 as a payload);
`flow://schema` is 11 477; `_manifest` 1 504. Every document fits with room.
A listing entry is a few hundred characters. A screenshot's bytes would not:
base64 adds a third, and a viewport PNG of a real page is commonly over 100 KB.
A recording is megabytes.

**What `read_resource` hands `show`.** A skill file reads as one
`ResourceContent` whose `content` is a `str` and whose `mime_type` is
`text/markdown`; `show` today `json.loads` it and refuses. The item templates
for screenshots, downloads and Files return `bytes`; `recordings/{name}`, and
`files/{name}` for a video, return the listing entry as JSON (`http/files.py`,
`_item_resource`, `recording_resource`).

**Where an entry comes from.** Every folder's listing already describes each
file once, with its `name`, `uri`, `size`, `created`, `content_type`, `image`,
a signed `url` and, outside Files, `keep_with` (`files.describe`). A listing
never opens a browser (`Workspaces.browser`, never `resolve`); the Downloads
listing asks the Grid's `/se/files` store for a live browser and nothing for an
absent one.

**The skill names its files by URI, not by relative link.** Across `SKILL.md`
and the eight references there are two markdown links, both `https://…`, and
one `](url)` inside a code example. Every pointer to another file is a code
span holding an absolute URI (`` `skill://selenium-flow/references/FLOWS.md` ``,
`` `workspace://files` ``), as AGENTS.md requires. Some are placeholders
(`` `flow://flows/{name}` ``, `` `workspace://` ``).

**The markdown the skill uses.** `marked`'s lexer over all nine files, with
`SKILL.md`'s YAML front matter removed, yields block tokens `heading`,
`paragraph`, `list`, `code`, `table`, `space` and inline `text`, `strong`,
`em`, `codespan`, `link`. Left in, the front matter lexes as a rule and a
setext heading. Each file opens with one `#` heading.

**The bundle.** The app is 84.4 KB gzipped against a 110 KB budget
(`npm run size`, this branch). A vite 8 minified ES build of `marked` 18.1.0
measured 15.0 KB gzipped with its renderer, 14.6 KB importing only `Lexer`;
`markdown-it` 15.0.2 measured 42.6 KB, which would not fit.

**Escaping is enforced.** `ui/eslint.config.js` makes `svelte/no-at-html-tags`
an error and forbids `innerHTML` ("Render with a template"). A markdown
library's HTML string cannot be dropped into the page; tokens have to be drawn
by Svelte.

**Relative URLs resolve on our schemes.** WHATWG `URL` treats `skill:` as a
non-special scheme with a host and a path: `new URL('references/FLOWS.md',
'skill://selenium-flow/SKILL.md')` is `skill://selenium-flow/references/FLOWS.md`,
and `'../SKILL.md'` from a reference climbs back (Node 24, measured).

**What the host lets the app reach.** `ResourceCSP.resource_domains` maps to
`img-src` and `media-src` as well as scripts, styles and fonts (ext-apps 2.0.3,
`spec.types.d.ts`), so the server's own origin — declared when
`PUBLIC_BASE_URL` is set (`apps._csp`) — covers an `<img>` and a `<video>`.
The host advertises `openLinks` when it can open a URL for the app
(`App.openLink`).

**The boards.** `site-data`: a card titled *Site data* with its URI in mono
beside it and *saved 12:01* at the right; one row per host with a counts pill
and a chevron; a row navigates to `site`. `site`: *← Site data*, the host with
its URI, a *Cookies* block (name, value or `•••`, then *httpOnly · secure ·
session* or *.herokuapp.com · 1 y*), a *Local storage · <origin>* block of
key/value lines. `file`: a recording — name, URI, `● REC`, a 16:9 player, a
facts line *4:12 · 18.4 MB · 21:12 · firefox*, an **Open** button. `document`:
`TROUBLESHOOTING.md` rendered — its `#` heading as the title beside the URI,
paragraphs, a `##` heading, a code block. `context`: chips *Console*, *Network*
(E3's), *Site data 2* and *Files 111*; the Site data chip navigates to
`site-data`, the Files chip to nothing.

## Rulings

Claude, 2026-10-09, unattended — each for Dr K to overturn on #63:

1. **A single file is drawn from its folder's listing entry, looked up by
   `show`.** For a single-file URI, `show` reads the folder's own listing
   resource (`workspace://files`, or `…/screenshots|recordings|downloads`)
   through the server and takes the entry whose `name` is the URI's last
   segment, unquoted. No new resource; the item templates keep serving bytes,
   which is what lets a client that reads resources display a screenshot with
   no URL (`http/files.py` docstring). Cost if wrong: one listing read per file
   shown — for a download, the same Grid `/se/files` call the Downloads view
   already makes; if a folder ever grows large enough for that to hurt, JSON
   item resources are the fallback.
2. **A screenshot is drawn from its signed link, never inline.** A
   real screenshot's base64 alone passes `MAX_SHOWN`; the entry is a few
   hundred characters. Cost if wrong: without `PUBLIC_BASE_URL` the host's CSP
   blocks the image and the view says so (Design 8); a chat reopened after
   `link_ttl` (default 3 600 s) shows the same line.
3. **Markdown is lexed by `marked` 18.1.0 and drawn by Svelte components;
   never as an HTML string.** Only `Lexer` is imported. Raw HTML in a document
   is drawn as its text. A token type the renderer does not know is drawn as
   its `raw` text. Cost if wrong: 14.6 KB of the remaining 25.6 KB of budget;
   a construct the skill starts using renders as plain text until the renderer
   learns it.
4. **Links in a document.** A link, or a code span whose whole text is a
   concrete URI on one of the server's schemes (`skill`, `flow`, `workspace`,
   `secret`, with no `{ } < >` or space), drills through `show`. A link's
   `href` resolves against the document's URI first, so a relative link to a
   sibling file works though none exists today. `http(s)` links open through
   the host's `openLink` when it offers `openLinks`, else as a plain link in a
   new tab. Anything else — `#anchors`, `javascript:`, placeholders — is text.
   Cost if wrong: a URI with no resource behind it lands as `show`'s refusal in
   the shell's error line.
5. **A document's `data` is the resource's own content: its text for
   `text/markdown`, its JSON otherwise.** The `document` view draws a string as
   markdown and anything else as a JSON tree. Front matter is not drawn; the
   first `#` heading becomes the title. Cost if wrong: a future `text/plain`
   resource is refused until its row says how to read it.
6. **Inline, a document shows its first six blocks and "+N more" when the host
   offers fullscreen; everything when it does not.** Claude's guidelines keep
   inline views short and unscrolled, and a host with no fullscreen would
   otherwise hide the rest for good. The shell tells views whether it can
   expand (`expandable`). Cost if wrong: a tall inline document on a host
   without fullscreen.
7. **Component names: `file`, `sites`, `site`, `document`.** `sites`/`site`
   are the plural/singular pair beside `flows`/`flow`; the board is called
   `site-data`. Cost if wrong: a rename.
8. **`ui://` is exempt from the guard, by scheme.** The app shell is the
   drawing, not a thing to draw. Cost if wrong: none until a second `ui://`
   resource that is not a shell.
9. **The guard enumerates through a real client on a server with every section
   on** — data, secrets, recording, the built UI — instantiates each template
   by putting `sample` for each `{variable}`, and asserts `view_for` matches.
   A second assertion holds the other end: every component `show` can name is
   a key of the shell's `VIEWS` in `App.svelte`. Cost if wrong: a resource
   registered only under a setting the guard's server leaves off escapes it —
   so the fixture turns every section on, and its docstring says why.
10. **The `files` and `folder` tiles drill into `file` when the host can call
    tools; the Lightbox stays for a host that cannot.** Cost if wrong: the
    drill path loses the Lightbox's Prev/Next.
11. **Open uses the host's `openLink` when it offers `openLinks`, else a plain
    link** (`target="_blank" rel="noopener"`). Cost if wrong: on a host with
    neither, Open does nothing; the URL is still in the entry the model holds.
12. **Back names the view it returns to** (*← Site data*, as drawn), from the
    component below it on the stack: `files` *Files*, `folder` its folder,
    `flows` *Flows*, `sites` *Site data*, `context` *Workspace*, `secrets`
    *Secrets*, `document` its file name; anything else *Back*. Its accessible
    name stays *Back*. Cost if wrong: one label table in the shell.
13. **The context view gains the Site data chip; the Files chip waits.** The
    board wires only Site data, and `workspace://current` carries no files
    count to put on a Files chip. Cost if wrong: the drawn *Files 111* chip is
    missing until the resource carries a count.
14. **The file view's facts are what the entry carries, plus the duration a
    video reports once loaded.** The drawn *firefox* is not in the entry (a
    recording's note keeps only the Grid id), so it is left out. Cost if
    wrong: one fact fewer than drawn.
15. **`show`'s description names the schemes, not sixteen forms.** "Draw any
    resource this server serves — workspace://, flow://, secret:// or skill://
    — for the person to see"; the refusal still names every form. Cost if
    wrong: a model that guesses a URI is told the forms on its first miss.
16. **The `site` view is read-only.** No Forget, no Clear: the show spec keeps
    editing out of the app. The admin pane's counting rule moves to
    `ui/src/lib/sites.ts` so both surfaces count a host alike; markup is not
    shared, because the board and the admin pane lay a site out differently.
    Cost if wrong: two small views of one shape to keep in step.
17. **The Penpot file is *Admin UI*.** The programme spec calls it
    *selenium-flow*; the issue and the connected file say *Admin UI*.

## Goal

`show` draws all 25 URI forms the server serves today: one file as a picture,
a player or a card with Open; the saved site data as a list that opens a site;
the skill's pages rendered and linked; `flow://schema` and `_manifest` as a JSON
tree. A test fails the build the moment a resource or template is added
without a view.

## Non-goals

| Not built | Why |
|---|---|
| `console`, `network`, `request` views | E3's, under the same guard |
| Fullscreen for the player or the Lightbox | the show spec's next round |
| Forget, Clear, keep, delete from the app | the show spec: no editing in the app |
| A count on the Files chip | needs `workspace://current` to carry one (Ruling 13) |
| Refreshing an expired link in place | next round |
| JSON item resources for single files | Ruling 1 |
| `readServerResource` | still unconfirmed on Claude |

## Constraints

These hold for every task in the plan.

- `MAX_SHOWN` stays 100 000 characters, payload and line as sent.
- The URI-to-component table lives in `kubed/selenium_flow/mcp/show.py` and nowhere else.
- A single file is drawn from its listing entry, never its bytes; no resource is added or changed.
- `show` never calls `Workspaces.resolve`: drawing anything never opens a browser.
- The app stays within 110 KB gzipped (`npm --prefix ui run size`).
- `marked` is pinned at exactly `18.1.0` in `dependencies`, and only `Lexer` is imported from it.
- No `{@html}` and no `innerHTML` (the eslint config already errors on both).
- A link is clickable only when `safeHref` passes it or it is a concrete URI on `skill`, `flow`, `workspace` or `secret`.
- httpOnly cookie values arrive as `•••` from the server and are drawn as given.
- Python 3.10–3.14, `from __future__ import annotations`; `ruff check kubed` clean.
- UI: `npm --prefix ui run check`, `lint` and `test` clean.
- One CHANGELOG line, in `[Unreleased]` only.
- Penpot is read, never edited.

## Design

### 1. The table

`VIEWS` in `mcp/show.py` becomes rows of a `View` named tuple — `form`,
`pattern`, `component`, and `entry` (true when the URI is drawn from its
listing entry). First match wins, so each folder sits before the
`{name}` row it would otherwise match.

| Form | Pattern (fullmatch) | Component | Entry |
|---|---|---|---|
| `workspace://current` | literal | `context` | |
| `workspace://site-data` | literal | `sites` | |
| `workspace://site-data/{site}` | `workspace://site-data/[^/]+` | `site` | |
| `workspace://files` | literal | `files` | |
| `workspace://files/screenshots` | literal | `folder` | |
| `workspace://files/recordings` | literal | `folder` | |
| `workspace://files/downloads` | literal | `folder` | |
| `workspace://files/screenshots/{name}` | `…/screenshots/[^/]+` | `file` | ✓ |
| `workspace://files/recordings/{name}` | `…/recordings/[^/]+` | `file` | ✓ |
| `workspace://files/downloads/{name}` | `…/downloads/[^/]+` | `file` | ✓ |
| `workspace://files/{name}` | `workspace://files/[^/]+` | `file` | ✓ |
| `flow://flows` | literal | `flows` | |
| `flow://flows/{name}` | `flow://flows/[^/]+` | `flow` | |
| `flow://schema` | literal | `document` | |
| `secret://secrets` | literal | `secrets` | |
| `skill://selenium-flow/{path}` | `skill://selenium-flow/.+` | `document` | |

`view_for(uri) -> str` keeps its signature (other modules and tests call it);
`row_for(uri) -> View` is new and is what `show` uses. `SHOWABLE` is still the
forms in order, and still what the refusal names.

### 2. How `show` reads

- **A whole resource** (`entry` false): read through the server as today. If
  the content is a `str` with `mime_type` `text/markdown`, `data` is that text;
  otherwise it is `json.loads` of the content, refused as today when it is not
  JSON (`"<uri> is not a resource show can draw"`).
- **One file** (`entry` true): the listing URI is everything before the last
  `/`; `show` reads that listing the same way, unquotes the last segment, and
  takes the entry in `files` whose `name` equals it. None:
  `ValueError("no file at <uri>. <listing> lists what there is")`. The result's
  `uri` is the URI asked for; `data` is the entry.
- The payload, the one-line summary and the `MAX_SHOWN` check are unchanged.
  `summary` already falls back to `(<component>)` for a payload with no
  `count`, which is every new view.

### 3. The new views

All live in `ui/src/lib/views/`, take the host's tokens, and carry the
resource URI in mono beside the title as the boards draw it (the shell passes
`uri`; it is shown decoded, as a person reads it).

- **`FileView`** (`file`): the name, the URI, `● REC` when the URI is a
  recording. Then a preview by `content_type`: `image` → `<img>` at full width,
  height capped; `video/*` → `<video controls preload="metadata">`, 16:9; PDF →
  an `<iframe>` as the Lightbox does; anything else → the glyph block. A facts
  line — the video's duration once `loadedmetadata` fires, `bytes(size)`, the
  created time (clock time today, the date otherwise), and for a non-media file
  its `content_type` — and **Open** at the right (Ruling 11). When the image or
  video fails to load, the preview is replaced by one line: *"This link has
  expired, or this server's address is not reachable from here
  (PUBLIC_BASE_URL)."*
- **`SitesView`** (`sites`): *Site data*, the URI, *saved <time>* when
  `saved_at` is set. One row per `sites` entry: the host, `siteCounts(row)` as
  a pill, a chevron; the row is a button calling `onshow(row.uri)`. Without
  `onshow` the rows are text with the "Open isn't available in this client"
  tooltip `FilesView` already uses. Empty: *"Nothing saved — an agent calls
  save_site_data after signing in."*
- **`SiteView`** (`site`): the host and the URI. *Cookies*: one line each —
  name, value (clipped, whole on hover), then facts joined by ` · `: `httpOnly`,
  `secure`, the domain when the cookie is `shared`, and `session` or the time
  left (`until(expiry)`: *45 m*, *5 h*, *3 d*, *1 y*). Then per origin, only
  where it has entries, *Local storage · <origin>* and *Session storage ·
  <origin>* as key/value lines. A host with nothing at all: *"Nothing saved for
  this site."*
- **`DocumentView`** (`document`): a string is markdown — front matter removed,
  lexed, the first `#` heading lifted into the title, the rest drawn by
  `Markdown.svelte` (block tokens) and `MarkdownInline.svelte` (inline
  tokens). Anything else is drawn by `JsonTree.svelte`, titled by its `title`
  when it has a string one, else the URI's last segment. Inline limit per
  Ruling 6 (blocks counted without `space`).
- **`JsonTree`**: an object or array is a `<details>` per key with a count in
  its summary, open at the top level; scalars as `key: value`, strings quoted.

Rendering is tokens to elements, one `{#if}` per type: `heading` (h1–h6, the
app's own scale), `paragraph`, `text` (its inline tokens when it has them),
`list` (ordered or not, items' tokens), `code` (`<pre><code>`), `table`
(header and rows of inline tokens), `blockquote`, `hr`, `space` (nothing);
inline `strong`, `em`, `codespan`, `link`, `del`, `br`, `escape`, `text`.
Anything else, and `html`, is its `raw` as text.

`ui/src/lib/links.ts` decides what a link is: `target(href, base) → {kind:
'show', uri} | {kind: 'open', url} | null` and `showable(text) → string | null`
for a code span (Ruling 4).

### 4. Drill-downs

All through the shell's `onshow`, which calls `show` with `callServerTool`
and pushes the result — nothing new in the mechanism.

| From | Click | To |
|---|---|---|
| `files`, `folder` | a tile | `file` (the tile's `uri`) |
| `sites` | a row | `site` (the row's `uri`) |
| `context` | the *Site data* chip | `sites` (`site_data.uri`) |
| `document` | a URI code span or link | whatever that URI draws |

`FileGrid` already takes `onopen(i)`; `FilesView` and `FolderView` pass
`(i) => onshow(files[i].uri)` when `onshow` is set and leave it unset
otherwise, which keeps the Lightbox.

### 5. The shell

`App.svelte` registers `file`, `sites`, `site`, `document`; adds `document` to
`EXPANDS`; passes every view `uri`, `expandable` (the host lists
`fullscreen`) and `onlink` (`host.openLink({url})` when the host's
capabilities include `openLinks`, else undefined); labels Back by the view
below (Ruling 12).

### 6. The guard

`tests/test_show_inventory.py`:

- **Every listed resource and every template has a view.** A server with
  `data.dir`, `secrets.dirs`, `recording` (with an inbox) and the built UI;
  a `Client` lists resources and templates; each URI, and each template with
  `re.sub(r"\{[^}]+\}", "sample", t)`, must pass `view_for`, except `ui://`
  (Ruling 8). The failure names every URI without a view.
- **The inventory is what this spec says.** The same enumeration, with every
  `skill://selenium-flow/…` URI folded into one, equals the 16 forms of
  Research's table, so a new resource fails here as well as above and this
  table is updated with it; a new skill reference does not.
- **Every component `show` names, the shell draws.** The set of components in
  `VIEWS` equals the keys of `VIEWS` in `ui/src/App.svelte`, read by a regex.
- **Each form draws what it should.** `show` through the client returns the
  expected component for one concrete URI of each form (a screenshot, a
  recording, a download from a faked Grid listing, a kept file, a site with
  saved data, a skill reference, the schema).

### 7. Size

`marked` adds about 14.6 KB gzipped (measured), the four views and three
renderer components a few more: the app goes from 84.4 KB to roughly 100 KB of
110. The plan runs `npm --prefix ui run size` in the task that adds `marked`
and again at the end; over budget stops the task.

### 8. Failures

| Case | What the person or model sees |
|---|---|
| A URI no row matches | the refusal naming every form, as today |
| A file the listing does not have (gone, a download with no browser) | `no file at <uri>. <listing> lists what there is` |
| A site with no saved data | the resource's own refusal (`no saved data for … workspace://site-data lists them`) |
| Over `MAX_SHOWN` | as today: read the resource instead |
| An image or video that will not load | the expired-or-unreachable line in place of the preview |
| A markdown construct the renderer does not know | its raw text |
| A drill-down to a URI with nothing behind it | the refusal in the shell's error line; the view stays |

### 9. Documentation

- `SKILL.md`'s tool table: `show` — "draw any resource for the person (hosts
  that render apps): the workspace, files and one file, site data, flows, the
  secrets, these pages, the flow schema".
- AGENTS.md, "`show` is the one app tool": every URI the server serves has a
  view, `tests/test_show_inventory.py` holds it; a single file is drawn from its
  listing entry; documents are lexed, never `{@html}`.
- README: the apps paragraph lists the new views.
- CHANGELOG `[Unreleased]`: "`show` draws every resource: one file, the saved
  site data, the skill's pages and the flow schema."
- Wiki: `Installing.md` (and any page `grep 'show('` finds) names the views;
  generated pages are untouched, since `show` has no endpoint.

### 10. Testing

- **Python** (`tests/test_show.py`, `tests/test_show_inventory.py`): the guard
  (Design 6); a single file from its entry for each folder, its `url` signed and
  no bytes read; a missing file refused naming the listing; a skill file's
  `data` is its text; `flow://schema` and `_manifest` are their JSON; the site
  pair, with an httpOnly value `•••` in what is sent; no browser opened by any
  of it; the description names the schemes; the refusal names every form.
- **UI** (vitest + testing-library): `links.ts` cases; `Markdown` per token
  type, front matter dropped, raw HTML as text, a URI code span as a button
  that calls `onshow`, an `https` link through `onlink`; `JsonTree`; each view
  and its empty or failed state; tiles and rows drilling; the context chip; the
  shell registering the four components, `expandable`, `onlink` and the Back
  label; `siteCounts` unchanged for the admin pane.
- **Live, Dr K on Claude web:** "show me my last screenshot", "show me the
  recording", "show me the saved site data" → a site, "show me the skill" → a
  reference, "show me the flow schema"; light and dark.

## Verify first

1. **The inventory** — 19 listed + 6 templates (+1 `ui://` with the UI built),
   17 forms without a view. *Measured (probe, 2026-10-09).*
2. **`marked`'s size and token set** — 14.6 KB gzipped for `Lexer`; the skill
   uses only the types in Research. *Measured.* The plan re-measures the real
   bundle.
3. **Relative resolution on `skill:`** — *measured in Node*; the plan's
   `links.ts` test proves it under jsdom.
4. **A skill file reads as `str` + `text/markdown`** through
   `fastmcp.read_resource`. *Measured.*
5. **A `<video>` from the server's origin plays inside Claude's app iframe**
   with `resource_domains` alone. *Unverified* — Dr K's live check; if it does
   not, the file view's Open is the way to watch.
6. **Claude advertises `openLinks`.** *Unverified*; without it Open is a plain
   link (Ruling 11).

## Next round

- A files count on `workspace://current`, and the context view's Files chip.
- Refreshing an expired link in place (re-calling `show` from the view).
- Fullscreen for the player and the Lightbox.
- Prev/Next inside the `file` view, from the listing it was opened from.
- E3's `console`, `network`, `request` under the same guard.
