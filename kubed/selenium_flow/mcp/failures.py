"""What a failed tool call says, to the caller and to the log.

`errors.py` is the one place that decides what a failure means, and the HTTP
surface has always asked it. The MCP surface never did: FastMCP answered with
`str(exc)` — Selenium's `Message:` prefix and native stack dump included — and
logged a full traceback for every caller's mistake, the same as for our own
faults. So a refused `save_flow` filled the pod log with frames, and the
credential scrub #36 added reached HTTP callers only.

Two mechanisms, because FastMCP decides two things in two places:

- **the answer** is rewritten by `Explained`, a middleware, which sees the
  failure after FastMCP has wrapped it;
- **the log line** is written *inside* FastMCP before any middleware runs, so it
  is rewritten by `QuietCallerMistakes`, a filter on that logger.

A refused call gets a sentence naming the shape it wanted. `save_flow` already
refuses the same mistakes in sentences, because a step's arguments *are* a tool
call's arguments, so this asks `flows.document.argument_problems` rather than
growing a second vocabulary — and only if that finds nothing falls back to
pydantic's messages without their type codes and documentation links. A pilot
spent six calls on `selector` reading those (§F2.15).
"""

from __future__ import annotations

import logging

from fastmcp.exceptions import (
    FastMCPError,
    NotFoundError,
    ResourceError,
    ToolError,
    ValidationError,
)
from fastmcp.server.middleware import Middleware
from pydantic import ValidationError as PydanticValidationError

from .. import errors
from ..flows.document import argument_problems

# Where FastMCP logs a failed call or read, and the words it opens each with.
FASTMCP_LOGGER = "fastmcp.server.server"
FAILED_CALL = "Error calling tool"
FAILED_READ = "Error reading resource"


def _pydantic_lines(exc: Exception) -> list[str]:
    cause = exc.__cause__
    if not isinstance(cause, PydanticValidationError):
        return [str(exc)]
    lines = []
    for error in cause.errors(include_url=False):
        where = ".".join(str(part) for part in error.get("loc") or ())
        lines.append(f"{where}: {error.get('msg')}" if where else str(error.get("msg")))
    return lines


def explain(tool: str, arguments: dict, schema: dict, exc: Exception) -> str:
    """The sentence a call refused for its arguments is answered with."""
    problems = argument_problems(tool, arguments or {}, schema or {})
    problems = problems or _pydantic_lines(exc)
    if len(problems) == 1:
        return f"{tool} refused its arguments: {problems[0]}"
    return f"{tool} refused its arguments:\n" + "\n".join(f"- {p}" for p in problems)


class Explained(Middleware):
    """Answer a failed call in `errors`' words, and a refused one with its shape."""

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        try:
            return await call_next(context)
        except ValidationError as exc:
            arguments = context.message.arguments or {}
            fastmcp = context.fastmcp_context
            server = fastmcp.fastmcp if fastmcp else None
            tool = await server.get_tool(name) if server is not None else None
            schema = tool.parameters if tool is not None else {}
            raise ToolError(explain(name, arguments, schema, exc)) from None
        except ToolError as exc:
            # FastMCP wraps anything a tool raised as "Error calling tool
            # 'x': <str(exc)>". A ToolError with no cause, or one caused by
            # FastMCP itself, was already written for a caller.
            cause = exc.__cause__
            if cause is None or isinstance(cause, FastMCPError):
                raise
            said = errors.message(cause)
            raise ToolError(f"{FAILED_CALL} {name!r}: {said}") from cause

    async def on_read_resource(self, context, call_next):
        """A read's failure, in `errors`' words — the same rule as a call's.

        FastMCP wraps whatever a resource raised as "Error reading resource
        'uri': <str(exc)>", and a Grid failure quotes the Grid's own URL in that
        text. `read_resource` reads through this too, so the tool and a client's
        own resource reader get the same sentence (#39's live check).
        """
        try:
            return await call_next(context)
        except NotFoundError as exc:
            # "Unknown resource: '<uri>'" quotes the caller's URI too.
            raise NotFoundError(errors.without_userinfo(str(exc))) from None
        except ResourceError as exc:
            cause = exc.__cause__
            if cause is None or isinstance(cause, FastMCPError):
                raise
            # The URI is the caller's, and a caller can put credentials in one;
            # it is scrubbed on the same terms as the exception (Copilot, #40).
            uri = errors.without_userinfo(str(getattr(context.message, "uri", "")))
            said = errors.message(cause)
            raise ResourceError(f"{FAILED_READ} {uri!r}: {said}") from cause


class QuietCallerMistakes(logging.Filter):
    """One warning line for a caller's mistake; a scrubbed traceback for ours.

    Decided by `errors.status_for`, the same test that makes one a 400 and the
    other a 500 over HTTP.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if exc is None or not str(record.msg).startswith((FAILED_CALL, FAILED_READ)):
            return True
        # FastMCP wrote this line before any middleware ran, so it quotes the
        # requested URI as sent — scrubbed here, whichever branch it takes.
        said = errors.without_userinfo(record.getMessage())
        if errors.status_for(exc) < 500:
            record.levelno, record.levelname = logging.WARNING, "WARNING"
            record.msg = f"{said}: {errors.message(exc)}"
        else:
            record.msg = f"{said}\n{errors.formatted(exc)}"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


def install(mcp) -> None:
    """Both halves, on ``mcp`` and on FastMCP's logger — the latter once."""
    mcp.add_middleware(Explained())
    logger = logging.getLogger(FASTMCP_LOGGER)
    if not any(isinstance(f, QuietCallerMistakes) for f in logger.filters):
        logger.addFilter(QuietCallerMistakes())
