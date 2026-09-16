"""Reporting a long run while it runs, as MCP progress notifications.

A `tools/call` has exactly one answer, and nothing streams before it. What the
protocol does allow is `notifications/progress` on the same request while it is
still open, for a client that put a `progressToken` on the call. Two things
come of sending them:

- **the call stays alive.** Claude Code aborts an HTTP tool call that sends
  nothing for five minutes, and a flow whose job is to wait fifteen was reported
  as failed while it succeeded (pilot, §F2.15). Any notification resets that
  clock, which a spike against the real client confirmed before this was built.
- **a person can watch it.** A client that renders progress shows a bar and the
  step it is on. The model never sees any of it — it reads the final report.

So the ticker reports when the step changes *and* on a heartbeat inside a step,
because the run that was lost was one step long. It samples rather than queues:
steps that start and finish inside one look are coalesced into the next report,
so a bar jumps past them. That is deliberate — a notification per step of a
fast fifty-step flow is traffic nobody reads, and neither keeping a call alive
nor drawing a bar needs every one. A client that sent no token
gets nothing: `Context.report_progress` returns without sending.

This is an MCP concern only. `POST /flows/{name}/runs` answers once when the run
ends; streaming it would be server-sent events on that route, and nothing has
asked for it yet.
"""

from __future__ import annotations

import logging
import threading
import time

import anyio

log = logging.getLogger(__name__)

# Well inside the shortest idle window any client is known to use, and rare
# enough that a fifteen-minute wait is sixty notifications, not thousands.
HEARTBEAT = 15.0

# How often the ticker looks for a new step. A step change is reported at once
# rather than on the next heartbeat, so a bar moves as steps finish.
LOOK = 0.5

# Seconds into a step at which its sliver of the bar is half full. Progress has
# to rise with every notification, and a step's length is unknown, so the
# fraction approaches the next step without ever reaching it.
HALF = 30.0


class Watch:
    """Where a run is. Written by the worker thread, read by the ticker."""

    def __init__(self, total: int = 0):
        self.total = total
        self.number = 0
        self.summary = ""
        self.started = time.monotonic()
        self.stop = threading.Event()

    def step(self, entry: dict, total: int) -> None:
        """`flows.run`'s ``before_step``."""
        self.total = total
        self.summary = entry.get("summary") or str(entry.get("tool") or "")
        self.started = time.monotonic()
        # Last, so the ticker never sees a new number beside the old summary.
        self.number = int(entry.get("n") or 0)

    def progress(self, now: float) -> tuple[float, str]:
        """The bar's position and the line beside it, at ``now``."""
        if not self.number:
            return 0.0, "starting"
        elapsed = max(now - self.started, 0.0)
        fraction = elapsed / (elapsed + HALF)
        message = f"step {self.number}/{self.total}: {self.summary}"
        if elapsed >= HEARTBEAT:
            message += f" ({_duration(elapsed)})"
        return self.number - 1 + fraction, message


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


async def _tick(ctx, watch: Watch) -> None:
    sent_step = -1
    sent_at = 0.0
    last = -1.0
    while True:
        now = time.monotonic()
        if watch.number != sent_step or now - sent_at >= HEARTBEAT:
            value, message = watch.progress(now)
            # Strictly rising, as the protocol requires, even if two reads land
            # on the same instant.
            value = max(value, last + 1e-6)
            try:
                await ctx.report_progress(value, watch.total or None, message)
            except Exception:  # a lost notification is not a failed run
                log.debug("progress notification not sent", exc_info=True)
            sent_step, sent_at, last = watch.number, now, value
        await anyio.sleep(LOOK)


async def reporting(ctx, work, watch: Watch):
    """Run ``work()`` in a thread, reporting ``watch`` to ``ctx`` until it ends.

    The thread is abandoned on cancellation rather than waited for, because
    waiting would mean the cancelled coroutine could not set ``watch.stop``
    until the run it is meant to stop had finished on its own. Set, the run
    stops at its next step or its next poll, and `flows.run` reports why.

    With no ``ctx`` — a call from outside a request — nothing is reported, and
    cancellation still stops the run.
    """
    failure = None
    try:
        async with anyio.create_task_group() as group:
            if ctx is not None:
                group.start_soon(_tick, ctx, watch)
            try:
                result = await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
            except Exception as exc:  # noqa: BLE001 - re-raised below, unwrapped
                # Caught inside the group and raised outside it, or a task group
                # hands the caller an ExceptionGroup - and `errors` would see
                # that instead of the "no flow called ..." it knows how to read.
                # Cancellation is not an Exception and still propagates.
                failure = exc
            finally:
                group.cancel_scope.cancel()
    finally:
        watch.stop.set()
    if failure is not None:
        raise failure
    return result
