---
name: selenium-flow
description: Drive a real Chrome or Firefox browser on Selenium Grid through the selenium-flow MCP server. Use when a task needs a live browser - reading a page that only renders under JavaScript, logging in, filling and submitting a form, clicking through a multi-step flow, checking how a site looks or behaves in Chrome or Firefox, capturing a screenshot or PDF, saving a sequence to replay in one call, or signing in with a stored secret you are never shown. Use it before your first selenium-flow call, even for a quick read, and come back to it when a call fails or a person takes control of the browser.
---

# Driving a browser with selenium-flow

The browser is **real and persistent**. It lives on Selenium Grid and keeps its
page, cookies, storage and scroll position between your calls. You are steering
one tab, not sending independent requests, and that shapes everything below:

- **State carries over.** Signed in once is signed in for every later call. A
  browser left on the wrong page also makes your next selector fail for a
  reason that has nothing to do with the selector.
- **Slots are scarce.** The Grid runs a handful of browsers for everyone. One
  you leave open holds a slot until it is reaped, so ending it is capacity, not
  politeness.
- **Your workspace outlives the browser.** The workspace is the named box that
  keeps your browser choice, last page, files, flows and saved sign-ins; the
  session is the live browser in it, at most one. A session that goes away
  (reaped when idle, ended by you or an operator) costs nothing:
  `open_session()` with no arguments puts you back where you were.

## Before the first call

**Your workspace is named by whoever connects.** The client's config adds
`?workspace=<name>` to the server URL or sends an `X-Workspace` header (not
both); over stdio it is `stdio`. No tool takes a session id and no result
carries one: the same name always finds the same browser.

**Call `open_session` once, first.** Nothing opens a browser behind your back,
because `open_session` is the one place its browser, window and timeouts are
chosen. Once is enough for the whole task: calling it again ends the browser
you hold and starts a new one on the same URL, which waits on the Grid and loses
whatever you had not saved (a sign-in, a half-filled form). To get somewhere
else, pass `url=` to your next call. Reopen only to switch browser or change an
option, or with no arguments after you are told you have no browser.

**Everything to read is a URI**: `workspace://`, `flow://`, `secret://`,
`skill://`. Read it the way your client reads MCP resources: in Claude Code that
is `ReadMcpResourceTool` with server `selenium-flow`, which you may have to load
first. A client that cannot read resources gets `read_resource(uri)` and
`list_resources()` from this server instead. Read before you guess:
`secret://secrets` names the secrets, `workspace://current` says what you are
holding, and neither touches the browser.

## The six rules

**1. Read with `extract` or `outline`, not `screenshot`.** A picture of text
costs orders of magnitude more, cannot be quoted, and may show a half-rendered
page. Screenshot only when the *look* is the answer, and give a person its link.

**2. Never navigate and then act: put `url` on the action.** Every tool that
reads or acts on the page (`extract`, `outline`, `interact`, `write`,
`screenshot`, `execute_script`, `assert` and the rest; not `frame`, `dialog` or
`resize`) takes an optional `url` and goes there first only if the browser is
elsewhere. One call instead of two, and the page you meant is part of the call.

```
extract(url="https://example.com/settings", selector={"css": "h1"})
```

**3. Find selectors with `outline`, not by reading HTML or scanning the DOM in a
script.** It lists what is on the page with a checked selector for each, finds
one thing by its label with `outline(text="Save")`, and says why an element
cannot be used (hidden, covered, off-screen, disabled) and what to act on to
fix it.

**4. One `selector`: `{"css": …}` or `{"xpath": …}`.** `css` is shorter for ids,
classes and attributes; `xpath` is the only one that matches visible text
(`//button[contains(., 'Save')]`) or walks up to an ancestor. Both at once is an
error, so a typo in one cannot hide behind the other.

**5. Let the tools wait; never sleep.** Every tool that names an element waits
for it and carries on the moment it appears; `wait_timeout` is only how long it
may take. For an effect with no element of its own, `assert` a script that
returns true once it has happened. A `setTimeout` in `execute_script` guesses at
a duration, fails on a slow day and wastes time on a fast one.

**6. End the browser when you are done, including after a failure.**
`end_browser()` frees the slot; it ends the session, not your workspace, so it
costs you nothing. Make it the last call of every task, whatever happened.

`execute_script` is for what no tool does: many values in one round trip,
computed style, scrolling, page state. It is a function body, so `return` the
value; to wait on something async, `return` a promise, because a top-level
`await` works on Chrome and is a syntax error on Firefox. A script that
navigates is cut off mid-run. If a tool in the table below does the job, use
the tool.

## Where to go next

Load only what the task needs.

| Doing | Read |
|---|---|
| Naming or sharing a workspace, recovering a dead browser, a browser version or capability | `skill://selenium-flow/references/WORKSPACES.md` |
| A person took control, or a call was refused for it | `skill://selenium-flow/references/WORKSPACES.md` |
| Getting content out of a page, choosing a selector | `skill://selenium-flow/references/READING_PAGES.md` |
| Clicking, hovering, typing, uploading, dialogs, iframes, scrolling, waiting | `skill://selenium-flow/references/INTERACTION.md` |
| A timeout, an empty screenshot, a click that did nothing, a page that reported an error | `skill://selenium-flow/references/TROUBLESHOOTING.md` |
| Something you or another agent will do again: save it once, run it in one call | `skill://selenium-flow/references/FLOWS.md` |
| Typing a password, token or anything else you must not see | `skill://selenium-flow/references/SECRETS.md` |
| Staying signed in across a reaped or ended browser | `skill://selenium-flow/references/SITE_DATA.md` |
| Setting the server up, connecting a client, which setting to change | `skill://selenium-flow/references/CONFIGURATION.md` |

## A whole task, minimally

```
open_session(width=1400, height=900)
write(url="https://example.com/login", selector={"css": "input[name=email]"}, text="a@example.com")
write(selector={"css": "input[name=password]"},
      secret={"name": "example", "key": "password"})
interact(action="click", selector={"xpath": "//button[@type='submit']"})
assert(script="return !!document.querySelector('#avatar')", message="Not signed in.")
save_site_data()                               # the next browser comes back signed in
end_browser()                                  # last, whatever happened
```

**Never put a real password in `text`.** Name a secret and the server types it
without it passing through you: `secret://secrets` lists what there is.

**Chrome or Firefox.** `open_session(browser="firefox")` switches; the browser
you hold is ended first, and every other tool behaves the same on both. Use
Firefox when the task is about Firefox. Downloads go with an ended browser, so
`keep_file` one first; screenshots and prints are already in
`workspace://files`. To hold both browsers at once, use two workspace names.

## Every tool

| Tool | Does |
|---|---|
| `open_session` | start a browser once per task, or come back to the one you had; `browser`, `width`/`height`, `url`, `capture`, `record`, `fresh` |
| `end_browser` | free the Grid slot; your workspace survives |
| `navigate` | go to a URL, when going there is the whole point |
| `interact` | `click`, `double_click`, `right_click`, `hover` (keeps a `:hover` menu open), `scroll_to` |
| `drag` | drag an element onto another (`to`) or by an offset |
| `write` | type into a field, `text` or a `secret` you never see; `submit` presses Enter |
| `press_key` | a key or combination: `Enter`, `Escape`, `Control+a`; not for scrolling |
| `extract` | an element's text and HTML |
| `outline` | what is on the page, with a checked selector each and what is in the way |
| `screenshot` | the viewport, one element or the whole page, kept with a link to share |
| `print` | the page as a PDF or HTML file |
| `upload_file` | attach `text`, base64 `content` or a workspace `file` to a file input |
| `frame` | go into an iframe or back out; the switch sticks |
| `dialog` | answer an alert, confirm or prompt |
| `resize` | change the window size |
| `execute_script` | JavaScript, for what no tool here does |
| `assert` | JavaScript that must return true, asked until it does |
| `save_site_data` | keep the cookies and storage of the sites you visited, after a confirmed sign-in |
| `network_body` | one captured response body, by id from `workspace://network` |
| `await_control` | a person has your browser: wait for the hand-back and hear what they did |
| `show` | draw a resource for the person, in hosts that render apps |
| `keep_file` | keep a screenshot, recording or download in Files |
| `save_flow` | save a sequence of steps under a name |
| `run_flow` | run a saved flow in one call; read its `status`, a failed run is not an error |
| `delete_flow` | delete one of your flows |

## Everything to read

| URI | Is |
|---|---|
| `workspace://current` | what you are holding |
| `workspace://files` | Files' kept files, each with a link, and the three folders below |
| `workspace://files/{name}` | one kept file |
| `workspace://files/screenshots` | saved screenshots not yet kept |
| `workspace://files/screenshots/{name}` | one of those |
| `workspace://files/recordings` | recordings not yet kept, each with a link that plays it |
| `workspace://files/recordings/{name}` | one of those, described; its link plays it |
| `workspace://files/downloads` | the browser's downloads |
| `workspace://files/downloads/{name}` | one of those, while the browser is open |
| `workspace://site-data` | the sites with saved cookies or storage, never a value |
| `workspace://site-data/{site}` | one site's saved data; httpOnly values are masked |
| `workspace://console` | what the page logged; needs `open_session(capture=true)` |
| `workspace://console{?level,pages}` | the same, narrowed: `?level=error`, `?pages=2` |
| `workspace://network` | the requests the page made, each with an id |
| `workspace://network{?filter,pages}` | the same, narrowed: `?filter=failed`, `?filter=xhr` |
| `workspace://network/{id}` | one request in full |
| `secret://secrets` | the secrets you may type, never their values |
| `flow://flows` | the saved flows you can run |
| `flow://flows/{name}` | one flow's parameters and steps |
| `flow://schema` | what a step may contain |
| `skill://selenium-flow/SKILL.md` | this page; its references are beside it |

`repair_flow` and `build_flow` are prompts for a *person* to pick in their
client, not tools. A failed run names the one that would help, so you can say
which.
