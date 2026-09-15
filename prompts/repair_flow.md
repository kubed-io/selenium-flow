---
description: Fix a saved selenium-flow flow that has stopped working, by looking at the page it fails on.
arguments:
- name: flow
  description: The flow's name, as list_flows shows it.
  required: true
- name: step
  description: Which step failed, if you know. Leave empty to find out by running it.
  default: the run will say
- name: symptom
  description: What went wrong, in a few words. Leave empty to start from the failure itself.
  default: not described, so start from what the run reports
---
Repair the saved flow **{{ flow }}** with the selenium-flow tools.

- Failing step: {{ step }}
- Symptom: {{ symptom }}

A flow breaks for one of two reasons, and they have different fixes: the page
changed under it, or the flow is being run somewhere it was not written for.
Find out which before editing anything.

1. Read the flow: `get_flow(name="{{ flow }}")`. Note what each step addresses
   and what the flow declares as parameters.
2. Run it and read the failure: `run_flow(name="{{ flow }}", verbose=true)`.
   **If it declares required parameters**, step 1 showed you which — pass them
   as `params`, asking me for any value you do not have, or the run is refused
   before it reaches the step you came to look at. The failing step's `error` is
   the diagnosis: when the element is on the page but cannot be used, it already
   says why (a hidden ancestor, an overlay, no size, off-screen, disabled) and
   what to do about it.
3. Get to the page that step acts on. `navigate` there yourself, or run the
   steps before it by hand.
4. **`outline` that page** — scope it with `css` to the region in question, or
   `text` to search by label. Each entry carries a selector checked to match one
   element, and whether it can be used. This is how you find what the step
   should address now; do not read HTML to work it out.
5. Decide which failure you have:
   - **The page changed.** An element moved, was renamed, or is now behind a
     menu that opens on hover. Fix the step's selector, or add the `interact`
     step that opens what hides it.
   - **The flow was in the wrong place.** It assumed a page it did not navigate
     to. Give it a first step that navigates, or an `assert` that stops the run
     with a sentence saying where it must be.
6. Save it back with `save_flow`, keeping the name. Then `run_flow` again and
   confirm it passes for the right reason — read what it returns rather than
   trusting the status.
7. If the flow should refuse to run in some state (already signed in, say), add
   an `assert` whose `message` says so. A flow that stops with an explanation is
   worth more than one that fails at step nine with a form half filled.

Report what had changed, what you edited, and the run that proves it.
