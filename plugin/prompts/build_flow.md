---
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
Build a selenium-flow flow for: **{{ task }}**

- Starts at: {{ url }}
- Save it as: {{ name }}

Do it by hand first, then save what worked. A flow written from guesswork fails
at step nine with a form half filled.

1. Read `workspace://current` to see what you are holding.
2. `open_session()`, then `navigate` to {{ url }}.
3. `outline` the page before each action, scoped with a `selector` or filtered
   with `text`. Take the selector it gives you; each one is checked to match
   exactly one element. Note whether the element can be used.
4. Carry out the task one call at a time: `interact`, `write`, `press_key`,
   `extract`. The URL and title after an action tell you whether it did what you
   meant.
5. Note what should vary between runs, such as an account, an id or a site.
   Those become parameters, written `${name}` in any argument. A password is not
   a parameter: name a secret, and the server types it without it passing
   through you.
6. Note what must be true for the run to be worth continuing. Those become
   `assert` steps: JavaScript that has to come back `true`, with a `message`
   saying what should have been the case. Put one after anything that navigates.
7. The flow owns where it starts: its first step navigates, or asserts that the
   browser is already where it needs to be.
8. Save it with `save_flow`: name, description, `sites` and `paths`,
   `parameters` as JSON Schema, and the steps you ran.
9. Run it twice with `run_flow`: once in the state you built it in, and once
   from a clean start, `end_browser()` then `open_session(url="{{ url }}")`. A
   flow that only works from where you happened to be rots first.

Report the flow's name, what it takes, and both runs.
