# show: one MCP App tool that draws a resource

**Status: BUILT on branch `show` (PR #56), 2026-10-05; final review fixes in; live
test pending. Plan: `docs/superpowers/plans/2026-10-04-show.md`.** Dr K reads this
as it grows and drops rulings in chat; every ruling is recorded under *Rulings*.

## Brief

Dr K, 2026-10-04, after selenium-flow went behind the gateway and worked from
Claude web: *"I want a mcp-app for one flow. I want to ask show me a flow and see
it as an mcp app now too. Also let's give a context mcp app."* And: can it be one
dynamic UI, keyed by which resource is shown, rather than a tool per view?

## Research that shaped it

**What exists.** The server already serves one app shell,
`ui://selenium-flow/component` (`mcp/apps.py`, built from `ui/src/App.svelte`). It
draws whichever Svelte component a tool result names in `component`. One tool
uses it: `session_files`, which returns all three file sections as
`component: "fileSections"`. App tools are listed only for a client that declares
the MCP Apps extension (`apps.supported()`), and the gateway passes that
declaration through — checked live on both routes, 2026-10-04.

**What an app may do** (ext-apps 2.0, spec `2026-01-26`): call server tools
(`callServerTool`), read server resources (`readServerResource`), update the
model's context (`updateModelContext`), ask for a display mode
(`requestDisplayMode`), and send a chat message (`sendMessage`). A tool marked
`visibility: ["app"]` is hidden from the model; `["model", "app"]` is callable by
both.

**What Claude actually does** (research report, 2026-10-04):

| App request | claude.ai |
|---|---|
| `callServerTool` | works; the result goes to the app only, the model never sees it |
| `requestDisplayMode` | `inline` and `fullscreen`; `pip` is refused silently |
| `updateModelContext` | stored; the model reads it only if it chooses to |
| `sendMessage` | drafts text into the input box; the user must press send. Changed behaviour once (July 2026) — not to be relied on |
| `readServerResource` | unconfirmed |

**Claude's design guidelines for apps in chat:** inline views fit their content
and never scroll vertically inside; carousels of 3–8 equal cards with the next
one peeking; at most one button per card; detail views go fullscreen with the
app's own button; host style tokens only (light and dark); skeletons, not
spinners; no dropdowns or popovers. Selecting and expanding happen in the app;
anything needing Claude happens in chat. claude.ai ignores `size-changed` and
reads the document's own height, so the app sets `documentElement.style.height`
after each render. Tool results over ~150k characters never reach the app.

## Rulings

Dr K, 2026-10-04:

1. **One `show(uri)` tool, keyed by resource URI** — not a tool per view, and not
   an app hung on `read_resource` (which is the model's reading tool, hidden from
   clients that read resources natively).
2. **`show` replaces `session_files`.** One user, no back-compat.
3. **`show` follows the resource URIs one-to-one.** Asking for screenshots shows
   screenshots (the lightbox scroller), not the three-section files view.
   Files and downloads look the same way.
4. **The flow list is a horizontal card scroller**, not the admin's list.
   Selecting a flow shows that flow.
5. **The context view is simple**: `session://current` — session name, browser,
   current URL, live or idle, window, who is signed in.
6. **No `apps` client flag.** The client's MCP Apps declaration already decides,
   and it crosses the gateway.

## Goal

`show(uri)` draws any of six resources as an MCP App, inline in the chat, in one
generic shell. A click on a flow card opens that flow inside the same app.

## Non-goals

- **No Run button** (recommended for a later PR: it needs a parameter form; open
  for Dr K to override).
- No editing (flows, files, site data) from the app.
- No `sendMessage`-driven navigation; no `pip`.
- No change to resources, the mirror tools, or the admin UI's own panes.

## The design

### The tool

`show(uri: str)` lives in `kubed/selenium_flow/mcp/show.py`.

- **Showable URIs and their views** (one table, the single source of truth;
  matched in order, first match wins):

| URI | `component` |
|---|---|
| `session://current` | `context` |
| `session://files` | `files` |
| `session://files/screenshots` | `folder` |
| `session://files/downloads` | `folder` |
| `flow://flows` | `flows` |
| `flow://flows/{name}` | `flow` |

- **It reads the resource itself**, through the server (`read_resource`), so a
  view draws exactly what the resource serves and cannot drift from it. The
  result is `{"component": <view>, "uri": <uri>, "data": <the resource's JSON>}`,
  as structured content. Nothing new is computed for the views.
- **Anything else is refused** with one sentence naming every showable URI
  (`ValueError`, the house refusal). A URI that names a missing flow refuses the
  way the resource does.
- **App declaration:** the shared `apps.config_for(base)` gains
  `visibility=["model", "app"]`, so the app can call `show` itself for
  drill-down. It is listed only for a client that renders apps, as
  `session_files` was; elsewhere it still answers with the same JSON.
- **Description** (for the model): what it does, the six URIs, that it is for a
  person to *see* (read with resources or `read_resource` to *think*), and that
  the view the person is looking at is shared as context.

`session_files` (`http/files.py`, `FILES_TOOL`) and its `sections()` helper's MCP
use are removed; `sections()` stays if the admin page still uses it.

### The shell

`ui/src/App.svelte` stays the one shell. It gains:

- **A view table** keyed by `component`: `context`, `files`, `folder`, `flows`,
  `flow` (and `fileSections` goes with `session_files`).
- **A small navigation stack.** A view may ask to show another URI; the shell
  calls `callServerTool({name: "show", arguments: {uri}})`, pushes the result, and
  shows a Back control while the stack is deeper than one. One level is the
  intended depth (flow list → flow; files → a folder); deeper works but is not
  designed for.
- **`updateModelContext`** after every view change, with one line:
  `Showing <uri>` (+ the flow's name or the folder and count). Best effort; a
  host without it is ignored.
- **Host theming:** apply the host's theme and style variables
  (`applyDocumentTheme`, `applyHostStyleVariables`, `applyHostFonts`) from the
  initial host context and on `onhostcontextchanged`; keep the current
  `prefers-color-scheme` fallback for hosts that send none.
- **Sizing:** `autoResize` stays on, and after each render the shell also sets
  `document.documentElement.style.height` to the content's height (the claude.ai
  workaround). No `100vh`.
- **Fullscreen:** the `flow` view shows a button only when the host lists
  `fullscreen` in `availableDisplayModes` (the lightbox is next round: it needs
  a view→shell callback).
- **Capabilities are checked, never assumed:** without `serverTools`, a card is
  not clickable and says so in its tooltip; the view still draws.

### The views

All reuse the existing pieces where they exist and the host's tokens for colour.

- **`context`** — one card from `session://current`: session name, browser mark
  (as `SessionSummary` draws it), live/idle pill, current URL as a link, window,
  principal (`kind`, username), saved-site count. Inline, no scrolling.
- **`folder`** — one `FileGrid`, one horizontal row of tiles, for the folder's
  `files`, its tiles opening the existing `Lightbox`. Title from `data.folder`,
  count pill. Screenshots and downloads.
- **`files`** — the same `FileGrid`, one horizontal row of tiles, for Files' own
  kept files, plus a chip per folder from `data.folders` (`Screenshots 4`,
  `Downloads 1`); a chip drills into `show(<folder uri>)`.
- **`flows`** — a horizontal card scroller from `data.flows`: equal cards with the
  flow's name, description (two lines, clamped), parameter and step counts, and a
  `shared` mark; the next card peeks; `scroll-padding-inline` from the host's safe
  area. A card click drills into `show("flow://flows/<name>")`. Empty: one line,
  no cards.
- **`flow`** — the flow's name and description, its parameters (name, type,
  default, required) and its steps (number, tool, a one-line summary of the
  arguments, `id`/`note` when present, `onError: continue` marked). Inline it
  shows the parameters and the first steps with a "+N more" line; the
  fullscreen button shows everything.

## Testing

- **Server (pytest, through the real MCP surface):** `show` returns the right
  `component` and the resource's own JSON for each of the six URIs (a flow store
  and a kept file in fixtures); an unshowable URI is refused naming the six; a
  missing flow is refused; `show` is listed only for an app client and carries
  `visibility: ["model", "app"]`; `session_files` is gone. The tools golden is
  regenerated.
- **UI (vitest + testing-library):** the shell dispatches each component; a card
  click calls `callServerTool` with `show` and the flow's URI, pushes the view and
  shows Back; Back pops; `updateModelContext` is called with the shown URI; no
  `serverTools` → cards not clickable; the fullscreen button appears only when
  the host offers it; each view renders its empty state. The app stays within its
  gzip budget (`npm run size`, 110 KB).
- **Live, on Claude web (Dr K):** "show me my screenshots", "show me my flows" →
  click one, "show me the session", in light and dark.

## Documentation

- Skill: `SKILL.md`'s tool table gains `show` and loses nothing else
  (`session_files` was not in it); a line in `FLOWS.md` and `SESSIONS.md`.
- README: one feature line (advertise).
- AGENTS.md: the apps section — one tool, the view table, drill-down through
  `callServerTool`, why not `read_resource`, why not `sendMessage`.
- CHANGELOG: `show(uri)` draws a resource as an MCP App; `session_files` is gone.
- wiki: regenerated; `Installing.md`'s prose names `show` instead of
  `session_files`.

## Next round

- A Run button on the flow view (a parameter form; `run_flow` through
  `callServerTool`; the result drawn in place and pushed with
  `updateModelContext`).
- Fullscreen for the lightbox (a callback from `FolderView` to the shell).
- `readServerResource` for full-size images once Claude confirms it.
- More views: `session://site-data`, `secret://secrets`.
