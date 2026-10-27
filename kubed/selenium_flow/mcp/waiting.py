"""A tool that waits: as an MCP task where the client can, as a capped blocking
call with progress where it cannot (live control spec, R4-R6, rulings 8 and 15).

`await_control` is the first; the next long-running tool wraps the same helper.
Three things make a waiting tool, and all three are here or in `clients`:

- **the wait itself**, `waited`: the tool's body is ``work(cap)``, an awaitable
  that answers by itself at ``cap`` seconds - "call again" - or, with ``cap``
  None, only when it is done. In a background task the cap is off: a client
  polling a task is not holding a request open, and nothing times it out;
- **the heartbeat**: a progress notification at once and every
  `progress.HEARTBEAT` (reused, not copied). On a blocking call it keeps the
  client's idle clock from running out; in a task it is the ``statusMessage``
  a ``tasks/get`` reads;
- **the workspace, captured at submission**: the worker has no request, so
  ``?workspace=`` never reaches it. `clients.Carry` puts the caller's name in
  a private header the worker's snapshot keeps (R3, ruling 31).

A task tool must be ``async``: ``task=`` on a sync function is refused at
decoration (R6), and every other tool here is sync.

**Docket stays in memory** (ruling 9), named in code so a stray
``FASTMCP_DOCKET_URL`` cannot move it, and `Tasks` stops it only after the
server's waiters are let go: Docket's worker waits for the tasks it runs when it
stops, and a waiter nobody releases would hold the stop for ever.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TypeVar

import anyio
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension

from . import progress

log = logging.getLogger(__name__)

T = TypeVar("T")

# R4: the client polls a task, nothing is pushed, and the default is 5 s.
TASK = TaskConfig(mode="optional", poll_interval=timedelta(seconds=1))
MEMORY = "memory://"


class Tasks(TasksExtension):
    """FastMCP's tasks extension, Docket in memory, with ``first`` run before
    its worker stops.

    FastMCP enters the server's own lifespan first and the extensions' after
    it, so it leaves them in the reverse order: Docket's worker stops, waiting
    for every task it runs, before the server's lifespan has let anything go.
    ``first`` is that letting go (`Controls.stop`), moved in front.
    """

    def __init__(self, *, first: Callable[[], None]):
        super().__init__(url=MEMORY)
        self._first = first

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        async with super().lifespan():
            try:
                yield
            finally:
                self._first()


async def _beat(ctx, cap: float | None, message: Callable[[], str]) -> None:
    started = time.monotonic()
    last = -1.0
    while True:
        # Strictly rising, as the protocol requires.
        value = max(time.monotonic() - started, last + 1e-6)
        try:
            await ctx.report_progress(value, cap, message())
        except Exception:
            log.debug("progress notification not sent", exc_info=True)
        last = value
        await anyio.sleep(progress.HEARTBEAT)


async def waited(
    ctx,
    work: Callable[[float | None], Awaitable[T]],
    *,
    cap: float,
    message: Callable[[], str],
) -> T:
    """``await work(cap)``, reporting ``message()`` until it answers.

    ``cap`` is None in a background task (``ctx.is_background_task``), else as
    given. With no ``ctx`` - a call from outside a request - nothing is
    reported. Cancelled, the work is cancelled with it.
    """
    background = ctx is not None and ctx.is_background_task
    limit = None if background else cap
    failure: BaseException | None = None
    result = None
    async with anyio.create_task_group() as group:
        if ctx is not None:
            group.start_soon(_beat, ctx, limit, message)
        try:
            result = await work(limit)
        except Exception as exc:  # noqa: BLE001 - re-raised below, unwrapped
            # Raised outside the group, or the caller gets an ExceptionGroup
            # instead of the failure `errors` knows how to read.
            failure = exc
        finally:
            group.cancel_scope.cancel()
    if failure is not None:
        raise failure
    return result
