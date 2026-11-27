# A person in control

What to do while a person holds your browser. The person's side, the admin
page's Live tab, is in the `using-the-admin-ui` skill, row "A person watching or taking
over a browser in Live".

## When a person takes control

A person can take your browser from the admin's live view, to sign in, solve a
captcha or fix something by hand. While they hold it, every call that would
change the browser is refused with one sentence (over HTTP, a 409):

> alex has control of this browser since 14:02 UTC. Call await_control (POST /browser/await-control), which returns when they hand back.

If they have only asked, it reads `alex asked for control of this browser at
14:02 UTC. …` and ends the same way. A person the server has no name for is "a
person". Do not retry the refused call or try another tool: every tool that acts
is refused the same way. Call `await_control()`.

While control lasts, every successful MCP result carries a `control` field,
`{"state", "who", "since", "hint"}`. A call that fails for another reason carries
the `hint` as text after its error.

| `state` | Means | Do |
|---|---|---|
| `requested` | a person asked while this call ran; the browser is theirs once it ends | call `await_control` next |
| `held` | a person has it now; this call was a read, which still answers | call `await_control` before anything that acts |
| `handed_back` | `control of this browser was handed back at 14:20 UTC`, since your last result; said once | call `await_control` for what they did, or look at the page first |

Reads still answer: `extract`, `outline`, `screenshot`, `print`,
`network_body`, every resource, the flow library and `keep_file`. A read that
would move the page or the window is refused: a `url` the browser is not on, or a
`screenshot` with `width`, `height` or `full_page`. `execute_script`, `assert`,
`open_session` and `end_browser` count as acting.

`await_control()` returns when they hand back, or at once with nobody in control
(`control: null`, or a hand-back you had not been told of). A client that runs
MCP background tasks waits as long as it takes. Any other gets an answer after
about 90 seconds with `handed_back: false` and `still in alex's control; call again.`
Call it again until it hands back.

What it returns that needs reading:

| Field | Holds |
|---|---|
| `handed_back` | false at the 90-second cap, with nobody in control, or when the server is stopping (`message` says to call again once it is back) |
| `visited` | every page they reached, oldest first: the last whole, earlier ones as origin, path and hash route; at most 100, with `dropped` counting the rest. Absent when their pages could not be followed |
| `ended` | the browser ended while they had it: call `open_session` |
| `page_error` | stands in for `page` and `outline` when the page could not be read |

Read the summary before you carry on. The person may have signed in, moved
further on or gone to another site.

A flow stops at a refused step, whatever its `onError` says, and `await_control`
is not a step: call it yourself, then decide from its summary whether to run the
flow again.
