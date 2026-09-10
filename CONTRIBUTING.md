# Contributing

## Getting set up

```bash
git clone --recurse-submodules git@github.com:kubed-io/selenium-flow.git
pip install -e ".[test]"
ruff check .
pytest
```

`ruff check .` covers the whole checkout, tests included — the rule set and the
two documented exceptions live in `[tool.ruff.lint]` in `pyproject.toml`, so
there is no second list of paths to keep in sync.

Python 3.14 is the baseline — it is what the image runs and the only interpreter
a pull request is tested on. The package supports 3.10 and up, and CI sweeps the
whole range on main and on every release, so a 3.11+ feature is a build break on
the oldest leg rather than a style question. See
[`.github/instructions/python.instructions.md`](.github/instructions/python.instructions.md)
for the ones that actually come up.

The submodule is the GitHub wiki, checked out at `wiki/`. An existing clone
picks it up with `git submodule update --init`. Nothing in the build or the
tests needs it, so a clone without it works fine — you just have no wiki.

The tests wire a server against an unroutable Grid address and drive both
surfaces through the real ASGI app, so they need no browser and no network. A
test that reaches the Grid is an integration test and is marked as one.

## Where things live

| Path | Holds |
|---|---|
| `kubed/selenium_flow/actions.py` | what the server can do, as plain functions — the single source of truth |
| `kubed/selenium_flow/tools.py` | those actions as MCP tools |
| `kubed/selenium_flow/routes.py` | the same actions as HTTP endpoints |
| `kubed/selenium_flow/sessions.py` | who is calling, and which browser that resolves to |
| `kubed/selenium_flow/settings.py` | the env / client / explicit settings cascade |
| `skills/selenium-flow/` | the embedded Agent Skill, mapped into the package at build time |
| `static/` | the admin UI and the MCP app components, mapped in the same way |
| `wiki/` | the GitHub wiki, as a submodule — depth the README has no room for |
| `wiki/notes/` | hand-written prose folded into the generated wiki pages |

Adding a capability means adding one function to `actions.py` and registering it
on both surfaces. A test asserts the two sets match, so a tool without an
endpoint fails the build.

## The wiki is generated too

`scripts/generate_wiki.py` renders one wiki page per action from the spec it builds
in-process,
so the fourteen pages share one shape and cannot describe a server that never
shipped. `.github/workflows/wiki.yml` regenerates and pushes on every change to
`kubed/`, `wiki/notes/` or the generator. A pull request generates but
never pushes, and `workflow_call` leaves the decision to the caller — the same
shape as `image.yml`.

Staleness is caught by `tests/test_wiki.py`, not by that workflow: `test.yml`
checks out the submodule so those tests run, and a pull request that forgot to
regenerate fails the suite where a contributor is already looking.

Prose a schema cannot carry goes in `wiki/notes/<tool>.notes.md` and is folded into
that tool's page. It lives in this repo rather than the wiki on purpose: a wiki
page is addressed by basename regardless of directory, so `wiki/notes/foo.md`
and `wiki/foo.md` both answer to `/wiki/foo`, and GitHub serves the fragment.
`tests/test_wiki.py` fails on any such shadowing.

## The OpenAPI spec is generated

Request schemas come from the MCP tools themselves — the same objects FastMCP
publishes to agents — so the two contracts are the same schema rather than two
descriptions that happen to agree.

`openapi.yaml` is a **build artifact and is gitignored**. The server builds the
same document per request at `GET /openapi.yaml`, and the tests and the wiki
generator build it in-process, so nothing needs the file. Write a copy when you
want one to read, lint or publish:

```bash
python scripts/generate_openapi.py
```

Changes go in `openapi.py`, never in the generated file. Response shapes are the
one hand-maintained half there: the actions return plain dicts, so there is
nothing to introspect. A test asserts every endpoint has one.

## The embedded skill

`skills/` sits at the repo root and is mapped into the package by
`[tool.setuptools.package-dir]`, so it reads as documentation but installs
inside the wheel. **The directory name is the skill name** — FastMCP's
`SkillProvider` takes it from the folder, not the frontmatter.

`SKILL.md` is an index over `references/`, not the manual. Tests enforce that:
every path it names must exist, and every file on disk must be linked from it —
an unlinked reference is never lazily loaded, so it may as well not ship.

## The README is also the Docker Hub description

Docker Hub truncates it at 25,000 bytes, and the publish workflow pushes
`README.md` as-is — so going over ships a manual that stops mid-sentence, warned
about only in a build log. `tests/test_readme.py` fails before that can happen.

If it gets tight, the fix is not to compress prose: move contributor material
here and design rationale to `AGENTS.md`. The README advertises; those two
explain.

## What CI will say about it

A pull request runs these, and all of them are required to merge:

| Check | What it is |
|---|---|
| `PR Tasks` | assigns you, and fails if `CHANGELOG.md` has no new `[Unreleased]` entry — that section becomes the release notes. The `no changelog` label is the escape hatch |
| `Test (3.14)` | `ruff check .` and the full pytest suite |
| `Package` | builds the sdist + wheel, `twine check --strict`, then installs the wheel clean and imports it |
| `CodeQL` / `Dependency Audit` / `Workflow Audit` / `Dockerfile Lint` / `OpenAPI Spec` | `quality.yml` — code scanning, `pip-audit`, `zizmor`, `hadolint`, and a Redocly lint of the generated spec |
| Copilot review | reviews against `.github/copilot-instructions.md` |

The image is **not** built on a pull request: a multi-arch build is ~9 minutes
for a signal the merge build gives anyway. `package.yml` builds and installs the
wheel instead, which is the part that is ours.

When a quality gate is wrong rather than you, the fix is a rule exclusion **with
its reason** in `.github/zizmor.yml` or `.hadolint.yaml` — never a bare ignore.

## The changelog

`CHANGELOG.md` is not a work log. **It is the release notes**, verbatim:
`publish.yml` hands the `[Unreleased]` section to `duplocloud/version-bump`,
which stamps a version heading on it and puts it straight into the GitHub
Release. Whatever you write is what a stranger reads.

So write for that stranger:

- **One short line per entry**, saying what someone can now do. Not a paragraph,
  not the reasoning, not what it replaced. If a line needs a "because", the
  because belongs in `AGENTS.md` or the PR.
- **Lead with the capability**, bolded, and stop when the sentence is answered.
- **Internal work usually earns no line at all** — CI, refactors, dependency
  bumps, tests, types, docs. When it genuinely changes something a user would
  notice, it gets one terse line under `Changed`; when it does not, the PR takes
  the **`no changelog`** label and writes nothing. An entry nobody outside this
  repo can act on is worse than no entry, because it dilutes the ones that matter.
- **Only `Added` / `Changed` / `Fixed` / `Removed`**, in that order, and only the
  ones you actually have. Only a **BREAKING:** entry may run long.

Two rules about *where* you write:

- **Only ever edit `[Unreleased]`.** Every section below it carries a version
  number and is immutable — those notes shipped, and rewording them rewrites
  history somebody has already read.
- **Never add a version heading or bump a version.** Versions come from git tags
  via `setuptools_scm`, and the release flow owns them.

`pr.yml` fails a pull request whose diff does not touch `[Unreleased]`. That
check is a reminder, not the standard — a diff that adds a line of noise passes
it just as well as a good one.

## Writing a test

`tests/conftest.py` owns the shared doubles — `RecordingActions`, `FakeGrid`,
`manager()`, and the `named_caller` / `stateless_caller` fixtures that make the
ambient request look like one kind of client or the other. Import them from
there rather than writing another copy; two copies of `RecordingActions` had
already drifted apart, and which behaviours a test could assert depended on
which file it happened to live in.

**Test a behaviour once, at the layer that owns it.** `SessionManager.end_browser`
decides what happens to a record when a browser ends, so that belongs in
`test_sessions.py`. The admin route's job is the status code, the response shape
and the auth check — so `test_files_and_admin.py` asserts those and does not
re-assert the manager's contract through HTTP. When both files test the same
sentence, the second one is not extra safety: it is a second thing to update
when the behaviour changes, and it will be the one that gets missed.

## Before you push

`AGENTS.md` carries the design rules that are easy to break by accident — why
the browser must never answer a dialog, why waits re-raise with a message, why
session_id is shaped per request. Read it before changing any of those.
