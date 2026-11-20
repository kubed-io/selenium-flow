---
argument-hint: <flow> [step] [symptom]
description: Fix a saved selenium-flow flow that has stopped working, by looking at the page it fails on.
arguments:
- name: flow
  description: The flow's name, as flow://flows lists it.
  required: true
- name: step
  description: Which step failed, if you know. Leave empty to find out by running it.
  default: the run will say
- name: symptom
  description: What went wrong, in a few words. Leave empty to start from the failure itself.
  default: not described, so start from what the run reports
---
Repair the saved flow {{ flow }} with the selenium-flow tools.

- Failing step: {{ step }}
- Symptom: {{ symptom }}

A flow breaks because the page changed under it, or because it ran somewhere it
was not written for. Find out which before editing anything.

1. Read `workspace://current`. If `live` is false, call `open_session()`: a
   flow never opens a browser.
2. Read `flow://flows/{{ flow }}` for its steps and parameters.
3. `run_flow(name="{{ flow }}", verbose=true)`, passing its required
   parameters as `params`; ask me for any value you do not have. The failing
   step's `error` is the diagnosis: for an element that is there but cannot be
   used, it says why and what to do.
4. Go to the page that step acts on, with `navigate` or by running the steps
   before it by hand.
5. `outline` that page, scoped with a `selector` or filtered with `text`, to see
   what the step should address now.
6. Fix the cause:
   - The page changed: an element moved, was renamed or is now behind a menu.
     Fix the step's selector, or add the `interact` step that opens what hides
     it.
   - The flow assumed a page it did not navigate to: give it a first step that
     navigates, or an `assert` whose `message` says where it must be.
7. `save_flow` under the same name, then `run_flow` again and read what it
   returns to confirm it passes for the right reason.
8. If the flow should refuse to run in some state, such as already signed in,
   add an `assert` whose `message` says so.

Report what had changed, what you edited, and the run that proves it.
