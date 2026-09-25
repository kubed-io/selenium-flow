# The Admin UI on Svelte — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `static/` — the admin page, the MCP App shell, the shared components and the stylesheet — as Svelte 5 components built by Vite into `kubed/selenium_flow/http/static/`, looking and working exactly as today, with the UI optional for a pure-Python install.

**Architecture:** A Vite + Svelte + TypeScript project in `ui/` builds two surfaces (admin, app) to six fixed-name files inside the Python package, gitignored. `admin.py`'s `page()` keeps inlining each surface's JS and CSS into its shell. Without a build, the admin URL serves a "Selenium Flow" page and no MCP App is offered. The page's hand-written race guards become structure: a session's whole view is one keyed component whose loads abort when it goes.

**Tech Stack:** Svelte 5.57.1, Vite 8.3.1, @sveltejs/vite-plugin-svelte 7.3.1, TypeScript 6.0.3, Vitest 5.0.2 + jsdom 30.1.1 + @testing-library/svelte 5.4.2, ESLint 10 + eslint-plugin-svelte 3.23.0, svelte-check 4.7.6, @modelcontextprotocol/ext-apps 2.0.0; Python 3.10+, Starlette, pytest; Node 24.

**Spec:** `docs/superpowers/specs/2026-09-25-svelte-admin-ui-design.md` (approved by Dr K, 2026-09-25). Rulings: saga `Chapter_4_The_Hangar.md` §F4.14–§F4.17. **Read both before any task.** The spec's *Behaviour inventory* (A1…C1) is the parity contract every task cites.

## Global Constraints

- **Pure refactor (§F4.16).** No new views, buttons, fields, settings, routes or API changes. The only differences are the spec's three *Deliberate differences*: the MCP App starts working, the UI is optional, and the styling polish.
- **Generated output is never committed (§F4.15).** The build writes to `kubed/selenium_flow/http/static/`, which is gitignored; so is `ui/node_modules/`. `ui/package-lock.json` is source and IS committed.
- **pip never runs Node.** Unbuilt, `pip install .` and `python -m build` must succeed (§F4.17).
- **Unbuilt UI (§F4.17):** `GET <mount>/` answers 200 with exactly `PLACEHOLDER` (title and only heading **Selenium Flow**); the MCP App resource is not registered; startup logs one line naming `npm --prefix ui run build`.
- **Exact pinned versions** in `ui/package.json` (no `^`/`~`): the Tech Stack line above, plus `typescript-eslint 8.70.1`, `@eslint/js 10.0.1`, `globals 17.12.0`, `svelte-eslint-parser 1.8.1`, `@tsconfig/svelte 5.0.8`, `@testing-library/jest-dom 7.0.1`, `@testing-library/user-event 14.6.7`. TypeScript stays on **6.0.3** — `svelte-check` and `typescript-eslint` do not accept TS 7.
- **No `{@html}` anywhere.** `svelte/no-at-html-tags` is an ESLint **error**. No `innerHTML` in `ui/src`.
- **`app.css` is copied to `ui/src/app.css` byte-for-byte and stays global.** Component `<style>` blocks exist only for Task 12's polish and never target an element that exists today.
- **Frozen selectors** (the integration flows are not edited): ids `token`, `loginForm`, `tabFlows`, `screenshots`, `kept`; classes `card`, `name` (the session pill), `file`, `keep`; and the tile name is `<div class="name">` with **exactly** that class attribute. Keep every other id from today's markup on the same element too.
- **Every user-visible string is copied verbatim from today's `static/admin.html` / `static/components.js`** (including `…`, `—`, `’`, `‹`, `›`, `&rarr;` → `→`). Entities become literal characters.
- **`sessionStorage['sf-token']`**, never `localStorage`.
- **Svelte 5 runes only** (`$state`, `$derived`, `$effect`, `$props`); no Svelte 4 syntax (`export let`, `$:`, `on:click`, stores are not needed).
- **svelte-check: 0 errors, 0 warnings.** A needed `<!-- svelte-ignore … -->` carries a one-line reason.
- **Comments earn their lines** (Dr K trims narration): keep the non-obvious *why* from today's comments, drop the rest.
- CHANGELOG: one short line per user-visible change under `[Unreleased]` (CONTRIBUTING.md).
- **One PR, opened only after asking Dr K.** Never push a second branch or open a second PR.

## How to run things (every task)

```bash
cd /projects/modules/selenium-flow
export SFENV=/tmp/claude-1000/-projects-cluster/2f3f76f1-c974-4758-ae06-6f18b8dd88b9/scratchpad/sfenv
# a user-site `kubed` package shadows this namespace package, hence PYTHONNOUSERSITE
alias t='PYTHONNOUSERSITE=1 PYTHONPATH=$PWD:$SFENV python3 -m pytest -q -p no:cacheprovider --ignore=tests/integration'
alias lint='PYTHONNOUSERSITE=1 PYTHONPATH=$SFENV python3 -m ruff check kubed tests scripts'
# the UI (after Task 2)
npm --prefix ui ci            # once, and after package.json changes
npm --prefix ui test          # vitest run
npm --prefix ui run check     # svelte-check
npm --prefix ui run lint      # eslint
npm --prefix ui run build     # writes kubed/selenium_flow/http/static/
```

Record the Python baseline before Task 1 (`t`; expected ≈ `1379 passed, 20 skipped` on 196bd91). Never run the integration suite in the pod; CI runs it. Never run psalm/php-cs-fixer/Behat (not this repo; the rule stands).

## File map

| Path | Responsibility after this plan |
|---|---|
| `ui/package.json`, `ui/package-lock.json` | pinned toolchain; scripts `build`, `dev`, `test`, `check`, `lint`, `size` |
| `ui/vite.config.ts` | two surfaces by `--mode`, six fixed-name files, vitest config |
| `ui/svelte.config.js`, `ui/tsconfig.json`, `ui/eslint.config.js` | compiler, types, lint (`no-at-html-tags` = error) |
| `ui/scripts/size.mjs` | prints each surface's gzipped size; fails over budget |
| `ui/public/admin.html`, `ui/public/app.html` | the two shells with `__CSS__ __JS__ __MOUNT__ __CONSOLE__` |
| `ui/src/app.css` | today's `static/app.css`, verbatim, global |
| `ui/src/admin.ts`, `ui/src/app.ts` | entries |
| `ui/src/App.svelte` | the MCP App: SDK connection, renders one shared component |
| `ui/src/lib/types.ts` | API shapes |
| `ui/src/lib/format.ts` | `bytes`, `ago`, `browserMark`, `glyphFor`, `safeHref`, `countsText`, `metaLine` |
| `ui/src/lib/FileTile.svelte`, `FileGrid.svelte`, `FileSections.svelte`, `Lightbox.svelte`, `SessionList.svelte`, `SessionSummary.svelte` | shared components (both surfaces) |
| `ui/src/admin/api.ts` | `createApi`, `sessionPath` |
| `ui/src/admin/latest.ts` | `Latest` — newest load wins, `settled()` |
| `ui/src/admin/router.svelte.ts` | `parse`, `hashes`, `router`, `go`, `replace` |
| `ui/src/admin/flow.ts` | `mapping`, `listed`, `own`, `selectorText`, `bindsSecret`, `argValue`, `TOOL_ICON`, `TYPE_ICON` |
| `ui/src/admin/modal.ts`, `Modal.svelte` | `ModalSpec`; the one confirm/editor modal |
| `ui/src/admin/live.svelte.ts` | `Live`: sessions payload, EventSource, badge, fallback poll |
| `ui/src/admin/session.svelte.ts` | `SessionModel`, `NO_FILES`: one session's files/flows state and loads |
| `ui/src/admin/Admin.svelte`, `Login.svelte`, `SessionsView.svelte`, `ConsolePane.svelte` | shell, sign-in, list, console |
| `ui/src/admin/SessionDetail.svelte`, `Section.svelte`, `FilesPane.svelte` | a session: header, toolbar, tabs, files, overlays |
| `ui/src/admin/FlowsPane.svelte`, `StepRow.svelte`, `ParamRow.svelte`, `StepDetail.svelte`, `ParamDetail.svelte`, `Pair.svelte` | flows |
| `ui/src/admin/SecretsPane.svelte` | secrets |
| `ui/src/motion.ts` | Task 12: reduced-motion aware durations |
| `ui/src/test/setup.ts`, `ui/src/test/helpers.ts` | jest-dom matchers; `fakeFetch`, `deferred`, `FakeEventSource` |
| `kubed/selenium_flow/http/admin.py` | `static_path`, `ui_built`, `read`, `page`, `PLACEHOLDER`, the UI route |
| `kubed/selenium_flow/mcp/apps.py` | `available()`; CSP without unpkg; `page("app")` |
| `kubed/selenium_flow/server.py` | apps offered only when built; one log line when the UI is not built |
| `pyproject.toml`, `.gitignore`, `.dockerignore`, `Dockerfile` | packaging and image |
| `.github/workflows/ui.yml` (new), `package.yml`, `integration.yml`, `copilot-setup-steps.yml`, `image.yml`; `.github/dependabot.yml` | CI |
| `tests/conftest.py`, `tests/test_ui_serving.py` (new), `tests/test_packaging.py`, `tests/test_files_and_admin.py`; `tests/test_admin_ui.py` (deleted) | Python side |
| `static/` | **deleted** in Task 10 |

---

### Task 1: Traceability table, and the "before" pictures

The 109 tests in `tests/test_admin_ui.py`, the page-grepping tests in `tests/test_files_and_admin.py` (`test_the_admin_page_needs_no_token` through `test_the_components_render_no_action_buttons`, and `test_the_flow_panel_shows_a_selector_as_one_expression`), and the `static` row of `tests/test_packaging.py::DATA_DIRS` all read a file this plan deletes. Before anything moves, record what each one protects.

**Files:**
- Create: `docs/superpowers/plans/2026-09-25-svelte-admin-ui-traceability.md`

**Interfaces:**
- Produces: the table every later task consults, and Task 10 deletes against.

- [ ] **Step 1: Write the table.** One row per test, in file order:

```markdown
| Test | Protects (one line, in behaviour terms) | Becomes |
|---|---|---|
| test_admin_ui.py::test_files_and_flows_are_tabs | Files and Flows are tabs under a session | D3 → `SessionDetail.test.ts` "Files and Flows are tabs, routed by the hash" (Task 7) |
| test_admin_ui.py::test_clear_downloads_needs_a_live_browser_not_an_attached_one | Clear downloads needs `live`, not `attached` | F3 → `FilesPane.test.ts` "Clear downloads needs a live browser" (Task 7) |
| … | … | RETIRED: asserts the function name `filesSettled` — the guarantee is tested as X3 in `session.test.ts` (Task 7) |
```

Rules: "Becomes" names an inventory item (spec, A1…C1) **and** the test file + test title that will carry it, **and** the task that writes it. A row may say `RETIRED:` only when the test asserts an implementation detail (a function name, a `gone(key)` count, a string in source) whose *behaviour* is covered by another row — name that row. A behaviour that no inventory item covers is a spec gap: add it to the inventory in the spec in this same commit and cite it.

- [ ] **Step 2: Check coverage both ways.** Every inventory item A1…C1 appears in at least one "Becomes" cell or in a later task's test list below; every test row has a destination. Paste the two counts at the top of the file (`N tests mapped, M retired; every inventory item covered`).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-09-25-svelte-admin-ui-traceability.md docs/superpowers/specs/2026-09-25-svelte-admin-ui-design.md
git commit -m "Map every page test to the behaviour it protects before the page moves"
```

- [ ] **Step 4 (controller, not a subagent): the "before" pictures.** With selenium-flow's own tools against the live deployment (https://selenium.kellyferrone.com/flow/, sign in with `write` + `secret={"name":"admin","key":"token"}`), take `screenshot` of: sign-in, session list, a session on Files (with at least one download, one screenshot, one kept file), the lightbox, the Clear downloads confirm, a session on Flows with a step picked and with a param picked, the Edit YAML modal, Secrets, Grid console, — each at 1280×900 and 390×844 (`resize`), in the Grid browser's default (light) scheme. Name them `before-<view>-<width>.png` and `keep_file` each so they survive. Seed and clean up exactly as the Chapter 4 self-test did (memory: `selenium-flow-hangar-pr-41`). Dark mode is compared in Task 13 by emulating `prefers-color-scheme` via `execute_script` + CSS media override, before and after on the same build pair.

---

### Task 2: The `ui/` project, its tooling, and an empty build

**Files:**
- Create: `ui/package.json`, `ui/vite.config.ts`, `ui/svelte.config.js`, `ui/tsconfig.json`, `ui/eslint.config.js`, `ui/scripts/size.mjs`, `ui/public/admin.html`, `ui/public/app.html`, `ui/src/app.css` (copy), `ui/src/admin.ts`, `ui/src/app.ts`, `ui/src/admin/Admin.svelte` (stub), `ui/src/App.svelte` (stub), `ui/src/test/setup.ts`, `ui/src/test/helpers.ts`, `ui/src/smoke.test.ts`
- Modify: `.gitignore`, `.dockerignore`

**Interfaces:**
- Produces: `npm --prefix ui run build` → exactly `admin.html admin.css admin.js app.html app.css app.js` in `kubed/selenium_flow/http/static/`; the test helpers below, used by every later UI task.

- [ ] **Step 1: `ui/package.json`**

```json
{
  "name": "selenium-flow-ui",
  "private": true,
  "type": "module",
  "scripts": {
    "clean": "node -e \"require('fs').rmSync('../kubed/selenium_flow/http/static', {recursive: true, force: true})\"",
    "build": "npm run clean && vite build --mode admin && vite build --mode app",
    "dev": "npm run clean && (vite build --mode admin --watch & vite build --mode app --watch)",
    "test": "vitest run",
    "check": "svelte-check --tsconfig ./tsconfig.json --fail-on-warnings",
    "lint": "eslint .",
    "size": "node scripts/size.mjs"
  },
  "dependencies": {
    "@modelcontextprotocol/ext-apps": "2.0.0"
  },
  "devDependencies": {
    "@eslint/js": "10.0.1",
    "@sveltejs/vite-plugin-svelte": "7.3.1",
    "@testing-library/jest-dom": "7.0.1",
    "@testing-library/svelte": "5.4.2",
    "@testing-library/user-event": "14.6.7",
    "@tsconfig/svelte": "5.0.8",
    "eslint": "10.11.0",
    "eslint-plugin-svelte": "3.23.0",
    "globals": "17.12.0",
    "jsdom": "30.1.1",
    "svelte": "5.57.1",
    "svelte-check": "4.7.6",
    "svelte-eslint-parser": "1.8.1",
    "typescript": "6.0.3",
    "typescript-eslint": "8.70.1",
    "vite": "8.3.1",
    "vitest": "5.0.2"
  }
}
```

Then `npm --prefix ui install` to create `ui/package-lock.json`. If npm reports a peer conflict, stop and report it — do not use `--legacy-peer-deps` or bump a version without saying so.

- [ ] **Step 2: `ui/vite.config.ts`** — measured in a spike on 2026-09-25: two builds, fixed names, one JS + one CSS each, dynamic imports folded in (`codeSplitting: false`; Vite 8 deprecates `inlineDynamicImports`).

```ts
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { svelteTesting } from '@testing-library/svelte/vite'

// Inside the package, gitignored (§F4.15): pip collects it when it exists and
// installs fine without it (§F4.17). Never `build/` — setuptools prunes it
// from the sdist.
const OUT = '../kubed/selenium_flow/http/static'

// One surface per build. Each must be ONE script and ONE stylesheet, because
// page() inlines them: a shared chunk would be an import an inlined module
// cannot resolve.
export default defineConfig(({ mode }) => {
  const surface = mode === 'app' ? 'app' : 'admin'
  return {
    plugins: [svelte(), svelteTesting()],
    // The shells are copied once, by the admin build.
    publicDir: surface === 'admin' ? 'public' : false,
    build: {
      outDir: OUT,
      emptyOutDir: false,
      target: 'es2022',
      cssCodeSplit: false,
      rolldownOptions: {
        input: `src/${surface}.ts`,
        output: {
          entryFileNames: `${surface}.js`,
          assetFileNames: `${surface}[extname]`,
          codeSplitting: false,
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['src/test/setup.ts'],
      include: ['src/**/*.test.ts'],
      restoreMocks: true,
      unstubGlobals: true,
    },
  }
})
```

- [ ] **Step 3: compiler, types, lint**

`ui/svelte.config.js`:

```js
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte'

export default { preprocess: vitePreprocess() }
```

`ui/tsconfig.json`:

```json
{
  "extends": "@tsconfig/svelte/tsconfig.json",
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "allowJs": true,
    "checkJs": true,
    "isolatedModules": true,
    "verbatimModuleSyntax": true,
    "types": ["vite/client", "vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src/**/*.ts", "src/**/*.svelte", "vite.config.ts", "scripts/**/*.mjs"]
}
```

`ui/eslint.config.js`:

```js
import js from '@eslint/js'
import svelte from 'eslint-plugin-svelte'
import globals from 'globals'
import ts from 'typescript-eslint'

export default ts.config(
  js.configs.recommended,
  ...ts.configs.recommended,
  ...svelte.configs.recommended,
  { languageOptions: { globals: { ...globals.browser, ...globals.node } } },
  {
    files: ['**/*.svelte', '**/*.svelte.ts'],
    languageOptions: { parserOptions: { parser: ts.parser, extraFileExtensions: ['.svelte'] } },
  },
  {
    rules: {
      // The whole point of the move (spec, "Escaping becomes the default").
      'svelte/no-at-html-tags': 'error',
      'no-restricted-properties': ['error', { property: 'innerHTML', message: 'Render with a template.' }],
    },
  },
  { ignores: ['node_modules/', '../kubed/'] },
)
```

- [ ] **Step 4: the shells.** `ui/public/admin.html` — today's head comment trimmed to its *why*, the meta tags, and the mount point carrying the two server values as attributes (the same trust and context as today's `data-src="__CONSOLE__"`):

```html
<!-- The admin dashboard. Filled in by admin.page(): __CSS__ and __JS__ are the
     built bundle, __MOUNT__ and __CONSOLE__ come from the server. -->
<!-- In the document, not only the header: the charset because the UI is largely
     glyphs and a host that re-serves this loses the header; the viewport because
     a phone otherwise lays the page out at 980px. -->
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>selenium-flow</title>
<style>__CSS__</style>
<div id="root" data-mount="__MOUNT__" data-console="__CONSOLE__"></div>
<script type="module">__JS__</script>
```

`ui/public/app.html`:

```html
<!-- The MCP App shell: one shared component, named by the tool result. Filled
     in by admin.page("app"). -->
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>selenium-flow</title>
<style>__CSS__</style>
<style>
  /* A panel in someone else's window, not a page. */
  body { background: transparent; }
  .wrap { padding: 0; max-width: none; }
</style>
<div class="wrap"><div id="root"><div class="empty">Loading…</div></div></div>
<script type="module">__JS__</script>
```

- [ ] **Step 5: the stylesheet and stub entries.** `cp static/app.css ui/src/app.css` (then `cmp static/app.css ui/src/app.css` prints nothing). Entries:

`ui/src/admin.ts`:

```ts
import './app.css'
import { mount } from 'svelte'
import Admin from './admin/Admin.svelte'

const root = document.getElementById('root')!
mount(Admin, {
  target: root,
  props: { mount: root.dataset.mount ?? '', console: root.dataset.console ?? '/' },
})
```

`ui/src/app.ts`:

```ts
import './app.css'
import { mount } from 'svelte'
import App from './App.svelte'

const root = document.getElementById('root')!
root.textContent = ''
mount(App, { target: root })
```

Stubs (replaced in Tasks 5 and 6): `ui/src/admin/Admin.svelte`

```svelte
<script lang="ts">
  let { mount, console: consoleUrl }: { mount: string; console: string } = $props()
</script>

<p data-mount={mount} data-console={consoleUrl}>selenium-flow</p>
```

`ui/src/App.svelte`: `<div class="empty">Loading…</div>`

- [ ] **Step 6: test setup and helpers.** `ui/src/test/setup.ts`:

```ts
import '@testing-library/jest-dom/vitest'
```

`ui/src/test/helpers.ts`:

```ts
import { vi } from 'vitest'

export interface Reply { status?: number; body?: unknown }
type Route = Reply | ((init: RequestInit) => Reply | Promise<Reply>)

export function deferred<T = void>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((a, b) => { resolve = a; reject = b })
  return { promise, resolve, reject }
}

/** A fetch that answers `"METHOD /path"`; anything else is a 404 with an error body. */
export function fakeFetch(routes: Record<string, Route>) {
  const calls: { method: string; path: string; body?: unknown; headers: Headers; signal?: AbortSignal }[] = []
  const fn = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase()
    const path = new URL(url, 'http://test').pathname
    calls.push({
      method, path, headers: new Headers(init.headers),
      body: init.body ? JSON.parse(String(init.body)) : undefined,
      signal: init.signal ?? undefined,
    })
    const route = routes[`${method} ${path}`]
    const reply = typeof route === 'function' ? await route(init) : route
    if (init.signal?.aborted) throw new DOMException('aborted', 'AbortError')
    const r = reply ?? { status: 404, body: { error: `no route ${method} ${path}` } }
    return new Response(JSON.stringify(r.body ?? {}), {
      status: r.status ?? 200, headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fn)
  return { fn, calls }
}

export class FakeEventSource {
  static last: FakeEventSource | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeEventSource.last = this }
  close() { this.closed = true }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }) }
}
```

`ui/src/smoke.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import Admin from './admin/Admin.svelte'

test('a component renders under jsdom', () => {
  render(Admin, { mount: '/flow', console: '/' })
  expect(screen.getByText('selenium-flow')).toHaveAttribute('data-mount', '/flow')
})
```

- [ ] **Step 7: `ui/scripts/size.mjs`** — the budget from the spec (admin ≤ 45 KB gzipped):

```js
import { gzipSync } from 'node:zlib'
import { readFileSync } from 'node:fs'

const OUT = new URL('../../kubed/selenium_flow/http/static/', import.meta.url)
const BUDGET = { admin: 45 * 1024, app: 110 * 1024 }
let over = false
for (const surface of ['admin', 'app']) {
  const bytes = ['html', 'css', 'js']
    .map((ext) => readFileSync(new URL(`${surface}.${ext}`, OUT)))
    .reduce((sum, b) => sum + gzipSync(b, { level: 9 }).length, 0)
  const ok = bytes <= BUDGET[surface]
  over ||= !ok
  console.log(`${surface}: ${(bytes / 1024).toFixed(1)} KB gzipped (budget ${BUDGET[surface] / 1024} KB)${ok ? '' : ' OVER'}`)
}
process.exit(over ? 1 : 0)
```

- [ ] **Step 8: ignores.** Append to `.gitignore`:

```gitignore
# The built UI (npm --prefix ui run build). Generated, never committed (§F4.15).
kubed/selenium_flow/http/static/
ui/node_modules/
```

Append the same two paths to `.dockerignore` (the image builds its own; a local build must not leak in).

- [ ] **Step 9: Run everything**

Run: `npm --prefix ui ci && npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint && npm --prefix ui run build && ls kubed/selenium_flow/http/static && git status --short`
Expected: 1 test passes; check and lint clean; `admin.css admin.html admin.js app.css app.html app.js`; `git status` shows only `ui/…`, `.gitignore`, `.dockerignore` — **no** static output.

- [ ] **Step 10: Commit**

```bash
git add ui .gitignore .dockerignore
git commit -m "A Svelte + Vite project in ui/ that builds two surfaces into the package, ignored"
```

---

### Task 3: The pure modules

**Files:**
- Create: `ui/src/lib/types.ts`, `ui/src/lib/format.ts`, `ui/src/admin/api.ts`, `ui/src/admin/latest.ts`, `ui/src/admin/router.svelte.ts`, `ui/src/admin/flow.ts`
- Test: `ui/src/lib/format.test.ts`, `ui/src/admin/api.test.ts`, `ui/src/admin/latest.test.ts`, `ui/src/admin/router.test.ts`, `ui/src/admin/flow.test.ts`

**Interfaces:**
- Produces (exact names; later tasks import them):
  - `types.ts`: `FileEntry`, `FilesData`, `Counts`, `SessionRow`, `SessionsPayload`, `FilesResponse`, `FlowSummary`, `FlowsListing`, `FlowDoc`, `SecretUse`, `Secret`, `SecretsPayload`, `Folder = 'downloads' | 'screenshots' | 'files'`
  - `format.ts`: `bytes(n)`, `ago(ms, now?)`, `browserMark(name)`, `glyphFor(name)`, `safeHref(u)`, `countsText(s)`, `metaLine(s, now?)`, `sessionLabel(s)`
  - `api.ts`: `type Api = <T>(path: string, method?: string, body?: unknown, signal?: AbortSignal) => Promise<T>`, `createApi({base, token, onUnauthorized}): Api`, `sessionPath(key, rest?)`
  - `latest.ts`: `class Latest { run(load, paint, fail?): Promise<void>; settled(): Promise<void>; abort(): void }`
  - `router.svelte.ts`: `type Route`, `parse(hash)`, `hashes`, `router` (`{ route: Route }`, reactive), `go(hash)`, `replace(hash)`, `listen(): () => void`
  - `flow.ts`: `mapping`, `listed`, `own`, `selectorText`, `bindsSecret`, `argValue`, `TOOL_ICON`, `TYPE_ICON`

- [ ] **Step 1: `types.ts`** (no test; checked by `svelte-check` through its users)

```ts
export type Folder = 'downloads' | 'screenshots' | 'files'

export interface FileEntry {
  name: string
  size: number
  url: string
  image?: boolean
  created?: number | null
  content_type?: string | null
}

export interface FilesData {
  downloads: FileEntry[]
  screenshots: FileEntry[]
  files: FileEntry[]
  browser: boolean
}

export interface Counts { downloads: number; screenshots: number; files: number }

export interface SessionRow {
  key: string
  name?: string | null
  owner?: string | null
  browser?: string | null
  version?: string | null
  session_id?: string | null
  live?: boolean
  attached?: boolean
  started?: number | null
  node?: string | null
  url?: string | null
  window?: string | null
  counts?: Counts | null
  files_count?: number | null
  files_rev?: string | number | null
  flows_rev?: string | number | null
}

export interface SessionsPayload { sessions: SessionRow[]; events_url?: string }

export interface FilesResponse {
  session?: SessionRow
  downloads?: FileEntry[]
  screenshots?: FileEntry[]
  files?: FileEntry[]
  browser?: boolean
}

export interface FlowSummary { name: string; step_count: number; shared?: boolean }
export interface FlowsListing { enabled: boolean; flows?: FlowSummary[]; session?: string; rev?: string | number | null }

/** A stored flow, as edited by a person: nothing about its shape is guaranteed (§F1.6). */
export interface FlowDoc {
  name: string
  shared?: boolean
  description?: string
  steps?: unknown
  parameters?: unknown
  uses?: unknown
  yaml?: string
}

export interface SecretUse { flow: string; steps: number[]; shared?: boolean; session?: string }
export interface Secret {
  name: string
  description?: string
  keys?: string[]
  restricted?: boolean
  allowed_urls?: string[]
  allowed_urls_rejected?: string | string[]
  source?: string
  location?: string
  uses?: SecretUse[]
}
export interface SecretsPayload {
  enabled: boolean
  secrets: Secret[]
  undefined: { name: string; uses: SecretUse[] }[]
}
```

- [ ] **Step 2: failing tests for `format.ts`** (`ui/src/lib/format.test.ts`)

```ts
import { describe, expect, test } from 'vitest'
import { ago, browserMark, bytes, countsText, glyphFor, metaLine, safeHref, sessionLabel } from './format'

describe('format', () => {
  test('bytes', () => {
    expect(bytes(512)).toBe('512 B')
    expect(bytes(2048)).toBe('2.0 KB')
    expect(bytes(3 * 1048576)).toBe('3.0 MB')
    expect(bytes(Number.NaN)).toBe('')
  })
  test('ago', () => {
    const now = 1_000_000_000
    expect(ago(0, now)).toBe('')
    expect(ago(now - 5_000, now)).toBe('5s ago')
    expect(ago(now - 120_000, now)).toBe('2m ago')
    expect(ago(now - 7_200_000, now)).toBe('2h ago')
  })
  test('browserMark is keyed on the browser actually running', () => {
    expect(browserMark('chrome')).toBe('🟢')
    expect(browserMark('Firefox')).toBe('🦊')
    expect(browserMark('msedge')).toBe('🌊')
    expect(browserMark('safari')).toBe('🧭')
    expect(browserMark(null)).toBe('🌐')
  })
  test('glyphFor never finds what Object gave the map', () => {
    expect(glyphFor('a.PDF')).toBe('📄')
    expect(glyphFor('noext')).toBe('📁')
    expect(glyphFor('x.constructor')).toBe('📁')
  })
  test('safeHref only lets http(s) through', () => {
    expect(safeHref('https://a.b/c')).toBe('https://a.b/c')
    expect(safeHref('javascript:alert(1)')).toBe('')
    expect(safeHref(null)).toBe('')
  })
  test('countsText says each folder in its own word, and falls back to files_count', () => {
    expect(countsText({ key: 'k', counts: { downloads: 1, screenshots: 2, files: 0 } })).toBe('1 download · 2 screenshots')
    expect(countsText({ key: 'k', files_count: 1 })).toBe('1 file')
    expect(countsText({ key: 'k' })).toBe('')
  })
  test('metaLine', () => {
    const now = 1_000_000_000
    expect(metaLine({ key: 'k', browser: 'chrome', version: '140', counts: { downloads: 0, screenshots: 1, files: 0 }, started: (now - 60_000) / 1000, node: 'n1' }, now))
      .toBe('chrome 140 · 1 screenshot · 1m ago · n1')
  })
  test('sessionLabel', () => {
    expect(sessionLabel({ key: 'k', name: 'mine' })).toBe('mine')
    expect(sessionLabel({ key: 'k', owner: 'stdio' })).toBe('stdio')
    expect(sessionLabel({ key: 'k' })).toBe('session')
  })
})
```

Run: `npm --prefix ui test -- format` → FAIL (module missing).

- [ ] **Step 3: `format.ts`**

```ts
import type { SessionRow } from './types'

const GLYPH: Record<string, string> = {
  pdf: '📄', png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
  html: '🌐', htm: '🌐', csv: '📊', json: '📊', xml: '📊',
  zip: '🗜️', gz: '🗜️', mp4: '🎬', webm: '🎬', mov: '🎬', txt: '📝', md: '📝',
}

/* Keyed on the capability the Grid reports — the browser actually running,
   not the one asked for. */
const BROWSER: Record<string, string> = { chrome: '🟢', firefox: '🦊', msedge: '🌊', edge: '🌊', safari: '🧭' }

const has = (map: Record<string, string>, key: string) => Object.hasOwn(map, key)

export function browserMark(name?: string | null): string {
  const key = String(name ?? '').toLowerCase()
  return has(BROWSER, key) ? BROWSER[key] : '🌐'
}

export function glyphFor(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase()
  return has(GLYPH, ext) ? GLYPH[ext] : '📁'
}

/* Escaping makes a URL safe to display, not safe to click: `javascript:` is a
   URL too. */
export const safeHref = (u: unknown): string => (/^https?:\/\//i.test(String(u ?? '')) ? String(u) : '')

export function bytes(n: number): string {
  if (!Number.isFinite(n)) return ''
  if (n < 1024) return n + ' B'
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1048576).toFixed(1) + ' MB'
}

export function ago(ms?: number | null, now = Date.now()): string {
  if (!ms) return ''
  const s = Math.max(0, (now - ms) / 1000)
  if (s < 60) return Math.round(s) + 's ago'
  if (s < 3600) return Math.round(s / 60) + 'm ago'
  return Math.round(s / 3600) + 'h ago'
}

/* Each folder gets its own word — any one can be zero without the others
   being. Older rows carry only files_count. */
export function countsText(s: SessionRow): string {
  const c = s.counts
  if (c) {
    return ([[c.downloads, ' download'], [c.screenshots, ' screenshot'], [c.files, ' file']] as const)
      .filter(([n]) => n)
      .map(([n, word]) => n + word + (n === 1 ? '' : 's'))
      .join(' · ')
  }
  return s.files_count === undefined || s.files_count === null
    ? '' : s.files_count + ' file' + (s.files_count === 1 ? '' : 's')
}

export function metaLine(s: SessionRow, now = Date.now()): string {
  const meta = countsText(s)
  return [s.browser, s.version].filter(Boolean).join(' ')
    + (meta ? ' · ' + meta : '')
    + (s.started ? ' · ' + ago(s.started * 1000, now) : '')
    + (s.node ? ' · ' + s.node : '')
}

/* The headline is the session, not the browser: a session outlives its browsers. */
export const sessionLabel = (s: SessionRow): string => s.name || s.owner || 'session'
```

Run: `npm --prefix ui test -- format` → PASS.

- [ ] **Step 4: failing tests for `api.ts`** (`ui/src/admin/api.test.ts`)

```ts
import { expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi, sessionPath } from './api'

test('every call carries the bearer token and hangs off BASE', async () => {
  const { calls } = fakeFetch({ 'GET /flow/admin/sessions': { body: { sessions: [] } } })
  const api = createApi({ base: '/flow', token: () => 'tok', onUnauthorized: () => {} })
  expect(await api('/admin/sessions')).toEqual({ sessions: [] })
  expect(calls[0].headers.get('Authorization')).toBe('Bearer tok')
})

test('a body is JSON with its content type', async () => {
  const { calls } = fakeFetch({ 'PUT /x': { body: {} } })
  await createApi({ base: '', token: () => 't', onUnauthorized: () => {} })('/x', 'PUT', { yaml: 'a: 1' })
  expect(calls[0].body).toEqual({ yaml: 'a: 1' })
  expect(calls[0].headers.get('Content-Type')).toBe('application/json')
})

test('a 401 signs out and fails the call (A4)', async () => {
  fakeFetch({ 'GET /x': { status: 401 } })
  const onUnauthorized = vi.fn()
  await expect(createApi({ base: '', token: () => 't', onUnauthorized })('/x')).rejects.toThrow('unauthorized')
  expect(onUnauthorized).toHaveBeenCalledOnce()
})

test("a failure says the server's own words, else the status (A5)", async () => {
  fakeFetch({ 'GET /a': { status: 400, body: { error: "the shared 'global' library is read-only" } }, 'GET /b': { status: 502 } })
  const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
  await expect(api('/a')).rejects.toThrow("the shared 'global' library is read-only")
  await expect(api('/b')).rejects.toThrow('request failed (502)')
})

test('sessionPath encodes the key', () => {
  expect(sessionPath('a b/c', '/files')).toBe('/admin/sessions/a%20b%2Fc/files')
})
```

- [ ] **Step 5: `api.ts`**

```ts
export type Api = <T = unknown>(path: string, method?: string, body?: unknown, signal?: AbortSignal) => Promise<T>

export function createApi(opts: { base: string; token: () => string; onUnauthorized: () => void }): Api {
  return async function api<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
    const res = await fetch(opts.base + path, {
      method,
      signal,
      headers: {
        Authorization: 'Bearer ' + opts.token(),
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (res.status === 401) { opts.onUnauthorized(); throw new Error('unauthorized') }
    if (!res.ok) {
      // The server's own message: it names the rule an operator hit.
      let said = ''
      try { said = (await res.json()).error || '' } catch { /* not JSON */ }
      throw new Error(said || 'request failed (' + res.status + ')')
    }
    return res.json() as Promise<T>
  }
}

export const sessionPath = (key: string, rest = ''): string =>
  '/admin/sessions/' + encodeURIComponent(key) + rest
```

- [ ] **Step 6: failing tests for `latest.ts`** (`ui/src/admin/latest.test.ts`) — the property today's four sequence counters and `filesSettled` guarded:

```ts
import { expect, test, vi } from 'vitest'
import { deferred } from '../test/helpers'
import { Latest } from './latest'

test('an overtaken load never paints, even when it answers last', async () => {
  const loads = new Latest()
  const a = deferred<string>(), b = deferred<string>()
  const paint = vi.fn()
  const first = loads.run(() => a.promise, paint)
  const second = loads.run(() => b.promise, paint)
  b.resolve('B'); await second
  a.resolve('A'); await first
  expect(paint.mock.calls).toEqual([['B']])
})

test('an overtaken load is aborted', async () => {
  const loads = new Latest()
  let signal: AbortSignal | undefined
  void loads.run((s) => { signal = s; return new Promise(() => {}) }, () => {})
  void loads.run(() => Promise.resolve(1), () => {})
  expect(signal?.aborted).toBe(true)
})

test('an overtaken failure is silent too', async () => {
  const loads = new Latest()
  const a = deferred<string>()
  const fail = vi.fn()
  const first = loads.run(() => a.promise, () => {}, fail)
  await loads.run(() => Promise.resolve('B'), () => {}, fail)
  a.reject(new Error('late')); await first
  expect(fail).not.toHaveBeenCalled()
})

test('settled() waits for the newest load, not the one it was handed (the Keep race)', async () => {
  const loads = new Latest()
  const mine = deferred<string>(), poll = deferred<string>()
  const painted: string[] = []
  void loads.run(() => mine.promise, (v) => painted.push(v))
  const settled = loads.settled()
  void loads.run(() => poll.promise, (v) => painted.push(v)) // the poll overtakes
  mine.resolve('stale')
  let done = false
  void settled.then(() => { done = true })
  await Promise.resolve(); await Promise.resolve()
  expect(done).toBe(false)
  poll.resolve('fresh'); await settled
  expect(painted).toEqual(['fresh'])
})

test('abort() stops everything in flight from painting', async () => {
  const loads = new Latest()
  const a = deferred<string>()
  const paint = vi.fn()
  const run = loads.run(() => a.promise, paint)
  loads.abort()
  a.resolve('A'); await run
  expect(paint).not.toHaveBeenCalled()
})
```

- [ ] **Step 7: `latest.ts`**

```ts
/* One panel's loads. Two can overlap for the same session — a slow answer for
   revision A landing after a fast one for B — so only the newest may paint, and
   the older one is aborted rather than left to finish for nothing. */
export class Latest {
  #controller: AbortController | null = null
  #seq = 0
  #current: Promise<void> = Promise.resolve()

  run<T>(load: (signal: AbortSignal) => Promise<T>, paint: (value: T) => void, fail?: (error: Error) => void): Promise<void> {
    this.#controller?.abort()
    const controller = (this.#controller = new AbortController())
    const mine = ++this.#seq
    const live = () => mine === this.#seq && !controller.signal.aborted
    const done = (async () => {
      try {
        const value = await load(controller.signal)
        if (live()) paint(value)
      } catch (error) {
        if (live() && fail) fail(error as Error)
      }
    })()
    this.#current = done
    return done
  }

  /* An overtaken load returns without painting, so awaiting it alone can hand
     back data from before what the caller just did. Wait for the newest. */
  async settled(): Promise<void> {
    let seen: number
    do { seen = this.#seq; await this.#current } while (seen !== this.#seq)
  }

  abort(): void {
    this.#controller?.abort()
    this.#seq++
  }
}
```

- [ ] **Step 8: failing tests for the router** (`ui/src/admin/router.test.ts`)

```ts
import { afterEach, expect, test } from 'vitest'
import { go, hashes, listen, parse, replace, router } from './router.svelte'

afterEach(() => { history.replaceState(null, '', '#/') })

test('parse (R2)', () => {
  expect(parse('')).toEqual({ view: 'list' })
  expect(parse('#/')).toEqual({ view: 'list' })
  expect(parse('#/console')).toEqual({ view: 'console' })
  expect(parse('#/secrets')).toEqual({ view: 'secrets' })
  expect(parse('#/sessions/a%20b')).toEqual({ view: 'session', key: 'a b', tab: 'files', flow: undefined })
  expect(parse('#/sessions/k/flows')).toEqual({ view: 'session', key: 'k', tab: 'flows', flow: undefined })
  expect(parse('#/sessions/k/flows/my%2Fflow')).toEqual({ view: 'session', key: 'k', tab: 'flows', flow: 'my/flow' })
  expect(parse('#/nonsense')).toEqual({ view: 'list' })
})

test('hashes round-trip through parse', () => {
  expect(parse(hashes.flow('a b', 'f/1'))).toEqual({ view: 'session', key: 'a b', tab: 'flows', flow: 'f/1' })
  expect(hashes.session('k')).toBe('#/sessions/k')
  expect(hashes.flows('k')).toBe('#/sessions/k/flows')
})

test('replace moves the address and the route without a history entry', () => {
  const before = history.length
  replace(hashes.flows('k'))
  expect(location.hash).toBe('#/sessions/k/flows')
  expect(router.route).toEqual({ view: 'session', key: 'k', tab: 'flows', flow: undefined })
  expect(history.length).toBe(before)
})

test('go routes through hashchange', async () => {
  const stop = listen()
  go('#/secrets')
  await new Promise((r) => window.addEventListener('hashchange', r, { once: true }))
  expect(router.route).toEqual({ view: 'secrets' })
  stop()
})
```

- [ ] **Step 9: `router.svelte.ts`**

```ts
/* The hash, not the history API: every other path under this mount is a real
   endpoint, and a history route would need a catch-all that answers a mistyped
   API call with HTML. A hash never reaches the server. */
export type Route =
  | { view: 'list' }
  | { view: 'console' }
  | { view: 'secrets' }
  | { view: 'session'; key: string; tab: 'files' | 'flows'; flow: string | undefined }

export function parse(hash: string): Route {
  const [view, key, tab, flow] = hash.replace(/^#\/?/, '').split('/')
  if (view === 'console') return { view: 'console' }
  if (view === 'secrets') return { view: 'secrets' }
  if (view === 'sessions' && key) {
    return {
      view: 'session',
      key: decodeURIComponent(key),
      tab: tab === 'flows' ? 'flows' : 'files',
      flow: flow ? decodeURIComponent(flow) : undefined,
    }
  }
  return { view: 'list' }
}

const enc = encodeURIComponent
export const hashes = {
  list: '#/',
  console: '#/console',
  secrets: '#/secrets',
  session: (key: string) => '#/sessions/' + enc(key),
  flows: (key: string) => '#/sessions/' + enc(key) + '/flows',
  flow: (key: string, name: string) => '#/sessions/' + enc(key) + '/flows/' + enc(name),
}

export const router = $state<{ route: Route }>({ route: parse(location.hash) })

export const go = (hash: string) => { location.hash = hash }

/* In the address bar and the route, without a history entry and without
   hashchange: picking through a dozen flows should not take a dozen Backs. */
export function replace(hash: string) {
  history.replaceState(null, '', hash)
  router.route = parse(hash)
}

export function listen(): () => void {
  const on = () => { router.route = parse(location.hash) }
  window.addEventListener('hashchange', on)
  on()
  return () => window.removeEventListener('hashchange', on)
}
```

- [ ] **Step 10: failing tests for `flow.ts`** (`ui/src/admin/flow.test.ts`) — W8's guards:

```ts
import { expect, test } from 'vitest'
import { argValue, bindsSecret, listed, mapping, own, selectorText, TOOL_ICON } from './flow'

test('mapping and listed say what a thing has to be to be used as one (W8)', () => {
  expect(mapping({ a: 1 })).toEqual({ a: 1 })
  expect(mapping([1])).toEqual({})
  expect(mapping(null)).toEqual({})
  expect(listed([1])).toEqual([1])
  expect(listed({})).toEqual([])
})

test('own never finds what Object gave the map (W8)', () => {
  expect(own(TOOL_ICON, 'navigate')).toBe('🧭')
  expect(own(TOOL_ICON, 'constructor')).toBeUndefined()
  expect(own(TOOL_ICON, 'toString')).toBeUndefined()
})

test('a selector reads as one expression (W3)', () => {
  expect(selectorText({ css: 'button.go' })).toBe('css button.go')
  expect(selectorText({ xpath: '//a' })).toBe('xpath //a')
  expect(selectorText({ css: 'a', xpath: '//a' })).toBeNull()
  expect(selectorText('css a')).toBeNull()
})

test('argValue: a secret by reference, a selector unwrapped, objects as JSON', () => {
  expect(argValue('secret', { name: 'admin', key: 'token' })).toBe('admin / token')
  expect(argValue('secret', {})).toBe('a secret')
  expect(argValue('selector', { css: 'a' })).toBe('css a')
  expect(argValue('options', { a: 1 })).toBe('{"a":1}')
  expect(argValue('url', '${admin}/')).toBe('${admin}/')
  expect(argValue('n', null)).toBe('null')
})

test('bindsSecret', () => {
  expect(bindsSecret({ args: { secret: { name: 'a', key: 'b' } } })).toBe(true)
  expect(bindsSecret({ args: {} })).toBe(false)
  expect(bindsSecret(null)).toBe(false)
})
```

- [ ] **Step 11: `flow.ts`**

```ts
/* A stored flow is a file a person edits (§F1.6) and nothing validates its
   shape on the way out, so `steps: {}` and `parameters: 1` reach the panel —
   the screen you open in order to reach the editor and fix them. */
export const mapping = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}

export const listed = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

/* Keys come from that same YAML, so `tool: constructor` is reachable. */
export const own = <T>(map: Record<string, T>, key: string): T | undefined =>
  Object.hasOwn(map, key) ? map[key] : undefined

/* One glyph per action a step may dispatch to (`flowrun.RUNNABLE`); anything
   else is refused by the runner, and its ❓ is meant to look wrong. */
export const TOOL_ICON: Record<string, string> = {
  navigate: '🧭', interact: '👆', drag: '🤜', write: '✍️', press_key: '⌨️',
  extract: '🔍', execute_script: '📜', screenshot: '📷', frame: '🪟', resize: '📐',
  dialog: '💬', upload_file: '📤', print: '🖨️', assert: '✅',
}

/* JSON types already have glyphs everyone reads; at 12px an emoji is a smudge. */
export const TYPE_ICON: Record<string, string> = {
  string: '"', number: '#', integer: '#', boolean: '?', object: '{}', array: '[]',
}

/* Only `write` has a `secret` argument; it types what nobody can see. */
export const bindsSecret = (step: unknown): boolean => !!mapping(mapping(step).args).secret

/* A selector carries exactly one of css/xpath (§F2.14); the run report
   unwraps it the same way (`flows.run.summarise`). */
export function selectorText(v: unknown): string | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  const o = v as Record<string, unknown>
  const named = ['css', 'xpath'].filter((k) => own(o, k))
  return named.length === 1 && Object.keys(o).length === 1 ? named[0] + ' ' + o[named[0]] : null
}

export function argValue(key: string, v: unknown): string {
  if (key === 'secret') {
    const s = mapping(v)
    return s.name ? s.name + ' / ' + s.key : 'a secret'
  }
  const selector = selectorText(v)
  if (selector !== null) return selector
  return v !== null && typeof v === 'object' ? JSON.stringify(v) : String(v)
}
```

Check each glyph against today's `TOOL_ICON` entity (`&#129517;` = 🧭, `&#128070;` = 👆, `&#129308;` = 🤜, `&#9997;&#65039;` = ✍️, `&#9000;&#65039;` = ⌨️, `&#128269;` = 🔍, `&#128220;` = 📜, `&#128247;` = 📷, `&#129695;` = 🪟, `&#128208;` = 📐, `&#128172;` = 💬, `&#128228;` = 📤, `&#128424;&#65039;` = 🖨️, `&#9989;` = ✅) — decode with `python3 -c "import html;print(html.unescape('&#129517;'))"` if unsure.

- [ ] **Step 12: Run and commit**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint` → all pass, clean.

```bash
git add ui/src
git commit -m "The page's pure logic as typed, tested modules: formatting, API, newest-load-wins, routes, flow guards"
```

---

### Task 4: The shared components

The components both surfaces render. They take data and optional callbacks, never a token (§F1.36): without `action` a tile renders no button, which is exactly what the MCP App needs.

**Files:**
- Create: `ui/src/lib/FileTile.svelte`, `FileGrid.svelte`, `FileSections.svelte`, `Lightbox.svelte`, `SessionList.svelte`, `SessionSummary.svelte`
- Test: `ui/src/lib/FileGrid.test.ts`, `Lightbox.test.ts`, `SessionList.test.ts`, `SessionSummary.test.ts`, `FileSections.test.ts`

**Interfaces:**
- Consumes: `types.ts`, `format.ts` (Task 3)
- Produces:
  - `FileGrid` props `{ files: FileEntry[]; base?: string; action?: 'keep' | 'delete'; empty?: string; onopen?: (i: number) => void; onkeep?: (f: FileEntry) => Promise<boolean>; ondelete?: (f: FileEntry) => void }`
  - `FileTile` props `{ f: FileEntry; base: string; action?: 'keep' | 'delete'; onopen: () => void; onkeep?: …; ondelete?: … }` — renders the tile's *contents*; `FileGrid` owns the `<div class="file">` (so Task 12 can animate it)
  - `Lightbox` props `{ files: FileEntry[]; index: number; base?: string; action?: { label: string; run: (f: FileEntry) => Promise<unknown> }; refresh?: (at: number) => Promise<{ files: FileEntry[]; index: number } | null>; onclose: () => void }`
  - `FileSections` props `{ data: { downloads?: FileEntry[]; screenshots?: FileEntry[]; files?: FileEntry[] }; base?: string }`
  - `SessionList` props `{ data: { sessions?: SessionRow[] } | null; onpick?: (key: string) => void }`
  - `SessionSummary` props `{ data: SessionRow | null }`

- [ ] **Step 1: failing tests** — `ui/src/lib/FileGrid.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import FileGrid from './FileGrid.svelte'

const png = { name: 'hangar-check.png', size: 2048, url: '/f/1', image: true, created: Date.now() - 5000 }
const pdf = { name: 'report.pdf', size: 10, url: '/f/2' }

test('an empty grid says so (F6)', () => {
  render(FileGrid, { files: [], empty: 'No screenshots yet.' })
  expect(screen.getByText('No screenshots yet.')).toHaveClass('empty')
})

test('the default empty text', () => {
  render(FileGrid, { files: [] })
  expect(screen.getByText('No files in this session yet.')).toBeInTheDocument()
})

test('a tile: thumbnail or glyph, name with an exact class, size and age (F5, frozen selector)', () => {
  const { container } = render(FileGrid, { files: [png, pdf], base: '/root' })
  const tiles = container.querySelectorAll('.files > .file')
  expect(tiles).toHaveLength(2)
  expect(tiles[0].querySelector('img')).toHaveAttribute('src', '/root/f/1')
  expect(tiles[0].querySelector('img')).toHaveAttribute('loading', 'lazy')
  expect(tiles[1].querySelector('.glyph')).toHaveTextContent('📄')
  const name = tiles[0].querySelector('.meta > div')!
  expect(name.getAttribute('class')).toBe('name')
  expect(name).toHaveTextContent('hangar-check.png')
  expect(tiles[0].querySelector('.meta .small')).toHaveTextContent('2.0 KB · 5s ago')
  expect(tiles[0].querySelector('a.thumb')).toHaveAttribute('target', '_blank')
})

test('no action, no button — the MCP App holds no credential', () => {
  const { container } = render(FileGrid, { files: [png] })
  expect(container.querySelector('button')).toBeNull()
})

test('keep: 📌 top-left, disabled for the round trip, re-armed on failure (F5)', async () => {
  let answer!: (ok: boolean) => void
  const onkeep = vi.fn(() => new Promise<boolean>((r) => { answer = r }))
  const { container } = render(FileGrid, { files: [png], action: 'keep', onkeep })
  const btn = container.querySelector('button.act.keep') as HTMLButtonElement
  expect(btn).toHaveAttribute('aria-label', 'Keep hangar-check.png beyond this browser')
  expect(btn).toHaveAttribute('title', 'Keep it beyond this browser')
  expect(btn).toHaveTextContent('📌')
  await fireEvent.click(btn)
  expect(btn).toBeDisabled()
  answer(false); await Promise.resolve(); await Promise.resolve()
  expect(btn).not.toBeDisabled()
})

test('delete: 🗑 hands the file to the page', async () => {
  const ondelete = vi.fn()
  const { container } = render(FileGrid, { files: [png], action: 'delete', ondelete })
  const btn = container.querySelector('button.act.drop')!
  expect(btn).toHaveAttribute('aria-label', 'Delete hangar-check.png')
  await fireEvent.click(btn)
  expect(ondelete).toHaveBeenCalledWith(png)
})

test('a plain click opens the viewer; a modified click follows the link', async () => {
  const onopen = vi.fn()
  const { container } = render(FileGrid, { files: [pdf, png], onopen })
  const thumb = container.querySelectorAll('a.thumb')[1]
  await fireEvent.click(thumb, { ctrlKey: true })
  expect(onopen).not.toHaveBeenCalled()
  await fireEvent.click(thumb)
  expect(onopen).toHaveBeenCalledWith(1)
})

test('without onopen the grid opens its own read-only lightbox (P1)', async () => {
  const { container } = render(FileGrid, { files: [png] })
  await fireEvent.click(container.querySelector('a.thumb')!)
  expect(container.ownerDocument.querySelector('.lightbox')).not.toBeNull()
  expect(screen.queryByText('📌 Keep')).toBeNull()
})
```

`ui/src/lib/Lightbox.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import Lightbox from './Lightbox.svelte'

const a = { name: 'a.png', size: 1, url: '/a', image: true }
const b = { name: 'b.pdf', size: 1, url: '/b', content_type: 'application/pdf' }
const c = { name: 'c.zip', size: 1, url: '/c' }

test('steps through the row it was opened on, stopping at the ends (X1, X2)', async () => {
  render(Lightbox, { files: [a, b, c], index: 0, base: '/r', onclose: () => {} })
  expect(screen.getByText('1 / 3')).toBeInTheDocument()
  expect(screen.getByText('‹ Prev')).toBeDisabled()
  expect(screen.getByRole('img')).toHaveAttribute('src', '/r/a')
  await fireEvent.keyDown(document, { key: 'ArrowRight' })
  expect(screen.getByTitle('b.pdf').tagName).toBe('IFRAME')
  await fireEvent.click(screen.getByText('Next ›'))
  expect(screen.getByText('No preview for this kind of file.')).toBeInTheDocument()
  expect(screen.getByText('Next ›')).toBeDisabled()
  expect(screen.getAllByText('Download')[0]).toHaveAttribute('download', 'c.zip')
})

test('Esc, Close and the backdrop close it; keys are ignored under a modal (X2)', async () => {
  const onclose = vi.fn()
  const { container } = render(Lightbox, { files: [a, b], index: 0, onclose })
  const modal = document.createElement('div'); modal.className = 'modal'; document.body.append(modal)
  await fireEvent.keyDown(document, { key: 'ArrowRight' })
  await fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.getByText('1 / 2')).toBeInTheDocument()
  expect(onclose).not.toHaveBeenCalled()
  modal.remove()
  await fireEvent.keyDown(document, { key: 'Escape' })
  await fireEvent.click(screen.getByText('Close'))
  await fireEvent.click(container.querySelector('.lightbox')!)
  expect(onclose).toHaveBeenCalledTimes(3)
})

test('the action moves on: the refreshed list at the same index, clamped (X3)', async () => {
  const run = vi.fn(async () => {})
  const refresh = vi.fn(async (at: number) => ({ files: [a, c], index: at }))
  render(Lightbox, { files: [a, b, c], index: 1, action: { label: '📌 Keep', run }, refresh, onclose: () => {} })
  await fireEvent.click(screen.getByText('📌 Keep'))
  await vi.waitFor(() => expect(screen.getByText('2 / 2')).toBeInTheDocument())
  expect(run).toHaveBeenCalledWith(b)
  expect(screen.getByText('📌 Keep')).not.toBeDisabled()
})

test('an empty refresh closes; cancelled is silent; any other failure alerts (X3)', async () => {
  const alert = vi.fn(); vi.stubGlobal('alert', alert)
  const onclose = vi.fn()
  const r1 = render(Lightbox, { files: [a], index: 0, action: { label: 'Go', run: async () => {} }, refresh: async () => ({ files: [], index: 0 }), onclose })
  await fireEvent.click(screen.getByText('Go'))
  await vi.waitFor(() => expect(onclose).toHaveBeenCalled())
  r1.unmount()
  render(Lightbox, { files: [a], index: 0, action: { label: 'No', run: async () => { throw new Error('cancelled') } }, onclose: () => {} })
  await fireEvent.click(screen.getByText('No'))
  await vi.waitFor(() => expect(screen.getByText('No')).not.toBeDisabled())
  expect(alert).not.toHaveBeenCalled()
})
```

`ui/src/lib/SessionList.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import SessionList from './SessionList.svelte'

const row = { key: 'k1', name: 'claudecode', browser: 'chrome', version: '140', session_id: 'abc', live: true, counts: { downloads: 0, screenshots: 2, files: 1 }, url: 'https://x.y/' }

test('No sessions yet.', () => {
  render(SessionList, { data: { sessions: [] } })
  expect(screen.getByText('No sessions yet.')).toBeInTheDocument()
})

test('a card: mark, name pill, id, live, meta, url (L3, frozen selector)', () => {
  const { container } = render(SessionList, { data: { sessions: [row, { key: 'k2', owner: 'stdio' }] } })
  const [card, idle] = container.querySelectorAll('.card')
  expect(card.querySelector('.bmark')).toHaveAttribute('title', 'chrome')
  expect(card.querySelector('span.pill.name')).toHaveTextContent('claudecode')
  expect(card.querySelector('.mono')).toHaveTextContent('abc')
  expect(card.querySelector('.pill.live')).toHaveTextContent('live')
  expect(card).toHaveTextContent('chrome 140 · 2 screenshots · 1 file')
  expect(card.querySelector('.url')).toHaveTextContent('https://x.y/')
  expect(idle.querySelector('.mono')).toHaveTextContent('no browser')
  expect(idle).toHaveTextContent('idle')
  expect(idle.querySelector('.pill.name')).toHaveTextContent('stdio')
})

test('picking is optional; without it the cards are plain (L3)', async () => {
  const onpick = vi.fn()
  const { container } = render(SessionList, { data: { sessions: [row] }, onpick })
  expect(container.querySelector('.card')).toHaveClass('click')
  await fireEvent.click(container.querySelector('.card')!)
  expect(onpick).toHaveBeenCalledWith('k1')
})
```

`ui/src/lib/SessionSummary.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import SessionSummary from './SessionSummary.svelte'

test('named: no key or held-by; groups by lifetime (D1)', () => {
  const { container } = render(SessionSummary, { data: { key: 'k', name: 'mine', owner: 'named', browser: 'firefox', session_id: 'id1', version: '130', live: false, url: 'https://a.b/' } })
  expect(container.querySelector('strong')).toHaveTextContent('mine')
  expect(screen.getByText('idle')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'https://a.b/' })).toHaveAttribute('rel', 'noopener noreferrer')
  const labels = [...container.querySelectorAll('.group > .label')].map((e) => e.textContent)
  expect(labels).toEqual(['session', 'browser'])
  expect(screen.queryByText('key')).toBeNull()
})

test('unnamed shows key and held-by; a javascript: page is text; nowhere yet', () => {
  const r = render(SessionSummary, { data: { key: 'stdio', owner: 'stdio', url: 'javascript:x' } })
  expect(screen.getByText('key')).toBeInTheDocument()
  expect(screen.getByText('held by')).toBeInTheDocument()
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.getByText('javascript:x')).toBeInTheDocument()
  r.unmount()
  render(SessionSummary, { data: { key: 'x' } })
  expect(screen.getByText('nowhere yet')).toBeInTheDocument()
  expect(screen.getByText('x')).toBeInTheDocument()
})
```

`ui/src/lib/FileSections.test.ts`:

```ts
import { render } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import FileSections from './FileSections.svelte'

test('three titled rows with counts, in order, read-only (P1)', () => {
  const { container } = render(FileSections, { data: { downloads: [], screenshots: [{ name: 's.png', size: 1, url: '/s', image: true }], files: [] } })
  const rows = [...container.querySelectorAll('section.row')]
  expect(rows.map((r) => r.querySelector('strong')!.textContent)).toEqual(['Downloads', 'Screenshots', 'Files'])
  expect(rows.map((r) => r.querySelector('.pill')!.textContent)).toEqual(['0', '1', '0'])
  expect(rows[2]).toHaveTextContent('Nothing here yet — prints land here, and anything you keep.')
  expect(container.querySelector('button')).toBeNull()
})
```

Run: `npm --prefix ui test -- lib` → FAIL (components missing).

- [ ] **Step 2: `FileTile.svelte`**

```svelte
<script lang="ts">
  import { ago, bytes, glyphFor } from './format'
  import type { FileEntry } from './types'

  let { f, base, action, onopen, onkeep, ondelete }: {
    f: FileEntry
    base: string
    action?: 'keep' | 'delete'
    onopen: () => void
    onkeep?: (f: FileEntry) => Promise<boolean>
    ondelete?: (f: FileEntry) => void
  } = $props()

  const href = $derived(base + f.url)
  // Disabled for the round trip so a second click cannot keep twice. A reload
  // hands this tile a fresh entry, which re-arms it — as today's redraw did.
  let busy = $state(false)
  $effect.pre(() => { void f; busy = false })

  async function keep() {
    busy = true
    if (!(await onkeep?.(f))) busy = false
  }

  function open(e: MouseEvent) {
    // The href stays, so middle-click and "open in new tab" still work.
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    onopen()
  }
</script>

{#if action === 'keep'}
  <button type="button" class="act keep" aria-label="Keep {f.name} beyond this browser"
          title="Keep it beyond this browser" disabled={busy} onclick={keep}>📌</button>
{:else if action === 'delete'}
  <button type="button" class="act drop" aria-label="Delete {f.name}" title="Delete this file"
          onclick={() => ondelete?.(f)}>🗑</button>
{/if}
<a class="thumb" {href} target="_blank" rel="noopener" onclick={open}>
  {#if f.image}
    <img loading="lazy" alt={f.name} src={href}>
  {:else}
    <span class="glyph">{glyphFor(f.name)}</span>
  {/if}
</a>
<div class="meta">
  <div class="name">{f.name}</div>
  <div class="small muted">{bytes(f.size)}{f.created ? ' · ' + ago(f.created) : ''}</div>
</div>
```

- [ ] **Step 3: `FileGrid.svelte`**

```svelte
<script lang="ts">
  import FileTile from './FileTile.svelte'
  import Lightbox from './Lightbox.svelte'
  import type { FileEntry } from './types'

  let { files, base = '', action, empty = 'No files in this session yet.', onopen, onkeep, ondelete }: {
    files: FileEntry[]
    base?: string
    action?: 'keep' | 'delete'
    empty?: string
    onopen?: (i: number) => void
    onkeep?: (f: FileEntry) => Promise<boolean>
    ondelete?: (f: FileEntry) => void
  } = $props()

  // The viewer a surface with no page around it (the MCP App) opens by itself.
  let open = $state<number | null>(null)
</script>

{#if !files.length}
  <div class="empty">{empty}</div>
{:else}
  <div class="files">
    {#each files as f, i (f.name)}
      <div class="file">
        <FileTile {f} {base} {action} {onkeep} {ondelete}
                  onopen={() => (onopen ? onopen(i) : (open = i))} />
      </div>
    {/each}
  </div>
{/if}
{#if open !== null}
  <Lightbox {files} index={open} {base} onclose={() => (open = null)} />
{/if}
```

- [ ] **Step 4: `Lightbox.svelte`**

```svelte
<script lang="ts">
  import { untrack } from 'svelte'
  import { glyphFor } from './format'
  import type { FileEntry } from './types'

  interface Action { label: string; run: (f: FileEntry) => Promise<unknown> }
  let { files, index, base = '', action, refresh, onclose }: {
    files: FileEntry[]
    index: number
    base?: string
    action?: Action
    refresh?: (at: number) => Promise<{ files: FileEntry[]; index: number } | null>
    onclose: () => void
  } = $props()

  // Opened once on a list; after an action the caller hands back the next one.
  let list = $state(untrack(() => files.slice()))
  let at = $state(untrack(() => index))
  let busy = $state(false)
  const f = $derived(list[at])
  const href = $derived(base + f.url)

  const step = (d: number) => { const n = at + d; if (n >= 0 && n < list.length) at = n }

  function onkeydown(e: KeyboardEvent) {
    // A confirm opened on top owns the keyboard: without this, Esc closed both.
    if (document.querySelector('.modal')) return
    if (e.key === 'Escape') onclose()
    else if (e.key === 'ArrowLeft') step(-1)
    else if (e.key === 'ArrowRight') step(1)
  }

  async function act() {
    if (!action) return
    busy = true
    try {
      await action.run(list[at])
      const next = refresh ? await refresh(at) : null
      if (!next || !next.files.length) return onclose()
      // The same index: a kept screenshot left the list, so the next one is here.
      list = next.files
      at = Math.min(next.index, list.length - 1)
      busy = false
    } catch (err) {
      busy = false
      if ((err as Error).message !== 'cancelled') alert((err as Error).message)
    }
  }
</script>

<svelte:document {onkeydown} />
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions — Esc is handled on the document -->
<div class="lightbox" onclick={(e) => { if (e.target === e.currentTarget) onclose() }}>
  <div class="head">
    <span class="name">{f.name}</span>
    <span class="pos">{at + 1} / {list.length}</span>
    <span class="grow"></span>
    <button type="button" disabled={at === 0} onclick={() => step(-1)}>‹ Prev</button>
    <button type="button" disabled={at === list.length - 1} onclick={() => step(1)}>Next ›</button>
    {#if action}<button type="button" disabled={busy} onclick={act}>{action.label}</button>{/if}
    <a {href} download={f.name}>Download</a>
    <button type="button" onclick={onclose}>Close</button>
  </div>
  <div class="body">
    {#if f.image}
      <img alt={f.name} src={href}>
    {:else if f.content_type === 'application/pdf'}
      <iframe title={f.name} src={href}></iframe>
    {:else}
      <div class="nopreview">
        <span class="glyph">{glyphFor(f.name)}</span>
        <p>No preview for this kind of file.</p>
        <a {href} download={f.name}>Download</a>
      </div>
    {/if}
  </div>
</div>
```

- [ ] **Step 5: `FileSections.svelte`**

```svelte
<script lang="ts">
  import FileGrid from './FileGrid.svelte'
  import type { FileEntry } from './types'

  let { data, base = '' }: {
    data: { downloads?: FileEntry[]; screenshots?: FileEntry[]; files?: FileEntry[] }
    base?: string
  } = $props()

  // Downloads, Screenshots, Files — each its own grid, so paging stays in a row.
  const rows = $derived([
    ['Downloads', data.downloads ?? [], 'No downloads.'],
    ['Screenshots', data.screenshots ?? [], 'No screenshots yet.'],
    ['Files', data.files ?? [], 'Nothing here yet — prints land here, and anything you keep.'],
  ] as const)
</script>

{#each rows as [title, files, empty] (title)}
  <section class="row">
    <div class="head"><strong>{title}</strong><span class="pill">{files.length}</span></div>
    <div class="body"><FileGrid {files} {base} {empty} /></div>
  </section>
{/each}
```

- [ ] **Step 6: `SessionList.svelte`**

```svelte
<script lang="ts">
  import { browserMark, metaLine, sessionLabel } from './format'
  import type { SessionRow } from './types'

  let { data, onpick }: { data: { sessions?: SessionRow[] } | null; onpick?: (key: string) => void } = $props()
  const sessions = $derived(data?.sessions ?? [])
</script>

{#if !sessions.length}
  <div class="empty">No sessions yet.</div>
{:else}
  {#each sessions as s (s.key)}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions — today's card is mouse-only; changing that is new behaviour -->
    <div class="card" class:click={!!onpick} onclick={onpick ? () => onpick(s.key) : undefined}>
      <div class="row">
        <span class="bmark" title={s.browser || 'browser'}>{browserMark(s.browser)}</span>
        <span class="pill name">{sessionLabel(s)}</span>
        <span class="mono grow small muted">{s.session_id || 'no browser'}</span>
        {#if s.live}<span class="pill live">live</span>{:else}<span class="pill">idle</span>{/if}
      </div>
      <div class="small muted" style="margin-top:6px">{metaLine(s)}</div>
      {#if s.url}<div class="small muted url">{s.url}</div>{/if}
    </div>
  {/each}
{/if}
```

- [ ] **Step 7: `SessionSummary.svelte`**

```svelte
<script lang="ts">
  import { browserMark, safeHref } from './format'
  import type { SessionRow } from './types'

  let { data }: { data: SessionRow | null } = $props()
  const s = $derived(data ?? ({ key: '' } as SessionRow))
  const href = $derived(safeHref(s.url))

  // Grouped by how long each fact lives: the session's survive its browser.
  const groups = $derived([
    ['session', [
      ['key', s.name ? null : s.key],
      ['held by', s.name ? null : s.owner],
      ['browser', s.browser],
      ['window', s.window],
      ['started', s.started ? new Date(s.started * 1000).toLocaleString() : null],
    ]],
    ['browser', [['version', s.version], ['id', s.session_id], ['node', s.node]]],
  ].map(([label, facts]) => [label, (facts as [string, unknown][]).filter(([, v]) => v)] as const)
    .filter(([, facts]) => facts.length))
</script>

<div class="card">
  <div class="row" style="margin-bottom:10px">
    <span class="bmark" title={s.browser || 'browser'}>{browserMark(s.browser)}</span>
    <strong class="grow">{s.name || s.owner || 'Session'}</strong>
    {#if s.live}<span class="pill live">live</span>{:else}<span class="pill">idle</span>{/if}
  </div>
  <div class="lastpage">
    <div class="k small muted">last page</div>
    {#if href}
      <a {href} target="_blank" rel="noopener noreferrer">{s.url}</a>
    {:else}
      <span class="small muted">{s.url || 'nowhere yet'}</span>
    {/if}
  </div>
  <div class="groups">
    {#each groups as [label, facts] (label)}
      <div class="group">
        <div class="label">{label}</div>
        {#each facts as [k, v] (k)}
          <div class="fact"><div class="k">{k}</div><div class="v">{v}</div></div>
        {/each}
      </div>
    {/each}
  </div>
</div>
```

- [ ] **Step 8: Run and commit**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint` → pass, clean.

```bash
git add ui/src/lib
git commit -m "The shared components in Svelte: tiles, grids, the lightbox, the session list and summary"
```

---

### Task 5: The MCP App surface, and its SDK bundled

Spec *Deliberate difference 1*: today's `app.html` imports `…/ext-apps/dist/index.js`, which 404s on every version, so the app has never started. The SDK is now a pinned npm dependency, bundled into `app.js`, and `https://unpkg.com` leaves the CSP (Task 10 does the Python half). v2's `App` takes `{name, version}` and delivers results through `addEventListener('toolresult', …)`; the host context has no `toolResult`, so today's `getHostContext().toolResult` branch had nothing to read and goes.

**Files:**
- Modify: `ui/src/App.svelte` (replace the stub)
- Test: `ui/src/App.test.ts`

**Interfaces:**
- Consumes: `FileSections`, `SessionList`, `SessionSummary` (Task 4)

- [ ] **Step 1: failing test** (`ui/src/App.test.ts`)

```ts
import { render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'

const host = vi.hoisted(() => ({ listeners: {} as Record<string, (r: unknown) => void>, fail: null as Error | null }))
vi.mock('@modelcontextprotocol/ext-apps', () => ({
  App: class {
    constructor(public info: { name: string; version: string }) {}
    addEventListener(event: string, fn: (r: unknown) => void) { host.listeners[event] = fn }
    async connect() { if (host.fail) throw host.fail }
  },
}))
import App from './App.svelte'

beforeEach(() => { host.listeners = {}; host.fail = null })

test('renders the component a tool result names (P1)', async () => {
  const { container } = render(App)
  expect(screen.getByText('Loading…')).toBeInTheDocument()
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  host.listeners.toolresult({ structuredContent: { component: 'fileSections', downloads: [], screenshots: [], files: [] } })
  await vi.waitFor(() => expect(container.querySelectorAll('section.row')).toHaveLength(3))
})

test('an unknown component says so (P1)', async () => {
  render(App)
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  host.listeners.toolresult({ structuredContent: { component: 'nope' } })
  await vi.waitFor(() => expect(screen.getByText('Nothing to show for "nope".')).toHaveClass('error'))
})

test('a host that cannot start the app says why (P1)', async () => {
  host.fail = new Error('no parent window')
  render(App)
  await vi.waitFor(() => expect(screen.getByText('This host could not start the app: no parent window')).toBeInTheDocument())
})
```

Run: `npm --prefix ui test -- App` → FAIL.

- [ ] **Step 2: `App.svelte`**

```svelte
<script lang="ts">
  import { App as Host } from '@modelcontextprotocol/ext-apps'
  import { onMount, type Component } from 'svelte'
  import FileSections from './lib/FileSections.svelte'
  import SessionList from './lib/SessionList.svelte'
  import SessionSummary from './lib/SessionSummary.svelte'

  // By the name a tool result carries in `component`, as `SF[name]` was.
  // No onpick: a click inside someone else's transcript has nowhere to go.
  const COMPONENTS: Record<string, Component<{ data: never }>> = {
    fileSections: FileSections as Component<{ data: never }>,
    sessionList: SessionList as Component<{ data: never }>,
    sessionSummary: SessionSummary as Component<{ data: never }>,
  }

  type View = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'data'; data: Record<string, unknown> }
  let view = $state<View>({ kind: 'loading' })
  const Shown = $derived(view.kind === 'data' && Object.hasOwn(COMPONENTS, String(view.data.component)) ? COMPONENTS[String(view.data.component)] : null)

  onMount(async () => {
    try {
      const host = new Host({ name: 'selenium-flow', version: '1' })
      host.addEventListener('toolresult', (result) => {
        const r = result as { structuredContent?: Record<string, unknown> }
        view = { kind: 'data', data: (r && (r.structuredContent || (r as Record<string, unknown>))) || {} }
      })
      await host.connect()
    } catch (err) {
      view = { kind: 'error', message: err instanceof Error ? err.message : String(err) }
    }
  })
</script>

{#if view.kind === 'loading'}
  <div class="empty">Loading…</div>
{:else if view.kind === 'error'}
  <div class="empty error">This host could not start the app: {view.message}</div>
{:else if Shown}
  <Shown data={view.data as never} />
{:else}
  <div class="empty error">Nothing to show{view.data.component ? ` for "${view.data.component}"` : ''}.</div>
{/if}
```

If `svelte-check` rejects the `toolresult` handler's parameter type, type it as the SDK's exported `McpUiToolResultNotification['params']` (see `node_modules/@modelcontextprotocol/ext-apps/dist/src/app.d.ts`, `AppEventMap.toolresult`) instead of casting.

- [ ] **Step 3: Run, build, measure, commit**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint && npm --prefix ui run build && npm --prefix ui run size`
Expected: pass; `app:` ≈ 60–70 KB gzipped (the SDK is ~60 KB of it), under its 110 KB budget.

```bash
git add ui/src/App.svelte ui/src/App.test.ts
git commit -m "The MCP App in Svelte, with the ext-apps SDK pinned and bundled instead of a 404ing CDN import"
```

---

### Task 6: The admin shell — sign-in, tabs, routes, the live list, the console, the modal

**Files:**
- Create: `ui/src/admin/modal.ts`, `Modal.svelte`, `live.svelte.ts`, `Login.svelte`, `SessionsView.svelte`, `ConsolePane.svelte`
- Modify: `ui/src/admin/Admin.svelte` (replace the stub); delete `ui/src/smoke.test.ts`
- Test: `ui/src/admin/Modal.test.ts`, `live.test.ts`, `Admin.test.ts`

**Interfaces:**
- Consumes: `createApi`, `Api` (Task 3); `router`, `listen`, `go`, `hashes` (Task 3); `SessionList` (Task 4)
- Produces:
  - `modal.ts`: `interface ModalSpec<T = unknown> { title: string; body: Snippet<[T]>; data: T; confirm?: string; danger?: boolean; scope?: 'downloads' | null; onconfirm: () => Promise<void> | void; oncancel?: () => void }`
  - `Modal.svelte` props `{ spec: ModalSpec; onclosed: (confirmed: boolean) => void }`
  - `live.svelte.ts`: `class Live { data; error; badge; watching; constructor(api: Api, root: string); load(): Promise<SessionsPayload | undefined>; stop(): void }`
  - `Admin.svelte` props `{ mount: string; console: string }`; it renders `SessionDetail` (Task 7) and `SecretsPane` (Task 9) — until those tasks land, render `<div class="empty">Loading…</div>` in their place and leave a `// Task 7`/`// Task 9` marker that those tasks remove.

- [ ] **Step 1: failing tests.** `ui/src/admin/Modal.test.ts` (M1) — render through a tiny harness so a snippet can be the body:

Create `ui/src/admin/ModalHarness.test.svelte`:

```svelte
<script lang="ts">
  import Modal from './Modal.svelte'
  import type { ModalSpec } from './modal'
  let { onconfirm, oncancel, onclosed, danger = false }: { onconfirm: () => Promise<void> | void; oncancel?: () => void; onclosed: (c: boolean) => void; danger?: boolean } = $props()
  const spec: ModalSpec<string[]> = { title: 'Clear <all>', body: names, data: ['a <b>', 'c'], confirm: 'Clear 2 files', danger, onconfirm, oncancel }
</script>

{#snippet names(list: string[])}<ul class="names">{#each list as n (n)}<li>{n}</li>{/each}</ul>{/snippet}
<Modal {spec} {onclosed} />
```

`ui/src/admin/Modal.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import Harness from './ModalHarness.test.svelte'

test('title, escaped body, Cancel and the confirm (M1)', () => {
  const { container } = render(Harness, { onconfirm: () => {}, onclosed: () => {}, danger: true })
  expect(container.querySelector('.modal .sheet .head')).toHaveTextContent('Clear <all>')
  expect(screen.getByText('a <b>').tagName).toBe('LI')
  expect(screen.getByText('Clear 2 files')).toHaveClass('danger')
  expect(screen.getByText('Cancel')).toBeInTheDocument()
})

test('Esc, the backdrop and Cancel all take the cancel path (M1)', async () => {
  for (const how of ['esc', 'backdrop', 'cancel'] as const) {
    const oncancel = vi.fn(), onclosed = vi.fn()
    const r = render(Harness, { onconfirm: () => {}, oncancel, onclosed })
    if (how === 'esc') await fireEvent.keyDown(document, { key: 'Escape' })
    if (how === 'backdrop') await fireEvent.click(r.container.querySelector('.modal')!)
    if (how === 'cancel') await fireEvent.click(screen.getByText('Cancel'))
    expect(oncancel).toHaveBeenCalledOnce()
    expect(onclosed).toHaveBeenCalledWith(false)
    r.unmount()
  }
})

test('confirm is disabled while running; success closes without cancelling (M1)', async () => {
  let finish!: () => void
  const oncancel = vi.fn(), onclosed = vi.fn()
  render(Harness, { onconfirm: () => new Promise<void>((r) => { finish = r }), oncancel, onclosed })
  await fireEvent.click(screen.getByText('Clear 2 files'))
  expect(screen.getByText('Clear 2 files')).toBeDisabled()
  finish(); await vi.waitFor(() => expect(onclosed).toHaveBeenCalledWith(true))
  expect(oncancel).not.toHaveBeenCalled()
})

test('a failure stays open, re-arms, and alerts (M1)', async () => {
  const alert = vi.fn(); vi.stubGlobal('alert', alert)
  const onclosed = vi.fn()
  render(Harness, { onconfirm: async () => { throw new Error('nope') }, onclosed })
  await fireEvent.click(screen.getByText('Clear 2 files'))
  await vi.waitFor(() => expect(alert).toHaveBeenCalledWith('nope'))
  expect(screen.getByText('Clear 2 files')).not.toBeDisabled()
  expect(onclosed).not.toHaveBeenCalled()
})
```

`ui/src/admin/live.test.ts` (L1, U1–U3):

```ts
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource, fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { Live } from './live.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('EventSource', FakeEventSource) })
afterEach(() => { vi.useRealTimers() })

test('loads, then streams from the signed URL against ROOT (U1, U2)', async () => {
  fakeFetch({ 'GET /admin/sessions': { body: { sessions: [{ key: 'a' }], events_url: '/flow/admin/events?sig=1' } } })
  const live = new Live(api, '/base')
  expect(live.badge).toEqual({ text: 'connecting…', cls: '' })
  await live.load()
  expect(live.data?.sessions).toEqual([{ key: 'a' }])
  expect(FakeEventSource.last!.url).toBe('/base/flow/admin/events?sig=1')
  FakeEventSource.last!.onopen!()
  expect(live.badge).toEqual({ text: 'live', cls: 'live' })
  FakeEventSource.last!.emit({ sessions: [{ key: 'b' }] })
  expect(live.data?.sessions).toEqual([{ key: 'b' }])
  FakeEventSource.last!.onerror!()
  expect(live.badge.text).toBe('reconnecting…')
})

test('a silent stream falls back to polling and reopens on a fresh URL (U3)', async () => {
  let n = 0
  fakeFetch({ 'GET /admin/sessions': () => ({ body: { sessions: [], events_url: `/e?sig=${++n}` } }) })
  const live = new Live(api, '')
  await live.load()
  const first = FakeEventSource.last!
  first.onopen!()
  await vi.advanceTimersByTimeAsync(30_000)
  expect(FakeEventSource.last).toBe(first) // heard from within 45 s
  await vi.advanceTimersByTimeAsync(30_000)
  expect(live.badge.text).toBe('polling')
  expect(first.closed).toBe(true)
  expect(FakeEventSource.last!.url).toBe('/e?sig=2')
})

test('an error shows in place of the list (L2)', async () => {
  fakeFetch({ 'GET /admin/sessions': { status: 500, body: { error: 'grid down' } } })
  const live = new Live(api, '')
  await live.load()
  expect(live.error).toBe('grid down')
})

test('stop closes the stream and the poll (A4)', async () => {
  fakeFetch({ 'GET /admin/sessions': { body: { sessions: [], events_url: '/e' } } })
  const live = new Live(api, '')
  await live.load()
  live.stop()
  expect(FakeEventSource.last!.closed).toBe(true)
  expect(live.watching).toBe(false)
})
```

`ui/src/admin/Admin.test.ts` (A1–A3, A6, R1–R6, L1–L2, C1):

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource, fakeFetch } from '../test/helpers'
import Admin from './Admin.svelte'

const SESSIONS = { sessions: [{ key: 'k1', name: 'claudecode', live: true }], events_url: '/e' }
beforeEach(() => {
  sessionStorage.clear(); localStorage.clear()
  history.replaceState(null, '', '/#/')
  vi.stubGlobal('EventSource', FakeEventSource)
})

test('signed out: the sign-in card, and no Sign out (A1, A2)', async () => {
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid' })
  expect(container.querySelector('#login')).toBeVisible()
  expect(container.querySelector('#token')).toHaveAttribute('type', 'password')
  expect(container.querySelector('#token')).toBeRequired()
  expect(screen.queryByText('Sign out')).toBeNull()
})

test('a refused token says so; an accepted one is kept in sessionStorage only (A2)', async () => {
  let ok = false
  fakeFetch({ 'GET /admin/sessions': () => (ok ? { body: SESSIONS } : { status: 401 }) })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await fireEvent.input(container.querySelector('#token')!, { target: { value: ' bad ' } })
  await fireEvent.submit(container.querySelector('#loginForm')!)
  await vi.waitFor(() => expect(screen.getByText('That token was refused.')).toBeVisible())
  ok = true
  await fireEvent.input(container.querySelector('#token')!, { target: { value: ' good ' } })
  await fireEvent.submit(container.querySelector('#loginForm')!)
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  expect(sessionStorage.getItem('sf-token')).toBe('good')
  expect(localStorage.length).toBe(0)
  expect(screen.getByText('claudecode')).toBeInTheDocument()
})

test('a stored token is probed; a dead one lands on sign-in (A3)', async () => {
  sessionStorage.setItem('sf-token', 'old')
  fakeFetch({ 'GET /admin/sessions': { status: 401 } })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => expect(container.querySelector('#login')).toBeVisible())
  expect(sessionStorage.getItem('sf-token')).toBeNull()
})

test('BASE is the page path and server URLs resolve against ROOT (A6)', async () => {
  history.replaceState(null, '', '/base/flow/#/')
  sessionStorage.setItem('sf-token', 't')
  const { calls } = fakeFetch({ 'GET /base/flow/admin/sessions': { body: { ...SESSIONS, events_url: '/flow/admin/events?s=1' } } })
  render(Admin, { mount: '/flow', console: '/grid' })
  await vi.waitFor(() => expect(FakeEventSource.last?.url).toBe('/base/flow/admin/events?s=1'))
  expect(calls[0].path).toBe('/base/flow/admin/sessions')
})

test('three top tabs, one pane at a time; the live badge (R1, L1)', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => expect(screen.getByText('Live sessions')).toBeInTheDocument())
  expect(container.querySelector('#tabSessions')).toHaveAttribute('aria-selected', 'true')
  expect(container.querySelector('#live')).toHaveTextContent('connecting…')
  expect(container.querySelector('#paneSecrets')).toBeNull()
})

test('the console tab hides itself when it would frame this page (R3, C1)', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  const self = render(Admin, { mount: '', console: '/' })
  await vi.waitFor(() => expect(screen.getByText('Live sessions')).toBeInTheDocument())
  expect(self.container.querySelector('#tabConsole')).not.toBeVisible()
  self.unmount()
  history.replaceState(null, '', '/#/console')
  const other = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => expect(other.container.querySelector('iframe.console')).toHaveAttribute('src', 'http://localhost:3000/grid'))
  expect(other.container.querySelector('#tabConsole')).toHaveAttribute('aria-selected', 'true')
})

test('picking a session routes through the hash (L3, R2)', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => screen.getByText('claudecode'))
  await fireEvent.click(screen.getByText('claudecode'))
  expect(location.hash).toBe('#/sessions/k1')
})

test('Sign out stops the stream and forgets the token (A4)', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => screen.getByText('Sign out'))
  await fireEvent.click(screen.getByText('Sign out'))
  expect(FakeEventSource.last!.closed).toBe(true)
  expect(sessionStorage.getItem('sf-token')).toBeNull()
  expect(container.querySelector('#login')).toBeVisible()
})
```

(jsdom's default origin is `http://localhost:3000`; if the installed jsdom reports another, use `new URL('/grid', location.href).href` in that assertion instead of the literal.)

Run: `npm --prefix ui test -- admin` → FAIL.

- [ ] **Step 2: `modal.ts` and `Modal.svelte`**

`ui/src/admin/modal.ts`:

```ts
import type { Snippet } from 'svelte'

/* One modal, three uses: the confirm that lists what it removes (native
   confirm() cannot show a list, and "Delete 12 files?" without saying which
   is an assertion, not a disclosure), the flow delete confirm, the YAML editor. */
export interface ModalSpec<T = unknown> {
  title: string
  body: Snippet<[T]>
  data: T
  confirm?: string
  danger?: boolean
  /* 'downloads' when it acts on the browser's downloads: a browser change
     closes it without touching session-scoped dialogs (§F4.9). */
  scope?: 'downloads' | null
  onconfirm: () => Promise<void> | void
  oncancel?: () => void
}
```

`ui/src/admin/Modal.svelte`:

```svelte
<script lang="ts">
  import type { ModalSpec } from './modal'

  let { spec, onclosed }: { spec: ModalSpec; onclosed: (confirmed: boolean) => void } = $props()
  let busy = $state(false)

  // Cancel, Esc, the backdrop: anything that is not a successful confirm runs
  // oncancel — without it a lightbox button disabled behind this box stayed
  // disabled forever.
  function cancel() {
    spec.oncancel?.()
    onclosed(false)
  }

  async function confirm() {
    busy = true
    try {
      await spec.onconfirm()
      onclosed(true)
    } catch (err) {
      // Stay open: closing would throw away what they typed in the editor.
      busy = false
      alert((err as Error).message)
    }
  }
</script>

<svelte:document onkeydown={(e) => { if (e.key === 'Escape') cancel() }} />
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions — Esc is handled on the document -->
<div class="modal" onclick={(e) => { if (e.target === e.currentTarget) cancel() }}>
  <div class="sheet">
    <div class="head">{spec.title}</div>
    <div class="body">{@render spec.body(spec.data)}</div>
    <div class="foot">
      <button type="button" onclick={cancel}>Cancel</button>
      <button type="button" class={spec.danger ? 'danger' : 'primary'} disabled={busy} onclick={confirm}>
        {spec.confirm ?? 'OK'}
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: `live.svelte.ts`**

```ts
import type { Api } from './api'
import type { SessionsPayload } from '../lib/types'

/* The session list is pushed, not polled. EventSource cannot send an
   Authorization header, so the URL it opens is signed and comes back from an
   authenticated call. */
export class Live {
  // Raw: replaced wholesale, and SessionDetail compares it by identity.
  data = $state.raw<SessionsPayload | null>(null)
  error = $state<string | null>(null)
  badge = $state({ text: 'connecting…', cls: '' })
  watching = $state(false)
  #events: EventSource | null = null
  #lastBeat = 0
  #fallback: ReturnType<typeof setInterval> | undefined

  constructor(private api: Api, private root: string) {}

  async load(): Promise<SessionsPayload | undefined> {
    try {
      const data = await this.api<SessionsPayload>('/admin/sessions')
      this.#apply(data)
      if (data.events_url) this.#watch(data.events_url)
      return data
    } catch (e) {
      this.error = (e as Error).message
    }
  }

  stop() {
    this.#events?.close()
    this.#events = null
    clearInterval(this.#fallback)
    this.watching = false
  }

  #apply(data: SessionsPayload) { this.data = data; this.error = null }

  #beat() { this.#lastBeat = Date.now(); this.badge = { text: 'live', cls: 'live' } }

  #watch(url: string) {
    if (this.#events) return
    const events = (this.#events = new EventSource(this.root + url))
    this.watching = true
    events.onopen = () => this.#beat()
    events.onmessage = (e) => {
      this.#beat()
      try { this.#apply(JSON.parse(e.data)) } catch { /* a keepalive */ }
    }
    // EventSource reconnects by itself; the badge just stops claiming live.
    events.onerror = () => { this.badge = { text: 'reconnecting…', cls: '' } }

    // A stream swallowed by something in the middle would leave the page
    // silently wrong. A slow poll makes that failure invisible, not permanent.
    clearInterval(this.#fallback)
    this.#fallback = setInterval(async () => {
      if (Date.now() - this.#lastBeat < 45000) return
      this.badge = { text: 'polling', cls: '' }
      try {
        const data = await this.api<SessionsPayload>('/admin/sessions')
        this.#apply(data)
        // The stream URL is signed and expires: a reconnect after that 401s
        // forever. This answer carries a freshly signed one.
        if (data.events_url) {
          this.#events?.close()
          this.#events = null
          this.#watch(data.events_url)
        }
      } catch { /* the next tick tries again */ }
    }, 30000)
  }
}
```

- [ ] **Step 4: `Login.svelte`, `SessionsView.svelte`, `ConsolePane.svelte`**

`Login.svelte`:

```svelte
<script lang="ts">
  let { onsubmit, refused }: { onsubmit: (token: string) => void; refused: boolean } = $props()
  let token = $state('')
</script>

<div id="login" class="wrap login">
  <div class="card">
    <h2 style="margin-top:0">Sign in</h2>
    <p class="muted small">
      There are no accounts here. The server's token is the whole credential —
      anyone holding it can already drive every browser through the API, so this
      box asks for that rather than inventing a second identity to get wrong.
    </p>
    <form id="loginForm" class="row" onsubmit={(e) => { e.preventDefault(); onsubmit(token.trim()) }}>
      <input id="token" type="password" placeholder="MCP token" autocomplete="off" required bind:value={token}>
      <button class="primary" type="submit">Enter</button>
    </form>
    <p id="loginError" class="small error" hidden={!refused}>That token was refused.</p>
  </div>
</div>
```

`SessionsView.svelte`:

```svelte
<script lang="ts">
  import SessionList from '../lib/SessionList.svelte'
  import type { Live } from './live.svelte'
  import { go, hashes } from './router.svelte'

  let { live }: { live: Live } = $props()
</script>

<div id="sessionsView">
  <div class="row" style="margin-bottom:12px">
    <strong class="grow">Live sessions</strong>
    <span id="live" class="pill{live.badge.cls ? ' ' + live.badge.cls : ''}" title="Updates arrive as they happen">{live.badge.text}</span>
  </div>
  <div id="sessions">
    {#if live.error}
      <div class="empty error">{live.error}</div>
    {:else if !live.data}
      <div class="empty">Loading…</div>
    {:else}
      <SessionList data={live.data} onpick={(key) => go(hashes.session(key))} />
    {/if}
  </div>
</div>
```

`ConsolePane.svelte`:

```svelte
<script lang="ts">
  let { src }: { src: string } = $props()
</script>

<section id="paneConsole">
  <p class="small muted">
    The Grid's own console, same-origin in a frame. Its live view works from
    here because both halves are served from one host.
  </p>
  <iframe class="console" {src} title="Selenium Grid console"></iframe>
</section>
```

- [ ] **Step 5: `Admin.svelte`**

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import { createApi } from './api'
  import ConsolePane from './ConsolePane.svelte'
  import { Live } from './live.svelte'
  import Login from './Login.svelte'
  import { go, hashes, listen, router } from './router.svelte'
  import SessionsView from './SessionsView.svelte'
  // Task 7: import SessionDetail from './SessionDetail.svelte'
  // Task 9: import SecretsPane from './SecretsPane.svelte'

  let { mount, console: consoleUrl }: { mount: string; console: string } = $props()

  // Served at the root of wherever this server is mounted, so its own path is
  // the base every call hangs off. Server-issued URLs already carry the mount
  // and resolve against ROOT: whatever an ingress stripped.
  const BASE = location.pathname.replace(/\/+$/, '')
  const ROOT = mount && BASE.endsWith(mount) ? BASE.slice(0, -mount.length) : BASE

  // At a root mount the default console URL is this page: framing it nests the
  // dashboard in itself without end. No tab beats a mirror.
  const target = new URL(consoleUrl, location.href)
  const consoleSelf = target.origin === location.origin && target.pathname.replace(/\/+$/, '') === BASE

  // sessionStorage, not localStorage: the server's full-privilege token should
  // not outlive the tab it was typed into.
  let token = $state(sessionStorage.getItem('sf-token') || '')
  let phase = $state<'probing' | 'login' | 'in'>(token ? 'probing' : 'login')
  let refused = $state(false)

  const api = createApi({ base: BASE, token: () => token, onUnauthorized: signOut })
  const live = new Live(api, ROOT)

  function signOut() {
    live.stop()
    token = ''
    sessionStorage.removeItem('sf-token')
    phase = 'login'
  }

  async function signIn(value: string) {
    token = value
    try {
      await api('/admin/sessions')
      sessionStorage.setItem('sf-token', token)
      refused = false
      phase = 'in'
    } catch {
      refused = true
    }
  }

  onMount(() => {
    const stop = listen()
    if (phase === 'probing') api('/admin/sessions').then(() => { phase = 'in' }, () => { phase = 'login' })
    return stop
  })

  const route = $derived(router.route)
  const top = $derived(route.view === 'secrets' ? 'secrets' : route.view === 'console' && !consoleSelf ? 'console' : 'sessions')

  // The list reloads whenever it (or the console, which sits on the same live
  // stream) comes on screen; a deep link into a session starts the stream too.
  $effect(() => {
    if (phase !== 'in') return
    if (route.view === 'list' || route.view === 'console' || (route.view === 'session' && !live.watching)) void live.load()
  })
</script>

<header class="bar">
  <span class="brand">selenium-flow</span>
  <span class="grow"></span>
  {#if phase === 'in'}<button id="signout" onclick={signOut}>Sign out</button>{/if}
</header>

{#if phase === 'login'}
  <Login onsubmit={signIn} {refused} />
{:else if phase === 'in'}
  <main id="app" class="wrap">
    <div class="tabs">
      <button id="tabSessions" aria-selected={top === 'sessions'} onclick={() => go(hashes.list)}>Sessions</button>
      <button id="tabSecrets" aria-selected={top === 'secrets'} onclick={() => go(hashes.secrets)}>Secrets</button>
      <button id="tabConsole" aria-selected={top === 'console'} hidden={consoleSelf} onclick={() => go(hashes.console)}>Grid console</button>
    </div>
    {#if top === 'sessions'}
      <section id="paneSessions">
        {#if route.view === 'session'}
          <!-- Task 7: {#key route.key}<SessionDetail … />{/key} -->
          <div class="empty">Loading…</div>
        {:else}
          <SessionsView {live} />
        {/if}
      </section>
    {:else if top === 'secrets'}
      <!-- Task 9: <SecretsPane {api} /> -->
      <section id="paneSecrets"><div id="secrets"><div class="empty">Loading…</div></div></section>
    {:else}
      <ConsolePane src={target.href} />
    {/if}
  </main>
{/if}
```

Delete `ui/src/smoke.test.ts` (its job is done).

- [ ] **Step 6: Run and commit**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint` → pass, clean.

```bash
git add -A ui/src
git commit -m "The admin shell in Svelte: sign-in, tabs and routes, the live list, the console, the modal"
```

---

### Task 7: A session — header, toolbar, tabs, Files, the lightbox, and the race guards as structure

The heart of the refactor. `SessionDetail` is rendered inside `{#key route.key}`: switching sessions destroys the whole subtree — its loads (aborted), its lightbox, its modal — which is what `gone(key)`, `closeLightbox`/`closeModal` on every switch, and the sequence counters did by hand.

**Files:**
- Create: `ui/src/admin/session.svelte.ts`, `Section.svelte`, `FilesPane.svelte`, `SessionDetail.svelte`
- Modify: `ui/src/admin/Admin.svelte` (render `SessionDetail`, remove the Task 7 marker)
- Test: `ui/src/admin/session.test.ts`, `SessionDetail.test.ts`

**Interfaces:**
- Consumes: `Api`, `sessionPath` (Task 3); `Latest` (Task 3); `replace`, `go`, `hashes`, `router` (Task 3); `Live` (Task 6); `Modal`, `ModalSpec` (Task 6); `FileGrid`, `Lightbox`, `SessionSummary` (Task 4)
- Produces:
  - `session.svelte.ts`: `NO_FILES`, `filesStamp(row)`, `class SessionModel { row; files; view; filesError; loadingFiles; flows; flowsError; flowsBlanked; flowDoc; flowDocError; constructor(key, api); loadFiles(); filesSettled(); loadFlows(currentFlow, vanished); loadFlow(name); onPushed(data, currentFlow, vanished, browserChanged); dispose() }`
  - `SessionDetail` props `{ key: string; tab: 'files' | 'flows'; flow: string | undefined; api: Api; live: Live; root: string }`. It renders `FlowsPane` (Task 8) on the Flows tab — until Task 8, `<div class="empty">Loading…</div>` with a `// Task 8` marker.

- [ ] **Step 1: failing tests for the model** (`ui/src/admin/session.test.ts`) — the page's worst bugs, each as a property:

```ts
import { expect, test, vi } from 'vitest'
import { deferred, fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { NO_FILES, SessionModel } from './session.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
const shot = (name: string) => ({ name, size: 1, url: '/' + name, image: true })
const files = (over = {}) => ({ session: { key: 'k', live: true, session_id: 'b1', files_rev: 1, flows_rev: 1 }, downloads: [], screenshots: [shot('a.png')], files: [], browser: true, ...over })

test('a load paints the rows and the header (F2, D1)', async () => {
  fakeFetch({ 'GET /admin/sessions/k/files': { body: files() } })
  const m = new SessionModel('k', api)
  expect(m.view).toBeNull()
  await m.loadFiles()
  expect(m.view!.screenshots.map((f) => f.name)).toEqual(['a.png'])
  expect(m.row.session_id).toBe('b1')
  expect(m.files).not.toBe(NO_FILES)
})

test('a failed load leaves nothing actionable behind (F7)', async () => {
  fakeFetch({ 'GET /admin/sessions/k/files': { status: 500, body: { error: 'boom' } } })
  const m = new SessionModel('k', api)
  await m.loadFiles()
  expect(m.files).toBe(NO_FILES)
  expect(m.filesError).toBe('boom')
  expect(m.flowsBlanked).toBe(true)
})

test('dispose: an answer after the session left never paints (D4)', async () => {
  const late = deferred<{ body: unknown }>()
  fakeFetch({ 'GET /admin/sessions/k/files': () => late.promise })
  const m = new SessionModel('k', api)
  const load = m.loadFiles()
  m.dispose()
  late.resolve({ body: files() }); await load
  expect(m.view).toBeNull()
})

test('filesSettled sees the newest data even when the poll overtook the caller (X3, the Keep race)', async () => {
  const slow = deferred<{ body: unknown }>()
  let n = 0
  fakeFetch({ 'GET /admin/sessions/k/files': () => (++n === 1 ? slow.promise : { body: files({ screenshots: [] }) }) })
  const m = new SessionModel('k', api)
  void m.loadFiles()            // Keep's own reload
  const poll = m.loadFiles()    // the poll's, overtaking it
  slow.resolve({ body: files() })
  await poll; await m.filesSettled()
  expect(m.files.screenshots).toEqual([])
})

test('a pushed row refetches files only when the stamp moves (F8)', async () => {
  const { calls } = fakeFetch({ 'GET /admin/sessions/k/files': { body: files() }, 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [], rev: 1 } } })
  const m = new SessionModel('k', api)
  await m.loadFiles(); await m.loadFlows(() => null, () => {})
  const before = calls.length
  m.onPushed({ sessions: [{ key: 'k', live: true, session_id: 'b1', files_rev: 1, flows_rev: 1 }] }, () => null, () => {}, () => {})
  expect(calls.length).toBe(before)
  m.onPushed({ sessions: [{ key: 'k', live: true, session_id: 'b1', files_rev: 2, flows_rev: 1 }] }, () => null, () => {}, () => {})
  expect(calls.length).toBe(before + 1)
})

test('a changed browser blanks Downloads, disarms the clears, and forces a reload (F8)', async () => {
  fakeFetch({ 'GET /admin/sessions/k/files': { body: files({ downloads: [shot('d.pdf')] }) }, 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [], rev: 1 } } })
  const m = new SessionModel('k', api)
  await m.loadFiles(); await m.loadFlows(() => null, () => {})
  const changed = vi.fn()
  const load = vi.spyOn(m, 'loadFiles')
  m.onPushed({ sessions: [{ key: 'k', live: false, session_id: 'b1', files_rev: 1, flows_rev: 1 }] }, () => null, () => {}, changed)
  expect(changed).toHaveBeenCalledOnce()
  expect(m.files).toBe(NO_FILES)
  expect(m.view!.downloads).toEqual([])
  expect(m.view!.downloadsEmpty).toBe('No downloads.')
  expect(m.view!.screenshots).toHaveLength(1) // session-owned rows are untouched
  expect(load).toHaveBeenCalled()
})

test('a pushed row for another session, or none, changes nothing', () => {
  fakeFetch({})
  const m = new SessionModel('k', api)
  m.onPushed({ sessions: [{ key: 'other' }] }, () => null, () => {}, () => {})
  expect(m.row).toEqual({ key: 'k' })
})

test('an open flow that vanished from the listing is closed (W7)', async () => {
  fakeFetch({ 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [{ name: 'b', step_count: 1 }], rev: 2 } } })
  const m = new SessionModel('k', api)
  const vanished = vi.fn()
  await m.loadFlows(() => 'a', vanished)
  expect(vanished).toHaveBeenCalledOnce()
  expect(m.flowDoc).toBeNull()
})

test('a still-listed open flow reloads its document (W7)', async () => {
  fakeFetch({
    'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [{ name: 'a', step_count: 1 }], rev: 2 } },
    'GET /admin/sessions/k/flows/a': { body: { name: 'a', steps: [] } },
  })
  const m = new SessionModel('k', api)
  await m.loadFlows(() => 'a', () => {})
  await vi.waitFor(() => expect(m.flowDoc?.name).toBe('a'))
})
```

Prove it is not vacuous (spec, Testing): temporarily make `SessionModel.dispose()` a no-op and run the `dispose` test — it must FAIL; make `filesSettled()` return immediately — the Keep-race test must FAIL. Restore both. Note the two runs in the commit message.

- [ ] **Step 2: `session.svelte.ts`**

```ts
import type { Api } from './api'
import { sessionPath } from './api'
import { Latest } from './latest'
import type { FileEntry, FilesData, FilesResponse, FlowDoc, FlowsListing, SessionRow, SessionsPayload } from '../lib/types'

/* Handed to a session that has not answered yet, or whose load failed: the
   clears must never act on a previous session's names. */
export const NO_FILES: FilesData = Object.freeze({ downloads: [], screenshots: [], files: [], browser: false }) as FilesData

/* Not a count: deleting the kept copy of a name that is also a download leaves
   the count alone while the tile changes. The browser id is in it because a
   different browser is a different file store. */
export const filesStamp = (row: SessionRow) => (row.session_id || '-') + ':' + row.files_rev

const downloadsEmpty = (hasBrowser: boolean) => (hasBrowser ? 'No downloads.' : 'No browser — downloads go with it.')

export interface FilesView {
  downloads: FileEntry[]
  screenshots: FileEntry[]
  files: FileEntry[]
  downloadsEmpty: string
}

/* One session on screen. Created by SessionDetail and disposed with it, so
   nothing it loads can land on another session's page. */
export class SessionModel {
  // Raw state throughout: every value is replaced wholesale, and `files` is
  // compared by identity with NO_FILES — a deep proxy would never be equal.
  row = $state.raw<SessionRow>({ key: '' })
  /* What the actions act on: the clears' lists, the lightbox's list. */
  files = $state.raw<FilesData>(NO_FILES)
  /* What the three rows show; null while the first load is out. Separate from
     `files` because a browser change blanks only Downloads on screen. */
  view = $state.raw<FilesView | null>(null)
  filesError = $state<string | null>(null)
  loadingFiles = $state(false)
  flows = $state.raw<FlowsListing | null>(null)
  flowsError = $state<string | null>(null)
  /* The Flows count blanks with a failed files load, until flows answer again. */
  flowsBlanked = $state(false)
  flowDoc = $state.raw<FlowDoc | null>(null)
  flowDocError = $state<{ name: string; message: string } | null>(null)

  #shownFiles: string | null = null
  #shownBrowser: string | null = null
  #shownLive: boolean | null = null
  #shownFlows: string | number | null = null
  #fileLoads = new Latest()
  #flowLoads = new Latest()
  #docLoads = new Latest()

  constructor(readonly key: string, private api: Api) {
    this.row = { key }
  }

  loadFiles(): Promise<void> {
    this.loadingFiles = true
    return this.#fileLoads.run(
      (signal) => this.api<FilesResponse>(sessionPath(this.key, '/files'), 'GET', undefined, signal),
      (data) => {
        const row = data.session || { key: this.key }
        this.row = row
        this.#shownFiles = filesStamp(row)
        this.#shownBrowser = row.session_id || null
        this.#shownLive = !!row.live
        this.files = {
          downloads: data.downloads || [],
          screenshots: data.screenshots || [],
          files: data.files || [],
          browser: !!data.browser,
        }
        this.view = { ...this.files, downloadsEmpty: downloadsEmpty(this.files.browser) }
        this.filesError = null
        this.loadingFiles = false
      },
      (e) => {
        this.files = NO_FILES
        this.filesError = e.message
        this.flowsBlanked = true
        this.loadingFiles = false
      },
    )
  }

  filesSettled(): Promise<void> {
    return this.#fileLoads.settled()
  }

  loadFlows(currentFlow: () => string | null, vanished: () => void): Promise<void> {
    return this.#flowLoads.run(
      (signal) => this.api<FlowsListing>(sessionPath(this.key, '/flows'), 'GET', undefined, signal),
      (data) => {
        this.flows = data
        this.flowsError = null
        this.flowsBlanked = false
        this.#shownFlows = data.rev ?? null
        // The open flow is part of the library this listing replaced, and the
        // one most likely to be out of date.
        const name = currentFlow()
        if (name && !(data.flows || []).some((f) => f.name === name)) {
          this.flowDoc = null
          vanished()
        } else if (name) {
          void this.loadFlow(name)
        }
      },
      (e) => { this.flowsError = e.message },
    )
  }

  loadFlow(name: string): Promise<void> {
    return this.#docLoads.run(
      (signal) => this.api<FlowDoc>(sessionPath(this.key, '/flows/' + encodeURIComponent(name)), 'GET', undefined, signal),
      (doc) => { this.flowDoc = doc; this.flowDocError = null },
      (e) => { this.flowDocError = { name, message: e.message } },
    )
  }

  /* A pushed session list, applied to this session. */
  onPushed(data: SessionsPayload, currentFlow: () => string | null, vanished: () => void, browserChanged: () => void) {
    const row = (data.sessions || []).find((s) => s.key === this.key)
    // Gone from the store means expired: what is on screen is still a true record.
    if (!row) return
    // A different browser is a different download store, and the Grid deletes
    // it with the browser — so only Downloads is blanked. `live` is checked too:
    // a reap can leave the recorded id unchanged.
    if ((row.session_id || null) !== this.#shownBrowser || !!row.live !== this.#shownLive) {
      browserChanged()
      this.#shownBrowser = row.session_id || null
      this.#shownLive = !!row.live
      this.files = NO_FILES
      this.#shownFiles = null
      if (this.view) this.view = { ...this.view, downloads: [], downloadsEmpty: downloadsEmpty(!!row.session_id) }
    }
    this.row = row
    if (filesStamp(row) !== this.#shownFiles) void this.loadFiles()
    if ((row.flows_rev ?? null) !== this.#shownFlows) void this.loadFlows(currentFlow, vanished)
  }

  dispose() {
    this.#fileLoads.abort()
    this.#flowLoads.abort()
    this.#docLoads.abort()
  }
}
```

Run: `npm --prefix ui test -- session` → PASS; then the two vacuity checks from Step 1.

- [ ] **Step 3: `Section.svelte`** — the accordion (F1):

```svelte
<script lang="ts">
  import type { Snippet } from 'svelte'

  let { id, title, count, children, actions }: {
    id: string
    title: string
    count: string | number
    children: Snippet
    actions?: Snippet
  } = $props()
  let open = $state(true)
  const bodyId = $derived(id.replace(/Section$/, 'Body'))
</script>

<section class="section" {id} data-open={String(open)}>
  <div class="head">
    <!-- A button, not a styled span: the only way to open or close the section,
         so it must be reachable by keyboard and announce its state. -->
    <button type="button" class="title" aria-expanded={open} aria-controls={bodyId} onclick={() => (open = !open)}>
      <span class="caret">{open ? '▾' : '▸'}</span>{title}</button>
    <span id={id.replace(/Section$/, 'Count')} class="pill">{count}</span>
    <span class="grow"></span>
    {@render actions?.()}
  </div>
  <div class="body" id={bodyId}>{@render children()}</div>
</section>
```

The three sections use ids `downloadsSection`, `screenshotsSection`, `keptSection` → bodies `downloadsBody`/`screenshotsBody`/`keptBody` and counts `downloadsCount`/`screenshotsCount`/`keptCount`, as today.

- [ ] **Step 4: failing component tests** (`ui/src/admin/SessionDetail.test.ts`) — D1–D5, F1–F8, X1–X3 through the page:

```ts
import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { Live } from './live.svelte'
import SessionDetail from './SessionDetail.svelte'

const shot = (name: string) => ({ name, size: 1, url: '/s/' + name, image: true })
const row = { key: 'k', name: 'mine', live: true, attached: true, session_id: 'b1', files_rev: 1, flows_rev: 1, files_count: 3 }
const FILES = { session: row, downloads: [shot('d.png')], screenshots: [shot('a.png'), shot('b.png')], files: [shot('d.png')], browser: true }
const FLOWS = { enabled: true, flows: [], rev: 1, session: 'k' }
const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })

function setup(routes = {}, props = {}) {
  const net = fakeFetch({ 'GET /admin/sessions/k/files': { body: FILES }, 'GET /admin/sessions/k/flows': { body: FLOWS }, ...routes })
  const live = new Live(api, '')
  const r = render(SessionDetail, { key: 'k', tab: 'files', flow: undefined, api, live, root: '', ...props })
  return { ...r, ...net, live }
}
beforeEach(() => { history.replaceState(null, '', '/#/sessions/k'); vi.stubGlobal('alert', vi.fn()) })

test('switch-in: Loading… everywhere, then the three rows with counts (D4, F1, F2, F6)', async () => {
  const { container } = setup()
  expect(within(container.querySelector('#screenshots')!).getByText('Loading…')).toBeInTheDocument()
  await vi.waitFor(() => expect(container.querySelector('#screenshotsCount')).toHaveTextContent('2'))
  expect(container.querySelector('#downloadsCount')).toHaveTextContent('1')
  expect(container.querySelector('#keptCount')).toHaveTextContent('1')
  expect(container.querySelector('#filesTotal')).toHaveTextContent('3')
  const toggle = container.querySelector('[aria-controls="screenshotsBody"]')!
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await fireEvent.click(toggle)
  expect(container.querySelector('#screenshotsSection')).toHaveAttribute('data-open', 'false')
})

test('Files and Flows are tabs, routed by the hash (D3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => container.querySelector('#tabFlows'))
  await fireEvent.click(container.querySelector('#tabFlows')!)
  expect(location.hash).toBe('#/sessions/k/flows')
  expect(container.querySelector('#tabFiles')).toHaveAttribute('aria-selected', 'true') // the prop moves, not the click
})

test('Clear downloads needs a live browser and this session’s files (F3)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/files': { body: { ...FILES, session: { ...row, live: false } } } })
  await vi.waitFor(() => expect(container.querySelector('#downloadsCount')).toHaveTextContent('1'))
  expect(container.querySelector('#clearDownloads')).toBeDisabled()
})

test('Clear downloads lists every name with its fate, and clears (F3)', async () => {
  const { container, calls } = setup({ 'DELETE /admin/sessions/k/files/downloads': { body: {} } })
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearDownloads')!)
  const sheet = container.ownerDocument.querySelector('.modal')!
  expect(sheet).toHaveTextContent('Deletes what this browser downloaded. The Grid has no per-file delete, so this clears all of them.')
  expect(sheet.querySelector('.fate.stays')).toHaveTextContent('— copy in Files stays')
  await fireEvent.click(within(sheet as HTMLElement).getByText('Clear 1 file'))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/admin/sessions/k/files/downloads')).toBe(true))
})

test('Clear screenshots lists them and says what stays (F4)', async () => {
  const { container } = setup()
  await vi.waitFor(() => expect(container.querySelector('#clearScreenshots')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearScreenshots')!)
  const sheet = container.ownerDocument.querySelector('.modal')!
  expect(sheet).toHaveTextContent('Deletes all 2 screenshots in this session. Anything you kept is in Files and stays.')
  expect(within(sheet as HTMLElement).getByText('Delete 2 screenshots')).toHaveClass('danger')
})

test('a tile keep posts to its own folder and reloads; a failure alerts (F5)', async () => {
  const { container, calls } = setup({ 'POST /admin/sessions/k/files/screenshots/a.png/keep': { status: 409, body: { error: 'clash' } } })
  await vi.waitFor(() => container.querySelector('#screenshots button.keep'))
  await fireEvent.click(container.querySelector('#screenshots button.keep')!)
  await vi.waitFor(() => expect(alert).toHaveBeenCalledWith('Could not keep that file: clash'))
  expect(calls.at(-1)!.path).toBe('/admin/sessions/k/files/screenshots/a.png/keep')
})

test('a Files tile deletes behind a confirm (F5)', async () => {
  const { container, calls } = setup({ 'DELETE /admin/sessions/k/files/d.png': { body: {} } })
  await vi.waitFor(() => container.querySelector('#kept button.drop'))
  await fireEvent.click(container.querySelector('#kept button.drop')!)
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(sheet).toHaveTextContent('This removes it from Files for good. There is no undo.')
  await fireEvent.click(within(sheet).getByText('Delete'))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/admin/sessions/k/files/d.png')).toBe(true))
})

test('the lightbox Keep moves on to the next screenshot (X3)', async () => {
  let kept = false
  const { container } = setup({
    'GET /admin/sessions/k/files': () => ({ body: kept ? { ...FILES, screenshots: [shot('b.png')], files_rev: 2 } : FILES }),
    'POST /admin/sessions/k/files/screenshots/a.png/keep': () => { kept = true; return { body: {} } },
  })
  await vi.waitFor(() => container.querySelector('#screenshots a.thumb'))
  await fireEvent.click(container.querySelector('#screenshots a.thumb')!)
  expect(screen.getByText('1 / 2')).toBeInTheDocument()
  await fireEvent.click(screen.getByText('📌 Keep'))
  await vi.waitFor(() => expect(screen.getByText('1 / 1')).toBeInTheDocument())
  expect(container.ownerDocument.querySelector('.lightbox .name')).toHaveTextContent('b.png')
})

test('End browser asks, deletes, and reloads; disabled when not attached (D2)', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  const { container, calls } = setup({ 'DELETE /admin/sessions/k': { body: {} } })
  await vi.waitFor(() => expect(container.querySelector('#endBrowser')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#endBrowser')!)
  expect(confirm).toHaveBeenCalledWith('End this browser? The session and its context are kept — whatever the browser was holding is lost.')
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/files').length).toBe(2))
})

test('a browser change closes the Downloads lightbox and its confirm (F8)', async () => {
  const { container, live } = setup()
  await vi.waitFor(() => container.querySelector('#downloads a.thumb'))
  await fireEvent.click(container.querySelector('#downloads a.thumb')!)
  expect(container.ownerDocument.querySelector('.lightbox')).not.toBeNull()
  live.data = { sessions: [{ ...row, session_id: 'b2' }] }
  await vi.waitFor(() => expect(container.ownerDocument.querySelector('.lightbox')).toBeNull())
})

test('the session going away takes its overlays and cancels its modal (D4, M1)', async () => {
  const { container, unmount } = setup()
  await vi.waitFor(() => expect(container.querySelector('#clearScreenshots')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearScreenshots')!)
  unmount()
  expect(document.querySelector('.modal')).toBeNull()
})
```

Run: `npm --prefix ui test -- SessionDetail` → FAIL.

- [ ] **Step 5: `FilesPane.svelte`**

```svelte
<script lang="ts">
  import FileGrid from '../lib/FileGrid.svelte'
  import type { FileEntry, Folder } from '../lib/types'
  import Section from './Section.svelte'
  import type { SessionModel } from './session.svelte'
  import { NO_FILES } from './session.svelte'

  let { m, root, hidden, onopen, onkeep, ondelete, onclear }: {
    m: SessionModel
    root: string
    hidden: boolean
    onopen: (folder: Folder, i: number) => void
    onkeep: (folder: Folder, f: FileEntry) => Promise<boolean>
    ondelete: (f: FileEntry) => void
    onclear: (what: 'downloads' | 'screenshots') => void
  } = $props()

  // Ending needs attached; clearing downloads needs it RUNNING — the Grid
  // deletes the download store with the browser — and this session's own files.
  const clearDownloadsOff = $derived(!m.row.live || m.files === NO_FILES || m.loadingFiles)
  // No per-screenshot delete on the Grid, so the clear is offered only when
  // there is something to take.
  const clearScreenshotsOff = $derived(!m.files.screenshots.length || m.loadingFiles)
  const count = (n: number | undefined) => (m.filesError || !m.view ? '' : String(n ?? ''))
</script>

{#snippet rows(folder: 'downloads' | 'screenshots' | 'kept')}
  {#if m.filesError}
    <div class="empty error">{m.filesError}</div>
  {:else if !m.view}
    <div class="empty">Loading…</div>
  {:else if folder === 'downloads'}
    <FileGrid files={m.view.downloads} base={root} action="keep" empty={m.view.downloadsEmpty}
              onopen={(i) => onopen('downloads', i)} onkeep={(f) => onkeep('downloads', f)} />
  {:else if folder === 'screenshots'}
    <FileGrid files={m.view.screenshots} base={root} action="keep" empty="No screenshots yet."
              onopen={(i) => onopen('screenshots', i)} onkeep={(f) => onkeep('screenshots', f)} />
  {:else}
    <FileGrid files={m.view.files} base={root} action="delete"
              empty="Nothing here yet — prints land here, and anything you keep."
              onopen={(i) => onopen('files', i)} {ondelete} />
  {/if}
{/snippet}

<div id="paneFiles" {hidden}>
  <Section id="downloadsSection" title="Downloads" count={count(m.view?.downloads.length)}>
    {#snippet actions()}
      <button id="clearDownloads" class="danger" disabled={clearDownloadsOff}
              title="Delete everything this browser downloaded. Kept files stay."
              onclick={() => onclear('downloads')}>Clear downloads</button>
    {/snippet}
    <div id="downloads">{@render rows('downloads')}</div>
  </Section>
  <Section id="screenshotsSection" title="Screenshots" count={count(m.view?.screenshots.length)}>
    {#snippet actions()}
      <button id="clearScreenshots" class="danger" disabled={clearScreenshotsOff}
              title="Delete every screenshot. Anything you kept is in Files and stays."
              onclick={() => onclear('screenshots')}>Clear screenshots</button>
    {/snippet}
    <div id="screenshots">{@render rows('screenshots')}</div>
  </Section>
  <!-- No clear: everything in Files was put there on purpose (§F4.1). -->
  <Section id="keptSection" title="Files" count={count(m.view?.files.length)}>
    <div id="kept">{@render rows('kept')}</div>
  </Section>
</div>
```

- [ ] **Step 6: `SessionDetail.svelte`**

```svelte
<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import Lightbox from '../lib/Lightbox.svelte'
  import SessionSummary from '../lib/SessionSummary.svelte'
  import type { FileEntry, Folder, SessionsPayload } from '../lib/types'
  import { sessionPath, type Api } from './api'
  import FilesPane from './FilesPane.svelte'
  import type { Live } from './live.svelte'
  import Modal from './Modal.svelte'
  import type { ModalSpec } from './modal'
  import { go, hashes, replace } from './router.svelte'
  import { SessionModel } from './session.svelte'
  // Task 8: import FlowsPane from './FlowsPane.svelte'

  let { key, tab, flow, api, live, root }: {
    key: string
    tab: 'files' | 'flows'
    flow: string | undefined
    api: Api
    live: Live
    root: string
  } = $props()

  const m = untrack(() => new SessionModel(key, api))
  let destroyed = false
  let modal = $state.raw<ModalSpec<never> | null>(null)
  let lightbox = $state<{ folder: Folder; index: number } | null>(null)
  let flowName = $state<string | null>(null)

  const ask = <T,>(spec: ModalSpec<T>) => { modal = spec as ModalSpec<never> }
  const closeModal = () => { modal = null }
  // The backstop the structure cannot give: a confirm click and a switch can
  // land back to back, so every action on this session checks first.
  const refuseIfGone = () => { if (destroyed) throw new Error('that session is no longer on screen') }

  /* Whatever is on screen about ONE browser's downloads, closed when that
     browser goes — a reap noticed in a push, or End browser. */
  function dropBrowserOverlays() {
    if (lightbox?.folder === 'downloads') lightbox = null
    if (modal?.scope === 'downloads') { modal.oncancel?.(); modal = null }
  }

  const vanished = () => {
    flowName = null
    replace(hashes.flows(key))
  }
  const known = (name: string) => (m.flows?.flows || []).some((f) => f.name === name)

  function openFlow(name: string) {
    flowName = name
    m.flowDoc = null
    replace(hashes.flow(key, name))
    return m.loadFlow(name)
  }

  onMount(() => {
    void (async () => {
      await m.loadFiles()
      if (destroyed) return
      await m.loadFlows(() => flowName, vanished)
      if (destroyed) return
      // A deep link opens a flow only once the listing says it exists (D5).
      if (flow && flow !== flowName && known(flow)) await openFlow(flow)
    })()
    return () => {
      destroyed = true
      m.dispose()
      modal?.oncancel?.()
    }
  })

  // The hash edited by hand while here.
  $effect(() => {
    const f = flow
    untrack(() => { if (f && f !== flowName && m.flows && known(f)) void openFlow(f) })
  })

  // Pushed updates. The first value is what the list already had.
  let seen: SessionsPayload | null = untrack(() => live.data)
  $effect(() => {
    const data = live.data
    if (!data || data === seen) return
    seen = data
    untrack(() => m.onPushed(data, () => flowName, vanished, dropBrowserOverlays))
  })

  async function keep(folder: Folder, f: FileEntry): Promise<boolean> {
    try {
      refuseIfGone()
      await api(sessionPath(key, '/files/' + encodeURIComponent(folder) + '/' + encodeURIComponent(f.name) + '/keep'), 'POST')
    } catch (err) {
      alert('Could not keep that file: ' + (err as Error).message)
      return false
    }
    if (!destroyed) void m.loadFiles()
    return true
  }

  function deleteFile(f: FileEntry, resolve?: () => void, reject?: (e: Error) => void) {
    ask({
      title: 'Delete a file', body: deleteBody, data: f.name, confirm: 'Delete', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(key, '/files/' + encodeURIComponent(f.name)), 'DELETE')
        if (resolve) resolve()
        else if (!destroyed) void m.loadFiles()
      },
      oncancel: reject ? () => reject(new Error('cancelled')) : undefined,
    })
  }

  function clear(what: 'downloads' | 'screenshots') {
    if (what === 'downloads') {
      // Which downloads Files also holds a copy of: the download goes, the
      // copy stays — said per row, the only version both complete and true.
      const kept = new Set(m.files.files.map((f) => f.name))
      const names = m.files.downloads.map((f) => ({ name: f.name, stays: kept.has(f.name) }))
      ask({
        scope: 'downloads', title: 'Clear downloads', body: clearDownloadsBody, data: names, danger: true,
        confirm: names.length ? 'Clear ' + names.length + ' file' + (names.length === 1 ? '' : 's') : 'Clear',
        onconfirm: async () => {
          refuseIfGone()
          await api(sessionPath(key, '/files/downloads'), 'DELETE')
          if (!destroyed) void m.loadFiles()
        },
      })
    } else {
      const names = m.files.screenshots.map((f) => f.name)
      ask({
        title: 'Clear screenshots', body: clearScreenshotsBody, data: names, danger: true,
        confirm: 'Delete ' + names.length + ' screenshot' + (names.length === 1 ? '' : 's'),
        onconfirm: async () => {
          refuseIfGone()
          await api(sessionPath(key, '/files/screenshots'), 'DELETE')
          if (!destroyed) void m.loadFiles()
        },
      })
    }
  }

  /* Ending drops the browser, not the session: its next open carries on. */
  async function endBrowser() {
    if (!confirm('End this browser? The session and its context are kept — whatever the browser was holding is lost.')) return
    try {
      await api(sessionPath(key), 'DELETE')
    } catch (err) {
      alert('Could not end the browser: ' + (err as Error).message)
      return
    }
    if (destroyed) { void live.load(); return }
    dropBrowserOverlays()
    void m.loadFiles()
    void m.loadFlows(() => flowName, vanished)
  }

  const lightboxAction = $derived.by(() => {
    if (!lightbox) return undefined
    const folder = lightbox.folder
    return folder === 'files'
      ? { label: '🗑 Delete', run: (f: FileEntry) => new Promise<void>((resolve, reject) => { refuseIfGone(); deleteFile(f, resolve, reject) }) }
      : { label: '📌 Keep', run: async (f: FileEntry) => {
          refuseIfGone()
          await api(sessionPath(key, '/files/' + encodeURIComponent(folder) + '/' + encodeURIComponent(f.name) + '/keep'), 'POST')
        } }
  })

  async function refreshLightbox(at: number) {
    if (destroyed || !lightbox) return null
    const folder = lightbox.folder
    await m.loadFiles()
    await m.filesSettled()
    if (destroyed) return null
    return { files: m.files[folder], index: at }
  }

  const filesTotal = $derived(m.filesError ? '' : String(m.row.files_count ?? ''))
  const flowsTotal = $derived(!m.flows || m.flowsBlanked ? '' : m.flows.enabled ? String((m.flows.flows || []).length) : 'off')
</script>

{#snippet deleteBody(name: string)}
  <p>This removes it from Files for good. There is no undo.</p>
  <ul class="names"><li>{name}</li></ul>
{/snippet}

{#snippet clearDownloadsBody(names: { name: string; stays: boolean }[])}
  <p>Deletes what this browser downloaded. The Grid has no per-file delete, so this clears all of them.</p>
  {#if names.length}
    <ul class="names">
      {#each names as n (n.name)}
        <li>{n.name}{#if n.stays}<span class="fate stays"> — copy in Files stays</span>{:else}<span class="fate gone"> — gone</span>{/if}</li>
      {/each}
    </ul>
  {:else}
    <p class="small muted">There is nothing downloaded to clear.</p>
  {/if}
{/snippet}

{#snippet clearScreenshotsBody(names: string[])}
  <p>Deletes {names.length === 1 ? 'the 1 screenshot' : 'all ' + names.length + ' screenshots'} in this session. Anything you kept is in Files and stays.</p>
  <ul class="names">{#each names as n (n)}<li>{n}</li>{/each}</ul>
{/snippet}

<div id="detailView">
  <div class="row" style="margin-bottom:12px">
    <button id="back" onclick={() => go(hashes.list)}>← Sessions</button>
    <span class="grow"></span>
    <button id="endBrowser" class="danger" disabled={!m.row.attached}
            title="Quit this browser. The session and its context are kept." onclick={endBrowser}>End browser</button>
  </div>
  <div id="detailHeader">{#if m.view || m.filesError || m.row.session_id !== undefined}<SessionSummary data={m.row} />{/if}</div>

  <div class="tabs subtabs" id="sessionTabs">
    <button id="tabFiles" aria-selected={tab === 'files'} onclick={() => go(hashes.session(key))}>Files <span id="filesTotal" class="count">{filesTotal}</span></button>
    <button id="tabFlows" aria-selected={tab === 'flows'} onclick={() => go(hashes.flows(key))}>Flows <span id="flowsTotal" class="count">{flowsTotal}</span></button>
  </div>

  <!-- Both panes stay mounted and toggle `hidden`, as today: a section
       collapsed on Files is still collapsed after a look at Flows. -->
  <FilesPane {m} {root} hidden={tab !== 'files'}
             onopen={(folder, index) => (lightbox = { folder, index })}
             onkeep={keep} ondelete={(f) => deleteFile(f)} onclear={clear} />
  <div id="paneFlows" hidden={tab !== 'flows'}>
    <!-- Task 8: <FlowsPane … /> -->
    <div class="section"><div class="body"><div id="flows"><div class="empty">Loading…</div></div></div></div>
  </div>
</div>

{#if lightbox}
  <Lightbox files={m.files[lightbox.folder]} index={lightbox.index} base={root}
            action={lightboxAction} refresh={refreshLightbox} onclose={() => (lightbox = null)} />
{/if}
{#if modal}
  <Modal spec={modal} onclosed={closeModal} />
{/if}
```

Notes the implementer must keep:
- Today the header is blank until the first answer (`detailHeader.innerHTML = ''` on switch). The `{#if}` above reproduces that: nothing until files answer (or a push lands a row).
- `paneFiles`/`paneFlows` both stay mounted and toggle `hidden`, as today, so an accordion's state survives a tab switch. Tab switching on the same session does not refetch, because the model lives in `SessionDetail` (D3). Add a test: collapse Screenshots, switch the `tab` prop to `flows` and back (`rerender`), and it is still collapsed.

- [ ] **Step 7: wire it into `Admin.svelte`** — replace the Task 7 marker and placeholder with:

```svelte
{#key route.key}
  <SessionDetail key={route.key} tab={route.tab} flow={route.flow} {api} {live} root={ROOT} />
{/key}
```

and uncomment the import.

- [ ] **Step 8: Run and commit**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint` → pass, clean.

```bash
git add -A ui/src
git commit -m "A session in Svelte: header, End browser, Files with its clears and lightbox; the race guards become structure

Vacuity checked: dispose() as a no-op fails the switch test; filesSettled() returning at once fails the Keep-race test."
```

---

### Task 8: Flows

**Files:**
- Create: `ui/src/admin/FlowsPane.svelte`, `StepRow.svelte`, `ParamRow.svelte`, `StepDetail.svelte`, `ParamDetail.svelte`, `Pair.svelte`
- Modify: `ui/src/admin/SessionDetail.svelte` (render `FlowsPane`, remove the Task 8 marker)
- Test: `ui/src/admin/FlowsPane.test.ts`

**Interfaces:**
- Consumes: `SessionModel` (Task 7), `ModalSpec` (Task 6), `flow.ts` (Task 3), `sessionPath`, `replace`, `hashes`
- Produces: `FlowsPane` props `{ m: SessionModel; flowName: string | null; api: Api; ask: <T>(spec: ModalSpec<T>) => void; refuseIfGone: () => void; isGone: () => boolean; onpick: (name: string) => void; onclosed: () => void }` — `onclosed` is called after a move or delete: `SessionDetail` sets `flowName = null`, `replace(hashes.flows(key))`, and reloads flows.

- [ ] **Step 1: failing tests** (`ui/src/admin/FlowsPane.test.ts`)

```ts
import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { Live } from './live.svelte'
import SessionDetail from './SessionDetail.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
const DOC = {
  name: 'login', shared: false, description: 'Signs in.',
  parameters: { properties: { user: { type: 'string', description: 'who' }, n: { type: 'integer', default: 2 } }, required: ['user'] },
  steps: [
    { tool: 'navigate', args: { url: '${site}/' } },
    { id: 'type-pass', tool: 'write', args: { selector: { css: '#p' }, secret: { name: 'admin', key: 'token' } }, onError: 'continue' },
    { tool: 'execute_script', args: { script: 'a;\nb;' } },
    null,
  ],
  uses: { user: [1] },
  yaml: '# comments survive\nsteps: []\n',
}
const LIST = { enabled: true, session: 'k', rev: 1, flows: [{ name: 'login', step_count: 4 }, { name: 'shared-one', step_count: 1, shared: true }] }

function setup(routes = {}, flow: string | undefined = 'login') {
  const net = fakeFetch({
    'GET /admin/sessions/k/files': { body: { session: { key: 'k', flows_rev: 1 }, downloads: [], screenshots: [], files: [] } },
    'GET /admin/sessions/k/flows': { body: LIST },
    'GET /admin/sessions/k/flows/login': { body: DOC },
    ...routes,
  })
  const r = render(SessionDetail, { key: 'k', tab: 'flows', flow, api, live: new Live(api, ''), root: '' })
  return { ...r, ...net }
}
beforeEach(() => { history.replaceState(null, '', '/#/sessions/k/flows/login'); vi.stubGlobal('alert', vi.fn()) })

test('the list: names, step counts, a globe only when shared, the open one selected (W1)', async () => {
  const { container } = setup()
  await vi.waitFor(() => expect(container.querySelectorAll('.flowlist .item')).toHaveLength(2))
  const [a, b] = container.querySelectorAll('.flowlist .item')
  expect(a).toHaveTextContent('login4 steps')
  expect(a.querySelector('.globe')).toBeNull()
  expect(b.querySelector('.globe')).toHaveAttribute('aria-label', 'global')
  await vi.waitFor(() => expect(a).toHaveAttribute('aria-selected', 'true'))
})

test('flows off, and none yet (W1)', async () => {
  const off = setup({ 'GET /admin/sessions/k/flows': { body: { enabled: false } } }, undefined)
  await vi.waitFor(() => expect(off.container).toHaveTextContent('Flows are off: this server was started with no FLOW_DATA_DIR.'))
  expect(off.container.querySelector('#flowsTotal')).toHaveTextContent('off')
  off.unmount()
  const none = setup({ 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [] } } }, undefined)
  await vi.waitFor(() => expect(none.container).toHaveTextContent('No flows yet.'))
})

test('the outline: params first with type glyph and required star; steps with tool glyph, id and lock (W3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => container.querySelector('.outline'))
  const [params, steps] = container.querySelectorAll('.outline .rows')
  expect(params.querySelector('[data-param="user"] .req')).toHaveAttribute('aria-label', 'required')
  expect(params.querySelector('[data-param="user"] .icon')).toHaveTextContent('"')
  const rows = steps.querySelectorAll('.row')
  expect(rows[0].querySelector('.icon')).toHaveAttribute('title', 'navigate')
  expect(rows[0].querySelector('.chip')).toHaveTextContent('navigate')
  expect(rows[1].querySelector('.chip')).toHaveTextContent('type-pass')
  expect(rows[1].querySelector('.lock')).toHaveAttribute('aria-label', 'types a secret')
  expect(rows[3].querySelector('.icon')).toHaveTextContent('❓') // a [null] step never breaks the panel (W8)
  expect(container).toHaveTextContent('Pick a parameter or a step to see what it holds.')
})

test('step detail: number, chip, tool, arguments, code, selector, secret, behaviour (W3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => container.querySelector('[data-step="1"]'))
  await fireEvent.click(container.querySelector('.outline [data-step="1"]')!)
  const pane = container.querySelector('.pane') as HTMLElement
  expect(pane.querySelector('.dhead')).toHaveTextContent('2type-passwrite')
  expect(within(pane).getByText('css #p')).toBeInTheDocument()
  expect(within(pane).getByText('admin / token')).toBeInTheDocument()
  expect(within(pane).getByText('onError').nextElementSibling).toHaveTextContent('continue')
  await fireEvent.click(container.querySelector('.outline [data-step="2"]')!)
  expect(container.querySelector('.pane pre.pv.code')!.textContent).toBe('a;\nb;')
  await fireEvent.click(container.querySelector('.outline [data-step="2"]')!)
  expect(container).toHaveTextContent('Pick a parameter or a step to see what it holds.') // unpick
})

test('param detail: required, description, default, used-by rows that jump (W3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => container.querySelector('[data-param="user"]'))
  await fireEvent.click(container.querySelector('.outline [data-param="user"]')!)
  const pane = container.querySelector('.pane') as HTMLElement
  expect(within(pane).getByText('required')).toHaveClass('pill')
  expect(pane).toHaveTextContent('who')
  await fireEvent.click(pane.querySelector('.rows [data-step="1"]')!)
  expect(container.querySelector('.pane .dhead')).toHaveTextContent('type-pass')
  await fireEvent.click(container.querySelector('.outline [data-param="n"]')!)
  expect(container.querySelector('.pane')).toHaveTextContent('No step reads ${n}. Supplying it would change nothing.')
  expect(container.querySelector('.pane')).toHaveTextContent('optional')
})

test('Edit reads the file again, shows it raw, and saves it (W4)', async () => {
  const { container, calls } = setup({ 'PUT /admin/sessions/k/flows/login': { body: {} } })
  await vi.waitFor(() => container.querySelector('[data-edit]'))
  await fireEvent.click(container.querySelector('[data-edit]')!)
  const sheet = await vi.waitFor(() => container.ownerDocument.querySelector('.modal') as HTMLElement)
  expect(sheet.querySelector('.head')).toHaveTextContent('Edit login')
  const area = sheet.querySelector('textarea.yaml') as HTMLTextAreaElement
  expect(area.value).toBe('# comments survive\nsteps: []\n')
  await fireEvent.input(area, { target: { value: 'steps: [x]' } })
  await fireEvent.click(within(sheet).getByText('Save'))
  await vi.waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ yaml: 'steps: [x]' }))
})

test('Move and Delete say where, act, and close the flow (W5, W6)', async () => {
  const { container, calls } = setup({ 'POST /admin/sessions/k/flows/login/move': { body: {} }, 'DELETE /admin/sessions/k/flows/login': { body: {} } })
  await vi.waitFor(() => container.querySelector('[data-move]'))
  expect(container.querySelector('[data-move]')).toHaveAttribute('title', 'Move to global')
  await fireEvent.click(container.querySelector('[data-move]')!)
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(sheet).toHaveTextContent('It moves into the shared global library, where every session can list and run it.')
  await fireEvent.click(within(sheet).getByText('Move'))
  await vi.waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ to: 'global' }))
  await vi.waitFor(() => expect(location.hash).toBe('#/sessions/k/flows'))
})

test('a malformed flow never breaks the panel (W8)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/flows/login': { body: { name: 'login', steps: {}, parameters: 1, uses: [] } } })
  await vi.waitFor(() => expect(container).toHaveTextContent('This flow takes nothing.'))
})
```

- [ ] **Step 2: the small components**

`Pair.svelte` — a value that spans lines is code and gets the full width:

```svelte
<script lang="ts">
  let { k, v }: { k: string; v: string } = $props()
</script>

{#if !v.includes('\n')}
  <div class="pair"><span class="pk">{k}</span><span class="pv">{v}</span></div>
{:else}
  <div class="pair block"><span class="pk">{k}</span><pre class="pv code">{v}</pre></div>
{/if}
```

`StepRow.svelte`:

```svelte
<script lang="ts">
  import { bindsSecret, mapping, own, TOOL_ICON } from './flow'

  let { entry, i, cite = false, picked }: { entry: unknown; i: number; cite?: boolean; picked: boolean } = $props()
  // `steps:` with an empty item parses to [null].
  const s = $derived(mapping(entry))
  const tool = $derived(String(s.tool || '?'))
</script>

<div class="row" data-step={i} aria-selected={picked}>
  {#if cite}<span class="n">{i + 1}</span>{/if}
  <span class="icon" role="img" aria-label={tool} title={tool}>{own(TOOL_ICON, tool) || '❓'}</span>
  <span class="chip">{String(s.id || tool)}</span>
  {#if bindsSecret(s)}
    <span class="grow"></span>
    <span class="lock" role="img" aria-label="types a secret" title="This step types a secret">🔒</span>
  {/if}
</div>
```

`ParamRow.svelte`:

```svelte
<script lang="ts">
  import { mapping, own, TYPE_ICON } from './flow'

  let { name, spec, required, picked }: { name: string; spec: unknown; required: boolean; picked: boolean } = $props()
  const ty = $derived(String(mapping(spec).type || ''))
  const said = $derived(ty || 'any type')
</script>

<div class="row" data-param={name} aria-selected={picked}>
  <span class="icon" role="img" aria-label={said} title={said}>{own(TYPE_ICON, ty) || '·'}</span>
  <span class="chip">{name}{#if required}<span class="req" role="img" aria-label="required" title="required">*</span>{/if}</span>
</div>
```

`StepDetail.svelte`:

```svelte
<script lang="ts">
  import type { FlowDoc } from '../lib/types'
  import { argValue, listed, mapping } from './flow'
  import Pair from './Pair.svelte'

  let { f, i }: { f: FlowDoc; i: number } = $props()
  const steps = $derived(listed(f.steps))
  const s = $derived(mapping(steps[i]))
  const tool = $derived(String(s.tool || '?'))
  const args = $derived(Object.entries(mapping(s.args)))
</script>

{#if i < 0 || i >= steps.length}
  <div class="hint">That step is gone.</div>
{:else}
  <div class="dhead">
    <span class="n">{i + 1}</span>
    <span class="chip">{String(s.id || tool)}</span>
    {#if s.id}<span class="ty">{tool}</span>{/if}
  </div>
  {#if s.note}<div class="hint">{String(s.note)}</div>{/if}
  <div class="dlabel">Arguments</div>
  {#if !args.length}
    <div class="hint">This step takes nothing.</div>
  {:else}
    <div class="pairs">{#each args as [k, v] (k)}<Pair {k} v={argValue(k, v)} />{/each}</div>
  {/if}
  <!-- Only when set: `onError: abort` on every step would bury the one `continue`. -->
  {#if s.onError || s.return}
    <div class="dlabel">Behaviour</div>
    <div class="pairs">
      {#if s.onError}<Pair k="onError" v={String(s.onError)} />{/if}
      {#if s.return}<Pair k="return" v="this step’s full result" />{/if}
    </div>
  {/if}
{/if}
```

`ParamDetail.svelte`:

```svelte
<script lang="ts">
  import type { FlowDoc } from '../lib/types'
  import { listed, mapping, own } from './flow'
  import Pair from './Pair.svelte'
  import StepRow from './StepRow.svelte'

  let { f, name }: { f: FlowDoc; name: string } = $props()
  const declared = $derived(mapping(mapping(f.parameters).properties))
  // `term:` with nothing after it is declared, and says nothing about itself.
  const spec = $derived(mapping(own(declared, name)))
  const required = $derived(listed(mapping(f.parameters).required).includes(name))
  const steps = $derived(listed(f.steps))
  const used = $derived(listed(own(mapping(f.uses), name)) as number[])
</script>

{#if !Object.hasOwn(declared, name)}
  <div class="hint">That parameter is gone.</div>
{:else}
  <div class="dhead">
    <span class="chip">{name}</span>
    {#if spec.type}<span class="ty">{String(spec.type)}</span>{/if}
    <span class="grow"></span>
    <span class="pill">{required ? 'required' : 'optional'}</span>
  </div>
  {#if spec.description}<div class="hint">{String(spec.description)}</div>{/if}
  {#if spec.default !== undefined}
    <div class="dlabel">Default</div>
    <div class="pairs"><Pair k="default" v={typeof spec.default === 'object' ? JSON.stringify(spec.default) : String(spec.default)} /></div>
  {/if}
  <div class="dlabel">Used by</div>
  {#if used.length}
    <!-- Rows, not prose: clicking one jumps to the step that reads this. -->
    <div class="rows">{#each used as i (i)}<StepRow entry={steps[i] || {}} {i} cite picked={false} />{/each}</div>
  {:else}
    <div class="hint">No step reads <code>${'{'}{name}{'}'}</code>. Supplying it would change nothing.</div>
  {/if}
{/if}
```

- [ ] **Step 3: `FlowsPane.svelte`**

```svelte
<script lang="ts">
  import { untrack } from 'svelte'
  import { sessionPath, type Api } from './api'
  import { listed, mapping } from './flow'
  import type { ModalSpec } from './modal'
  import ParamDetail from './ParamDetail.svelte'
  import ParamRow from './ParamRow.svelte'
  import type { SessionModel } from './session.svelte'
  import StepDetail from './StepDetail.svelte'
  import StepRow from './StepRow.svelte'

  let { m, flowName, api, ask, refuseIfGone, isGone, onpick, onclosed }: {
    m: SessionModel
    flowName: string | null
    api: Api
    ask: <T>(spec: ModalSpec<T>) => void
    refuseIfGone: () => void
    isGone: () => boolean
    onpick: (name: string) => void
    onclosed: () => void
  } = $props()

  // One selection across params and steps: the pane beside them shows one thing.
  let picked = $state<{ kind: 'param'; key: string } | { kind: 'step'; key: number } | null>(null)
  // A different document clears the selection; a refresh of the same one keeps it.
  $effect(() => { void flowName; untrack(() => { picked = null }) })
  let draft = $state('')

  const data = $derived(m.flows ?? { enabled: true, flows: [] })
  const f = $derived(m.flowDoc && m.flowDoc.name === flowName ? m.flowDoc : null)
  const docError = $derived(m.flowDocError && m.flowDocError.name === flowName ? m.flowDocError.message : null)
  const declared = $derived(f ? mapping(mapping(f.parameters).properties) : {})
  const required = $derived(f ? listed(mapping(f.parameters).required) : [])
  const names = $derived(Object.keys(declared))
  const steps = $derived(f ? listed(f.steps) : [])
  // `global` is a folder like any other; a session literally named `global` IS
  // the shared library, so the move would go nowhere.
  const canMove = $derived(!m.flows || m.flows.session !== 'global')

  function pick(e: MouseEvent) {
    const row = (e.target as HTMLElement).closest('.rows .row') as HTMLElement | null
    if (!row) return
    const next = row.dataset.param !== undefined
      ? { kind: 'param' as const, key: row.dataset.param }
      : { kind: 'step' as const, key: Number(row.dataset.step) }
    picked = picked && picked.kind === next.kind && picked.key === next.key ? null : next
  }
  const isPicked = (kind: 'param' | 'step', key: string | number) => !!picked && picked.kind === kind && picked.key === key

  async function edit() {
    const doc = f!
    const path = sessionPath(m.key, '/flows/' + encodeURIComponent(doc.name))
    // Read again: the editor's Save is a blind PUT, and the copy on screen can
    // be minutes old (§F1.40 records the rest).
    let fresh: { yaml?: string }
    try { fresh = await api(path) } catch (err) { alert('Could not read that flow: ' + (err as Error).message); return }
    draft = fresh.yaml || ''
    ask({
      title: 'Edit ' + doc.name, body: editorBody, data: null, confirm: 'Save',
      onconfirm: async () => {
        refuseIfGone()
        await api(path, 'PUT', { yaml: draft })
        if (!isGone()) await m.loadFlows(() => flowName, onclosed)
      },
    })
  }

  function move() {
    const doc = f!
    const to = doc.shared ? String(m.flows!.session) : 'global'
    ask({
      title: doc.shared ? 'Move to this session' : 'Move to global', body: moveBody, data: { shared: !!doc.shared, to }, confirm: 'Move',
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(m.key, '/flows/' + encodeURIComponent(doc.name) + '/move'), 'POST', { to })
        if (!isGone()) onclosed()
      },
    })
  }

  function drop() {
    const doc = f!
    ask({
      title: 'Delete this flow?', body: dropBody, data: (doc.shared ? 'global / ' : '') + doc.name, confirm: 'Delete', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(m.key, '/flows/' + encodeURIComponent(doc.name)), 'DELETE')
        if (!isGone()) onclosed()
      },
    })
  }
</script>

{#snippet editorBody(_: null)}<textarea class="yaml" spellcheck="false" bind:value={draft}></textarea>{/snippet}
{#snippet moveBody(d: { shared: boolean; to: string })}
  {#if d.shared}
    <p>It moves out of the shared library into <strong>{d.to}</strong>. Other sessions stop seeing it.</p>
  {:else}
    <p>It moves into the shared <strong>global</strong> library, where every session can list and run it. No agent can change what is in there — only an operator, here.</p>
  {/if}
{/snippet}
{#snippet dropBody(label: string)}
  <p>It is removed from the folder it lives in. A flow in the <strong>global</strong> folder goes for every session, not just this one.</p>
  <ul class="names"><li>{label}</li></ul>
{/snippet}

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions — today's rows are mouse-only -->
<div id="flows" onclick={pick}>
  {#if m.flowsError}
    <div class="empty error">{m.flowsError}</div>
  {:else if !m.flows}
    <div class="empty">Loading…</div>
  {:else if !data.enabled}
    <div class="empty">Flows are off: this server was started with no FLOW_DATA_DIR.</div>
  {:else if !(data.flows || []).length}
    <div class="empty">No flows yet.</div>
  {:else}
    <div class="flows">
      <div class="flowlist" id="flowlist">
        {#each data.flows || [] as item (item.name)}
          <div class="item" aria-selected={item.name === flowName} onclick={() => onpick(item.name)}>
            <div class="grow">
              <div class="nm">{item.name}</div>
              <div class="ct">{item.step_count} step{item.step_count === 1 ? '' : 's'}</div>
            </div>
            {#if item.shared}<span class="globe" role="img" aria-label="global" title="In the shared global library">🌐</span>{/if}
          </div>
        {/each}
      </div>
      <div id="flowpanel">
        {#if !flowName}
          <div class="empty">Pick a flow.</div>
        {:else if docError}
          <div class="empty error">{docError}</div>
        {:else if !f}
          <div class="empty">Loading…</div>
        {:else}
          <div class="panel">
            <div class="head">
              <span class="nm">{f.name}</span>
              {#if f.shared}<span class="pill">🌐 global</span>{/if}
              <span class="grow"></span>
              <div class="acts">
                <button type="button" data-edit title="Edit YAML" aria-label="Edit YAML" onclick={edit}>✏️</button>
                {#if canMove}
                  <button type="button" data-move title={f.shared ? 'Move to this session' : 'Move to global'}
                          aria-label={f.shared ? 'Move to this session' : 'Move to global'} onclick={move}>{f.shared ? '🏠' : '🌐'}</button>
                {/if}
                <button type="button" class="danger" data-drop title="Delete this flow" aria-label="Delete this flow" onclick={drop}>🗑️</button>
              </div>
            </div>
            {#if f.description}<div class="desc">{f.description}</div>{/if}
            <div class="panes">
              <div class="outline">
                <div class="olabel">Params</div>
                {#if names.length}
                  <div class="rows">{#each names as n (n)}<ParamRow name={n} spec={declared[n]} required={required.includes(n)} picked={isPicked('param', n)} />{/each}</div>
                {:else}
                  <div class="none">This flow takes nothing.</div>
                {/if}
                <div class="olabel">Steps</div>
                <div class="rows">{#each steps as s, i (i)}<StepRow entry={s} {i} picked={isPicked('step', i)} />{/each}</div>
              </div>
              <div class="rule"></div>
              <div class="pane">
                {#if !picked}
                  <div class="hint">Pick a parameter or a step to see what it holds.</div>
                {:else if picked.kind === 'param'}
                  <ParamDetail {f} name={picked.key} />
                {:else}
                  <StepDetail {f} i={picked.key} />
                {/if}
              </div>
            </div>
          </div>
        {/if}
      </div>
    </div>
  {/if}
</div>
```

Today `#flows` sits inside `<div class="section"><div class="body">…`; keep that wrapper in `SessionDetail` around `<FlowsPane>` (`<div id="paneFlows"><div class="section"><div class="body"><FlowsPane …/></div></div></div>`). The glyphs: ✏️ = `&#9999;&#65039;`, 🏠 = `&#127968;`, 🌐 = `&#127760;`, 🗑️ = `&#128465;&#65039;`, 🔒 = `&#128274;`.

- [ ] **Step 4: wire it into `SessionDetail.svelte`** — replace the Task 8 marker:

```svelte
<div id="paneFlows" hidden={tab !== 'flows'}>
  <div class="section"><div class="body">
    <FlowsPane {m} {flowName} {api} {ask} {refuseIfGone} isGone={() => destroyed}
               onpick={(name) => void openFlow(name)}
               onclosed={() => { flowName = null; replace(hashes.flows(key)); void m.loadFlows(() => flowName, vanished) }} />
  </div></div>
</div>
```

`openFlow` already clears the doc so the panel shows `Loading…` (W2). The listing's own "vanished" path uses `vanished` (no reload: the listing just answered).

- [ ] **Step 5: Run and commit**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint` → pass, clean.

```bash
git add -A ui/src
git commit -m "Flows in Svelte: the list, the outline and its detail, edit, move and delete"
```

---

### Task 9: Secrets

**Files:**
- Create: `ui/src/admin/SecretsPane.svelte`
- Modify: `ui/src/admin/Admin.svelte` (render it, remove the Task 9 marker)
- Test: `ui/src/admin/SecretsPane.test.ts`

**Interfaces:**
- Consumes: `Api`, `Latest` (Task 3); `SecretsPayload` (Task 3)
- Produces: `SecretsPane` props `{ api: Api }`

- [ ] **Step 1: failing tests** (`ui/src/admin/SecretsPane.test.ts`) — S1:

```ts
import { render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import SecretsPane from './SecretsPane.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })

test('Loading…, then a card per secret with keys, allowed, source and backlinks (S1)', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 'admin', description: 'The token.', keys: ['token'], restricted: true, allowed_urls: ['https://a'], source: 'file', location: '/s/admin',
        uses: [{ flow: 'login', steps: [2], session: 'k 1' }, { flow: 'g', steps: [1, 3], shared: true }] },
      { name: 'open', keys: ['k'], restricted: false, uses: [] },
      { name: 'bad', keys: ['k'], allowed_urls_rejected: ['ftp://x'], uses: [] },
    ],
    undefined: [{ name: 'ghost', uses: [{ flow: 'f', steps: [1], session: 's' }] }],
  } } })
  const { container } = render(SecretsPane, { api })
  expect(screen.getByText('Loading…')).toBeInTheDocument()
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const [admin, open, bad, ghost] = container.querySelectorAll('.card.secret')
  expect(admin).toHaveTextContent('🔑admin')
  expect(admin).toHaveTextContent('allowedhttps://a')
  expect(admin).toHaveTextContent('fromfile · /s/admin')
  expect(admin.querySelector('a')).toHaveAttribute('href', '#/sessions/k%201/flows/login')
  expect(admin.querySelector('a')).toHaveTextContent('k 1 →')
  expect(admin).toHaveTextContent('steps 1, 3')
  expect(admin).toHaveTextContent('🌐 shared')
  expect(open.querySelector('.pill.warn')).toHaveTextContent('any site')
  expect(open).toHaveTextContent('No flow uses it.')
  expect(bad.querySelector('.pill.warn')).toHaveTextContent('unusable until fixed')
  expect(bad).toHaveTextContent('allowed_urls: ftp://x')
  expect(screen.getByText('Named by a flow, not defined')).toBeInTheDocument()
  expect(ghost.querySelector('.pill.warn')).toHaveTextContent('not defined')
})

test('off, and an error (S1)', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: { enabled: false, secrets: [], undefined: [] } } })
  const r = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('No secrets are configured on this server.')).toBeInTheDocument())
  r.unmount()
  fakeFetch({ 'GET /admin/secrets': { status: 500, body: { error: 'nope' } } })
  render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
})
```

- [ ] **Step 2: `SecretsPane.svelte`**

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import type { Secret, SecretsPayload, SecretUse } from '../lib/types'
  import type { Api } from './api'
  import { Latest } from './latest'

  let { api }: { api: Api } = $props()
  let data = $state<SecretsPayload | null>(null)
  let error = $state<string | null>(null)
  const loads = new Latest()

  onMount(() => {
    void loads.run((signal) => api<SecretsPayload>('/admin/secrets', 'GET', undefined, signal),
      (d) => { data = d; error = null }, (e) => { error = e.message })
    return () => loads.abort()
  })

  const flowHash = (u: SecretUse) => '#/sessions/' + encodeURIComponent(String(u.session)) + '/flows/' + encodeURIComponent(u.flow)
  const warnOf = (s: Secret) => (s.allowed_urls_rejected ? 'unusable until fixed' : s.restricted ? '' : 'any site')
  const reasonOf = (s: Secret) => (s.allowed_urls_rejected ? 'allowed_urls: ' + ([] as string[]).concat(s.allowed_urls_rejected).join(', ') : '')
</script>

{#snippet card(s: Secret, warn: string, reason: string)}
  <div class="card secret">
    <div class="row"><span>🔑</span><strong class="grow">{s.name}</strong>{#if warn}<span class="pill warn">{warn}</span>{/if}</div>
    {#if s.description}<div>{s.description}</div>{/if}
    {#if reason}<div class="small error">{reason}</div>{/if}
    {#if s.keys}
      <div class="fact"><span class="k">keys</span>{#each s.keys as k (k)}<span class="pill">{k}</span>{/each}</div>
      <div class="fact"><span class="k">allowed</span>{s.restricted ? (s.allowed_urls || []).join(', ') || '—' : 'any site'}</div>
    {/if}
    {#if s.source}<div class="fact"><span class="k">from</span><span class="small muted">{s.source + ' · ' + (s.location || '')}</span></div>{/if}
    <div class="used">
      <div class="k">USED BY</div>
      {#each s.uses || [] as u (u.flow + (u.session ?? ''))}
        <div class="use">
          <span class="pill name">{u.flow}</span>
          <span class="small muted">step{u.steps.length === 1 ? ' ' : 's '}{u.steps.join(', ')}</span>
          <span class="grow"></span>
          {#if u.shared}<span class="small muted">🌐 shared</span>{:else}<a href={flowHash(u)}>{u.session} →</a>{/if}
        </div>
      {:else}
        <div class="small muted">No flow uses it.</div>
      {/each}
    </div>
  </div>
{/snippet}

<section id="paneSecrets">
  <div id="secrets">
    {#if error}
      <div class="empty error">{error}</div>
    {:else if !data}
      <div class="empty">Loading…</div>
    {:else if !data.enabled}
      <div class="empty">No secrets are configured on this server.</div>
    {:else}
      <h2>Secrets</h2>
      <p class="small muted">What flows can type without anyone seeing it. Read-only here: names, keys and where each may be used — never a value.</p>
      {#each data.secrets as s (s.name)}{@render card(s, warnOf(s), reasonOf(s))}{/each}
      {#if data.undefined.length}
        <h3>Named by a flow, not defined</h3>
        <p class="small muted">These flows will fail at the step that types the secret.</p>
        {#each data.undefined as u (u.name)}
          {@render card({ name: u.name, uses: u.uses }, 'not defined', 'A flow names this secret and the catalogue has no such entry, so the step will fail when it runs.')}
        {/each}
      {/if}
    {/if}
  </div>
</section>
```

- [ ] **Step 3: wire into `Admin.svelte`** — replace the Task 9 placeholder with `<SecretsPane {api} />` and uncomment its import. Leaving Secrets for a session unmounts `SessionDetail` (R4) — no `current = null` needed.

- [ ] **Step 4: Run, build, measure, commit**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint && npm --prefix ui run build && npm --prefix ui run size` → pass; `admin:` well under 45 KB.

```bash
git add -A ui/src
git commit -m "Secrets in Svelte; the admin page is complete"
```

---

### Task 10: The cut-over — Python serves the build, the UI is optional, `static/` goes

**Files:**
- Modify: `kubed/selenium_flow/http/admin.py:50-92` (`STATIC_DIR`, `static_path`, `read`, `page`; add `PLACEHOLDER`, `ui_built`), `:306-319` (the UI route)
- Modify: `kubed/selenium_flow/mcp/apps.py:44-48` (`SDK_ORIGIN` goes), `:90-96` (`_csp`), `:117-118` (`page("app")`); add `available()`
- Modify: `kubed/selenium_flow/server.py` (apps only when built; one log line)
- Modify: `pyproject.toml:95-135` (packages, package-dir, package-data and their comments)
- Modify: `tests/conftest.py` (fixtures), `tests/test_files_and_admin.py` (the page-grepping tests per the traceability table; the two CSP tests; the app-resource tests), `tests/test_packaging.py:355-395` (`DATA_DIRS`)
- Create: `tests/test_ui_serving.py`
- Delete: `static/` (all four files), `tests/test_admin_ui.py`

**Interfaces:**
- Produces: `admin.PLACEHOLDER: str`, `admin.ui_built(name: str) -> bool`, `admin.page(name: str, **subs) -> str` (name WITHOUT extension: `"admin"`, `"app"`), `apps.available() -> bool`; conftest fixtures `ui_dir` (autouse: an empty static dir) and `built_ui`.

- [ ] **Step 1: fixtures** — append to `tests/conftest.py`:

```python
from kubed.selenium_flow.http import admin as _admin


@pytest.fixture(autouse=True)
def ui_dir(tmp_path, monkeypatch):
    """An empty UI directory for every test: the suite must not depend on
    whether this checkout happens to have run `npm --prefix ui run build`."""
    folder = tmp_path / "ui-static"
    folder.mkdir()
    monkeypatch.setattr(_admin, "static_path", lambda: folder)
    return folder


SHELL = (
    '<title>{name}</title><style>__CSS__</style>'
    '<div id="root" data-mount="__MOUNT__" data-console="__CONSOLE__"></div>'
    '<script type="module">__JS__</script>'
)


@pytest.fixture
def built_ui(ui_dir):
    """A stand-in for the build: the six files, recognisable by content."""
    for name in ("admin", "app"):
        (ui_dir / f"{name}.html").write_text(SHELL.format(name=name))
        (ui_dir / f"{name}.css").write_text(f"/* {name} css */")
        (ui_dir / f"{name}.js").write_text(f"/* {name} js */")
    return ui_dir
```

(`tests/integration` runs the server as a subprocess, so this patch never reaches it.)

- [ ] **Step 2: failing tests** — `tests/test_ui_serving.py`:

```python
"""Serving the built UI, and serving without one (§F4.15, §F4.17)."""

import logging

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.http import admin
from kubed.selenium_flow.mcp import apps
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit


def _server():
    return SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)


def test_without_a_build_the_page_is_the_placeholder():
    res = TestClient(_server().mcp.http_app()).get("/")
    assert res.status_code == 200
    assert res.text == admin.PLACEHOLDER
    assert "<title>Selenium Flow</title>" in res.text and "<h1>Selenium Flow</h1>" in res.text


def test_without_a_build_no_app_is_offered_and_the_files_resource_stays():
    import asyncio

    uris = {str(r.uri) for r in asyncio.run(_server().mcp.list_resources())}
    assert apps.RESOURCE_URI not in uris
    assert "session://files" in uris


def test_without_a_build_startup_says_how_to_build(caplog):
    with caplog.at_level(logging.INFO):
        _server()
    assert any("npm --prefix ui run build" in r.getMessage() for r in caplog.records)


def test_with_a_build_the_page_inlines_its_bundle_and_fills_the_server_values(built_ui):
    res = TestClient(_server().mcp.http_app()).get("/")
    assert "/* admin css */" in res.text and "/* admin js */" in res.text
    assert 'data-console="/"' in res.text and 'data-mount=""' in res.text
    assert "__" not in res.text.replace("__init__", "")


def test_the_bundle_goes_in_after_the_placeholders(built_ui):
    """A placeholder-shaped string inside the bundle is never substituted."""
    (built_ui / "admin.js").write_text("const s = '__MOUNT__'")
    assert "const s = '__MOUNT__'" in admin.page("admin", MOUNT="/flow", CONSOLE="/")


def test_a_script_close_in_the_bundle_cannot_end_the_inline_script(built_ui):
    (built_ui / "admin.js").write_text("const s = '</script><b>'; const t = '</SCRIPT>'")
    out = admin.page("admin", MOUNT="", CONSOLE="/")
    assert out.count("</script>") == 1 and "</SCRIPT>" not in out


def test_ui_built_needs_all_three_files(ui_dir):
    (ui_dir / "admin.html").write_text("x")
    (ui_dir / "admin.js").write_text("x")
    assert not admin.ui_built("admin")
    (ui_dir / "admin.css").write_text("x")
    assert admin.ui_built("admin")


async def test_with_a_build_the_app_shell_is_a_ui_resource(built_ui):
    uris = {str(r.uri) for r in await _server().mcp.list_resources()}
    assert apps.RESOURCE_URI in uris


def test_the_app_csp_admits_our_own_origin_and_nothing_else():
    """The SDK is bundled now, so no CDN is declared (spec, difference 1)."""
    csp = apps.config_for("https://selenium.example.com/flow").csp
    assert csp.resource_domains == ["https://selenium.example.com"]
    assert csp.connect_domains == ["https://selenium.example.com"]
```

Run: `t tests/test_ui_serving.py` → FAIL.

- [ ] **Step 3: `admin.py`** — replace `STATIC_DIR` through `page()`:

```python
STATIC_DIR = "static"

# What the UI's URL answers when no UI was built (§F4.17): the server is whole
# without it, and says only its name.
PLACEHOLDER = (
    "<!doctype html>\n"
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>Selenium Flow</title>\n"
    "<h1>Selenium Flow</h1>\n"
)


def static_path() -> Path:
    """The built UI, beside this module.

    `npm --prefix ui run build` writes it here — gitignored, never committed
    (§F4.15) — so a checkout and an installed wheel look in the same place, and
    an install that skipped the build simply has none (§F4.17).
    """
    return Path(__file__).parent / STATIC_DIR


def ui_built(name: str) -> bool:
    """Whether surface ``name`` ("admin" or "app") has all three of its files."""
    folder = static_path()
    return all((folder / f"{name}.{ext}").is_file() for ext in ("html", "css", "js"))


def read(name: str) -> str:
    """One file from the built UI."""
    return (static_path() / name).read_text(encoding="utf-8")


def page(name: str, **substitutions: str) -> str:
    """Surface ``name``'s shell with its bundle inlined and ``__NAME__`` filled.

    Deliberately not a template engine. The placeholders are filled first and
    the bundle goes in last, so nothing inside the bundle is ever substituted;
    and a literal ``</script`` in it is escaped so it cannot end the inline
    script early.
    """
    html = read(f"{name}.html")
    for key, value in substitutions.items():
        html = html.replace(f"__{key}__", value)
    js = re.sub(r"</(script)", r"<\\/\1", read(f"{name}.js"), flags=re.IGNORECASE)
    return html.replace("__CSS__", read(f"{name}.css"), 1).replace("__JS__", js, 1)
```

Add `import re` to the module's imports. In the route (`admin_ui`):

```python
        if not ui_built("admin"):
            return HTMLResponse(PLACEHOLDER)
        return HTMLResponse(page("admin", CONSOLE=console, MOUNT=prefix))
```

Update its docstring's last paragraph: unauthenticated on purpose (the sign-in form); without a build it is the placeholder (§F4.17).

- [ ] **Step 4: `apps.py`** — delete `SDK_ORIGIN` and its comment; `_csp` becomes:

```python
def _csp(base: str) -> ResourceCSP:
    """What the sandboxed iframe may reach: our own files. The SDK is bundled
    into the app (it used to come from a CDN, at a URL that 404s)."""
    own = [d for d in (origin(base),) if d]
    return ResourceCSP(resource_domains=own, connect_domains=own)
```

Add:

```python
def available() -> bool:
    """Whether the app shell was built. Without it there is nothing to render,
    so the tools behave as with APPS_ENABLED=false and still return their data
    (§F4.17)."""
    return admin.ui_built("app")
```

and `component_app` returns `admin.page("app")`.

- [ ] **Step 5: `server.py`** — where `apps_enabled` is first used (before `app_config = …`, around line 146):

```python
        if not admin.ui_built("admin"):
            log.info("The admin UI is not built, so its URL shows a placeholder: run `npm --prefix ui run build`.")
        # An app is a view of the built shell; without the shell there is none.
        apps_enabled = apps_enabled and apps.available()
```

Import `admin` from `.http` if the module does not already, and use the module's existing `log` (add `log = logging.getLogger(__name__)` if absent). The one log line covers both surfaces — don't log twice.

Run: `t tests/test_ui_serving.py` → PASS.

- [ ] **Step 6: `pyproject.toml`** — remove `"kubed.selenium_flow.http.static",` from `packages`; remove the package-dir line `"kubed.selenium_flow.http.static" = "static"` and the comment paragraph "The web UI is mapped the same way…"; in package-data replace the static line with:

```toml
# The built UI (§F4.15): npm writes it beside admin.py, gitignored. A glob, not
# a mapped package, so an install that skipped the build succeeds without it
# (§F4.17) — a mapped directory that does not exist fails the whole install.
"kubed.selenium_flow.http" = ["static/*"]
```

- [ ] **Step 7: the old tests.** Delete `tests/test_admin_ui.py`. In `tests/test_files_and_admin.py` delete exactly the tests the traceability table marks as moved or retired (each already has its new home), and replace `test_the_app_csp_admits_our_own_origin_and_the_sdk` (now in `test_ui_serving.py`). `test_the_app_shell_is_a_ui_resource` and `test_the_file_tools_return_for_a_client_that_renders_apps` need the build: add the `built_ui` fixture to their signatures (the server fixture must be created *after* it — take `built_ui` as the first parameter and build the server inside the test with `SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)` if the shared fixture was created too early). In `tests/test_packaging.py` remove the `static` row from `DATA_DIRS` and add:

```python
def test_the_built_ui_ships_by_glob_so_an_unbuilt_install_still_works():
    """§F4.15/§F4.17: collected when it exists, and never a mapped package —
    a mapped directory that is missing fails `pip install`."""
    data = tomllib.loads(PYPROJECT.read_text())
    setuptools = data["tool"]["setuptools"]
    assert "kubed.selenium_flow.http.static" not in setuptools["packages"]
    assert "kubed.selenium_flow.http.static" not in setuptools["package-dir"]
    assert setuptools["package-data"]["kubed.selenium_flow.http"] == ["static/*"]
    assert "kubed/selenium_flow/http/static/" in (REPO / ".gitignore").read_text()
```

- [ ] **Step 8: delete `static/`.** `git rm -r static`. Then `grep -rn "static/\|components.js\|app.html\|admin.html" --include=*.py --include=*.toml --include=*.md . | grep -v node_modules | grep -v "^./saga\|^./docs/superpowers"` and fix every live reference (not the saga or past plans).

- [ ] **Step 9: prove pip does not care** (in a scratch copy, so the checkout is untouched):

```bash
S=/tmp/claude-1000/-projects-cluster/2f3f76f1-c974-4758-ae06-6f18b8dd88b9/scratchpad/cutover
rm -rf $S && git worktree add -q $S HEAD && cd $S
PYTHONPATH=$SFENV python3 -m build -q -o dist . && python3 -m zipfile -l dist/*.whl | grep -c "http/static/" ; echo "(0 expected: unbuilt)"
npm --prefix ui ci --silent && npm --prefix ui run build --silent
rm -rf dist && PYTHONPATH=$SFENV python3 -m build -q -o dist . && python3 -m zipfile -l dist/*.whl | grep "http/static/"; tar tzf dist/*.tar.gz | grep -c "http/static/"
git status --short; cd - && git worktree remove --force $S
```

Expected: the unbuilt build succeeds with 0 static files; the built one lists all six in the wheel and in the sdist; `git status` is clean both times. (Install `build` into `$SFENV` with `pip install --target $SFENV build` if missing.)

- [ ] **Step 10: Run everything and commit**

Run: `t && lint && npm --prefix ui test`
Expected: Python green (count = baseline − deleted grep tests + new serving tests; state both numbers), ruff clean.

```bash
git add -A
git commit -m "Serve the Svelte build, make the UI optional, and remove static/

Unbuilt, the UI URL is a 'Selenium Flow' page and no MCP App is offered;
built, page() inlines each surface. The page-grepping tests go, each mapped in
the traceability table; the app CSP no longer names a CDN."
```

---

### Task 11: The build in the image and in CI

**Files:**
- Modify: `Dockerfile`, `.github/workflows/package.yml`, `.github/workflows/integration.yml`, `.github/workflows/copilot-setup-steps.yml`, `.github/workflows/image.yml` (path filters), `.github/dependabot.yml`
- Create: `.github/workflows/ui.yml`
- Modify: `tests/test_packaging.py` (the Dockerfile and path-filter tests)

**Interfaces:**
- Consumes: `npm --prefix ui ci`, `npm --prefix ui run build`, `lint`, `check`, `test`, `size` (Task 2)

- [ ] **Step 1: failing packaging tests** — add to `tests/test_packaging.py`:

```python
def test_the_ui_is_built_in_its_own_stage_and_reaches_the_package():
    text = DOCKERFILE.read_text()
    assert re.search(r"^FROM node:24-slim AS ui$", text, re.M)
    assert "npm ci" in text and "npm run build" in text
    assert re.search(r"^COPY --from=ui \S+ kubed/selenium_flow/http/static$", text, re.M)
    # before the project install, so the wheel the venv gets has the UI in it
    assert text.index("COPY --from=ui") < text.index("pip install --no-cache-dir .[redis]")


def test_node_never_reaches_the_runner():
    runner = DOCKERFILE.read_text().split("AS runner", 1)[1]
    assert "node" not in runner.lower() and "npm" not in runner


def test_a_ui_change_rebuilds_the_image():
    text = (REPO / ".github/workflows/image.yml").read_text()
    assert "'ui/**'" in text or '"ui/**"' in text or "- ui/**" in text
```

(Reuse the module's existing `DOCKERFILE`/`REPO` constants and `re` import; add them if absent.) Run: `t tests/test_packaging.py` → the three FAIL.

- [ ] **Step 2: `Dockerfile`** — a new first stage after `ARG PY_VERSION=3.14`, with a comment that earns its lines:

```dockerfile
# ---- ui: the admin UI, built by npm (§F4.15). Its own stage so Node never
#      reaches the runner; the lockfile alone first, so a UI edit does not
#      reinstall the toolchain.
FROM node:24-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build
```

In the builder, between `COPY . .` and the project install:

```dockerfile
# The built UI into the package, where pip collects it (§F4.17: optional for a
# source install; the image always has it).
COPY --from=ui /kubed/selenium_flow/http/static kubed/selenium_flow/http/static
```

(Vite's `outDir` is `../kubed/selenium_flow/http/static` relative to `/ui`, i.e. `/kubed/selenium_flow/http/static`.) Keep hadolint happy: `quality.yml` runs it — run `docker run --rm -i hadolint/hadolint < Dockerfile` if Docker is available, otherwise rely on CI.

- [ ] **Step 3: `.github/workflows/ui.yml`** — the required gate. Copy the header comment style, permissions and pin style (`actions/checkout@v7`, SHA or major as the repo does) from `test.yml`:

```yaml
name: 🎨 UI
# The Svelte admin UI and MCP App: lint, types, tests, the build and its size.
# No `paths` filter on pull_request, deliberately: this job is REQUIRED.
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
  workflow_call: {}

permissions:
  contents: read

jobs:
  ui:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v7
      with:
        persist-credentials: false
    - uses: actions/setup-node@v5
      with:
        node-version: 24
        cache: npm
        cache-dependency-path: ui/package-lock.json
    - run: npm --prefix ui ci --no-audit --no-fund
    - run: npm --prefix ui run lint
    - run: npm --prefix ui run check
    - run: npm --prefix ui test
    - run: npm --prefix ui run build
    - run: npm --prefix ui run size
```

Match `actions/setup-node`'s major to the newest the repo's Dependabot would accept (check `gh api repos/actions/setup-node/releases/latest --jq .tag_name`); `zizmor` in `quality.yml` lints this file — keep `persist-credentials: false`.

- [ ] **Step 4: the other workflows.** `package.yml`: before `pip install .[build]`, add the same `setup-node` step and `npm --prefix ui ci --no-audit --no-fund && npm --prefix ui run build`; after the wheel is built, add a step that fails unless `python -m zipfile -l dist/*.whl | grep -q 'http/static/admin.js'`. `integration.yml`: the same Node setup and build before `pip install .[test,redis]` (the flows drive the real page). `copilot-setup-steps.yml`: Node setup and `npm --prefix ui ci` so the agent can build. `image.yml`: add `ui/**` to its `paths` filter beside the existing entries (the image builds the UI). `test.yml` and `quality.yml` stay as they are — pure Python, exercising the unbuilt path.

- [ ] **Step 5: Dependabot** — append to `.github/dependabot.yml`, following the file's house rules:

```yaml
# npm: the admin UI's toolchain and the ext-apps SDK, in ui/.
- package-ecosystem: npm
  directory: /ui
  schedule:
    interval: weekly
    day: monday
  open-pull-requests-limit: 5
  labels:
  - dependencies
  - javascript
  - no changelog
  commit-message:
    prefix: ui(deps)
  cooldown:
    default-days: 7
  groups:
    ui-minor-and-patch:
      update-types:
      - minor
      - patch
```

Copy the exact `cooldown`/`groups` keys the existing entries use if they differ from the above. Create the `javascript` label if it does not exist (`gh label create javascript --repo kubed-io/selenium-flow --color f1e05a`) — Dependabot silently drops labels that do not exist.

- [ ] **Step 6: Run and commit**

Run: `t tests/test_packaging.py && lint` → pass.

```bash
git add Dockerfile .github tests/test_packaging.py
git commit -m "Build the UI in its own image stage and in CI; a required UI gate; Dependabot for npm"
```

---

### Task 12: The styling polish

Spec *Deliberate difference 3*: motion only — no layout, text or behaviour change. Every transition ≤ 150 ms, `|local` where the element can appear on first paint, and zero under `prefers-reduced-motion`.

**Files:**
- Create: `ui/src/motion.ts`
- Modify: `ui/src/admin/Modal.svelte`, `ui/src/lib/Lightbox.svelte`, `ui/src/admin/Section.svelte`, `ui/src/lib/FileGrid.svelte`, `ui/src/admin/SessionsView.svelte`
- Test: `ui/src/motion.test.ts`

**Interfaces:**
- Produces: `motion.ts`: `ms(duration: number): number` — 0 when reduced motion is preferred

- [ ] **Step 1: failing test** (`ui/src/motion.test.ts`)

```ts
import { expect, test, vi } from 'vitest'

test('durations drop to zero under prefers-reduced-motion', async () => {
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q === '(prefers-reduced-motion: reduce)' }))
  vi.resetModules()
  const { ms } = await import('./motion')
  expect(ms(150)).toBe(0)
})

test('and are capped at 150 ms otherwise', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
  vi.resetModules()
  const { ms } = await import('./motion')
  expect(ms(120)).toBe(120)
  expect(ms(400)).toBe(150)
})
```

- [ ] **Step 2: `motion.ts`**

```ts
/* Polish only (spec, difference 3): never longer than 150 ms, and none at all
   for someone who asked their system for less motion. */
const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

export const ms = (duration: number): number => (reduce ? 0 : Math.min(duration, 150))
```

- [ ] **Step 3: apply it**

- `Modal.svelte`: `import { fade, scale } from 'svelte/transition'` and `import { ms } from '../motion'`; add `transition:fade={{ duration: ms(120) }}` to `.modal` and `in:scale={{ start: 0.97, duration: ms(150) }}` to `.sheet`.
- `Lightbox.svelte`: `transition:fade={{ duration: ms(120) }}` on `.lightbox`; wrap the body's content in `{#key f.name}<div class="frame" in:fade={{ duration: ms(150) }}>…</div>{/key}` with a scoped `<style>.frame { display: contents; }</style>` so layout is untouched.
- `Section.svelte`: keep `▾` always; add a scoped rule `.caret { transition: transform 150ms; } [aria-expanded='false'] .caret { transform: rotate(-90deg); } @media (prefers-reduced-motion: reduce) { .caret { transition: none; } }` — scoped styles here target only the caret span, which no flow selects. Wrap the body content in `{#if open}<div transition:slide|local={{ duration: ms(150) }}>…</div>{/if}` inside the `.body` element, keeping `data-open` on the section (the global `display:none` rule then never has anything to hide).
- `FileGrid.svelte`: `import { flip } from 'svelte/animate'`, `import { fade } from 'svelte/transition'`; on the `<div class="file">` add `animate:flip={{ duration: ms(150) }} out:fade|local={{ duration: ms(120) }}`. Do NOT add a `class` or scoped style to `.file` or anything inside the tile.
- `SessionsView.svelte`: a pulsing dot inside the badge only while live — `{#if live.badge.cls === 'live'}<span class="dot" aria-hidden="true"></span>{/if}` before the text, with scoped CSS: `.dot { display:inline-block; width:6px; height:6px; border-radius:50%; background: currentColor; margin-right:5px; vertical-align:middle; animation: pulse 2s ease-in-out infinite; } @keyframes pulse { 50% { opacity: .35; } } @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }`. The badge's text must stay exactly `live` (tests read it) — assert `#live` text is `live` with the dot present.

- [ ] **Step 4: Run and commit**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint && npm --prefix ui run build && npm --prefix ui run size` → pass, clean, under budget. Re-run the frozen-selector test from Task 4 (the tile name's class is still exactly `name`).

```bash
git add ui/src
git commit -m "Polish: fades for the modal and lightbox, a sliding accordion, gliding tiles, a live pulse"
```

---

### Task 13: Docs, saga, and the whole-branch check

**Files:**
- Modify: `AGENTS.md` (contributor notes: the UI is in `ui/`, `npm --prefix ui ci && npm --prefix ui run build`, optional for a source install, `npm --prefix ui run dev` for watch), `CHANGELOG.md` (`[Unreleased]`), `README.md` only if it tells a user how to run from source (one line: build the UI or get the placeholder), `wiki/` pages that describe `static/` (grep first; the wiki is a submodule — commit and push it too, memory `push-the-wiki-submodule`), `saga/Chapter_4_The_Hangar.md` (§F4.18 "What building it decided", Part IV status line)
- Modify: `docs/superpowers/plans/2026-09-25-svelte-admin-ui-traceability.md` (tick every row)

- [ ] **Step 1: CHANGELOG** under `[Unreleased]`:

```markdown
- The admin UI and MCP App are Svelte components built by npm into the package; `static/` is gone.
- The MCP App starts: its SDK is bundled and pinned instead of loaded from a CDN URL that 404'd.
- The UI is optional: a source install without `npm --prefix ui run build` serves a "Selenium Flow" page and offers no MCP App.
```

- [ ] **Step 2: saga §F4.18** — what building decided that the spec had not (each ruling taken during Tasks 2–12, as it was made), and the Part IV status → "BUILT, in PR #N".

- [ ] **Step 3: tick the traceability table** — every row's new test exists and passes (`npm --prefix ui test -- -t "<title>"` for a spot check of five).

- [ ] **Step 4: whole-branch verification**

```bash
t && lint
npm --prefix ui ci && npm --prefix ui run lint && npm --prefix ui run check && npm --prefix ui test && npm --prefix ui run build && npm --prefix ui run size
git status --short   # nothing generated is tracked
git ls-files | grep -E "http/static/|node_modules" && echo "GENERATED FILE TRACKED — fix" || echo "clean"
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Docs, changelog and saga for the Svelte admin UI"
```

---

## After the tasks (controller)

1. **Whole-branch review** (superpowers:requesting-code-review) against the spec and this plan.
2. **Ask Dr K** before pushing the branch or opening the PR (one PR per session). Then push over https and open it.
3. **Branch image:** `gh workflow run image.yml --ref svelte-ui`; set `newTag: svelte-ui` in `/projects/cluster/apps/selenium/components/mcp/kustomization.yaml` (uncommitted, as with #41 — currently still `file-sections`); `kubectl -n flow rollout restart deploy/selenium-flow`; confirm with `curl https://selenium.kellyferrone.com/flow/info`.
4. **Self-test** with selenium-flow's own tools: the three integration flows via `run_flow`, then the "after" pictures — the same views, sizes and names as Task 1 with `after-` — and the dark pair. Show Dr K each before/after side by side (a small artifact page is fine), and walk the inventory by hand for anything a picture cannot show (keys, live updates, End browser).
5. **CI green, Copilot threads answered and resolved with `gh`.**
6. After merge: move the cluster pin back to `main` and apply (warn first: applying `apps/selenium` restarts live Grid browsers).
