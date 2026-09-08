"""The MCP tool surface.

Thin wrappers over ``actions.py``. Type hints here are not decoration: FastMCP
turns each signature into the tool's JSON schema, so the parameter names, types
and defaults written below are exactly what a model sees and fills in.

Docstrings are prompt. They are written for a model deciding whether to call the
tool, not for a developer reading the source.

``session_id`` is optional on every tool because ``sessions.py`` can supply it
from the caller's key. When it cannot, the error says how to fix it. The HTTP
surface never does this — see ``routes.py``.
"""

from __future__ import annotations

import base64
from collections.abc import Callable

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from .actions import KEYS, Actions
from .sessions import NAME_PARAM, SessionManager

INSTRUCTIONS = f"""\
Drives a real Chrome browser on Selenium Grid. The browser is persistent: it \
stays alive between tool calls and keeps its page, cookies and scroll position.

Lifecycle:
1. Call open_session to start a browser. It returns a session_id.
2. Pass that session_id to the other calls.
3. Call close_session when finished, including after a failure. Sessions are a \
scarce resource and an abandoned one holds a slot until the Grid reaps it.

If this server can identify your client it will remember the browser for you \
and session_id becomes optional. When it cannot, the error tells you so; either \
pass session_id every time, or add ?{NAME_PARAM}=<name> to the MCP URL to name \
a session the server can hold on your behalf.

Elements are addressed by XPath, e.g. //input[@name='q'].

Most actions take an optional url. It is not an assertion: if the browser is \
somewhere else it navigates there first, so you can jump straight to a page \
instead of clicking a path to it.

Prefer extract to read a page — it is far cheaper than a screenshot. Use \
execute_script for anything the other tools do not cover, scrolling included.
"""


def register(mcp: FastMCP, actions: Actions, sessions: SessionManager) -> None:
    """Register every action as an MCP tool on ``mcp``."""

    def run(session_id: str | None, call: Callable[[str], dict]) -> dict:
        """Resolve the caller's browser, act, and remember where it ended up.

        The three steps every tool shares. ``touch`` is what lets a later
        refresh reopen on the right page, and keeps an in-use session from
        expiring out of the store.
        """
        key = sessions.key()
        result = call(sessions.resolve(key, session_id))
        if isinstance(result, dict):
            sessions.touch(key, result.get("url"))
        return result

    @mcp.tool
    def open_session(
        url: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict:
        """Start a browser session and return its session_id.

        Optionally navigates to a starting URL. Set width and height when layout
        matters: the headless default is small and varies between Grid nodes.

        The returned session_id is remembered for this client where possible, so
        later calls may omit it — but it is always returned, and passing it
        explicitly always works and always wins.
        """
        opened = actions.open_session(url=url, width=width, height=height)
        sessions.remember(sessions.key(), opened["session_id"], opened.get("url", ""))
        return opened

    @mcp.tool
    def close_session(session_id: str | None = None) -> dict:
        """Quit the browser session and free its Grid slot.

        Call this when finished, including after a failure. Sessions are limited
        and an abandoned one stays open until the Grid reaps it. Omit session_id
        to close the one this client has been using.
        """
        key = sessions.key()
        resolved = sessions.resolve(key, session_id)
        result = actions.close_session(resolved)
        sessions.forget(key, resolved)
        return result

    @mcp.tool
    def navigate(url: str, session_id: str | None = None) -> dict:
        """Go to a URL. Returns the resulting URL and page title.

        Use this to move somewhere unconditionally. To act on a page in one
        step, prefer passing url to click, write, extract or screenshot.
        """
        return run(session_id, lambda s: actions.navigate(s, url))

    @mcp.tool
    def click(
        xpath: str,
        session_id: str | None = None,
        url: str | None = None,
        wait_timeout: int = 30,
    ) -> dict:
        """Click an element, waiting for it to become clickable.

        Returns the URL and title *after* the click, so any navigation the
        click caused is visible in the result.
        """
        return run(
            session_id,
            lambda s: actions.click(s, xpath, url=url, wait_timeout=wait_timeout),
        )

    @mcp.tool
    def write(
        xpath: str,
        text: str,
        session_id: str | None = None,
        url: str | None = None,
        clear: bool = True,
        submit: bool = False,
        wait_timeout: int = 30,
    ) -> dict:
        """Type text into an input, textarea or contenteditable.

        Set submit to press Enter afterwards, which fills and submits a search
        box in one call. Returns the field's value read back off the element, so
        you can confirm the text actually landed.
        """
        return run(
            session_id,
            lambda s: actions.write(
                s,
                xpath,
                text,
                url=url,
                clear=clear,
                submit=submit,
                wait_timeout=wait_timeout,
            ),
        )

    # The description is passed rather than left as a docstring so the real key
    # list is interpolated in — a model guessing key names gets a 400, and the
    # list cannot drift from the mapping it is generated from.
    @mcp.tool(
        description=(
            "Press a named key, at an element or wherever focus currently is.\n\n"
            "For Tab, Escape, Enter, arrows and similar. Known keys: "
            f"{', '.join(sorted(KEYS))}.\n\n"
            "Not a reliable way to scroll - page_down only moves the page when "
            "focus happens to be on the scrollable container. Use execute_script "
            "to scroll."
        )
    )
    def press_key(
        key: str,
        session_id: str | None = None,
        xpath: str | None = None,
        url: str | None = None,
        wait_timeout: int = 30,
    ) -> dict:
        return run(
            session_id,
            lambda s: actions.press_key(
                s, key, xpath=xpath, url=url, wait_timeout=wait_timeout
            ),
        )

    @mcp.tool
    def extract(
        xpath: str,
        session_id: str | None = None,
        url: str | None = None,
        wait_timeout: int = 30,
    ) -> dict:
        """Read an element's visible text and innerHTML.

        The primary way to read a page — prefer it over a screenshot, which
        costs far more. //body reads everything, but a narrower XPath keeps the
        result small.
        """
        return run(
            session_id,
            lambda s: actions.extract(s, xpath, url=url, wait_timeout=wait_timeout),
        )

    @mcp.tool
    def execute_script(
        script: str, session_id: str | None = None, url: str | None = None
    ) -> dict:
        """Run JavaScript in the page and return its result.

        The escape hatch for anything the other tools do not cover: scrolling
        (window.scrollTo(0, 2000)), drag and drop, computed styles, direct DOM
        access. Use `return` to send a value back.
        """
        return run(session_id, lambda s: actions.execute_script(s, script, url=url))

    @mcp.tool
    def screenshot(
        session_id: str | None = None,
        url: str | None = None,
        xpath: str | None = None,
        full_page: bool = False,
        width: int | None = None,
        height: int | None = None,
        wait_timeout: int = 30,
    ) -> Image:
        """Capture a PNG of the page and return it as an image you can see.

        Three modes: pass xpath for one element, full_page for the whole
        scrollable page, or neither for the visible viewport.

        Only reach for this when the *visual* result matters — layout, styling,
        a rendered chart. To read content, extract is far cheaper.
        """
        result = run(
            session_id,
            lambda s: actions.screenshot(
                s,
                url=url,
                xpath=xpath,
                full_page=full_page,
                width=width,
                height=height,
                wait_timeout=wait_timeout,
            ),
        )
        return Image(data=base64.b64decode(result["image"]), format="png")
