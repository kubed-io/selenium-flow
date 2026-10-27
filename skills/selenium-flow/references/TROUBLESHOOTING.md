# When something goes wrong

**Read the error first.** When the element is on the page but cannot be used,
the failure says which of these it is and what to do about it: an ancestor is
hidden — a `:hover` menu looks exactly like this, and the message names the
*visible* element to hover when it can identify one, because nothing with
`display: none` can receive a pointer — something is on top of it (dismiss the
banner), it has no size, it is outside the viewport (`scroll_to`), or it is
disabled. That sentence is the diagnosis; the list below
is for when there is no element at all.

## A timeout on an XPath you believe in

**Read the URL in the error first.** It says what was waited for, for how long,
and the page the browser was on when it gave up. The browser is persistent, so
a failed earlier step can leave it somewhere you did not expect; if that URL is
not the page you meant, the selector was never the problem. Put that page on the
call as `url=` and try again, rather than rewriting the selector.

Check where you are, cheaply:

```
extract(selector={"xpath": "//title"})
```

or read `workspace://current`, which reports the URL without touching the browser.

If the URL is right and the element still is not found, in order of likelihood:

1. **It is inside an iframe** — or you are *already* inside one and the element
   is not. Check `in_frame` on `workspace://current`: a frame switch sticks until
   something switches back, so a locator on the main page fails while you are
   still in a frame. Use `frame(action="switch", ...)` to go in and
   `frame(action="default")` to come back.
2. **It has not rendered yet** and the wait was too short — raise `wait_timeout`.
3. **It is off-screen in a virtualised list** — scroll it into view first
   (`skill://selenium-flow/references/INTERACTION.md`).
4. **The XPath is brittle** — positional paths break on any layout change; match
   on an attribute or on visible text instead.

## A call failed and the page said why

A result may carry a `page` hint: `{"errors": 1, "failed_requests": 2, "see": ...}`.
It means the page itself reported errors, or requests failed, *because of that
call* - and a failed call carries the same thing as a sentence on its error.
It is often the real diagnosis: the click landed and the page threw.

1. Read what `see` names first: `workspace://console?level=error` for page
   errors, `workspace://network?filter=failed` for failed requests.
2. For a request, read `workspace://network/{id}` (headers, status, timing),
   then `network_body(id)` for the response itself.

Warnings and failed images are never hinted; they are in the buffers if you ask.
On Chrome a `fetch` whose body the page never reads reports its failure late or
never, so no hint: when a call looks wrong but says nothing, check
`workspace://network?filter=failed`, and `workspace://network` for a request
still without a status.
This needs capture on: `open_session(capture=true)`. It is **not inherited** -
an `open_session()` without it turns capture off **and forgets what was
captured**, so read the console and the network before you reopen.
`capturing: false` in `workspace://current` means capture is paused until the
next call.

## "name your workspace"

You called without naming one. Add `?workspace=<name>` to the server URL, or send
an `X-Workspace` header — whichever your client can set. Any name of letters,
digits, dots, dashes and underscores is fine except `stdio` and `global`, which
are reserved. The same name always comes back to the same workspace.

**"name your workspace once"** is the other half: the request carried *both*, and
two names is two ideas about who is calling. Send whichever one you control and
drop the other.

Neither is transient; retrying unchanged will not help. There is no `session_id`
to pass on any call — see `skill://selenium-flow/references/WORKSPACES.md`.

## "no session is open in workspace …"

Nothing opens a browser implicitly — `open_session` is the only place that
happens, because it is the only place window size and timeouts can be chosen.
Call it once, then carry on.

## Signed in yesterday, signed out today

A new browser starts empty unless you saved the old one's cookies and storage.
Sign in, `save_site_data()`, and the next browser comes back signed in. Saved
and still signed out? The site ended its own session:
`skill://selenium-flow/references/SITE_DATA.md`.

## An invalid or unknown session

The Grid reaped the browser. Nothing to do: your workspace survives it, and the
next call opens a new session and returns to the last URL. Just retry.

`workspace://current` shows `live: false` in the meantime.

## "unexpected alert open", or a null url and title

A native dialog is open, and it blocks reading the page. The action that opened
it will have told you so:

```
{"action": "click", "url": null, "title": null, "dialog": "Delete everything?"}
```

Answer it with `dialog` — `accept`, `dismiss`, or `send_text` for a prompt — and
carry on. See `skill://selenium-flow/references/INTERACTION.md`. Nothing else will work until you do.

## A dialog does not mean the session died

An open dialog blocks most commands, including the one that checks whether a
session is still alive. That is a *blocked* session, not a gone one — answer the
dialog and carry on with the same browser. Nothing needs reopening.

## A blank or single-colour screenshot

The page had not finished rendering. Force a wait by extracting something from
it first, then capture:

```
extract(selector={"xpath": "//main"})
screenshot()
```

Over HTTP, a `bytes` value near zero is the same signal.

## A click that appears to do nothing

`click` returns the URL and title *after* the click. If they are unchanged, the
click either hit the wrong element or the page uses JavaScript that has not
settled. Confirm what you actually clicked:

```
extract(selector={"xpath": "//button[contains(., 'Submit')]"})
```

If an overlay or cookie banner is intercepting the click, dismiss it first —
they are the most common cause of a click that lands on nothing.

## A hover that reported success and opened nothing

The server steps the pointer aside before a hover onto something it is already
inside, and says `nudged: true`, so a menu that stays shut is not that.

Do not try to check afterwards with `document.querySelectorAll(':hover')` — it
comes back **empty** while the `:hover` styling is plainly applying. Read the
thing you wanted instead: `outline` the menu, or check the revealed element's
computed `display`.

## The advice said hover and hovering did nothing

Then it is a menu that opens on **click**. `outline` says which: an entry's
`open_with` is `click` when the trigger carries `aria-expanded`, and the trigger
itself is in `revealed_by`. Click that.

## A certificate error

The browser refuses a self-signed certificate. When you know the site is served
with one, reopen with `open_session(insecure=true)`. It is remembered for the
workspace, so do not use it for a site that does not need it.

## Everything is slow or a session will not open

The Grid runs a small, fixed number of browsers. If they are all held, a new
session waits for a slot. That is almost always abandoned sessions from earlier
runs, not load. End what you own, and remember that failure paths need to
reach `end_browser` too.

**`"ready": false` with no nodes is not a broken Grid** when the Grid scales
itself — KEDA on Kubernetes, say. An idle autoscaled Grid runs no browsers at
all, so its status says it is not ready and lists no nodes, and it starts a
browser when a session is asked for. The first `open_session` after a quiet spell
can take a minute while one boots. Only a failing `open_session` means something
is wrong.

## No Grid node took this browser

`open_session` failed with *no Grid node took this browser before the Grid gave
up waiting: it asked for chrome 152, kubed:pool=headless …*. The Grid queued the
request and no node took it before its own timeout (often 300 s). The server
waits exactly as long as the Grid does, so a node that was still booting had
its chance.

The message lists what was asked for. The usual cause is one of them: a
`version` no node offers, or a capability no node class carries. Drop the odd
one (`version="latest"`, `capabilities={}`) and open again, or ask whoever runs
the Grid what it offers; its console lists the nodes that are up. If nothing
unusual was asked for, every node was busy: see *Running out of slots*.

## The Grid node refused the browser

A node took the request and could not start the browser.

- *the Grid node refused this browser: … The usual cause is what this open
  asked for: capabilities … and version …* is a **400**. The node's own reason is
  quoted, and one of the things you asked for is the likely cause. Open again
  without it: `capabilities={}` forgets the raw ones, `version="latest"` the
  version. Sending the same request again will fail the same way.
- *the Grid node could not start this browser: … Nothing this open asked for is
  unusual, so trying again may succeed* is a **503**. The node had trouble of
  its own, so try once more after a moment.

## Sending a capability no option covers

`open_session(capabilities={...})` sends extra W3C capabilities to the Grid as
given, best effort. Use it only when no option in
`skill://selenium-flow/references/WORKSPACES.md` (*Choosing the browser*) does
the job: a node class (`{"kubed:pool": "headless"}`), a browser flag
(`{"goog:chromeOptions": {"args": ["--lang=fr"]}}`), a Firefox pref.

- **It is remembered whole.** Every later `open_session()` sends it again, and
  so does a reopen after the Grid reaps the browser. `capabilities={}` forgets
  it.
- **A key an option controls is refused**, before anything is ended, with the
  option to use instead: *browserName cannot be sent in capabilities: use
  browser= instead*. The same goes for `browserVersion` (`version=`),
  `acceptInsecureCerts` (`insecure=`), `se:recordVideo` and `se:videoName`
  (`record=`), `timeouts` (`page_load_timeout=` / `script_timeout=`), and the
  three the server needs for itself: `unhandledPromptBehavior`,
  `se:downloadsEnabled`, `webSocketUrl`.
- **Browser options are merged, not replaced.** Your `args` are added to the
  server's own and your `prefs` beside them. Where both set the same pref, the
  server's wins, because its tools depend on it, and the result's `ignored`
  names each of yours that lost. `args` must be a list of strings and `prefs`
  an object, or the open is refused.
- **A key you send replaces the operator's default** for the same key, which
  is how you pick another node class.

A remembered capability that a newer server controls itself is refused when
the browser is reopened, with *clear it with open_session(capabilities={})*:
do exactly that.

## Running out of slots

The Grid's capacity is shared. An unclosed browser holds a slot until its idle
timeout expires, so the cost of forgetting to close is paid by whoever runs
next. Close in a finally-shaped way: on success, on failure, and before you give
up on a task.
