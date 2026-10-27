"""Tell the agent, on its own results, that a person has control of its browser.

`Tunnels.notice` knows (live view, ruling 35). This is the MCP half: a
middleware that adds the notice to a tool's result, or to its error. The HTTP
surface carries none, by design. The name is read the way `clients.caller()`
reads it, never from `ctx.session_id`, and a caller that names no workspace
hears nothing and keeps its own answer.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from mcp.types import TextContent

from ..live.tunnels import Tunnels
from . import clients


class Notices(Middleware):
    def __init__(self, tunnels: Tunnels):
        self.tunnels = tunnels

    def _notice(self) -> str | None:
        # Read at the call's end, so a call in flight when a person took over
        # carries it. Whatever stops the name being read is no reason to
        # change the call's answer.
        caller = clients.caller()
        if caller.refusal is not None or not caller.named:
            return None
        return self.tunnels.notice(caller.named)

    async def on_call_tool(self, context, call_next):
        try:
            result = await call_next(context)
        except ToolError as exc:
            notice = self._notice()
            if notice is None:
                raise
            raise ToolError(f"{exc}\n\n{notice}") from exc.__cause__
        notice = self._notice()
        if notice is not None:
            result.content.append(TextContent(type="text", text=notice))
        return result
