---
name: building-flows
description: Use when a selenium-flow result carries flows_here, steps will be repeated and are worth saving, a flow run fails, or a flow needs building or repairing.
---

# Building flows

A flow is a list of selenium-flow tool calls saved under a name and replayed in
one `run_flow` call. Everything about flows is on one page; open it at the
section that matches.

When a result carries `flows_here`, a saved flow applies to the page. Run it
with `run_flow` when its description is your task and you have every value in
its `required`. Read its `uri` first when the description is unclear, a value is
one you cannot fill, or the flow would change something you were not asked to
change. A flow that fits the page may not fit the task.

| When | Read, in `references/FLOWS.md` |
|---|---|
| Running a flow, reading its result | *Running one* |
| A run's `status` is `failed`, or its `hint` names a section | *When a flow fails*, and the section the hint names |
| Writing a flow, its steps, its parameters | *Building one*, *Parameters* |
| Saying where a flow applies, so `flows_here` offers it | *Where a flow applies*, *Tags* |
| A flow that must stop when the page is wrong | *Say what must be true* |
| A run that has to wait minutes | *How long a run may take* |
| Whose flows you can run, save or delete; the shared library | *Whose flows you see* |

`build_flow` and `repair_flow` are prompts: a person picks them in their client.
Where the selenium-flow plugin is installed they are also commands,
`/selenium-flow:build_flow` and `/selenium-flow:repair_flow`, which you may run
yourself. A failed run's `hint` names the one that helps: run it if you can,
otherwise tell the person.

Crossing over: a password in a flow is a secret, the `signing-in` skill, row
"A password, token or key to type". Moving a flow into the shared library is
admin work, the `using-the-admin-ui` skill. The tools a step calls are the
`driving-a-browser` skill's.
