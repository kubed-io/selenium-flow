"""The spare tab: a tab that can stand on any origin without the site loading.

Site data is read and written there, which is how another origin's storage is
reached from a browser that is on a different one.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager as ContextManager

from ..bidi import Channel

# What a spare tab is answered with. The marker tells it from a site's own
# page, which a service worker serves before any intercept sees the request.
SPARE_MARKER = "selenium-flow-spare"
SPARE_PAGE = f'<!doctype html><meta name="{SPARE_MARKER}">'
# The only URL the intercept matches, on any origin: an intercept that outlived
# its tab (a teardown that failed) can then never hold up a real page load,
# which would wait out the page-load timeout (CI, #51).
SPARE_PATH = "/__selenium-flow-spare__"


class ServiceWorkerAnswered(RuntimeError):
    """A spare tab got the site's own page: its service worker answered before
    the intercept could. Nothing is read or written there."""


@contextlib.contextmanager
def spare_tab(
    channel: Channel,
    context: str | None = None,
    *,
    hidden: Callable[[str], ContextManager[None]] | None = None,
) -> Iterator[Callable[[str, str], object]]:
    """A tab whose every request is answered with :data:`SPARE_PAGE`, so it
    can stand on any origin without that site loading — Playwright's way to
    reach another origin's storage.

    Yields ``run(origin, expression)``: stand on ``origin`` and return what
    ``expression`` evaluates to there, JSON-safe. A page without the marker is
    a service worker's (:class:`ServiceWorkerAnswered`) and is never touched.

    With ``context``, that existing tab is intercepted instead and left open:
    the main tab, for its sessionStorage. With ``hidden`` and no ``context``,
    the tab's whole life is spent inside ``hidden(tab)``, so a capture running
    on the same connection never sees it.
    """
    with contextlib.ExitStack() as life:
        if context is None:
            tab = channel.call(
                "browsingContext.create", {"type": "tab", "background": True}
            )["context"]
            if hidden is not None:
                life.enter_context(hidden(tab))
            life.callback(_close, channel, tab)
        else:
            tab = context
        intercept: list[str] = []

        def remove() -> None:
            # Once: the way out of the block and the way out of a failed
            # subscribe both come here.
            while intercept:
                with contextlib.suppress(Exception):
                    channel.call(
                        "network.removeIntercept", {"intercept": intercept.pop()}
                    )

        life.callback(remove)

        def answer(_method: str, params: dict) -> None:
            # On the loop, once per event: a refused response is not an error
            # here, it leaves the request blocked and the navigate times out.
            if params.get("isBlocked"):
                channel.post("network.provideResponse", {
                    "request": params["request"]["request"],
                    "statusCode": 200,
                    "headers": [{
                        "name": "content-type",
                        "value": {"type": "string", "value": "text/html"},
                    }],
                    "body": {"type": "string", "value": SPARE_PAGE},
                })

        intercept.append(channel.call("network.addIntercept", {
            "phases": ["beforeRequestSent"],
            "contexts": [tab],
            "urlPatterns": [{"type": "pattern", "pathname": SPARE_PATH}],
        })["intercept"])

        def run(origin: str, expression: str):
            channel.call("browsingContext.navigate", {
                "context": tab, "url": origin + SPARE_PATH, "wait": "complete",
            })
            reply = channel.call("script.evaluate", {
                "expression": _on_spare(expression, origin),
                "target": {"context": tab},
                "awaitPromise": True,
            })
            if reply.get("type") == "exception":
                details = reply.get("exceptionDetails") or {}
                raise RuntimeError(details.get("text") or "the script failed")
            got = json.loads(reply["result"]["value"])
            if not got["spare"]:
                raise ServiceWorkerAnswered(origin)
            if got["origin"] != origin:
                # A navigation that did not land must never be read as this
                # origin: the tab still holds the last one's page.
                raise RuntimeError(f"the spare tab is on {got['origin']}, not {origin}")
            return got["value"]

        # The intercept before its listener: the other way round, a request
        # caught in between has nobody to answer it and stays blocked.
        with channel.listen(
            ["network.beforeRequestSent"], answer, contexts=[tab], name="spare"
        ):
            try:
                yield run
            finally:
                remove()


def _close(channel: Channel, tab: str) -> None:
    with contextlib.suppress(Exception):
        channel.call("browsingContext.close", {"context": tab})


def _on_spare(expression: str, origin: str) -> str:
    """``expression``, run only on our page AND only on ``origin``, as a JSON
    string that also says whose page it was and where. A navigation that did
    not land leaves the tab on the last origin's spare page; evaluating there
    would write this origin's saved storage into the wrong one, so the origin
    is tested in the page, before the expression, not after in Python."""
    return (
        "JSON.stringify((document.querySelector('meta[name=\"" + SPARE_MARKER + "\"]')"
        " && location.origin === " + json.dumps(origin) + ")"
        " ? {spare: true, origin: location.origin, value: (" + expression + ")}"
        " : {spare: !!document.querySelector('meta[name=\"" + SPARE_MARKER + "\"]'),"
        " origin: location.origin})"
    )
