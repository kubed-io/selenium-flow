---
name: selenium-flow
description: Drive a real Chrome or Firefox browser on Selenium Grid through the selenium-flow MCP server. Use when a task needs a live browser - logging in, filling and submitting a form, clicking through a multi-step flow, reading a page that only renders under JavaScript, capturing how something looks, checking a page in a second browser, saving a sequence to replay in one call, or logging in with a stored secret you are never shown. Start here to name your session, then read the one reference that matches what you are doing.
---

# Driving a browser with selenium-flow

The browser is **real and persistent**. It lives on Selenium Grid, not in the
server, and it keeps its page, cookies, storage and scroll position between your
calls. You are steering one tab, not sending independent requests.

Three consequences drive everything else:

- **State carries over.** Log in once and every later call is logged in. But a
  browser left on the wrong page makes your next XPath fail for a reason that
  has nothing to do with the XPath.
- **Slots are scarce.** The Grid runs a handful of browsers in total. An
  abandoned one holds its slot until it is reaped, so ending one is capacity,
  not politeness.
- **Your session is not your browser.** The session outlives it, keeping your
  browser choice and the page you were on. So a browser going away — reaped for
  being idle, ended by you, ended by an operator — is never something to recover
  from: `open_session()` with no arguments puts you back where you were.

## Step 0: name your session

Every session is named by whoever calls, and the name is the whole contract:
add `?session=<name>` to the server URL, or send an `X-Session-Key` header.
Sending both is an error. Over stdio you are named `stdio` already.

There is **no session id anywhere** — no tool takes one, no result carries one.
Call again with the same name and you get the same browser back, after a
reconnect or a restart.

Read `session://current` before your first action if you want to know what you
are holding. It reports the session name, the browser, the page and whether one
is open. `skill://selenium-flow/references/SESSIONS.md` has the rest.

**Everything here to read is a URI** — `session://`, `flow://`, `secret://`,
`skill://` — whether this page, a hint in a failed run or an error names it. Read
it the way your client reads MCP resources. If your client cannot, this server
gives you `read_resource(uri)` and `list_resources()` instead, for the same URIs.

**Either way, call `open_session` first.** Nothing opens a browser implicitly,
because `open_session` is the only place its browser, window size and timeouts
can be set.

## Chrome or Firefox

`open_session(browser="firefox")` opens Firefox; the default is Chrome. Every
other tool behaves identically on both, so this is the only call that changes —
reach for Firefox when the task is *about* Firefox, such as confirming a
rendering difference or a site that treats the two differently, and otherwise
leave it alone.

To switch, just call `open_session(browser="firefox")` again — the browser you
are holding is ended for you first, so do not close and reopen. **The files it
had go with it**: the Grid keeps a file store per browser and deletes it with
the browser. `keep_file(name)` copies one out first — a kept file belongs to
your session instead, so it survives switching, ending, and the Grid reaping an
idle browser. `session://files` lists both kinds and marks which is which.

One session holds one browser. To use both at once, use two session names;
`session://current` reports which browser the one you are holding is.

## If you are told you have no browser

That is an ordinary state, not an error to work around. The Grid expires idle
browsers, and an operator can end one — either way your session survives with
its context.

**Call `open_session()` with no arguments.** It comes back on the same browser,
the same window size, and the page you were last on. Pass arguments only to
change something.

## The five rules

**1. Read with `extract`, not `screenshot`.** An image of text costs orders of
magnitude more, cannot be quoted, and may be a picture of a half-rendered page.
Screenshot only when the *visual* is the answer.

**2. `url` is not an assertion.** Every action except open/close takes an
optional `url` and navigates there first if the browser is elsewhere. One call,
not two:

```
extract(url="https://example.com/settings", selector={"xpath": "//h1"})
```

**3. Find selectors with `outline`, not by reading HTML.** It lists what is on
the page with a checked selector for each, and says whether an element can be
used or what is in the way — a hidden ancestor, an overlay, no size, off-screen,
disabled. `extract` is for *content*.

**4. Address an element with one `selector`: `{"css": …}` or `{"xpath": …}`.**
`css` is shorter for ids, classes and attributes; `xpath` is the only one that
can match visible text (`//button[contains(., 'Save')]`) or walk up to an
ancestor. Giving both is an error rather than a preference — see
`skill://selenium-flow/references/READING_PAGES.md`.

**5. Always end the browser.** Including on failure paths. `end_browser()`
frees the slot; skipping it makes the next person wait. It ends the *browser*,
not your session — the session keeps your browser choice and last page, so this
costs you nothing.

## Where to go next

Load only what the task needs.

| Doing | Read |
|---|---|
| Naming a session, sharing one, or recovering a dead browser | `skill://selenium-flow/references/SESSIONS.md` |
| Getting content out of a page, choosing a selector | `skill://selenium-flow/references/READING_PAGES.md` |
| Clicking, hovering, typing, uploading, dialogs, scrolling, waiting | `skill://selenium-flow/references/INTERACTION.md` |
| A timeout, an empty screenshot, a click that did nothing | `skill://selenium-flow/references/TROUBLESHOOTING.md` |
| Setting the server up, connecting a client, which env var to change | `skill://selenium-flow/references/CONFIGURATION.md` |
| Doing a sequence you or another agent will repeat — save it once, run it in one call | `skill://selenium-flow/references/FLOWS.md` |
| Typing a password, token or anything else you must not see | `skill://selenium-flow/references/SECRETS.md` |

## A whole task, minimally

Name your session once in the URL or the header, then `open_session` once:

```
open_session(width=1400, height=900)
write(url="https://example.com/login", selector={"xpath": "//input[@name='email']"}, text="a@example.com")
write(selector={"xpath": "//input[@name='password']"},
      secret={"name": "example", "key": "password"})
interact(action="click", selector={"xpath": "//button[@type='submit']"})
extract(selector={"xpath": "//h1"})            # confirm you landed
end_browser()
```

**Never put a real password in `text`.** Name a secret instead and the server
types it without it ever passing through you — `secret://secrets` shows what
there is, and `skill://selenium-flow/references/SECRETS.md` covers the rest.

If you will do this again, save it as a flow and it becomes one `run_flow` call
(`skill://selenium-flow/references/FLOWS.md`).

## Every tool

One row per tool. If what you need is not here, it is `execute_script` — and
check this table twice before reaching for it.

| Tool | Does | Key arguments |
|---|---|---|
| `open_session` | start a browser, or come back to the one you had | `browser`: `chrome` \| `firefox`, `width`, `height`, `url`, `fresh` |
| `end_browser` | free the Grid slot; your session survives | — |
| `navigate` | go to a URL | `url` |
| `interact` | a mouse gesture on an element | `selector`, `action`: `click` \| `double_click` \| `right_click` \| `hover` \| `scroll_to`, `glide` |
| `drag` | drag an element onto another, or by an offset | `selector`, then `to` or `by_x`/`by_y`, `glide` |
| `write` | type into a field, or type a secret you never see | `selector`, `text` or `secret`, `clear`, `submit` |
| `press_key` | a key or a combination | `key`: `Enter`, `Escape`, `a`, `Control+a` |
| `extract` | read an element's text and HTML | `selector` |
| `outline` | what is on the page: selectors, and what works | `selector`, `text`, `limit`, `interactive` |
| `screenshot` | the viewport, one element, or the whole page — saved, with a link to share | `full_page`, `filename`, `save` |
| `save_pdf` | print the page into your files | `filename` |
| `upload_file` | attach a file to a file input | `text`, `content`, `kept` or `path`, `filename` |
| `frame` | move into or out of an iframe | `action`: `switch` \| `parent` \| `default` |
| `dialog` | answer an alert, confirm or prompt | `action`: `accept` \| `dismiss` \| `read` \| `send_text` |
| `resize` | change the window size | `width`, `height` |
| `execute_script` | run JavaScript — only for what nothing above does | `script` |
| `assert` | JavaScript that must return true, or the call fails | `script`, `message`, `stable_for` |
| `keep_file` | keep a file past the browser | `name` |
| `save_flow` | save a sequence of steps under a name | `name`, `parameters`, `steps`, `timeout` |
| `run_flow` | run a saved flow in one call | `name`, `params` |
| `delete_flow` | delete one of your flows | `name` |

And everything to read:

| URI | Is |
|---|---|
| `session://current` | what you are holding |
| `session://files` | what the browser downloaded and what you kept, each with a link |
| `session://files/{name}` | one of those files |
| `secret://secrets` | the secrets you may type — never their values |
| `flow://flows` | the saved flows you can run |
| `flow://flows/{name}` | one flow's parameters and steps |
| `flow://schema` | what a step may contain |
| `skill://selenium-flow/SKILL.md` | this page; its references are beside it |

Every tool that names an element waits for it in the background and carries on
the moment it appears. `wait_timeout` is only how long it may take, never a
pause.

**There are prompts too, and they are not for you.** `repair_flow` and
`build_flow` are templates a *person* picks in their client. You cannot invoke
one; when a run fails its report names the one that would repair it, so you can
say which to pick.

`hover` leaves the pointer where it put it, so a `:hover` menu stays open for the
next call — and it is the only way to open one. `press_key` is **not** a reliable
way to scroll; `execute_script` is. The detail is in `skill://selenium-flow/references/INTERACTION.md`.
