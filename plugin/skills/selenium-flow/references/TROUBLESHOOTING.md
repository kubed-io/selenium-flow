# When something goes wrong

Read the error first. When the element is on the page but cannot be used, the
failure says which of these it is and what to do about it: an ancestor is hidden
(a `:hover` menu looks like this, and the message names the visible element to
hover when it can identify one), something is on top of it (an overlay to
dismiss, an invisible cover, or a plain sibling), it has no size, it is outside
the viewport (`scroll_to`), or it is disabled. That sentence is the diagnosis;
the list below is for when there is no element at all.

## A timeout on an XPath you believe in

Read the URL in the error first. It says what was waited for, for how long, and
the page the browser was on when it gave up. A failed earlier step can leave the
browser somewhere you did not expect; if that URL is not the page you meant, the
selector was never the problem. Put that page on the call as `url=` and try
again.

Check where you are, cheaply:

```
extract(selector={"xpath": "//title"})
```

or read `workspace://current`, which reports the URL without touching the browser.

If the URL is right and the element still is not found, in order of likelihood:

1. **It is inside an iframe**, or you are already inside one and the element is
   not. Check `in_frame` on `workspace://current`: a frame switch sticks until
   something switches back. Use `frame(action="switch", ...)` to go in and
   `frame(action="default")` to come back.
2. **It has not rendered yet** and the wait was too short: raise `wait_timeout`.
3. **It is off-screen in a virtualised list**: scroll it into view first
   (`skill://selenium-flow/references/INTERACTION.md`).
4. **The XPath is brittle**: positional paths break on any layout change; match
   on an attribute or on visible text instead.

## A call failed and the page said why

A result may carry a `page` hint: `{"errors": 1, "failed_requests": 2, "see": ...}`.
It means the page itself reported errors, or requests failed, because of that
call, and a failed call carries the same thing as a sentence on its error. It is
often the real diagnosis: the click landed and the page threw.

1. Read what `see` names first: `workspace://console?level=error` for page
   errors, `workspace://network?filter=failed` for failed requests.
2. For a request, read `workspace://network/{id}` (headers, status, timing),
   then `network_body(id)` for the response itself.

Warnings and failed images are never hinted; they are in the buffers if you ask.
On Chrome a `fetch` whose body the page never reads reports its failure late or
never, so no hint: when a call looks wrong but says nothing, check
`workspace://network?filter=failed`, and `workspace://network` for a request
still without a status.
This needs capture on: `open_session(capture=true)`. It is not inherited: an
`open_session()` without it turns capture off and forgets what was captured, so
read the console and the network before you reopen.

## "name your workspace"

You called without naming one. Add `?workspace=<name>` to the server URL, or send
an `Sf-Workspace` header, whichever your client can set
(`skill://selenium-flow/references/WORKSPACES.md`). "name your workspace once"
means the request carried both; send one and drop the other. Neither is
transient, so retrying unchanged will not help.

## "no session is open in workspace …"

Only `open_session` opens a browser. Call it once, then carry on.

## Signed in yesterday, signed out today

A new browser starts empty unless you saved the old one's cookies and storage.
Sign in, `save_site_data()`, and the next browser comes back signed in. Saved
and still signed out? The site ended its own session:
`skill://selenium-flow/references/SITE_DATA.md`.

## A result says `reopened`

The browser was replaced: the Grid reaped it after it sat idle (`reaped`), or it
ended while you were working (`lost`). The new one is on the same page with the
same settings, and saved site data is back, but anything that lived only in the
old page is gone. Check the page and redo any unsaved step. The server keeps a
browser alive for `session.keep_alive` after your last call (15 minutes by
default). Between the reap and your next call, `workspace://current` shows
`live: false`.

## "unexpected alert open", or a null url and title

A native dialog is open and blocks reading the page, so the session looks dead
but is not: nothing needs reopening. The action that opened it tells you:

```
{"action": "click", "url": null, "title": null, "dialog": "Delete everything?"}
```

Answer it with `dialog`
(`skill://selenium-flow/references/INTERACTION.md`). Nothing else works until
you do.

## A blank or single-colour screenshot

The page had not finished rendering. Extract something from it first, which
waits, then capture:

```
extract(selector={"xpath": "//main"})
screenshot()
```

Over HTTP, a `bytes` value near zero is the same signal.

## A click that appears to do nothing

`click` returns the URL and title after the click. If they are unchanged, the
click either hit the wrong element or the page uses JavaScript that has not
settled. Confirm what you clicked:

```
extract(selector={"xpath": "//button[contains(., 'Submit')]"})
```

First read the click's `received_by` and `warning`. A deep or unpainted receiver
means the selected element got the click only by bubbling: select the inner
element you meant. A `double_click` or `right_click` presses whatever is on top,
and its `warning` says when that was something else.

Then `matched` and `index`. They appear only when the selector matched several,
and `index` (counting from 0) is the match that was used. Another match may be
the one you meant: narrow the selector.

When the click is refused because something is on top, the error says what; it
calls it an overlay only when it is fixed or stacked above the page, and you
dismiss that first.

## A hover that reported success and opened nothing

The server steps the pointer aside before a hover onto something it is already
inside, and says `nudged: true`, so a menu that stays shut is not that.

Do not check afterwards with `document.querySelectorAll(':hover')`: it comes
back empty while the `:hover` styling is applying. `outline` the menu, or check
the revealed element's computed `display`.

## The advice said hover and hovering did nothing

Then it is a menu that opens on **click**. `outline` says which: an entry's
`open_with` is `click` when the trigger carries `aria-expanded`, and the trigger
itself is in `revealed_by`. Click that.

When the error says a stylesheet couldn't be read, the hover is a guess. If
hovering does nothing, look for a control that clicks it open: `outline` the
area around it.

## A certificate error

The browser refuses a self-signed certificate, and the call fails with "the
site's certificate is not trusted: for a site with a self-signed certificate,
open_session(insecure=true); an insecure browser gets no saved site data". When
you know the site is served with one, reopen with `open_session(insecure=true)`.
It is remembered for the workspace, so do not use it for a site that does not
need it.

## Everything is slow or a session will not open

The Grid runs a small, fixed number of browsers, shared by every caller. If they
are all held, a new session waits for a slot. That is almost always abandoned
sessions from earlier runs. End what you own, on success, on failure and before
you give up on a task: a browser you leave open holds its slot until the Grid
reaps it.

An autoscaled Grid with no browsers running reports `"ready": false` and lists
no nodes, and starts a browser when a session is asked for. The first
`open_session` after a quiet spell can take a minute. Only a failing
`open_session` means something is wrong.

## No Grid node took this browser

`open_session` failed with *no Grid node took this browser before the Grid gave
up waiting: it asked for chrome 152, kubed:pool=large …*. The Grid queued the
request and no node took it before its own timeout (often 300 s).

The message lists what was asked for. The usual cause is one of them: a
`version` no node offers, or a capability no node class carries. Drop the odd
one (`version="latest"`, `capabilities={}`) and open again, or ask whoever runs
the Grid what it offers. If nothing unusual was asked for, every node was busy:
see *Everything is slow or a session will not open*.

## The Grid node refused the browser

A node took the request and could not start the browser.

- *the Grid node refused this browser: … The usual cause is what this open
  asked for: capabilities … and version …* is a 400. The node's own reason is
  quoted, and one of the things you asked for is the likely cause. Open again
  without it: `capabilities={}` forgets the raw ones, `version="latest"` the
  version. The same request will fail the same way.
- *the Grid node could not start this browser: … Nothing this open asked for is
  unusual, so trying again may succeed* is a 503. The node had trouble of its
  own, so try once more after a moment.

## Sending a capability no option covers

`open_session(capabilities={...})` sends extra W3C capabilities to the Grid as
given, best effort. Use it only when no option in
`skill://selenium-flow/references/WORKSPACES.md` (*Choosing the browser*) does
the job: a node class (`{"kubed:pool": "large"}`), a browser flag
(`{"goog:chromeOptions": {"args": ["--lang=fr"]}}`), a Firefox pref.

- It is remembered whole. Every later `open_session()` sends it again, and so
  does a reopen after the Grid reaps the browser. `capabilities={}` forgets it.
- A key an option controls is refused, before anything is ended, with the option
  to use instead: *browserName cannot be sent in capabilities: use browser=
  instead*. The same goes for `browserVersion` (`version=`),
  `acceptInsecureCerts` (`insecure=`), a headless flag in either browser's `args`
  or `MOZ_HEADLESS` in Firefox's `env` (`headless=`), `se:recordVideo` and
  `se:videoName` (`record=`), `timeouts` (`page_load_timeout=` /
  `script_timeout=`), and the three the server needs for itself:
  `unhandledPromptBehavior`, `se:downloadsEnabled`, `webSocketUrl`.
- Browser options are merged, not replaced. Your `args` are added to the
  server's own and your `prefs` beside them. Where both set the same pref, the
  server's wins, and the result's `ignored` names each of yours that lost. `args`
  must be a list of strings and `prefs` an object, or the open is refused.
- A key you send replaces the operator's default for the same key, which is how
  you pick another node class.

A remembered capability that a newer server controls itself is refused when the
browser is reopened, with *clear it with open_session(capabilities={})*: do
exactly that.
