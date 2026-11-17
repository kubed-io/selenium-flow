"""The road every browser action takes.

Check what it was asked, reattach to the session's browser (putting it on
``url`` first when one was named and it is somewhere else), wait for the element
it acts on, act, and report where the page ended up. An action is its own
argument checks plus a body for `Recipe.run`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, NamedTuple

from selenium.common.exceptions import (
    StaleElementReferenceException,
    UnexpectedAlertPresentException,
    WebDriverException,
)

from .. import faults
from ..urls import allowed_navigation
from ..workspace import locks
from . import browser
from .coerce import as_int

# Under the name it has always logged as: operators filter by logger name.
log = logging.getLogger("kubed.selenium_flow.core.actions")

# How long a locator waits for its element: the default on both surfaces, and
# the fallback for an unreadable value.
WAIT_TIMEOUT = 30

# A native dialog is open or it is not; there is nothing to load.
DIALOG_TIMEOUT = 10

# What the element has to be before the body gets it: present for anything that
# only reads, clickable for anything that acts. Shown is the pointer target that
# is not pressed (hover, scroll_to, drag's `to`): the first displayed match,
# else the first.
PRESENCE = "presence"
CLICKABLE = "clickable"
SHOWN = "shown"


@contextmanager
def node_answering():
    """Say so when the browser's Grid node stops answering mid-call.

    The hub's text quotes the node's address and the Grid's session id, which
    no result or log line may carry, so it becomes `faults.NodeLost` and is
    dropped with its context. Every road to a browser takes this.
    """
    try:
        yield
    except WebDriverException as exc:
        if not faults.lost(exc):
            raise
        raise faults.NodeLost() from None


class At(NamedTuple):
    """What a body is handed: the browser, and what the recipe found in it."""

    driver: Any
    # What the wait found, or None for a recipe that does not wait.
    element: Any
    # The selector as the (strategy, value) pair Selenium wants, or None.
    target: tuple[str, str] | None
    # `wait_timeout`, read: for a body that waits for something else itself.
    timeout: int
    # Reads where the page is. See `Recipe.run` for when a body calls it.
    state: Callable[[], dict]
    # Which match the wait settled on, or None for a recipe that does not wait.
    found: browser.Found | None = None


class Recipe:
    """One action's road: validate, reconnect, wait, body, page state.

    ``reconnect(session_id, url, look=)`` reattaches to the session's browser
    and, when ``url`` is given, puts it there unless it is already there
    (``Actions._at``, the seam tests reattach through). It calls
    ``look(driver)`` once reattached and before it moves anything, so the page
    state can tell a same-document navigation the call made (`browser.landed`);
    a seam that never calls it only goes without that wait. ``wait`` is
    ``PRESENCE``, ``CLICKABLE``, ``SHOWN`` or None. ``retry_stale`` finds the
    element and runs the body once more when the page replaced the element
    between the two.
    """

    __slots__ = ("reconnect", "retry_stale", "wait")

    def __init__(self, reconnect, wait=None, retry_stale=False):
        if wait not in (None, PRESENCE, CLICKABLE, SHOWN):
            raise ValueError(f"unknown wait {wait!r}")
        if retry_stale and wait is None:
            # The retry re-runs the wait; without one it would reuse the dead
            # reference and fail the same way twice.
            raise ValueError("a stale retry finds the element again: it needs a wait")
        self.reconnect = reconnect
        self.wait = wait
        self.retry_stale = retry_stale

    def run(
        self,
        session_id: str,
        body: Callable[[At], dict],
        *,
        url=None,
        selector=None,
        wait_timeout=WAIT_TIMEOUT,
    ) -> dict:
        """The body's fields, then where the page ended up.

        The selector and the ``url`` are checked before the browser is touched.
        A recipe that waits always needs a selector; one that does not resolves
        whatever selector it was given, for a body that waits on its own terms.

        The page state (`browser.landed`) is read once. A body that has to know
        where the page is before it can answer (an assertion's failure names
        the page) reads it through ``at.state`` as its last act, and that
        reading is the one reported; otherwise the recipe reads it after the
        body.

        From the reconnect to that page state the session's browser is this
        call's alone (`workspace.locks`). A host (`Workspaces.act`, a flow step,
        a bound write) already holds it and takes it again for free; called on
        its own, the checks above are made before it waits.
        """
        target = browser.locator(selector) if self.wait or selector else None
        timeout = as_int(wait_timeout, WAIT_TIMEOUT)
        if url:
            allowed_navigation(url)

        with locks.driving(session_id), node_answering():
            before: list[dict | None] = []
            driver = self.reconnect(
                session_id, url, look=lambda d: before.append(browser.document(d))
            )
            seen = before[0] if before else None

            read: list[dict] = []

            def state() -> dict:
                read.append(browser.landed(driver, seen))
                return read[-1]

            chosen: list[browser.Found | None] = []

            def attempt() -> dict:
                found = None
                if self.wait == CLICKABLE:
                    found = browser.wait_for_clickable(driver, target, timeout)
                elif self.wait == SHOWN:
                    found = browser.wait_for_shown(driver, target, timeout)
                elif self.wait == PRESENCE:
                    found = browser.Found(
                        browser.wait_for_element(driver, target, timeout), 0, 1
                    )
                chosen.append(found)
                element = found.element if found else None
                return body(At(driver, element, target, timeout, state, found))

            try:
                if self.retry_stale:
                    fields = self._once_more_if_stale(attempt)
                else:
                    fields = attempt()
            except UnexpectedAlertPresentException:
                # The driver refused the call outright: nothing was done, so it
                # is a failure, not a success carrying page state.
                message = browser.page_state(driver).get("dialog", "")
                raise ValueError(
                    f"a dialog is open ({message!r}) and blocked this call; "
                    "nothing was done: answer it with dialog"
                ) from None
            found = chosen[-1] if chosen else None
            return {
                **fields,
                **(found.fields() if found else {}),
                **(read[-1] if read else browser.landed(driver, seen)),
            }

    @staticmethod
    def _once_more_if_stale(attempt):
        """Find the element and act on it, once more if it goes stale first.

        A page that repaints replaces the element between the wait and the act.
        The wait is part of the retry, since the act alone would reuse the dead
        reference, and the whole body is retried, pointer move included. Once,
        because a page that replaces an element faster than it can be acted on
        is a real finding a loop would bury.
        """
        try:
            return attempt()
        except StaleElementReferenceException:
            log.info("element went stale before it could be used; finding it again")
            return attempt()
