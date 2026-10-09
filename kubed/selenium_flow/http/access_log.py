"""uvicorn's access log, with every request named by the route that serves it."""

from __future__ import annotations

import logging
import re

from starlette.routing import compile_path


class RouteTemplates(logging.Filter):
    """Rewrites uvicorn's access line to the route that answered, not the path.

    A session name is the credential past the token, and it rides in the query
    (``?session=``) *and* in paths: ``/admin/workspaces/<name>/history``,
    ``/kept/<name>/...``; a signed link's ``sig`` opens a file. An access line
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
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], self.template(args[2]), *args[3:])
        return True


def quiet(app) -> None:
    """Log ``app``'s route templates in uvicorn's access log, one filter however
    often it boots — the last app booted is the one being served."""
    access = logging.getLogger("uvicorn.access")
    found = next((f for f in access.filters if isinstance(f, RouteTemplates)), None)
    if found is None:
        access.addFilter(RouteTemplates(app.routes))
    else:
        found.serve(app.routes)
