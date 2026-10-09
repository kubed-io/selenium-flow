"""The road every browser action takes, stated once.

Every action does the same five things in the same order: check what it was
asked, reattach to the session's browser (putting it on ``url`` first when one
was named and it is somewhere else), wait for the element it acts on, act, and
report where the page ended up. Each used to spell that out for itself, and the
copies drifted: four resolved their selector only after the browser had been
reattached and moved, so a selector naming both ``xpath`` and ``css`` cost a
page load before it was refused.

`Recipe.run` is the one copy. An action is its own argument checks plus a body,
and anything that must hold for every action - one call at a time per session
(`workspace.locks`), a deadline per call - has one place to attach.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, NamedTuple

from selenium.common.exceptions import StaleElementReferenceException

from ..urls import allowed_navigation
from ..workspace import locks
from . import browser
from .coerce import as_int

# Under the name it has always logged as: operators filter by logger name.
log = logging.getLogger("kubed.selenium_flow.core.actions")

# How long a locator waits for its element before giving up. Named because the
# same number is the default on BOTH surfaces — every tool in tools.py and every
# endpoint in routes.py derives its default from here, and an unreadable value
# falls back to it here. Two copies of a literal 30 across two files is exactly
# how the surfaces come to disagree about what an omitted argument means, which
# is the drift this project is built to prevent.
WAIT_TIMEOUT = 30

# Dialogs get less. A native dialog is either already open or it is not — there
# is nothing to render and nothing to load — so a caller that guessed wrong
# should find out in ten seconds rather than thirty.
DIALOG_TIMEOUT = 10

# What the element has to be before the body gets it. Presence for anything
# that only reads or scrolls - requiring clickability would refuse exactly the
# off-screen element `scroll_to` is for - and clickable for anything that acts.
PRESENCE = "presence"
CLICKABLE = "clickable"


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


class Recipe:
    """One action's road: validate, reconnect, wait, body, page state.

    ``reconnect(session_id, url)`` reattaches to the session's browser and,
    when ``url`` is given, puts it there unless it is already there - one
    callable rather than two steps, because ``Actions._at`` is that pair and is
    the seam every test that drives an action without a Grid reattaches through.
    ``wait`` is ``PRESENCE``, ``CLICKABLE`` or None. ``retry_stale`` finds the
    element and runs the body once more when the page replaced the element
    between the two.
    """

    __slots__ = ("reconnect", "retry_stale", "wait")

    def __init__(self, reconnect, wait=None, retry_stale=False):
        if wait not in (None, PRESENCE, CLICKABLE):
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

        The selector and the ``url`` are checked before the browser is touched:
        a selector naming both ``xpath`` and ``css``, or neither, is a mistake,
        and so is a URL that is not the web's (Ruling 4), and finding that out
        after a reconnect and a navigation costs a page load to learn nothing.
        A recipe that waits always needs a selector; one that does not resolves
        whatever selector it was given, for a body that waits on its own terms.

        The page state is read once. A body that has to know where the page is
        before it can answer - an assertion's failure names the page - reads it
        through ``at.state`` as its last act, and that reading is the one
        reported; otherwise the recipe reads it after the body. Either way it is
        `browser.page_state`, which reports an open dialog as state rather than
        failing an action that succeeded (AGENTS.md "Dialogs").

        From the reconnect to that page state the session's browser is this
        call's alone (`workspace.locks`): another waits its turn, and the checks
        above are made before it does, so a refused call never waits.
        """
        target = browser.locator(selector) if self.wait or selector else None
        timeout = as_int(wait_timeout, WAIT_TIMEOUT)
        if url:
            allowed_navigation(url)

        with locks.driving(session_id):
            driver = self.reconnect(session_id, url)

            read: list[dict] = []

            def state() -> dict:
                read.append(browser.page_state(driver))
                return read[-1]

            def attempt() -> dict:
                element = None
                if self.wait == CLICKABLE:
                    element = browser.wait_for_clickable(driver, target, timeout)
                elif self.wait == PRESENCE:
                    element = browser.wait_for_element(driver, target, timeout)
                return body(At(driver, element, target, timeout, state))

            if self.retry_stale:
                fields = self._once_more_if_stale(attempt)
            else:
                fields = attempt()
            return {**fields, **(read[-1] if read else browser.page_state(driver))}

    @staticmethod
    def _once_more_if_stale(attempt):
        """Find the element and act on it, once more if it goes stale first.

        A page that repaints replaces the element between the wait and the act,
        and WebDriver reports that as a stale reference. It is not a mistake by
        the caller and there is nothing to fix in the selector: the element it
        found is simply not the one on the page any more. Found by the admin UI,
        whose workspace list repaints on a two-second poll — a click on a row was
        racy on every page that refreshes itself, which is a great many of them.

        Retried **once**, and the wait is part of the retry: retrying the act
        alone would reuse the same dead reference. The whole body is retried,
        so `interact`'s pointer move is re-done along with its click. Once
        rather than until it works, because a page that replaces an element
        faster than we can act on it is a real finding, and a loop would bury it
        as a slow call.
        """
        try:
            return attempt()
        except StaleElementReferenceException:
            log.info("element went stale before it could be used; finding it again")
            return attempt()
