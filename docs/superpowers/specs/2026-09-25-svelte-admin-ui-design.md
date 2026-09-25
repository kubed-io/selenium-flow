# The admin UI on Svelte: same page, real components

Design record for the Svelte overhaul, Chapter 4 Part IV. Written 2026-09-25,
before any code moved. Rulings: §F4.14 (Svelte 5), §F4.15 (a separate npm build,
output never committed), §F4.16 (a pure refactor), §F4.17 (the UI is optional).
**Approved by Dr K, 2026-09-25**, with its deliberate differences.

## Goal

Rebuild `static/` — the admin page, the MCP App shell, the component library
and the stylesheet — as Svelte 5 components built by Vite, so that:

- **it looks and works exactly the same.** Every behaviour in the inventory
  below survives, and the three integration flows pass unchanged;
- **there is less code to maintain**, because the framework owns rendering,
  escaping, event wiring and teardown;
- **it is safer**: no `innerHTML`, no hand-placed `esc()`, no `{@html}`;
- **the build is small** and Python stays pure. `pip` collects files, never runs
  Node;
- **the UI is optional** (§F4.17). A `pip install` without a UI build is the
  whole MCP server; the admin URL then serves a page titled **Selenium Flow**.

## Non-goals

This is a refactor (§F4.16). None of these change:

- the admin API, its routes, auth, signed URLs or the event stream;
- what a page shows or can do. No new views, buttons, fields or settings;
- how pages are served: `page()` still returns one HTML document per surface,
  with everything inlined;
- the pre-existing findings in the Chapter 4 self-test (the outline hover hint,
  sessions lost from the store). Those are separate work.

The only differences are listed under **Deliberate differences**.

## What there is today

| File | Lines | Role |
|---|---|---|
| `static/admin.html` | 1,580 | Admin page: markup plus ~100 functions in one inline script |
| `static/components.js` | 348 | `SF.*` render functions, shared with the MCP App |
| `static/app.css` | 477 | One stylesheet for both surfaces |
| `static/app.html` | 57 | MCP App shell: renders one `SF` component from a tool result |

`admin.py`'s `page()` substitutes `__CSS__`, `__COMPONENTS__`, `__CONSOLE__` and
`__MOUNT__` into the HTML. The MCP App renders exactly one component,
`fileSections` (`files.py`, `admin.py`), which uses `fileGrid` and a read-only
`lightbox`. The served admin page is 147 KB, 45 KB gzipped, and none of it is
minified.

## Layout

A Vite + Svelte project laid out like the official `create-vite` `svelte-ts`
template. That template follows SvelteKit's `src/lib` shape, but SvelteKit
itself is not used: it brings file-based routing and a directory of assets, and
this app is hash-routed, mounted under a prefix by Python, and has to inline
into one MCP App document.

```
ui/                                  source — tracked
├── package.json, package-lock.json  lockfile is source, not output
├── vite.config.ts                   two surfaces, fixed output names (below)
├── svelte.config.js
├── tsconfig.json
├── eslint.config.js
├── public/                          copied verbatim into the output
│   ├── admin.html                   page shell: __CSS__ __JS__ __MOUNT__ __CONSOLE__
│   └── app.html                     MCP App shell: __CSS__ __JS__
└── src/
    ├── admin.ts                     admin entry: mount(Admin)
    ├── app.ts                       MCP App entry: the ext-apps SDK + FileSections
    ├── app.css                      tokens, dark mode, base elements — global
    ├── lib/                         shared by both surfaces
    │   ├── format.ts                bytes, ago, browserMark, glyphFor, safeHref
    │   ├── FileGrid.svelte, FileTile.svelte, FileSections.svelte
    │   ├── Lightbox.svelte
    │   ├── SessionList.svelte, SessionSummary.svelte
    │   └── *.test.ts                colocated tests
    └── admin/                       the admin page only
        ├── Admin.svelte             shell: header, sign-in, tabs, routes
        ├── api.ts                   fetch + bearer token; 401 signs out; server error text
        ├── router.svelte.ts         the hash routes (~30 lines, no dependency)
        ├── live.svelte.ts           EventSource, badge, 30 s fallback poll
        ├── latest.ts                "newest load wins" (replaces the four sequence counters)
        ├── flow.ts                  mapping, listed, own, selectorText, TOOL_ICON, TYPE_ICON
        ├── Modal.svelte
        ├── SessionsView.svelte, SessionDetail.svelte
        ├── FilesPane.svelte, Section.svelte (the accordion)
        ├── FlowsPane.svelte, FlowPanel.svelte, StepDetail.svelte, ParamDetail.svelte
        ├── SecretsPane.svelte, ConsolePane.svelte
        └── *.test.ts
kubed/selenium_flow/http/static/     build output — gitignored, never committed
```

The repo-root `static/` is deleted. The UI is written in TypeScript: the API's
shapes (session rows, file entries, flow documents) become types that
`svelte-check` enforces, and those shapes are exactly where the page's worst
bugs lived.

## Build and packaging (§F4.15)

**The npm build and pip never meet.** `npm --prefix ui ci && npm --prefix ui run
build` writes six files into `kubed/selenium_flow/http/static/`:

| File | What |
|---|---|
| `admin.html`, `app.html` | the shells, from `ui/public/` |
| `admin.js`, `admin.css` | the admin page, minified, one file each |
| `app.js`, `app.css` | the MCP App, with the ext-apps SDK bundled |

Names are fixed, not hashed: nothing fetches them by URL, so there is no cache to
bust. `page()` inlines each surface's JS and CSS into its shell, as it does today.
Two Vite builds, one per surface, each with `inlineDynamicImports`, so neither
surface can emit a shared chunk that an inlined script could not import.
`vite-plugin-singlefile` is not needed: `page()` already does the inlining.

**Python side:**

- `pyproject.toml` drops the `kubed.selenium_flow.http.static` package and its
  `package-dir` mapping, and adds `"kubed.selenium_flow.http" = ["static/*"]` to
  package-data. This was measured in a throwaway package. Unbuilt, `pip install`
  and `python -m build` both succeed and produce a package with no UI. Built,
  the files are in the sdist and the wheel. The git tree stays clean either way.
  `build/` was ruled out because setuptools prunes it from the sdist
  (§F4.15).
- `admin.static_path()` reads only the package directory. The repo-root fallback
  goes, because the build output now lives where an installed copy looks,
  whether the checkout is editable or not.
- `page(name)` reads `<name>.html` and inlines `<name>.css` and `<name>.js`,
  then fills `__MOUNT__` and `__CONSOLE__`. `__COMPONENTS__` goes away.
- `.gitignore` adds `kubed/selenium_flow/http/static/` and `ui/node_modules/`.
  `.dockerignore` adds both too, so a local build never leaks into an image.

**Docker:** a new first stage, `FROM node:24-slim AS ui`, runs `npm ci` on the
lockfile alone (a cached layer), then copies `ui/` and runs the build. The
builder stage copies the output into `kubed/selenium_flow/http/static/` after
`COPY . .` and before `pip install .`. The runner stage is unchanged, has no
Node, and is still Python-only.

**CI:**

- **New `ui.yml` (🎨 UI)**, a required gate on every PR: `npm ci`, `eslint`,
  `svelte-check`, `vitest run` and `vite build`, plus a size report.
- **`package.yml`** and **`integration.yml`** set up Node and build the UI
  before their Python steps. The wheel must contain the UI, and the
  integration flows drive the real page.
- **`test.yml`** and **`quality.yml`** stay pure Python: with no UI built they
  exercise the optional-UI path, and the serving tests use a small fixture
  static directory.
- **`image.yml`** needs no change, because the Dockerfile builds the UI.
- **`copilot-setup-steps.yml`** adds Node and `npm ci`, so the agent can build.
- **Dependabot** gets an `npm` entry for `/ui` with the house rules (weekly,
  grouped minor+patch, cooldown, its own commit prefix).

**Local:** `npm --prefix ui run build` once after cloning. `npm --prefix ui run
dev` is `vite build --watch` into the same folder, so the Python server serves
the new build on reload. There is no dev server and no proxy.

## How the page is put together

**One component per piece of today's markup**, with the same DOM classes, so
`app.css` keeps applying and the integration flows' XPaths still match.
`app.css` moves to `ui/src/app.css` **unchanged and global**: it is what makes the page look
the same, and leaving it whole is the cheapest proof. Component `<style>` blocks
hold only the new polish (difference 3).

**The integration flows are not edited, so what they select is frozen.** They
select `#token`, `#loginForm button[type=submit]`, `#tabFlows`, `#screenshots`,
a `.card` holding a `.name` pill, a `.file` tile, its `button.keep`, and
`//div[@class='name']` — an *exact* class match. Svelte adds a `svelte-xxxx`
class to every element its scoped CSS styles, which would break that XPath with
no error. So the ids stay, and the tile's name, the card's name pill and the
tile's buttons are styled from the global sheet, not from a scoped block. A test
asserts the tile name's `class` is exactly `name`.

**The race guards become structure.** Today, correctness across session
switches rests on `gone(key)` in about twenty places, four sequence counters,
`filesSettled()`, `refuseIfGone`, `closeLightbox`/`closeModal` on every switch,
and `dropBrowserOverlays`. The new structure:

- `{#key current}<SessionDetail key={current}/>{/key}`. A switch destroys the
  old session's whole subtree: its loads, its lightbox, its modals. Every load
  takes an `AbortSignal` tied to the component's life, so a late reply from A
  cannot reach B's screen, because A's component no longer exists.
- Overlapping loads of the same panel go through `latest.ts`. Each new load
  aborts the one before it, and `settled()` resolves once the newest load has
  painted. That is today's `filesSettled` guarantee, written once and tested.
- The Downloads lightbox and the Clear-downloads confirm render inside
  `{#key browserStamp}` (`session_id` + `live`). A browser change tears them down
  on its own, which replaces `dropBrowserOverlays`.
- Actions still read the key they were opened for, and still check it before
  firing (`refuseIfGone` survives as a one-line guard). The structure removes
  the common case, and the guard backs up the click that lands mid-switch.

**Escaping becomes the default.** Every `innerHTML` site becomes a template, and
Svelte escapes text and attributes. The ESLint rule `svelte/no-at-html-tags` is
an error. HTML entities in the code (`&#128204;`) become literal characters.
`safeHref` still gates the session's last-page link, and `own()` still guards
lookups keyed by YAML content: those are data rules, not rendering.

**Shared components know nothing about auth.** `FileGrid`, `Lightbox` and
friends take `action` and `onopen` as optional props. The MCP App passes
neither, so it renders no buttons, exactly as today.

## Behaviour inventory — the parity contract

Each item must hold on the new page. The plan maps every item to a test, or to
an integration flow step where only a real browser can tell.

**Shell and sign-in**
- A1. The header shows the brand, and Sign out appears only when signed in.
- A2. The sign-in card has its text, a required password field ("MCP token") and
  an Enter button. A refused token shows "That token was refused." Success stores
  the token in `sessionStorage['sf-token']` (never `localStorage`) and starts
  the page.
- A3. On load, a stored token is probed against `/admin/sessions`. Success
  starts the page, and failure shows sign-in.
- A4. Any 401 signs out: the stream stops, the token is cleared, and sign-in is
  shown.
- A5. A failed call shows the server's `error` text, or else `request failed (N)`.
- A6. `BASE` is the page path. `ROOT` strips `MOUNT` for server-issued URLs,
  so the page works directly and behind an ingress.

**Tabs and routes**
- R1. Sessions / Secrets / Grid console: one pane at a time, with `aria-selected`.
- R2. Hash routes: `#/`, `#/console`, `#/secrets`, `#/sessions/<k>`,
  `…/flows`, `…/flows/<name>`. Anything else goes to the list.
- R3. The console tab is hidden when the console URL is this page itself. The
  iframe gets its `src` only otherwise.
- R4. Going to Secrets from a session drops the session (no background refetch),
  closes its overlays and loads secrets.
- R5. A deep link into a session starts the live stream.
- R6. "← Sessions" goes to `#/`.

**Session list and live updates**
- L1. "Live sessions" with the badge: connecting… / live / reconnecting… / polling.
- L2. "Loading…" appears only when the list is empty. An error shows in the list.
- L3. Each card shows: the browser mark (🟢 🦊 🌊 🧭, 🌐 otherwise, with the
  browser as its title), a name pill, the session id or "no browser", a live or
  idle pill, a meta line (browser and version · per-folder counts, pluralised,
  falling back to `files_count` · started ago · node) and the URL line. A click
  goes to `#/sessions/<key>`. With no sessions it reads "No sessions yet."
- U1. The EventSource opens on the signed `events_url`, resolved against `ROOT`.
- U2. A message repaints the list when it is visible, and otherwise refreshes
  the open session.
- U3. Every 30 s, if nothing has arrived for 45 s, the badge reads "polling",
  the page refetches, applies the result, and reopens the stream on the freshly
  signed URL.

**Session detail**
- D1. The summary card: the mark, the name (or owner, or "Session"), live or idle,
  and the last page as a link only for http(s), otherwise as text or "nowhere
  yet". A session group (key and held-by only when unnamed; browser; window;
  started, localised) and a browser group (version, id, node). An empty group is
  omitted.
- D2. End browser is disabled unless attached. It asks through the native
  confirm with today's text, then sends DELETE. A failure alerts "Could not end
  the browser: …". On success, if the same session is still shown, the
  downloads overlays close and files and flows reload; otherwise the list
  reloads.
- D3. The Files | Flows subtabs carry counts (`files_count`, and the flow count
  or "off") and are routed by the hash. Switching tabs on the same session does
  not refetch.
- D4. Switching sessions closes the lightbox and modal, shows "Loading…" in every
  grid and in Flows, clears the header, blanks the counts, disables the clears,
  then loads files and then flows. It stops if the operator moves on in the
  meantime.
- D5. A deep-linked flow opens only if the listing contains it.

**Files**
- F1. Three accordions, Downloads, Screenshots and Files, open by default. The
  toggle is a real button with `aria-expanded` and `aria-controls`.
- F2. Each row has a count pill.
- F3. Clear downloads is enabled only when the browser is live and this
  session's files have loaded, and it is disabled while a load is in flight. Its
  confirm (scoped to downloads) lists every name with "— gone" or "— copy in
  Files stays", or says there is nothing to clear. The button reads "Clear N
  file(s)" or "Clear". It sends DELETE, then reloads.
- F4. Clear screenshots is disabled when there are none, or while loading. Its
  confirm reads "Deletes the 1 screenshot / all N screenshots … Anything you
  kept is in Files and stays." and lists every name. The button reads "Delete N
  screenshot(s)".
- F5. A tile shows a lazy thumbnail or a glyph by extension, the name, and the
  size · age. A click opens the lightbox, while a modifier-click or middle-click
  follows the link. The action button sits top-left: 📌 on Downloads and
  Screenshots, 🗑 on Files. It shows on hover, always shows on touch devices,
  and is keyboard-focusable. Keep is disabled during its request; a failure
  alerts "Could not keep that file: …", and success reloads. Delete asks
  through a "Delete a file" confirm.
- F6. The empty texts, including Downloads' "No downloads." versus "No browser —
  downloads go with it."
- F7. A failed load blanks the counts, shows the error in all three rows, and
  disables both clears.
- F8. A heartbeat refetches files only when `session_id:files_rev` changes. A
  browser change (`session_id` or `live`) closes the downloads overlays, blanks
  Downloads, disables Clear screenshots and forces a reload.
- F9. Files carries no clear action of its own — everything in it was kept on
  purpose, so only Downloads and Screenshots offer Clear (§F4.1).

**Lightbox**
- X1. The name, "i / n", Prev and Next (disabled at the ends), the action (📌
  Keep, or 🗑 Delete behind a confirm), Download and Close. An image shows as an
  image, a PDF in an iframe, anything else as a glyph with "No preview for this
  kind of file." and a Download link.
- X2. Esc, ← and → work, and are ignored while a modal is open. A backdrop click
  closes it.
- X3. The action is disabled while it runs. On success, the lightbox reloads to
  the newest data and stays at the same index, clamped to the end. It closes
  when the list is empty. A cancelled confirm is silent; any other failure
  alerts.

**Flows**
- W1. "Flows are off…" when there is no data dir, and "No flows yet." when there
  are none. Each list item shows the name, "N step(s)" and, only when shared, a
  globe, with the selected state marked.
- W2. Picking a flow clears the selection, shows "Loading…", puts the flow in the
  hash via `replaceState` (not a new history entry) and loads the document.
- W3. The panel shows the name, a global pill when shared, and three actions: ✏️,
  move (🌐 or 🏠, hidden for the `global` session) and 🗑️. Then the
  description and an outline. **Params** shows the type glyph, a red required
  star, or "This flow takes nothing." **Steps** shows the tool glyph (❓ when
  unknown), the id chip, and 🔒 on a step that types a secret. The detail pane
  says "Pick a parameter or a step…". Step detail has the number, chip and tool,
  the note, **Arguments** (key/value pairs, multi-line values as code, a
  selector unwrapped to `css …`, a secret as `name / key`, or "This step takes
  nothing.") and **Behaviour** (`onError`, `return`) only when set. Parameter
  detail has the type, required or optional, the description, **Default**, and
  **Used by** as clickable step rows, or "No step reads `${name}`…". A removed
  one reads "That step/parameter is gone." Clicking the picked row unpicks it.
- W4. Edit refetches the YAML first; a failure alerts "Could not read that
  flow: …". It opens "Edit <name>" with the raw file in a textarea. Save sends
  PUT `{yaml}`, stays open with an alert on error, and reloads flows if the same
  session is still shown.
- W5. Move shows today's wording for both directions, sends POST `move {to}`,
  closes the flow, `replaceState`s back to `…/flows` and reloads.
- W6. Delete shows its confirm, with a "global / " prefix for a shared flow,
  sends DELETE, and then behaves as Move does.
- W7. When the listing refreshes, a flow that has vanished closes and its hash
  resets. Otherwise its document reloads and the selection stays. A heartbeat
  reloads flows only when `flows_rev` moves.
- W8. A malformed flow file (`steps: {}`, `parameters: 1`, `[null]` steps, a
  prototype key like `constructor`) never breaks the panel.
- W9. The Flows tab holds the session detail's full width and has no accordion
  to open or close — Files' three-row layout does not carry over to it.

**Secrets**
- S1. "Loading…" first, then an error, the off message, or the heading and
  intro. Each secret gets a card: 🔑 and the name, a warning pill ("unusable
  until fixed" or "any site"), the description, the rejection reason, key pills,
  "allowed", "from" (source · location), and **USED BY** rows. A row shows the
  flow pill, its step(s), and either a "🌐 shared" marker or a `session →` link
  to `#/sessions/<s>/flows/<flow>`; with no uses it reads "No flow uses it." The
  "Named by a flow, not defined" section follows, with its text.

**Modal**
- M1. The title, body, and Cancel plus a confirm button (danger or primary). Esc,
  the backdrop and Cancel all take the cancel path, which runs `oncancel`. The
  confirm is disabled while running. A failure keeps the modal open and alerts.
  One modal at a time. It closes on a session switch, and a downloads-scoped
  one also closes on a browser change.

**MCP App**
- P1. It renders the component named by `structuredContent.component`
  (`fileSections`): three titled rows with counts, read-only tiles, and a
  read-only lightbox. An unknown name shows 'Nothing to show for "x".', and a
  host failure shows its message. The body is transparent, with no page padding.

**Grid console**
- C1. The explanatory note, and the iframe.

## Deliberate differences — approved by Dr K, 2026-09-25

1. **The MCP App starts working.** It has never started in any host, because
   `app.html` imports `…/ext-apps/dist/index.js`, and that path 404s on both 1.x
   and 2.x. The rewrite has to replace that import anyway, so it bundles
   `@modelcontextprotocol/ext-apps` from npm at a pinned version. That also
   means `https://unpkg.com` leaves the app's CSP, and the app no longer floats
   to whatever major ships next. The cost is ~78 KB gzipped inside the ui://
   document — measured: the app surface is 80.6 KB gzipped in total against a
   110 KB budget.
2. **The UI is optional (§F4.17).** With no UI build, `GET <mount>/` answers
   200 with a minimal page whose title and only heading are **Selenium Flow**,
   and the MCP App is not registered — the file tools behave as with
   `APPS_ENABLED=false` and still return their data. `admin.ui_built(name)` is
   the one test for it; startup logs one line saying the UI is not built.
3. **Styling polish, with no change to layout, text or behaviour** (§F4.16's
   allowance):
   - the modal and lightbox fade in, with the sheet scaling up slightly;
   - an accordion slides open and shut, and its caret rotates instead of
     swapping ▾ for ▸;
   - when a tile leaves the grid (Keep, Delete), its neighbours glide into place
     (`animate:flip`) and the tile fades out;
   - the lightbox crossfades between files as you step through them;
   - the live badge gets a soft pulsing dot while the stream is live.

   Every transition is at most 150 ms, is `|local` (no animation on first paint
   or on a route change), and drops to zero under `prefers-reduced-motion`.
   Outgoing elements must not linger where an XPath could match them twice, and
   the integration flows prove it.

## Testing

- **Component and unit tests:** Vitest with jsdom and `@testing-library/svelte`,
  colocated `*.test.ts` files. The pure logic (`format.ts`, `flow.ts`,
  `latest.ts`, the router) is unit-tested. Components are tested through what a
  user sees and does, not through their internals.
- **The 158 source-grep tests** in `tests/test_admin_ui.py` and
  `tests/test_files_and_admin.py` grep a file that will no longer exist. The
  plan's first task is a traceability table. For each test it names either
  (a) the inventory item and new test that replace it, or (b) why it is retired
  (it asserts an implementation detail, such as a function name or a
  `gone(key)` count). Tests in those files that exercise the Python API stay as
  they are.
- **Python serving tests** (pure Python, fixture static dir): `page()` inlines
  and substitutes; an unbuilt UI serves the Selenium Flow page and offers no MCP
  App; the app CSP no longer lists unpkg; the wheel contains the UI once it is
  built (in `package.yml`).
- **Integration:** the three flows in `tests/integration/flows/` run unchanged
  against the new page.
- **Visual parity:** before and after screenshots of every view (sign-in, list,
  detail on Files and on Flows, lightbox, each modal, Secrets, console, the MCP
  App), at desktop and phone widths and in light and dark. They are taken with
  selenium-flow itself against the branch image, and Dr K reviews them side by
  side before merge.
- **Proven not vacuous:** a behaviour test for a race guard (a late reply after
  a switch, a load overtaken during Keep) is shown failing with the guard
  removed, so no test passes by testing nothing.

## Budgets

- The admin page is at most 45 KB gzipped (today's figure). A Svelte runtime of
  ~15 KB plus minified code should land well under it, and the UI job reports
  the number on every PR. Measured: 35.1 KB gzipped.
- The MCP App grows by the SDK (~78 KB gzipped); see difference 1. Measured:
  80.6 KB gzipped, against a 110 KB budget.

## Rollout

One PR. As with #41: the branch image is deployed to the `flow` namespace, then
the server tests itself (the integration flows and the visual-parity pass), and
after merge the cluster pin moves back to `main`. Docs: the AGENTS.md
contributor notes gain the npm build step, the CHANGELOG gets an entry, and the
saga records what building it decided.

## Out of scope — noted, not done

- A CSP header on the admin page. Svelte makes a strict one possible (no
  `eval`, no inline handlers), but it is a new behaviour.
- Serving hashed assets with long-lived caching instead of inlining.
- A Vite dev server with HMR proxied to the Python server.
- The pre-existing Chapter 4 findings.
