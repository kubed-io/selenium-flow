"""What a failed tool call says, to the caller and to the log, in `errors`' words.

FastMCP decides the two in two places:

- the answer is rewritten by `Explained`, a middleware, which sees the failure
  after FastMCP has wrapped it;
- the log line is written inside FastMCP before any middleware runs, so it is
  rewritten by `QuietCallerMistakes`, a filter on that logger.

A call refused for its arguments is answered by
`flows.document.argument_problems`, the sentences `save_flow` gives a step with
the same mistake, and only when that finds nothing by pydantic's messages
without their type codes and documentation links.
"""

from __future__ import annotations

import logging
import re

from fastmcp.exceptions import (
    FastMCPError,
    NotFoundError,
    ResourceError,
    ToolError,
    ValidationError,
)
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import ValidationError as PydanticValidationError

from .. import errors, faults
from ..flows.document import argument_problems
from ..names import retired_uri
from ..urls import without_userinfo

# Where FastMCP logs a failed call or read, and the words it opens each with.
FASTMCP_LOGGER = "fastmcp.server.server"
FAILED_CALL = "Error calling tool"
FAILED_READ = "Error reading resource"
# MCP's code for a resource that is not there (the spec's "Resource not
# found"); `mcp_types` names only the JSON-RPC ones.
RESOURCE_NOT_FOUND = -32002


# A `session://<name>…` URI: the name is a token's subject for a sub-session,
# so a log line shows `session://…`. `session://downloads…` is the caller's own.
SESSION_NAME = re.compile(r"session://(?!downloads(?![^/?#\s'\"]))[^/?#\s'\"]+")


def nameless(text: str) -> str:
    """``text`` with the name of any `session://<name>` URI in it masked."""
    return SESSION_NAME.sub("session://…", text)


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
            said = faults.message(cause)
            raise ToolError(f"{FAILED_CALL} {name!r}: {said}") from cause

    async def on_read_resource(self, context, call_next):
        """A read's failure, in `errors`' words, as a call's is.

        FastMCP wraps whatever a resource raised as "Error reading resource
        'uri': <str(exc)>", and a Grid failure quotes the Grid's own URL in that
        text. `read_resource` reads through this too.

        A caller's mistake (a 4xx over HTTP, such as ``?pages=9``) is invalid
        params (-32602), and one that names something not there (a 404) is
        MCP's resource not found (-32002). A `ResourceError` would go out as an
        internal error (-32603).
        """
        try:
            return await call_next(context)
        except NotFoundError as exc:
            # A URI that moved is answered with where it went, as
            # `read_resource` does. FastMCP words a NotFoundError itself, so
            # this is an MCPError.
            try:
                retired_uri(getattr(context.message, "uri", ""))
            except ValueError as moved:
                raise MCPError(
                    code=RESOURCE_NOT_FOUND, message=without_userinfo(str(moved))
                ) from None
            # "Unknown resource: '<uri>'" quotes the caller's URI too.
            raise NotFoundError(without_userinfo(str(exc))) from None
        except ResourceError as exc:
            cause = exc.__cause__
            if cause is None or isinstance(cause, FastMCPError):
                raise
            # A caller can put credentials in a URI.
            uri = without_userinfo(str(getattr(context.message, "uri", "")))
            said = f"{FAILED_READ} {uri!r}: {faults.message(cause)}"
            status = errors.status_for(cause)
            if status == 404:
                # A `session://` name with no session may be a URI from before
                # `session://{name}` read sessions: say where that one went.
                try:
                    retired_uri(getattr(context.message, "uri", ""))
                except ValueError as moved:
                    said = without_userinfo(str(moved))
                raise MCPError(code=RESOURCE_NOT_FOUND, message=said) from cause
            if status < 500:
                raise MCPError(code=INVALID_PARAMS, message=said) from cause
            raise ResourceError(said) from cause


class QuietCallerMistakes(logging.Filter):
    """One warning line for a caller's mistake; a scrubbed traceback for ours.

    Decided by `errors.status_for`, the same test that makes one a 400 or a 503
    (a warning) and the other a 500 (a traceback) over HTTP.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if exc is None or not str(record.msg).startswith((FAILED_CALL, FAILED_READ)):
            return True
        # FastMCP's line quotes the requested URI as sent.
        said = nameless(without_userinfo(record.getMessage()))
        if errors.status_for(exc) != 500:
            record.levelno, record.levelname = logging.WARNING, "WARNING"
            record.msg = f"{said}: {nameless(faults.message(exc))}"
        else:
            record.msg = f"{said}\n{nameless(faults.formatted(exc))}"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


def install(mcp) -> None:
    """Both halves, on ``mcp`` and on FastMCP's logger (once)."""
    mcp.add_middleware(Explained())
    logger = logging.getLogger(FASTMCP_LOGGER)
    if not any(isinstance(f, QuietCallerMistakes) for f in logger.filters):
        logger.addFilter(QuietCallerMistakes())
