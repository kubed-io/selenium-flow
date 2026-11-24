"""Tell the agent, on its own results, that a person has control of its browser.

A middleware adds `Controls.notice` to a tool's result, as text and as a
``control`` field in its structured content, or to its error. Claude Code shows
a model only the structured content of a result that has some, so a notice in
text alone is never seen on a success. The HTTP surface carries none. A caller
that names no workspace hears nothing.

Passed through untouched: a refusal (`faults.Controlled`), which says it
already; a task's stub (`CreateTaskResult`), which has no text to add to, so an
unsaid hand-back waits for the next result; and `await_control`, whose
``control`` the notice would replace, spending a hand-back it did not say.
"""

from __future__ import annotations

import anyio
from kubed.mcp import Middleware, TextContent, ToolError, ToolResult

from ..faults import Controlled
from ..live.control import Controls
from . import clients

# The tools whose answer is about control already.
SAYS_CONTROL = frozenset({"await_control"})


class Notices(Middleware):
    """``workspaces`` (`Workspaces.reading`, duck-typed) says which session a
    call is about: control covers one session (LIVE-42), so a result carries the
    notice of its own. Without it, the workspace's default."""

    def __init__(self, controls: Controls, workspaces=None):
        self.controls = controls
        self.workspaces = workspaces

    def key_for(self, caller, named: str | None) -> str | None:
        """The key of the session ``caller`` reaches, naming ``named`` or its
        pin; None for a name that cannot be read or a session that does not
        exist (a refused call says that itself). Reads the store, so off the loop."""
        if caller.refusal is not None or not caller.named:
            return None
        if self.workspaces is None:
            return caller.named
        try:
            return self.workspaces.reading(caller, named)
        except Exception:  # noqa: BLE001 - the call said why, or will
            return None

    def notice_for(self, caller, named: str | None) -> dict | None:
        key = self.key_for(caller, named)
        return None if key is None else self.controls.notice(key)

    async def _notice(self, context) -> dict | None:
        # Read at the call's end, so a call in flight when a person asked
        # carries it.
        if self.controls.quiet():
            return None
        arguments = context.message.arguments or {}
        named = arguments.get("session")
        key = await anyio.to_thread.run_sync(
            self.key_for, clients.caller(), named if isinstance(named, str) else None
        )
        # On the loop, where the episodes live.
        return None if key is None else self.controls.notice(key)

    async def on_call_tool(self, context, call_next):
        if context.message.name in SAYS_CONTROL:
            return await call_next(context)
        try:
            result = await call_next(context)
        except ToolError as exc:
            if isinstance(exc.__cause__, Controlled):
                raise  # the refusal is the notice
            notice = await self._notice(context)
            if notice is None:
                raise
            raise ToolError(f"{exc}\n\n{notice['hint']}") from exc.__cause__
        if not isinstance(result, ToolResult):
            return result  # a task's stub: nothing to say it on
        notice = await self._notice(context)
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
