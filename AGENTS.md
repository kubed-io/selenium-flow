# AGENTS.md

Rules for an agent working in selenium-flow. Everything a person needs is in the files below;
this one adds only what an agent would otherwise get wrong.

## Read first

1. [`docs/superpowers/specs/README.md`](docs/superpowers/specs/README.md): what the server does,
   the area index, the glossary and the architecture. Before changing a behaviour, read its area
   file.
2. [CONTRIBUTING.md](CONTRIBUTING.md): setup, commands, tests, CI, releases, and the rules for
   writing code here.
3. [`plugin/skills/selenium-flow/SKILL.md`](plugin/skills/selenium-flow/SKILL.md): how a model is
   meant to use the server.

Use the glossary's words. A **workspace** is what a caller names (`?workspace=` or
`Sf-Workspace`); a **session** is the live browser open in it, at most one at a time. Never call a
workspace a session.

## Where things go

| What | Where |
|---|---|
| A requirement | `docs/superpowers/specs/<area>.md` |
| A design or a plan | `docs/superpowers/plans/YYYY-MM-DD-<topic>-design.md`, `YYYY-MM-DD-<topic>.md` |
| How to use a feature, for a user | a hand-written wiki page, `docs/<Page>.md` |
| What a schema cannot say about one tool | `docs/notes/<tool>.notes.md` |
| What a user can now do | one line under `## [Unreleased]` in `CHANGELOG.md` |

- **A behaviour change edits its area file in the same pull request.** Add, change or remove the
  requirement. A new requirement takes the area's next number; a removed one leaves a gap, and
  ids are never reused.
- A requirement that is not built yet is written like the others and tagged *(proposed)*. The pull
  request that builds it removes the tag.
- The spec states what is true now: present tense, symbol names, no line numbers, dates, issue
  numbers or history. Why it is that way goes in the design under `plans/`.
- A design opens with a `> **History.**` header saying what shipped and which requirement ids hold
  it now. Plans are history: add to the header, leave the body.
- `docs/saga/` is frozen. Code still cites it as `§F1.n`; look the number up there, and never edit
  or extend it.
- The generated wiki pages (one per tool, `Configuration.md`) are written by
  `scripts/generate_wiki.py`. Change the code or the notes and regenerate; never edit the page.

## The wiki submodule

`docs/` is the GitHub wiki, a separate repository checked out as a submodule, and the spec and
plans live in it.

- Commit inside `docs/` first, on the wiki branch with the same name as this repo's branch
  (`issue-137` for `issue-137`). Push that branch, then commit the new `docs` pointer here.
- Never push to the wiki's `master`. It is the public wiki, and only `wiki.yml` on main writes it,
  after a merge.

## Rules

- **The maintainer is Dr K.** This is a public repo: no personal names, emails, hostnames or
  internal URLs in code, docs, commits or pull requests. Examples use `example.com`.
- **A failing guard test is the design saying no.** When `test_surfaces`, `test_boundaries`,
  `test_scrub_coverage`, `test_show_inventory`, `test_wiki` or a golden fails, fix the code (add
  the missing half, regenerate the page), never the assertion. A golden is regenerated only for
  a contract change you meant, in a commit of its own (CONTRIBUTING, "Tests").
- **Don't add an integration test to be thorough.** Add a flow under `tests/integration/flows/`
  only when you can name what a person would see break that no flow catches. Everything else is a
  unit test.
- **Tool declarations are a contract.** In `mcp/tools.py` a signature is the tool's schema and a
  docstring is the description a model reads and the wiki page's text. Edit one only to change
  the tool, and write it for a model choosing what to call. A comment or prose pass leaves them
  alone.
- **Change an argument's shape in two steps.** Clients cache tool schemas; see CONTRIBUTING,
  "Changing an argument".
- **Internal work earns no changelog line.** A refactor, CI change, test pass or dependency bump
  takes the `no changelog` label.
- Read before you claim. A statement about the code, in the spec or a pull request, comes from
  reading the code, not from a design or this file.

## Agents in the workflows

The issue, pull request and review agents (`.github/workflows/agent-*.yml`) follow everything
above, and also:

- **The checks** are CI's ([CONTRIBUTING.md, "CI"](CONTRIBUTING.md#ci)); run the lint, the unit
  suite and, when `ui/` changed, the UI's, from its Commands table. The integration tests need a
  Grid and run in CI only.
- **You never commit.** What you leave changed is committed after you answer, `docs/` included:
  the workflow commits it to the wiki branch and moves the pointer.
- **You never edit `.github/`.** A person commits workflow changes.
