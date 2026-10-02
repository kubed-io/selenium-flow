# The optimal shape: a refactor and optimisation round

**Status: DRAFT, being filled as the research lands (2026-10-02).** Dr K reads this
as it grows and drops rulings in chat; every ruling is recorded under *Rulings*.

## Brief

Dr K, 2026-10-02: feature freeze. This round is a pure refactor and optimisation
round. *"In the beginning we did not know all the requirements and nuances yet.
Look at the project from the perspective of its requirements and use cases now,
and think about how this app could have been if you knew it all up front."*

- Research best practices: Python, FastMCP, test writing, UI design.
- Keep an eye out for security issues.
- Find the most high-value refactor that enables the trajectory.
- Is there a conceptual core that can be abstracted so the logical part of the
  app is cleaner, *"like how could I make a library out of this that other apps
  could use too"* — a way of thinking about foundations, not a package to ship.
- Look at the sibling mcp-kb for anything of value.
- The profiler and the bench job now measure the code; analyse them.
- **No regression on any core functionality.** Rip things apart to put them back
  together, but every behaviour survives.
- Safe and secure throughout.

## Non-goals

- No new capability: no new tool, endpoint, tab, flag or setting. The CHANGELOG
  takes the `no changelog` label unless a user would notice something.
- No change to any published shape: tool schemas, HTTP bodies and results, the
  OpenAPI document, URIs, the skill, error messages an agent reads. A client
  caches schemas (CONTRIBUTING, "Changing an argument's shape").
- Not a packaging exercise: the "library" is a way to draw the boundaries inside
  this repo, not a second distribution.

## Constraints

- Every rule in `AGENTS.md` stays a rule. The round may move where a rule is
  enforced, never whether.
- Behaviour is pinned before it is moved: the inventory below is the regression
  contract, and an UNPINNED behaviour gets a characterisation test first.
- The unit suite stays green at every commit; ruff, svelte-check, vitest and
  the UI bundle guards too. Integration runs on the branch before merge.
- Measured, not asserted: every optimisation names the bench or profile number
  it moves.

## Process

1. **Understand** (running): thirteen read-only analysts, one per subsystem
   plus research (Python/FastMCP, testing and Svelte, mcp-kb comparison, the
   profile and bench, a whole-package security review), then a completeness
   critic. Reports under `.superpowers/round3/understand/` (gitignored).
2. **Design**: three independent proposals for the core abstraction, judged on
   behaviour safety, what they enable, simplicity, performance, security and
   library shape; this spec is the synthesis.
3. **Plan**: `docs/superpowers/plans/2026-10-02-optimal-shape.md`, tasks sized
   for subagent-driven development, each ending green.
4. **Build**: fresh implementer per task, review after each, whole-branch
   review at the end; the PR loop until CI is green and Copilot asks for a
   human review.

## Behaviour contract

Pending: the inventory, one line per observable behaviour with the test that
pins it, filled from the Understand reports.

## Findings

Pending: structure, performance, security and test-suite findings, ranked.

## The core abstraction

Pending: the Design phase's synthesis.

## Plan of record

Pending: the ordered list of refactors and optimisations this round ships.

## Questions for Dr K

Pending: only the high-level ones.

## Rulings

- Dr K, 2026-10-02: feature freeze; refactor and optimisation only; no
  regression; rulings in chat, recorded here; questions only when high level.
