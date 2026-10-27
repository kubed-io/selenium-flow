"""Tell the agent, on its own results, that a person has control of its browser.

`Controls.notice` knows (live view, ruling 35; live control, ruling 5). This is
the MCP half: a middleware that adds the notice to a tool's result, as text and
as a ``control`` field in its structured content, or to its error. Claude Code
shows a model only the structured content of a result that has some, so a
notice in text alone is never seen on a success. The HTTP surface carries
none, by design (ruling 18). The name is read the way `clients.caller()` reads
it, never from `ctx.session_id`, and a caller that names no workspace hears
nothing and keeps its own answer.

A refusal (`faults.Controlled`) already says it, so it is answered as it is.
A call that became a task returns its stub (`CreateTaskResult`), which has no
text to add to: it passes untouched, and an unsaid hand-back stays unsaid for
the next result (R1).
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from ..faults import Controlled
from ..live.control import Controls
from . import clients


class Notices(Middleware):
    def __init__(self, controls: Controls):
        self.controls = controls

    def _notice(self) -> dict | None:
        # Read at the call's end, so a call in flight when a person asked
        # carries it. Whatever stops the name being read is no reason to
        # change the call's answer.
        caller = clients.caller()
        if caller.refusal is not None or not caller.named:
            return None
        return self.controls.notice(caller.named)

    async def on_call_tool(self, context, call_next):
        try:
            result = await call_next(context)
        except ToolError as exc:
            if isinstance(exc.__cause__, Controlled):
                raise  # the refusal is the notice
            notice = self._notice()
            if notice is None:
                raise
            raise ToolError(f"{exc}\n\n{notice['hint']}") from exc.__cause__
        if not isinstance(result, ToolResult):
            return result  # a task's stub: nothing to say it on (R1)
        notice = self._notice()
        if notice is None:
            return result
        # A new result rather than an edit: one made from a wire result answers
        # with that result whatever is changed on it.
        return ToolResult(
            content=[*result.content, TextContent(type="text", text=notice["hint"])],
            structured_content={**(result.structured_content or {}), "control": notice},
            meta=result.meta,
            is_error=result.is_error,
        )
