"""selenium-flow's own OpenTelemetry signals: how long calls take, how full the
Grid is, and a span for each call.

kubed-mcp owns the SDK and the exporters; this module touches only the API,
which does nothing until kubed-mcp sets its providers in the lifespan. The
server builds a `Telemetry` before that, so the instruments it creates here are
the API's proxies: they carry over to the real providers once those are set.

Nothing here may fail a call or raise into a reader of the metrics. A failure
is logged by its exception's type alone, since its text can hold a URL with
credentials in it.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
from collections.abc import Callable, Iterable

from opentelemetry import metrics, trace
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from .monitor.events import CallFinished

log = logging.getLogger(__name__)

SCOPE = "kubed.selenium_flow"
CALL_DURATION = "selenium_flow.call.duration"
GRID_SLOTS = "selenium_flow.grid.slots"

# The operation-duration buckets OpenTelemetry's MCP conventions advise.
BUCKETS: tuple[float, ...] = (
    0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300,
)


def _version() -> str | None:
    try:
        return importlib.metadata.version("kubed-selenium-flow")
    except importlib.metadata.PackageNotFoundError:
        return None


def grid_counts(status: dict) -> dict[tuple[str, str], int]:
    """The Grid's slots by `(browser, state)`, `state` being `busy` or `free`.

    A busy slot holds a session. A free slot counts only on a node that is up
    and below its `maxSessions`, and no more of them than that leaves room for:
    a slot a node would refuse a session on is not capacity.
    """
    counts: dict[tuple[str, str], int] = {}
    for node in (status.get("value") or {}).get("nodes") or ():
        slots = node.get("slots") or ()
        busy = sum(1 for slot in slots if slot.get("session"))
        for slot in slots:
            if slot.get("session"):
                key = ((slot.get("stereotype") or {}).get("browserName", ""), "busy")
                counts[key] = counts.get(key, 0) + 1
        if node.get("availability", "UP") != "UP":
            continue
        cap = node.get("maxSessions")
        room = cap - busy if cap is not None else None
        for slot in slots:
            if slot.get("session") or (room is not None and room <= 0):
                continue
            key = ((slot.get("stereotype") or {}).get("browserName", ""), "free")
            counts[key] = counts.get(key, 0) + 1
            if room is not None:
                room -= 1
    return counts


class Telemetry:
    """The instruments and the tracer, made once."""

    def __init__(self, *, tracer_provider=None, meter_provider=None):
        version = _version()
        self.tracer = trace.get_tracer(SCOPE, version, tracer_provider=tracer_provider)
        self.meter = metrics.get_meter(SCOPE, version, meter_provider=meter_provider)
        self._duration = self.meter.create_histogram(
            CALL_DURATION,
            unit="s",
            description=(
                "A call's own time: inside its session's turn, not counting "
                "the wait for it, for a call that takes one; from its start "
                "to its end for one that takes none."
            ),
            explicit_bucket_boundaries_advisory=BUCKETS,
        )
        self._failing = False
        self._stopped = False

    def stop(self) -> None:
        """The server is stopping: a collection from now on reads nothing.

        kubed-mcp shuts the meter provider down after the lifespan ends, and
        its last collection runs on the reader's thread, where a hanging Grid
        would hold the shutdown up.
        """
        self._stopped = True

    def call_finished(self, event: CallFinished) -> None:
        """The bus handler: one point per finished call."""
        self._duration.record(
            event.duration,
            {
                "selenium_flow.tool": event.tool,
                "selenium_flow.surface": event.surface,
                "selenium_flow.outcome": event.outcome,
                "selenium_flow.browser": event.browser,
            },
        )

    def grid_gauge(self, read: Callable[[], dict]) -> None:
        """Register the slots gauge; each collection calls `read` for the status."""

        def observe(_: CallbackOptions) -> Iterable[Observation]:
            counts = self._counts(read)
            for (browser, state), count in (counts or {}).items():
                yield Observation(
                    count,
                    {
                        "selenium_flow.browser": browser,
                        "selenium_flow.slot.state": state,
                    },
                )

        self.meter.create_observable_gauge(
            GRID_SLOTS,
            callbacks=[observe],
            unit="{slot}",
            description="The Grid's slots by browser, busy or free.",
        )

    def _counts(
        self, read: Callable[[], dict]
    ) -> dict[tuple[str, str], int] | None:
        if self._stopped:
            return None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # The last collection at shutdown runs on the loop's thread, and a
            # read of the Grid there would block it.
            return None
        try:
            counts = grid_counts(read())
        except Exception as exc:  # noqa: BLE001 - telemetry never raises
            # One line a streak: a Grid that stays down is not a line a minute.
            if not self._failing:
                log.warning(
                    "telemetry: the Grid was not read for its gauge (%s)",
                    type(exc).__name__,
                )
            self._failing = True
            return None
        self._failing = False
        return counts

    def start_call(
        self,
        workspace: str,
        *,
        sub_session: bool,
        tool: str,
        browser: str,
        start_time: int | None = None,
    ) -> Span | None:
        """Begin a call's span under the current context, without making it
        current. ``start_time`` (ns since the epoch) backdates it to when the
        call started, for a span made only once the call is over."""
        try:
            return self.tracer.start_span(
                f"call {tool}",
                kind=SpanKind.INTERNAL,
                attributes={
                    "selenium_flow.workspace": workspace,
                    "selenium_flow.sub_session": sub_session,
                    "selenium_flow.tool": tool,
                    "selenium_flow.browser": browser,
                },
                start_time=start_time,
            )
        except Exception as exc:  # noqa: BLE001 - telemetry never raises
            log.warning("telemetry: a call span failed (%s)", type(exc).__name__)
            return None

    def end_call(
        self,
        span: Span | None,
        *,
        surface: str,
        outcome: str,
        error: BaseException | None = None,
    ) -> None:
        """End a call's span. An error is its type only: its text can hold input."""
        if span is None:
            return
        try:
            span.set_attribute("selenium_flow.surface", surface)
            span.set_attribute("selenium_flow.outcome", outcome)
            if error is not None:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", type(error).__name__)
        except Exception as exc:  # noqa: BLE001 - telemetry never raises
            log.warning("telemetry: a call span failed (%s)", type(exc).__name__)
        # Apart, so a failed attribute never leaves the span open.
        try:
            span.end()
        except Exception as exc:  # noqa: BLE001 - telemetry never raises
            log.warning("telemetry: a call span failed (%s)", type(exc).__name__)
