---
name: driving-a-browser
description: Use when a task needs a live browser through selenium-flow - a page that renders only under JavaScript, a form to fill, steps to click through, Chrome or Firefox, a screenshot, PDF or download. Read it before the first selenium-flow call, and again when a call fails or is refused because a person holds the browser.
---

# Driving a browser with selenium-flow

The browser is real and stays open between calls, with its page, cookies,
storage and scroll position. Your workspace, named by your client's
`?workspace=` or `Sf-Workspace` (`stdio` over stdio), keeps your settings, last
page, files, flows and site data. A session is a live browser in it: the
workspace's default session, and sub-sessions you name. When a session ends,
`open_session()` with no arguments brings you back to the same page.

## Rules

1. Call `open_session` once, first. Nothing else opens a browser, and calling it
   again ends the one you hold, losing anything unsaved such as a sign-in or a
   half-filled form. Reopen only to switch browser or change an option, or after
   a result says you have no browser.
2. Put `url` on the action instead of navigating first. An action with an
   optional `url` goes there only if the browser is elsewhere. Use `navigate`
   when going to the page is the whole step.
3. Read with `extract`. Find selectors with `outline`, which checks each one and
   says why an element cannot be used yet; `outline(text="Save")` finds one by
   its label. Take a screenshot only when the look is the answer.
4. A selector is `{"css": ...}` or `{"xpath": ...}`, never both. Use XPath to
   match visible text (`//button[contains(., 'Save')]`) or to walk up to an
   ancestor, and CSS for the rest.
5. Never sleep. A tool that names an element waits for it, up to `wait_timeout`
   (30 seconds). For an effect no element shows, `assert` a script that returns
   true once it has happened.
6. Never put a password in `text`. Give `write` a
   `secret={"name": ..., "key": ...}` and the server types it;
   `secret://secrets` lists the secrets.
7. End every task with `end_browser()`, after a failure too. The Grid has few
   slots, and your workspace survives it.

`execute_script` is for what no tool does: many values in one round trip,
computed style, scrolling. The script is a function body, so `return` the value.
To wait on something async, return a promise: a top-level `await` is a syntax
error on Firefox. A script that navigates is cut off.

## Which page to read

Open the one row that matches.

| When | Read |
|---|---|
| Naming or sharing a workspace, a second browser or a subagent's session, a `reopened` result, browser version, headless | `skill://selenium-flow/driving-a-browser/references/WORKSPACES.md` |
| A call refused because a person holds the browser, a `control` field on a result | `skill://selenium-flow/driving-a-browser/references/CONTROL.md` |
| Reading a page, choosing a selector, `outline` entries, screenshots | `skill://selenium-flow/driving-a-browser/references/READING_PAGES.md` |
| A whole task end to end; hover menus, dragging, typing, keys, iframes, tabs, dialogs, scrolling | `skill://selenium-flow/driving-a-browser/references/INTERACTION.md` |
| A download, keeping a file, a link to give a person, a print, a recording, an upload | `skill://selenium-flow/driving-a-browser/references/FILES.md` |
| A timeout, an error, a click or hover that did nothing, a `page` hint, a blank screenshot, an `open_session` that fails | `skill://selenium-flow/driving-a-browser/references/TROUBLESHOOTING.md` |
| Every call refused: `this workspace's config is invalid` | `skill://selenium-flow/using-the-admin-ui/references/WORKSPACE_CONFIG.md`, *Managing a workspace* |
| Every resource URI and what it holds, and how to read one | `skill://selenium-flow/driving-a-browser/references/RESOURCES.md` |
| `flows_here` on a result, steps worth saving, a failed flow run | `skill://selenium-flow/building-flows/SKILL.md` |
| A password or token to type, a refused secret, staying signed in, signed out after a save | `skill://selenium-flow/signing-in/SKILL.md` |
| A person asking about the admin page, or for admin work | `skill://selenium-flow/using-the-admin-ui/SKILL.md` |
| Deploying the server, connecting a client, server settings | `skill://selenium-flow/deploying-the-server/SKILL.md` |

## Every tool

| Tool | Reach for it |
|---|---|
| `open_session` | first, once per task; with no arguments, back to the last page |
| `end_browser` | last, in every task |
| `navigate` | when going to a URL is the whole step |
| `interact` | click, double or right click, hover (keeps a `:hover` menu open), scroll an element into view |
| `drag` | a card onto a column (`to`), a slider (`by_x`) |
| `write` | type `text`, or a `secret` you never see |
| `press_key` | Enter, Escape, Tab, `Control+a`; not for scrolling |
| `extract` | read an element's text |
| `outline` | find a selector, and why an element cannot be used |
| `screenshot` | when the look is the answer; give a person its link |
| `print` | the page as a PDF or HTML file for a person |
| `upload_file` | fill a file input |
| `frame` | into an iframe and back out; the switch sticks |
| `tab` | list, open, switch or close tabs; a popup becomes the current tab by itself |
| `dialog` | an alert, confirm or prompt; nothing else works while one is open |
| `resize` | set the window before judging layout |
| `execute_script` | what no tool does |
| `assert` | check or wait for something no element shows |
| `save_site_data` | after a sign-in you have confirmed |
| `network_body` | a response body, by id from `workspace://network`; pass the `session` the id came from |
| `await_control` | a person holds your browser |
| `show` | draw a resource for the person, in a client that shows apps |
| `keep_file` | keep a screenshot, recording or download in Files |
| `save_flow` | save steps under a name |
| `run_flow` | run a flow; a failed run is a result, so read its `status` |
| `delete_flow` | delete one of your flows |
