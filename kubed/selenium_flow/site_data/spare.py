"""The spare tab: a tab that can stand on any origin without the site loading.

Site data is read and written there, which is how another origin's storage is
reached from a browser that is on a different one.
"""

from __future__ import annotations

import contextlib
import json

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
def spare_tab(bidi, context: str | None = None):
    """A tab whose every request is answered with :data:`SPARE_PAGE`, so it
    can stand on any origin without that site loading — Playwright's way to
    reach another origin's storage.

    Yields ``run(origin, expression)``: stand on ``origin`` and return what
    ``expression`` evaluates to there, JSON-safe. A page without the marker is
    a service worker's (:class:`ServiceWorkerAnswered`) and is never touched.

    With ``context``, that existing tab is intercepted instead and left open:
    the main tab, for its sessionStorage. Selenium subscribes once per event,
    with the first handler's contexts, so the handler is removed on the way
    out and the next tab subscribes afresh.
    """
    tab = context or bidi.browsing_context.create(type="tab", background=True)
    intercept = handler = None
    try:
        intercept = bidi.network.add_intercept(
            phases=["beforeRequestSent"],
            contexts=[tab],
            url_patterns=[{"type": "pattern", "pathname": SPARE_PATH}],
        )["intercept"]

        def answer(event):
            # On Selenium's own thread, one per event: a refused response is
            # not an error here, it leaves the request blocked and the
            # navigate times out (BIDI_TIMEOUT).
            if not isinstance(event, dict) or not event.get("isBlocked"):
                return
            with contextlib.suppress(Exception):
                bidi.network.provide_response(
                    request=event["request"]["request"],
                    status_code=200,
                    headers=[{
                        "name": "content-type",
                        "value": {"type": "string", "value": "text/html"},
                    }],
                    body={"type": "string", "value": SPARE_PAGE},
                )

        # `before_request`, not `before_request_sent`: the other name hands
        # the callback an event without the request's fields.
        handler = bidi.network.add_event_handler(
            "before_request", answer, contexts=[tab]
        )

        def run(origin: str, expression: str):
            bidi.browsing_context.navigate(
                context=tab, url=origin + SPARE_PATH, wait="complete"
            )
            reply = bidi.script.evaluate(
                expression=_on_spare(expression),
                target={"context": tab},
                await_promise=True,
            )
            if reply.get("type") != "success":
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

        yield run
    finally:
        # The intercept before its handler: the other way round, a request
        # caught in between has nobody to answer it and stays blocked.
        if intercept is not None:
            with contextlib.suppress(Exception):
                bidi.network.remove_intercept(intercept=intercept)
        if handler is not None:
            with contextlib.suppress(Exception):
                bidi.network.remove_event_handler("before_request", handler)
        if context is None:
            with contextlib.suppress(Exception):
                bidi.browsing_context.close(context=tab)


def _on_spare(expression: str) -> str:
    """``expression``, run only on our page, as a JSON string that also says
    whose page it was and where."""
    return (
        "JSON.stringify(document.querySelector('meta[name=\"" + SPARE_MARKER + "\"]')"
        " ? {spare: true, origin: location.origin, value: (" + expression + ")}"
        " : {spare: false, origin: location.origin})"
    )
