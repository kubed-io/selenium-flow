# Contributing

## Getting set up

```bash
pip install -e ".[test]"
ruff check kubed
pytest
```

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

Adding a capability means adding one function to `actions.py` and registering it
on both surfaces. A test asserts the two sets match, so a tool without an
endpoint fails the build.

## The OpenAPI spec is generated

Request schemas come from the MCP tools themselves — the same objects FastMCP
publishes to agents — so the two contracts are the same schema rather than two
descriptions that happen to agree. The document is committed so it can be
reviewed in a pull request, and a test fails if it drifts:

```bash
python scripts/generate_openapi.py   # the fix when that test fails
```

Response shapes are the one hand-maintained half, in `openapi.py`: the actions
return plain dicts, so there is nothing to introspect. A test asserts every
endpoint has one.

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

## Before you push

`AGENTS.md` carries the design rules that are easy to break by accident — why
the browser must never answer a dialog, why waits re-raise with a message, why
session_id is shaped per request. Read it before changing any of those.
