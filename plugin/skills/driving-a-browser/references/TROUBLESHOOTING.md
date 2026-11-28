# When something goes wrong

Read the error first. When the element is on the page but cannot be used, the
error says why and what to do: a hidden ancestor (a closed `:hover` menu, with
the element to hover when it can name one), something on top (an overlay to
dismiss, an invisible cover or a sibling), no size, outside the viewport
(`scroll_to`), or disabled. The sections below cover the rest.

## A timeout on a selector you believe in

The error names what it waited for, for how long, and the page the browser was
on. If that is not the page you meant, the selector was never the problem: put
the page on the call as `url=` and try again. `workspace://current` also
reports the URL (`session://<name>` in a named session).

If the page is right, in order of likelihood:

1. The element is inside an iframe, or you are inside one and it is not. Check
   `in_frame` on `workspace://current` (`session://<name>` in a named session);
   a `frame` switch sticks until something switches back (`references/INTERACTION.md`,
   *Iframes*).
2. It has not rendered yet: raise `wait_timeout`.
3. It is off-screen in a virtualised list: scroll it into view first.
4. The XPath is positional and the layout changed: match an attribute or
   visible text instead.

## A result has a `page` hint

`"page": {"errors": 1, "failed_requests": 2, "see": ...}` means the page reported
errors, or requests failed, because of that call. A failed call carries the same
as a sentence on its error. It is often the real diagnosis: the click landed and
the page threw.

1. Read what `see` names: `workspace://console?level=error` or
   `workspace://network?filter=failed`. For a call in a named session it
   already names it: `session://<name>/console?level=error`.
2. For a request, read `workspace://network/{id}` (`session://<name>/network/{id}`),
   then `network_body(id)` for the response, with the same `session`.

Warnings and failed images are never hinted, but they are in the buffers. On
Chrome a `fetch` whose body the page never reads reports its failure late or
never, so when a call looks wrong and says nothing, check
`workspace://network?filter=failed`, and `workspace://network` for a request
with no status yet; in a named session, `session://<name>/network`.

All of this needs `open_session(capture=true)`. A new browser opened without it
captures nothing and forgets what was captured, so read the console and network
before you reopen. An `open_session()` that joins your live browser keeps its
capture.

## "name your workspace"

The call named no workspace. Add `?workspace=<name>` to the server URL or send
an `Sf-Workspace` header (`references/WORKSPACES.md`). "name your workspace once" means the
request carried both: drop one. Retrying unchanged does not help.

## "no session is open in workspace …"

Call `open_session` once, then carry on.

## "that session is held by another caller"

You named a session another caller opened, so it is a 403 and nothing was
opened. Name your own, or leave `session` out to act in your own session
(`references/WORKSPACES.md`). Retrying unchanged does not help.

## "this workspace's config is invalid"

Every call that names the workspace is refused, `workspace://current` included,
until an admin fixes its `workspace.yaml`. Do not retry. Tell the person the
reason and the link the refusal gives, and offer the corrected YAML, as
the `using-the-admin-ui` skill, row "an invalid config", shows.

## A result says `reopened`

The Grid replaced the browser. It is back on the same page with the same settings
and saved site data, and anything only the old page held is gone. Check the page
and redo any unsaved step (`references/WORKSPACES.md`).

## "unexpected alert open", or a null url and title

A native dialog is open. The session is fine and nothing needs reopening. Answer
it with `dialog` (`references/INTERACTION.md`, *Native dialogs*); nothing else works until
you do.

## A blank or single-colour screenshot

The page had not finished rendering. Extract something first, which waits, then
capture:

```
extract(selector={"xpath": "//main"})
screenshot()
```

## A click that appears to do nothing

A click returns the URL and title after it. When they have not changed:

1. Read `received_by` and `warning`. A deep or unpainted receiver means the
   element you selected got the click only by bubbling: select the inner element
   you meant. A `double_click` or `right_click` presses whatever is on top, and
   its `warning` says when that was something else.
2. Read `matched` and `index`. They appear only when the selector matched
   several, and `index` (from 0) is the one used. Narrow the selector if another
   was meant.
3. Confirm what you clicked with `extract` on the same selector.

When the click is refused because something is on top, the error names it, and
calls it an overlay only when it is fixed or stacked above the page: dismiss
that first.

## A hover that opened nothing

The server already steps the pointer aside for a hover onto an element it is
inside (`nudged: true`), so that is not the cause. The menu probably opens on a
click: `outline` it, and an entry with `open_with: "click"` names the trigger in
`revealed_by`. Click that. When the error says a stylesheet could not be read,
its hover advice is a guess, so look for a control that clicks it open.

To check whether a menu opened, `outline` it or read the revealed element's
computed `display`. `document.querySelectorAll(':hover')` comes back empty even
while the hover styling applies.

## A certificate error

The call fails with "the site's certificate is not trusted". When you know the
site has a self-signed certificate, reopen with `open_session(insecure=true)`.
It is remembered for the workspace, and an insecure browser gets no saved site
data, so use it only for that site.

## Everything is slow, or a session will not open

The Grid runs a few browsers shared by every caller, and a new session waits for
a free slot. Abandoned sessions are almost always the cause, so end what you own
on success, on failure and before giving up.

An autoscaled Grid with no browsers running starts one when asked, so the first
`open_session` after a quiet spell can take a minute. Only a failing
`open_session` means something is wrong.

## No Grid node took this browser

The open failed with *no Grid node took this browser before the Grid gave up
waiting: it asked for chrome 152, kubed:pool=large …*. The Grid queued the
request and no node took it before its own timeout (often 300 seconds).

The message lists what was asked for, and the usual cause is one of them: a
`version` no node offers, or a capability no node class carries. Drop the odd one
(`version="latest"`, `capabilities={}`) and open again, or ask whoever runs the
Grid what it offers. If nothing unusual was asked for, every node was busy (see
the section above).

## The Grid node refused the browser

- *the Grid node refused this browser: … The usual cause is what this open asked
  for: …* is a 400. One of the things you asked for is the likely cause, and the
  same request fails the same way. Open again without it: `capabilities={}`
  forgets the raw capabilities, `version="latest"` the version.
- *the Grid node could not start this browser: … Nothing this open asked for is
  unusual, so trying again may succeed* is a 503. Try once more after a moment.

## Sending a capability no option covers

`open_session(capabilities={...})` sends extra W3C capabilities to the Grid as
given. Use it only when no `open_session` option does the job: a node class
(`{"kubed:pool": "large"}`), a browser flag
(`{"goog:chromeOptions": {"args": ["--lang=fr"]}}`), a Firefox pref.

- It is remembered whole and sent by every later open, a reopen after a reap
  included. `capabilities={}` forgets it.
- A key an option controls is refused before anything is ended, naming the
  option to use: `browserName` (`browser=`), `browserVersion` (`version=`),
  `acceptInsecureCerts` (`insecure=`), a headless flag in `args` or
  `MOZ_HEADLESS` in Firefox's `env` (`headless=`), `se:recordVideo` and
  `se:videoName` (`record=`), `timeouts` (`page_load_timeout=`,
  `script_timeout=`). The server keeps `unhandledPromptBehavior`,
  `se:downloadsEnabled` and `webSocketUrl` for itself.
- Your `args` are added to the server's and your `prefs` set beside its own.
  Where both set one pref, the server's wins, and the result's `ignored` names
  each of yours that lost. `args` must be a list of strings and `prefs` an
  object.
- A key you send replaces the operator's default for that key, which is how you
  pick another node class.
- A remembered capability that a newer server now controls is refused at the
  next open with *clear it with open_session(capabilities={})*: do that.
