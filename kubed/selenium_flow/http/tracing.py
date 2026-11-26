"""One SERVER span per HTTP request, named by the route template.

The span carries the method, the route template and the status, so nothing a
caller chose (a workspace, a signed link's query, a header) reaches it. ``/mcp``
is FastMCP's own, the probes are noise, and WebSockets are the live view's: none
of them gets a span.
"""

from __future__ import annotations

import logging

from opentelemetry import propagate
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .access_log import RouteTemplates

log = logging.getLogger(__name__)

METHODS = frozenset(
    ("GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH")
)
PROBES = ("/health", "/started", "/ready", "/info")


def _headers(scope: Scope) -> dict[str, str]:
    return {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in scope.get("headers", ())
    }


class RouteSpans:
    """ASGI middleware: a SERVER span around each HTTP request, current for the
    route, ended with its status; an exception from the app is re-raised as it
    came."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        tracer: Tracer,
        templates: RouteTemplates,
        prefix: str = "",
    ) -> None:
        self.app = app
        self.tracer = tracer
        self.templates = templates  # held, not copied: `quiet` may re-teach it
        self.prefixes = ("", prefix) if prefix else ("",)

    def _skipped(self, path: str) -> bool:
        for p in self.prefixes:
            if path in (*(p + probe for probe in PROBES), p + "/mcp"):
                return True
            if path.startswith(p + "/mcp/"):
                return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._skipped(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        method = method if method in METHODS else "_OTHER"
        route = self.templates.matched(scope.get("path", ""))
        try:
            attributes = {"http.request.method": method}
            if route is not None:
                attributes["http.route"] = route
            current = self.tracer.start_as_current_span(
                f"{method} {route}" if route is not None else method,
                context=propagate.extract(_headers(scope)),
                kind=SpanKind.SERVER,
                attributes=attributes,
                record_exception=False,
                set_status_on_exception=False,
            )
            span = current.__enter__()
        except Exception as exc:  # noqa: BLE001 - telemetry never raises
            log.warning("telemetry: a route span failed (%s)", type(exc).__name__)
            await self.app(scope, receive, send)
            return
        status = 0
        raised = False

        async def sending(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, sending)
        except BaseException:
            raised = True
            raise
        finally:
            self._end(current, span, status or (500 if raised else 0), raised)

    @staticmethod
    def _end(current, span, status: int, raised: bool) -> None:
        """Finish the span; a failure here is logged, never raised over the
        app's answer or exception."""
        try:
            if status:
                span.set_attribute("http.response.status_code", status)
            if raised or status >= 500:
                span.set_status(Status(StatusCode.ERROR))
        except Exception as exc:  # noqa: BLE001 - telemetry never raises
            log.warning("telemetry: a route span failed (%s)", type(exc).__name__)
        try:
            current.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001 - telemetry never raises
            log.warning("telemetry: a route span failed (%s)", type(exc).__name__)
