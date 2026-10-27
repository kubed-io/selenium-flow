"""uvicorn's access log, with every request named by the route that serves it."""

from __future__ import annotations

import logging
import re

from starlette.routing import compile_path


class RouteTemplates(logging.Filter):
    """Rewrites uvicorn's access line to the route that answered, not the path.

    A workspace name is the address of a browser anyone with the token can then
    drive, and it rides in the query (``?workspace=``) *and* in paths:
    ``/admin/workspaces/<name>/history``, ``/kept/<name>/...``; a signed link's
    ``sig`` opens a file. An access line
    quotes the whole request target into every pod log and into Loki, so it
    says ``/admin/workspaces/{key}/history`` instead. A path no route serves keeps
    only its first segment, so nothing unknown leaks either. Method and status
    stay: an operator still sees what was asked for and how it went.
    """

    def __init__(self, routes=()):
        super().__init__()
        self.templates: list[tuple[re.Pattern, str]] = []
        self.serve(routes)

    def serve(self, routes) -> None:
        """Learn ``routes`` (a Starlette app's), in the order Starlette tries them."""
        self.templates = list(self._learn(routes, ""))

    def _learn(self, routes, prefix: str):
        for route in routes:
            path = getattr(route, "path", None)
            if path is None:
                continue
            inner = getattr(route, "routes", None)
            if inner is not None:  # a Mount: its routes hang under its path
                yield from self._learn(inner, prefix + path)
                continue
            regex, template, _ = compile_path(prefix + path)
            yield regex, template

    def template(self, target: str) -> str:
        path = target.split("?", 1)[0]
        for regex, template in self.templates:
            if regex.match(path):
                return template
        first, _, rest = path.lstrip("/").partition("/")
        return f"/{first}/…" if rest else f"/{first}"

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if (
            isinstance(record.msg, str)
            and "WebSocket %s" in record.msg
            and isinstance(args, tuple)
            and len(args) >= 2
            and isinstance(args[1], str)
        ):
            # uvicorn's own WebSocket lines (on uvicorn.error): the path, query
            # and all, which for the live view is a workspace and a signed link.
            record.args = (args[0], self.template(args[1]), *args[2:])
        elif (
            record.name == "uvicorn.access"
            and isinstance(args, tuple)
            and len(args) >= 3
            and isinstance(args[2], str)
        ):
            record.args = (*args[:2], self.template(args[2]), *args[3:])
        return True


def quiet(app) -> None:
    """Log ``app``'s route templates in uvicorn's access log and in its
    WebSocket lines (on ``uvicorn.error``), one filter however often it boots —
    the last app booted is the one being served."""
    loggers = [logging.getLogger(n) for n in ("uvicorn.access", "uvicorn.error")]
    found = next(
        (f for lg in loggers for f in lg.filters if isinstance(f, RouteTemplates)),
        None,
    )
    if found is None:
        found = RouteTemplates(app.routes)
    else:
        found.serve(app.routes)
    for lg in loggers:
        if found not in lg.filters:
            lg.addFilter(found)
