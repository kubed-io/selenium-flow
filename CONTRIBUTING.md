# Contributing

How to set up the repo, run and test the server, and ship a change. What the server does is in
[the spec](https://github.com/kubed-io/selenium-flow/wiki/Specification); how to use it is on the
[wiki](https://github.com/kubed-io/selenium-flow/wiki).

## Set up

```bash
git clone --recurse-submodules git@github.com:kubed-io/selenium-flow.git
cd selenium-flow
pip install -e ".[test]"
```

The package supports Python 3.11 to 3.14, and the image runs 3.14. A 3.12+ syntax or stdlib feature
breaks the 3.11 leg of CI.

`docs/` is the GitHub wiki, checked out as a submodule; an existing clone gets it with
`git submodule update --init`. The build doesn't need it, but `tests/test_wiki.py` does.

The admin UI and the MCP App are Svelte, in `ui/`, and optional. Without a build the server runs
every tool, serves a placeholder page titled "Selenium Flow" and offers no MCP App. To build it:

```bash
npm --prefix ui ci && npm --prefix ui run build
```

The output lands in `kubed/selenium_flow/http/static/`, gitignored. `npm --prefix ui run dev`
rebuilds on every change.

## Run it

```bash
docker compose up --build
```

That starts the server on port 8000 and a standalone Chromium Grid on 4444, with auth off. Saved
flows, kept files and recordings are commented out in `docker-compose.yaml`; its comments say what
to uncomment.

Against a Grid you already have, without containers:

```bash
GRID_URL=http://localhost:4444 AUTH_TOKEN=dev python -m kubed.selenium_flow
```

Every setting has a config file key, an environment variable and a flag; the wiki's
[Configuration](https://github.com/kubed-io/selenium-flow/wiki/Configuration) page lists them all,
and [config.md](docs/superpowers/specs/config.md) holds the rules.

## Commands

| Command | Does |
|---|---|
| `ruff check .` | Lints the whole checkout, tests included. Rules in `[tool.ruff.lint]` |
| `pytest` | The unit suite: no browser, no network, `tests/bench` left out |
| `pytest tests/bench` | The benchmarks, after `pip install -e ".[test,bench]"` |
| `GRID_URL=... ADMIN_ORIGIN=... pytest tests/integration` | The integration suite (see Tests) |
| `GOLDEN_UPDATE=1 pytest` | Rewrites the goldens under `tests/golden/` |
| `python scripts/generate_openapi.py` | Writes `openapi.yaml` |
| `python scripts/generate_wiki.py` | Regenerates the wiki's tool pages and Configuration page in `docs/` |
| `npm --prefix ui run build` | Builds the admin UI, the MCP App and the live view |
| `npm --prefix ui test`, `run check`, `run lint`, `run size` | The UI's tests, types, lint and bundle sizes |

## Where things live

The spec's [architecture](https://github.com/kubed-io/selenium-flow/wiki/Specification#architecture) maps the packages under
`kubed/selenium_flow/`. Outside it:

| Path | Holds |
|---|---|
| `js/` | The JavaScript run in the page, mapped into the package as `core/js` |
| `plugin/` | The skill, prompts and agents the server serves, mapped into the package as `mcp/plugin` |
| `ui/` | The admin UI, the MCP App and the live view, in Svelte |
| `scripts/` | The OpenAPI and wiki generators, the PR report, the image's requirements |
| `tests/fakes.py`, `tests/conftest.py` | The shared test doubles and fixtures |
| `tests/golden/` | The published shapes, pinned as JSON |
| `tests/js/` | The harness that runs `js/` under jsdom |
| `tests/integration/` | The real server and a real browser, run by flow files |
| `docs/` | The wiki submodule: user pages, tool notes, the spec and the plans |

## Writing code here

These rules are how the code stays one design. Each has a test or a spec requirement behind it.

### One action, two surfaces

- Every browser action is a tool and an HTTP route from one row of `CAPABILITIES` in
  `core/capabilities.py` ([surfaces.md](docs/superpowers/specs/surfaces.md), SURF-2 and SURF-39).
  A new action is a row, a method on `core.actions.Actions` and a declaration in `mcp/tools.py`.
  `tests/test_surfaces.py` fails when one surface lacks it: add the missing half.
- Behaviour lives in `core/`, `workspace/` and the packages under them. `mcp/tools.py` and
  `routes.py` only declare and answer.
- The surfaces differ only where SURF-43 says. Operational routes (`/health`, `/ready`,
  `/openapi.yaml`) are HTTP only and are not capabilities.
- `tests/test_boundaries.py` holds the layers: `core/`, `workspace/`, `flows/`, `site_data/`,
  `monitor/` and `recordings/` import no protocol layer, and the plain modules import no Selenium.
  A rule two layers need goes in a small plain module (`names.py`, `urls.py`, `binding.py`,
  `faults.py`, `core/coerce.py`).

### Tool declarations

- A signature in `mcp/tools.py` is the tool's JSON schema. Annotate every parameter
  (`str | None = None`, never `str = None`), prefer `Literal[...]` for a closed set, and never a
  bare `dict` or untyped `**kwargs`.
- A docstring there is the description a model reads, and the wiki page's text. Write it for a
  model choosing a tool. The declarations are written out by hand on purpose: a decorator that
  forwarded arguments would erase the signature.
- `@mcp.tool` returns the plain function, so a computed description goes in `description=`
  (`press_key` interpolates its key list that way).
- Every tool declares honest annotations ([SURF-7](docs/superpowers/specs/surfaces.md)). A tool
  with none reads as destructive. `destructive_hint` is true wherever the page may interpret what
  the tool sends, since a click can place an order.
- Coerce at the boundary. Arguments arrive from hand-written JSON, form encoders and models, often
  as strings: use `as_bool` (`bool("false")` is `True`) and `as_int` (`int("")` raises).

### Changing an argument

A client caches tool schemas and ignores `notifications/tools/list_changed`, so a session live
across a rollout keeps the old schema. Never change an argument's shape in one release: accept
both for a release and say which is going, or give the new shape a new name. Adding an optional
argument is safe; renaming, retyping or removing one is not.

### Failures

- `errors.status_for` alone decides what a failure means
  ([SURF-41](docs/superpowers/specs/surfaces.md)). Anything unrecognised stays 500, because
  "the caller's fault" tells a client to stop retrying something that may be ours. A new status
  also goes in the `responses` block in `spec/`.
- Every wait goes through `browser._waited`, which names what it waited for, the timeout and the
  page it was on. Selenium's own `TimeoutException` says only `Message:`. No bare `WebDriverWait`.
- An action reports the page through `browser.page_state`, which reports an open dialog instead of
  raising. An action that navigates by any means other than a click or `driver.get` waits with
  `browser.settled`.
- Never swallow an exception to return an empty result: the caller is usually a model, and an
  empty answer reads as a real one.

### Auth, secrets and logs

- `http/auth.py` is the one place a credential is checked, with `hmac.compare_digest`; signed
  links are `http/links.py`. A new door goes through them.
- A secret's value never reaches a log line, a record, a bus event or an exception message. Log a
  failure near one by `type(exc).__name__`. No log line formats a Grid id
  (`test_no_log_line_formats_a_grid_id`), and monitor events carry only scalars
  (`test_events_carry_no_values`).
- `tests/test_scrub_coverage.py` puts every spelling of a secret into every answer. A new tool,
  resource or non-admin route is covered there, or goes on its exemption list with a reason. A new
  way out goes through one of the choke points in
  [secrets-and-scope.md](docs/superpowers/specs/secrets-and-scope.md).
- The admin API is unscrubbed, so widening the admin door widens what an agent can read.

### Workspaces and state

- Never identify a caller by `Context.session_id`: when FastMCP finds no session it returns a fresh
  `uuid4()` instead of failing, so every call looks new and leaks a browser. Callers are read from
  the request into a `Caller` (`mcp/clients.py`). Any new way to name a workspace must be one the
  client controls.
- No tool or resource lists other workspaces (`test_the_mcp_surface_never_lists_other_workspaces`).
  The cross-workspace view is the admin API's.
- Every record write goes through `store.update(key, fn)`. `fn` only computes and may run twice;
  do slow work outside it, and return what the write found through `store.change`.
- Nothing runs a cleanup loop. The Grid reaps idle browsers and the store expires records. A
  background task waits for something owed and ends when nothing is.
- The server runs as one replica. The MCP transport sessions, the per-session lock, the monitor,
  capture buffers, live tunnels, control and Docket's tasks are process memory.
- Everything an agent reads is a resource named by its URI, and every text names it that way. A
  client that cannot read resources gets `list_resources` and `read_resource`, never a tool per
  resource. A new resource gets its row in `mcp/show.py` and its view in the same change
  (`tests/test_show_inventory.py`).
- `prefix` in a registrar means the mount (`ROUTE_PREFIX`); each tree is fixed beneath it.

### The browser

- Plain W3C WebDriver, no CDP: CDP is Chrome-only and its Selenium API is deprecated. WebDriver
  BiDi goes through `bidi/` only, and `websockets` is imported in `bidi/connection.py` alone. A
  capability that needs CDP would be a separate tool, so the portable path keeps working.
- `ReattachDriver` skips `start_session` to bind to a browser it did not open, so `driver.caps` is
  empty and anything reading capabilities raises `KeyError: 'browserName'`.

### Config

- `config.py` is the schema. A setting's path is its name: `redis.host` is `REDIS_HOST` and
  `--redis-host` ([config.md](docs/superpowers/specs/config.md), CFG-4 for the naming limits).
- `os.environ` is read in `config.py` and `secrets.py` only
  (`test_the_environment_is_read_in_two_modules_only`). Everything else takes `Settings`.
- Defaults are the library's, never one deployment's: `REDIS_DB` is 0, and `REDIS_PREFIX` keeps a
  shared database safe.

### Private APIs we lean on

| What | Guarded by |
|---|---|
| `_Environment` overrides pydantic-settings' private `_load_env_vars` | `tests/test_config_load.py` |
| The live socket mounts through FastMCP's `_additional_http_routes` | `tests/test_admin_live.py` |

### The plugin

`plugin/` and `js/` are mapped into the package by `[tool.setuptools.package-dir]`. A file there
ships only if `[tool.setuptools.package-data]` matches it; the patterns spell out
`.claude-plugin/*`, because setuptools' glob skips dot-folders, and
`test_every_plugin_file_is_matched_by_package_data` checks every file. The skill's directory name
is its name, and `SKILL.md` is an index: every `references/` file it names exists and every file
is linked from it ([plugin.md](docs/superpowers/specs/plugin.md)).

### Python style

- `from __future__ import annotations` at the top of every new module, so `X | Y` works in
  annotations on 3.11.
- `pathlib` over `os.path`, f-strings over `%` and `.format`.
- A module docstring says why the module exists.

## Tests

The unit suite wires a server against an unroutable Grid address and drives both surfaces through
the real ASGI app. A test that reaches a Grid is an integration test and is marked
`@pytest.mark.integration`. `asyncio_mode = "auto"`, so an `async def test_` needs no decorator.

- **Use the shared doubles.** `FakeGrid`, `ScriptedDriver`, the fake clock, Redis and BiDi are in
  `tests/fakes.py`; `tests/conftest.py` re-exports them beside `RecordingActions`, `manager()` and
  the `named_caller` / `unnamed_caller` fixtures. Don't write another copy.
- **A published shape is a golden.** The tool list in both modes, the OpenAPI document, the error
  table, the admin routes and a run's report are compared with `tests/golden/`. If the change was
  meant, run `GOLDEN_UPDATE=1 pytest`, read the diff, and commit it on its own.
- **The page scripts run under jsdom.** `tests/js/run.mjs` evaluates a script from `js/` the way
  `execute_script` does. It needs `node`.
- **Test a behaviour once, at the layer that owns it.** What a record does when a browser ends is
  `test_workspaces.py`'s; the admin route's test checks the status, the shape and the auth.
- **Integration tests: less is more.** A test in `tests/integration/` is a flow file in
  `tests/integration/flows/`, run against the server's own admin page by one parametrised test.
  Add one only when you can name what a user would see break that no flow catches. A detail of a
  covered behaviour is a unit test; a tool the admin UI doesn't need is not tested here; and no
  Python goes beside the flows. `test_responsive.py` is the one exception.

To run the integration suite, give it a Grid and the address where the browser reaches this
server, which is never `localhost`:

```bash
GRID_URL=http://localhost:4444 ADMIN_ORIGIN=http://host.docker.internal:8765 \
  pytest tests/integration
```

Set `REDIS_URL` and `WORKSPACE_STORE=redis` to run it as production does.

## Docs

| Document | Holds |
|---|---|
| `README.md` | What the server does, for someone deciding whether to use it. It is also the Docker Hub description, cut at 25,000 bytes (`tests/test_readme.py`) |
| `docs/superpowers/specs/` | The spec: one file per area, numbered requirements, true of the code |
| `docs/superpowers/plans/` | Dated designs and plans, each with a History header |
| Wiki, hand-written pages | How to use a feature |
| Wiki, generated pages | Each tool's reference and the Configuration page |
| `CHANGELOG.md` | The release notes |

A behaviour change edits its area file in the same pull request. A design goes in `plans/` as
`YYYY-MM-DD-<topic>-design.md`, its plan beside it.

### The wiki

`scripts/generate_wiki.py` builds the OpenAPI document in-process and renders a page per tool, plus
the Configuration page from `config.py`. Prose a schema can't carry goes in
`docs/notes/<tool>.notes.md` and is folded into the bottom of that tool's page. The suffix matters:
a wiki addresses a page by basename in any folder, so `notes/<tool>.md` would shadow the tool's
page. `tests/test_wiki.py` fails when the pages are stale, a link is broken or a basename is
shadowed.

A change in `docs/` is a commit in the wiki repository: commit there on the wiki branch named after
your branch, push it, then commit the `docs` pointer here. Never push the wiki's `master`.
`wiki.yml` regenerates on a pull request without pushing, and on every merge to main merges the
pin with `master`, regenerates, publishes and records the pointer.

### The OpenAPI document

`openapi.yaml` is generated and gitignored. The server builds the same document per request at
`GET /openapi.yaml` and `/openapi.json`, and the tests and the wiki generator build it in-process,
so nothing reads the file. Request schemas are the tool schemas; response shapes are the `response`
column of each capability row, by hand, and `test_every_action_declares_a_response_shape` checks
each has one. Info, servers, tags and the error shape are `spec/schemas.py`. Change `spec/`, never
the file. The code generates the document because FastMCP derives tool schemas from signatures.
`info.version` is a placeholder in the file, since `setuptools_scm` would make it differ per
checkout; the served one is true.

## CI

`pr.yml` runs every gate on a pull request to `main` or an `issue-*` branch. A draft runs nothing.

| Check | Workflow | What it is |
|---|---|---|
| `PR Tasks` | `pr.yml` | Assigns the author, and fails without a new `[Unreleased]` entry unless the PR has the `no changelog` label (Dependabot is exempt) |
| `Test results` | `test.yml` | `ruff check .` and `pytest` on 3.11 and 3.14; the required check, failing if either leg fails |
| `UI` | `ui.yml` | Lint, types, tests, build, the eight built files (noVNC's MPL-2.0 banner kept), no `<script` or `</style` in the bundles, and the size report |
| `Integration` | `integration.yml` | The flows against a standalone Chrome and Redis; not required yet |
| `Dependency Audit`, `Dockerfile Lint`, `OpenAPI Spec` | `lint.yml` | `pip-audit`, `hadolint`, and a Redocly lint of the generated document |
| `Package` | `package.yml` | Builds the sdist and wheel, `twine check --strict`, installs the wheel clean and imports it |
| `Bench` | `bench.yml` | Times the read paths in `tests/bench/` against main's numbers; a slowdown never fails it |
| `Checks` | `pr.yml` | Collects what failed for whoever fixes it |

`quality.yml` (CodeQL and `zizmor`) and `wiki.yml` run on their own triggers. The image is not
built on a pull request; `package.yml` builds the part that is ours. On main, `test.yml` sweeps
3.11 to 3.14 and `image.yml` pushes `kubed/selenium-flow:main` and `:latest`.

When a quality gate is wrong, add a rule exclusion with its reason in `.github/zizmor.yml` or
`.hadolint.yaml`, never a bare ignore. Actions are pinned to release tags, as recorded in
`zizmor.yml`. A required check is never path-filtered: one that never reports blocks the PR.
Workflow conventions are in
[github-workflows.instructions.md](.github/instructions/github-workflows.instructions.md), YAML
formatting in [yaml.instructions.md](.github/instructions/yaml.instructions.md).

## Releases

Actions → 🧬 Publish Version, picking `patch`, `minor` or `major` (or a pre-release). Run it once with `push=false`: it runs the
tests, computes the version and builds the image and the package without pushing, so a broken
build never strands a tag. Then run it with `push=true`:

1. `test` (the full 3.11 to 3.14 matrix) and `ui` gate everything.
2. `version` rolls `[Unreleased]` into a version heading with `duplocloud/version-bump`, commits
   and tags `vX.Y.Z` on main.
3. `image` pushes `kubed/selenium-flow:vX.Y.Z`, `package` builds the sdist and wheel, and `wiki`
   republishes the wiki, each from the tag.
4. `release` attaches the built files to the GitHub Release, with `[Unreleased]` as its notes.

Versions come from git tags through `setuptools_scm`; never write one by hand.

### The changelog

`[Unreleased]` becomes the GitHub Release's notes verbatim, so write for a stranger:

- One short line per entry, saying what someone can now do. The reasoning goes in the pull
  request.
- Lead with the capability. Bold the lead only on the entries someone would upgrade for.
- A version may open with a short preamble framing the release.
- Internal work (CI, refactors, dependency bumps, tests, types, docs) earns no line and takes the
  `no changelog` label. When a user would notice it, one terse line under `Changed`.
- Only `Added`, `Changed`, `Fixed` and `Removed`, in that order. Only a **BREAKING:** entry may
  run long.
- Edit only `[Unreleased]`. The sections below it shipped.

## Deploying

This repo ships an image and a Python package, and doesn't deploy itself. The manifests live in
the cluster repo at `apps/selenium/components/mcp`, deployed as a component of the Selenium app,
since the server does nothing without a Grid. To roll out a release, bump `newTag` there. How to
run it elsewhere is the wiki's
[Deployment](https://github.com/kubed-io/selenium-flow/wiki/Deployment) page.
