# Watching and taking over a browser

The Live tab shows a session's browser screen as it is now. A person watches an
agent work there, and takes the browser over to sign in, solve a captcha or fix
something by hand. Only a person in this tab holds a browser: no tool, route or
flow takes control. An agent driving this page in its own browser could click
**Control**, and AGENT_AS_ADMIN.md forbids it; an agent waits for control with
`await_control`.

## Opening it

Live is disabled, with a reason in its title, when the server's live view is
switched off, when the Grid is not answering, when the session has no browser,
when the browser is headless, and when the Grid gives no screen for it. With
sub-sessions, pick the session's chip first: control covers one session's
browser, and the others keep working.

The tab opens in **Watch** (👁️): the screen moves, and nothing the person does
reaches the browser. A view nobody looks at closes after `live.grace` seconds
(30 by default) with `nobody was watching`; **Reconnect** opens it again.

## Taking control

1. Click **Control** (🖱️). The request is granted once the agent's calls that
   were already waiting have finished, so a click or a page load in flight is
   never cut off. Until then the request waits.
2. Click and type in the screen. Every call of the agent's that would change
   that browser is refused with a sentence naming the person and the time, and
   the agent waits in `await_control`. Its reads still answer.
3. **Paste** (📋), in Control only, types text at the element that has focus.
   Click into the field first. It types up to 10,000 characters, a trailing
   newline dropped, and is refused while the agent is working inside a frame.
4. Click **Watch** to hand the browser back. Closing the tab hands it back too,
   so does a hidden tab once the grace runs out, and so does the browser ending.

Several people can ask at once; the browser is handed back when the last of
them goes back to Watch or leaves. Control has no time limit.

## What the agent is told

When the person hands back, the agent's waiting `await_control` returns with
the pages the person reached, oldest first, at most 100, and whether the
browser ended under them. The agent is told once, and decides from that what
to do next, so a person who signed in or moved the page on need not explain.

Pixels are not scrubbed: the screen shows whatever a field displays, a secret's
value included in any field that is not a password field.
