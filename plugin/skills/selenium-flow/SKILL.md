---
name: selenium-flow
description: Drive a real Chrome or Firefox browser on Selenium Grid through the selenium-flow MCP server. Use when a task needs a live browser - reading a page that only renders under JavaScript, signing in, filling and submitting a form, clicking through several steps, checking a site in Chrome or Firefox, a screenshot or PDF, a flow saved to replay in one call, or a stored secret typed without you seeing it. Read it before your first selenium-flow call, and again when a call fails or a person takes control of the browser.
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

When a result carries `flows_here`, a saved flow applies to the page. Run it
with `run_flow` when its description is your task and you have every value in
its `required`. Read its `uri` first when the description is unclear, a value is
one you cannot fill, or the flow would change something you were not asked to
change. A flow that fits the page may not fit the task.

`open_session(browser="firefox")` switches browser and ends the one you hold. To
hold Chrome and Firefox at once, use two sessions, or two workspace names. Downloads go with an
ended browser, so `keep_file` one first.

## Sub-sessions

Pass `session="name"` to `open_session` for a second browser in the same
workspace, with its own sign-ins, beside your default. Give every later call that
acts on it the same `session`; a call without one reaches the default.
A caller with no token subject (the static token, stdio) always reaches the
default. A caller whose token has a subject that does not own the default gets
its own sub-session, labelled by its username and made on its first call; it
needs `open_session` for a browser.

- A main agent gives each subagent its own session name. The server cannot tell
  callers on one connection apart, and callers that name the same session are one
  caller: their calls take turns.
- A sub-session starts empty: no cookies, settings or page from another session.
  It lives `session.sub_ttl` (an hour) after its last use; after that, name it
  again with `open_session`.
- Only `open_session` opens a browser. A call that names a session never opened,
  or one that has expired, is refused with `NoSession`, so a typo opens nothing.
- A connection can pin its session with `?session=name` or `Sf-Session`; its
  calls then reach that session, and a different `session` argument is refused.
- The workspace's files, flows, config and history are shared by every session.
- `current`, `downloads`, `console`, `network`, `files` and `site-data` cannot
  name a session.

## A whole task

```
open_session(width=1400, height=900)
write(url="https://example.com/login", selector={"css": "input[name=email]"}, text="a@example.com")
write(selector={"css": "input[name=password]"}, secret={"name": "example", "key": "password"})
interact(action="click", selector={"xpath": "//button[@type='submit']"})
assert(script="return !!document.querySelector('#avatar')", message="Not signed in.")
extract(selector={"css": "main h1"})
save_site_data()    # the next browser comes back signed in
end_browser()
```

## Which reference to read

Open the one row that matches, at `skill://selenium-flow/` plus its path.

| When | Read |
|---|---|
| Naming or sharing a workspace, naming a session, a `reopened` result, browser version, headless or recording, a person in control of the browser | `references/WORKSPACES.md` |
| Reading a page, choosing a selector, `outline` entries, screenshots and prints | `references/READING_PAGES.md` |
| Hover menus, dragging, typing, keys, uploads, iframes, dialogs, scrolling | `references/INTERACTION.md` |
| A timeout, an error, a click or hover that did nothing, a blank screenshot, an `open_session` that fails | `references/TROUBLESHOOTING.md` |
| Saving or running a flow, a failed run | `references/FLOWS.md` |
| Typing a password or token, a refused secret | `references/SECRETS.md` |
| Staying signed in across browsers, starting as a new user | `references/SITE_DATA.md` |
| Deploying the server, connecting a client, server settings | `references/CONFIGURATION.md` |

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
| `network_body` | a response body, by id from `workspace://network` |
| `await_control` | a person holds your browser |
| `show` | draw a resource for the person, in a client that shows apps |
| `keep_file` | keep a screenshot, recording or download in Files |
| `save_flow` | save steps under a name |
| `run_flow` | run a flow; a failed run is a result, so read its `status` |
| `delete_flow` | delete one of your flows |

## Everything to read

Read a URI the way your client reads MCP resources: in Claude Code,
`ReadMcpResourceTool` with server `selenium-flow`. A client that cannot read
resources gets the tools `read_resource(uri)` and `list_resources()`.

| URI | Is |
|---|---|
| `workspace://current` | what you hold: page, browser, window, `live`, `in_frame`, `tabs` |
| `workspace://current/{part}` | one key of that, for instance `url` or `grid_timeout`; null when there is none |
| `workspace://files` | Files, each file with a link, and the two folders below |
| `workspace://files/{name}` | one file in Files |
| `workspace://files/screenshots` | screenshots not kept yet |
| `workspace://files/screenshots/{name}` | one of those |
| `workspace://files/recordings` | recordings not kept yet, each with a link that plays it |
| `workspace://files/recordings/{name}` | one of those |
| `session://downloads` | the open browser's downloads |
| `session://downloads/{name}` | one of those |
| `workspace://site-data` | the sites with saved site data, never a value |
| `workspace://site-data/{site}` | one site's saved data; httpOnly values masked |
| `workspace://console` | what the page logged; needs `open_session(capture=true)` |
| `workspace://console{?level,pages}` | the same, narrowed: `?level=error`, `?pages=2` |
| `workspace://network` | the requests the page made, each with an id; needs capture too |
| `workspace://network{?filter,pages}` | the same, narrowed: `?filter=failed`, `?filter=xhr` |
| `workspace://network/{id}` | one request in full |
| `session://{name}` | one session of the workspace, as `workspace://current` is yours; `default` is the workspace's own, and a name with no session is not found |
| `session://{name}/console` | that session's console |
| `session://{name}/console{?level,pages}` | the same, narrowed |
| `session://{name}/network` | that session's requests |
| `session://{name}/network{?filter,pages}` | the same, narrowed |
| `session://{name}/network/{id}` | one of that session's requests |
| `session://{name}/site-data` | that session's saved site data |
| `session://{name}/site-data/{site}` | one site of it |
| `session://{name}/downloads` | that session's downloads |
| `session://{name}/downloads/{file}` | one of those |
| `secret://secrets` | the secrets you may type, never their values |
| `flow://flows` | the flows you can run |
| `flow://flows/{name}` | one flow's parameters and steps |
| `flow://schema` | what a flow and its steps may contain |
| `skill://selenium-flow/SKILL.md` | this page; the references are beside it |

`build_flow` and `repair_flow` are prompts a person picks in their client; you
cannot call them. A failed run's `hint` names the one that helps, so tell the
person.
