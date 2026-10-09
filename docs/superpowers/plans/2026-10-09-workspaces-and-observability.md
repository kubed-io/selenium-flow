# Workspaces and observability — programme plan

> **For the orchestrator and Dr K.** This plan sequences six epics into GitHub
> issues and gates; it is not an implementation plan. Each epic's PR agent
> writes that with `writing-plans`, from the epic's own spec, and executes it
> with `subagent-driven-development`.

**Goal:** six epics from one design, run through the issue → spec → PR → plan
→ code workflow in an order that lets specs proceed in parallel and code merge
without conflict.

**Spec:** `docs/superpowers/specs/2026-10-09-workspaces-and-observability-design.md`
(the programme; cited as `programme R<n>` / `programme E<n>`).

**Design:** Penpot file *Admin UI* — every board the epics need is drawn.
Epic agents read it; they do not edit it (programme R15).

## Global constraints

- An epic's spec keeps the house form (Brief · Research · Rulings · Goal ·
  Non-goals · Design · Verify first · Next round) and cites the programme.
- The word is *workspace* (programme R1) in every spec, including those
  written before E1 merges.
- Events carry no values (R6); secrets never reach a buffer (R11); no
  workspace name on `/metrics` (R12).
- Merge order below is binding; spec order is not.

---

## Pre-flight (orchestrator, before the first issue)

- [x] **Labels.** `agent-issue.yml` and `agent-pr.yml` gate on `agent`,
      `spec approved`, `plan approved`; created 2026-10-09.
- [ ] **Saga pointer.** AGENTS.md says the design record lives in `saga/`;
      it lives in `docs/superpowers/` now and the saga is deprecated. Folded
      into E1's scope; no separate change.
- [ ] **Copilot.** The code loop wakes on Copilot's review *or* a comment from
      Dr K; with Copilot's quota gone, a comment after each push is the turn.
- [x] **Penpot.** Rename done (every page, component and text; the browser's
      sessionStorage strings kept). *Workspace · Console* (with `capture-off`)
      and *Workspace · Network* drawn with their states and flows. Pills
      `● REC`, `● capture`, `capture paused` on the cards and summaries;
      `summary / live|idle` carry `IDLE TIMEOUT` and `CAPTURE`; the tab
      variants carry Console and Network, muted in `capture-off`; *Admin ·
      login* carries the `oidc` row. *App · show* draws `context`, `console`,
      `network`, `request`, `site-data`, `site`, `file`, `document`, with the
      drill-downs wired.

## The issues

One issue per epic. The body is the epic's section of the programme spec,
verbatim, with the pointer `Programme: docs/superpowers/specs/2026-10-09-workspaces-and-observability-design.md`
on top. Labels: `enhancement` at creation; `agent` is Dr K's to add, since it
starts a paid spec run and fixes the order.

| # | Title | Add `agent` | Code after |
|---|---|---|---|
| E1 (#60) | Workspaces: the rename | now | — (first, alone) |
| E2 (#61) | The session monitor | now | E1 merged |
| E5 (#62) | The admin UI signs in with OIDC | now | E1 merged (parallel with E2) |
| E7 (#63) | `show` draws every resource | now | E1 merged (parallel with E2) |
| E3 (#64) | Console and network capture | once E2's spec is approved | E2 merged |
| E4 (#65) | Telemetry: `/metrics` and traces | once E2's spec is approved | E2 merged (parallel with E3) |
| E6 (#66) | Workspace ownership and per-surface roles | later, Dr K's call | last |

## Order and gates

```
specs      E1 ──┐   E2 ──┐   E5 ──┐   E7 ──┐        E6 (ticket)
                │        │        │        │
merge 1 ◄── E1 code      │        │        │
merge 2 ◄────────── E2 code       │   E5, E7 code ──► merge (any time after 1)
                         │
specs                    └──► E3 ──► code ──► merge   E4 ──► code ──► merge
                               (both after E2's spec is approved; code after merge 2)
```

Why this order: E1 touches every file every other epic touches, so it merges
first and alone. E3 and E4 consume E2's bus and watch interfaces, so their
specs wait for E2's *spec* (to cite real names) and their code for E2's
*merge*. E5 is independent of E2 and only needs E1's vocabulary in the UI.

## What each epic agent produces

1. **Spec rounds on the issue** until Dr K labels `spec approved` — the spec
   file lands on an `issue-<n>` branch with a draft PR.
2. **A plan on the PR** (`writing-plans`) until `plan approved`.
3. **Code**, one plan task per run, pushed; review threads answered.

Each spec must: cite the programme rulings it rests on; list its "Verify
first" items from the programme's section (E2: 1–3; E3: 4–5; E4: 6) with the
results once run; name the Penpot boards it reads.

## Orchestrator duties (this session, or its successor)

- [x] Opened #60–#66, 2026-10-09, `enhancement` only. E3 and E4 are open
      too; their bodies say the spec waits for E2's, so `agent` goes on them
      the day E2's spec is approved.
- [ ] Watch for drift between specs written in parallel: E3 and E4 both name
      `call.finished` and the bus; E2's spec owns those names.
- [ ] When a spec needs a board that is not drawn, draw it here and tell the
      issue which board to read.
- [ ] After E1 merges: confirm the other open branches rebase cleanly before
      their code starts.

## Done when

Every epic's PR is merged, `/metrics` is scraped in the cluster, a workspace
opened with `capture=true` shows its console and network in the admin UI and
answers `workspace://network`, and the word *session* in this repo means only
what the Grid and MCP mean by it.
