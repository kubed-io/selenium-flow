"""The MCP tool surface.

Thin wrappers over ``actions.py``. Type hints here are not decoration: FastMCP
turns each signature into the tool's JSON schema, so the parameter names, types
and defaults written below are exactly what a model sees and fills in.

Docstrings are prompt. They are written for a model deciding whether to call the
tool, not for a developer reading the source.
"""

from __future__ import annotations

import base64

from fastmcp import Context, FastMCP
from fastmcp.utilities.types import Image

from .actions import KEYS, Actions
from .sessions import SavedSessions

INSTRUCTIONS = """\
Drives a real Chrome browser on Selenium Grid. The browser is persistent: it \
stays alive between tool calls and keeps its page, cookies and scroll position.

Lifecycle, which you must follow:
1. Call open_session first. It returns a session_id.
2. Pass that session_id to every other call. Nothing is remembered for you.
3. Call close_session when finished, including after a failure. Sessions are a \
scarce resource and an abandoned one holds a slot until it times out.

Elements are addressed by XPath, e.g. //input[@name='q'].

Most actions take an optional url. It is not an assertion: if the browser is \
somewhere else it navigates there first, so you can jump straight to a page \
instead of clicking a path to it.

Prefer extract to read a page — it is far cheaper than a screenshot. Use \
execute_script for anything the other tools do not cover, scrolling included.
"""


def register(mcp: FastMCP, actions: Actions, saved: SavedSessions) -> None:
    """Register every action as an MCP tool on ``mcp``.

    ``session_id`` is optional on every tool because saved sessions may fill it
    in from the MCP session. When the feature is off, omitting it is an error
    with a message that says so — the tool still works, it just has to be told
    which browser. The HTTP surface never does this; see ``sessions.py``.
    """

    def sid(ctx: Context, session_id: str | None) -> str:
        return saved.resolve(getattr(ctx, "session_id", None), session_id)

    @mcp.tool
    def open_session(
        ctx: Context,
        url: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict:
        """Start a browser session and return its session_id.

        Optionally navigates to a starting URL. Set width and height when layout
        matters: the headless default is small and varies between Grid nodes.

        The returned session_id is remembered for this conversation, so later
        calls may omit it — but it is still returned, and passing it explicitly
        always works and always wins.
        """
        opened = actions.open_session(url=url, width=width, height=height)
        saved.remember(getattr(ctx, "session_id", None), opened["session_id"])
        return opened

    @mcp.tool
    def close_session(ctx: Context, session_id: str | None = None) -> dict:
        """Quit the browser session and free its Grid slot.

        Call this when finished, including after a failure. Sessions are limited
        and an abandoned one stays open until it times out. Omit session_id to
        close the one this conversation has been using.
        """
        resolved = sid(ctx, session_id)
        result = actions.close_session(resolved)
        saved.forget(getattr(ctx, "session_id", None))
        return result

    @mcp.tool
    def navigate(ctx: Context, url: str, session_id: str | None = None) -> dict:
        """Go to a URL. Returns the resulting URL and page title.

        Use this to move somewhere unconditionally. To act on a page in one
        step, prefer passing url to click, write, extract or screenshot.
        """
        return actions.navigate(sid(ctx, session_id), url)

    @mcp.tool
    def click(
        ctx: Context,
        xpath: str,
        session_id: str | None = None,
        url: str | None = None,
        wait_timeout: int = 30,
    ) -> dict:
        """Click an element, waiting for it to become clickable.

        Returns the URL and title *after* the click, so any navigation the
        click caused is visible in the result.
        """
        return actions.click(sid(ctx, session_id), xpath, url=url, wait_timeout=wait_timeout)

    @mcp.tool
    def write(
        ctx: Context,
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
        return actions.write(
            sid(ctx, session_id),
            xpath,
            text,
            url=url,
            clear=clear,
            submit=submit,
            wait_timeout=wait_timeout,
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
        ctx: Context,
        key: str,
        session_id: str | None = None,
        xpath: str | None = None,
        url: str | None = None,
        wait_timeout: int = 30,
    ) -> dict:
        return actions.press_key(
            sid(ctx, session_id), key, xpath=xpath, url=url, wait_timeout=wait_timeout
        )

    @mcp.tool
    def extract(
        ctx: Context,
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
        return actions.extract(sid(ctx, session_id), xpath, url=url, wait_timeout=wait_timeout)

    @mcp.tool
    def execute_script(
        ctx: Context, script: str, session_id: str | None = None, url: str | None = None
    ) -> dict:
        """Run JavaScript in the page and return its result.

        The escape hatch for anything the other tools do not cover: scrolling
        (window.scrollTo(0, 2000)), drag and drop, computed styles, direct DOM
        access. Use `return` to send a value back.
        """
        return actions.execute_script(sid(ctx, session_id), script, url=url)

    @mcp.tool
    def screenshot(
        ctx: Context,
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
        result = actions.screenshot(
            sid(ctx, session_id),
            url=url,
            xpath=xpath,
            full_page=full_page,
            width=width,
            height=height,
            wait_timeout=wait_timeout,
        )
        return Image(data=base64.b64decode(result["image"]), format="png")
