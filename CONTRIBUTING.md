# Contributing

## Getting set up

```bash
git clone --recurse-submodules git@github.com:kubed-io/selenium-flow.git
pip install -e ".[test]"
ruff check kubed scripts
pytest
```

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
| `Test (3.14)` | `ruff check kubed scripts` and the full pytest suite |
| `Package` | builds the sdist + wheel, `twine check --strict`, then installs the wheel clean and imports it |
| `CodeQL` / `Dependency Audit` / `Workflow Audit` / `Dockerfile Lint` / `OpenAPI Spec` | `quality.yml` — code scanning, `pip-audit`, `zizmor`, `hadolint`, and a Redocly lint of the generated spec |
| Copilot review | reviews against `.github/copilot-instructions.md` |

The image is **not** built on a pull request: a multi-arch build is ~9 minutes
for a signal the merge build gives anyway. `package.yml` builds and installs the
wheel instead, which is the part that is ours.

When a quality gate is wrong rather than you, the fix is a rule exclusion **with
its reason** in `.github/zizmor.yml` or `.hadolint.yaml` — never a bare ignore.

## Before you push

`AGENTS.md` carries the design rules that are easy to break by accident — why
the browser must never answer a dialog, why waits re-raise with a message, why
session_id is shaped per request. Read it before changing any of those.
