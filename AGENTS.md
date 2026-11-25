# AGENTS.md

Rules for an agent working in selenium-flow. [CONTRIBUTING.md](CONTRIBUTING.md) is the guide for
working in the repo and binds you too; this file adds only what an agent would otherwise get wrong.

## Read first

1. [`docs/superpowers/specs/Specification.md`](https://github.com/kubed-io/selenium-flow/wiki/Specification):
   what the server does, the area index, the rules for requirement ids, the glossary and the
   architecture. Before changing a behaviour, read its area file.
2. [CONTRIBUTING.md](CONTRIBUTING.md): setup, commands, tests, CI, releases, the docs and where each
   goes, and the rules for writing code here.
3. [`plugin/skills/driving-a-browser/SKILL.md`](plugin/skills/driving-a-browser/SKILL.md): how a
   model is meant to use the server; the other four skills in `plugin/skills/` are linked from it.

Use the glossary's words. A workspace is what a caller names (`?workspace=` or `Sf-Workspace`); a
session is the live browser open in it, at most one at a time. Never call a workspace a session.

## Docs

- A behaviour change edits its area file in the same pull request, following the Ids section of
  Specification.md. Why it is that way goes in the design under `plans/`.
- A design opens with a `> **History.**` header saying what shipped and which requirement ids hold
  it now. Plans are history: add to the header, leave the body.
- The project's history is `docs/superpowers/plans/Ledger.md`. Code that still cites a section of
  the deleted saga as `§F1.n` can look it up in the ledger's last table.
- Never edit a generated wiki page (one per tool, `Configuration.md`). Change the code or
  `docs/notes/<tool>.notes.md` and run `scripts/generate_wiki.py`.
- `docs/` is the wiki's own repository. CONTRIBUTING's "The wiki" says which branch to commit on;
  never push the wiki's `master`.

## Rules

- The maintainer is Dr K. This is a public repo: no personal names, emails, hostnames or internal
  URLs in code, docs, commits or pull requests. Examples use `example.com`.
- When `test_surfaces`, `test_boundaries`, `test_scrub_coverage`, `test_show_inventory`,
  `test_wiki` or a golden fails, fix the code (add the missing half, regenerate the page), never the
  assertion. A golden is regenerated only for a contract change you meant, in a commit of its own.
- A comment or prose pass leaves the signatures and docstrings in `mcp/tools.py` and
  `mcp/instructions.py` alone: they are the tool schemas and the descriptions a model reads. Edit one
  only to change the tool.
- The CONTRIBUTING rules agents break most often: an integration test added only to be thorough
  ("Tests"), an argument's shape changed in one release ("Changing an argument"), and a changelog
  line for internal work ("The changelog").
- Read before you claim. A statement about the code, in the spec or a pull request, comes from
  reading the code, not from a design or this file.

## Agents in the workflows

The issue, pull request and review agents (`.github/workflows/agent-*.yml`) follow everything
above, and also:

- The checks are CI's ([CONTRIBUTING.md, "CI"](CONTRIBUTING.md#ci)). Run the lint, the unit suite
  and, when `ui/` changed, the UI's, from its Commands table. The integration tests need a Grid and
  run in CI only.
- You never commit. What you leave changed is committed after you answer, `docs/` included: the
  workflow commits it to the wiki branch and moves the pointer.
- You never edit `.github/`. A person commits workflow changes.
- The Renovate agent (`agent-renovate.yml`) follows up Renovate's pull requests. There is no
  README `## Upgrading` table here: the pins are `pyproject.toml`'s dependencies, the
  Dockerfile's base images and `PY_VERSION`, `ui/package.json`, and the workflows' `uses:` lines.
  Its check is the lint and unit suite above, not `kubectl build`.
