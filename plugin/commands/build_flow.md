---
argument-hint: <task> <url> [name]
description: Walk a browser task by hand with selenium-flow, then save it as a flow that can be replayed in one call.
arguments:
- name: task
  description: What the flow should do, in a sentence — "sign in and open the orders page for an account".
  required: true
- name: url
  description: Where it starts.
  required: true
- name: name
  description: What to save it as. Leave empty and a name will be proposed from the task.
  default: propose one from the task, lowercase with hyphens
---
Build a selenium-flow flow for: {{ task }}

- Starts at: {{ url }}
- Save it as: {{ name }}

Drive it by hand first, then save what worked. How flows are written is in
the `building-flows` skill.

1. Read `workspace://current`. If `live` is false, call `open_session()`. Then
   go to {{ url }}.
2. `outline` the page before each action, scoped with a `selector` or filtered
   with `text`, and take the selector it gives you.
3. Carry out the task one call at a time with `interact`, `write`, `press_key`
   and `extract`. The URL and title after each action say whether it did what
   you meant.
4. Note what varies between runs, such as an account, an id or a site: those
   become parameters, written `${name}`. A password is a secret, never a
   parameter (`SECRETS.md` in the `signing-in` skill).
5. Note what must be true for the run to go on: those become `assert` steps with
   a `message` saying what should have been the case. Put one after anything
   that navigates.
6. Make the first step navigate, or assert that the browser is already where it
   needs to be.
7. `save_flow` with a name, description, `sites`, `paths`, `parameters` and the
   steps you ran.
8. Run it twice with `run_flow`: once where you built it, and once from a clean
   start, `end_browser()` then `open_session(url="{{ url }}")`.

Report the flow's name, what it takes, and both runs.
