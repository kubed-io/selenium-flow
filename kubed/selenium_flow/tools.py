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
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from pydantic import BaseModel, ConfigDict, Field

from . import flowrun
from . import secrets as secrets_module
from . import settings as settings_module
from .actions import (
    DIALOG_ACTIONS,
    DIALOG_TIMEOUT,
    FRAME_ACTIONS,
    KEYS,
    MOUSE_ACTIONS,
    WAIT_TIMEOUT,
    Actions,
)
from .hints import hints
from .sessions import NAME_PARAM, SessionManager

# Said the same way everywhere, because the one new way to get a call wrong is
# to pass both selectors or neither, and the fix has to be in front of the model
# at the point it is choosing.
SELECTOR = (
    "Address the element with EITHER xpath OR css, never both and never "
    "neither - e.g. xpath=\"//button[@type='submit']\" or "
    "css=\"button[type=submit]\"."
)

class SecretRef(BaseModel):
    """Which secret, and which key inside it."""

    # Unknown fields are refused rather than dropped, the same way the flow
    # validator refuses them. Pydantic's default is to ignore them silently,
    # which turns a caller's mistake into a different request than they sent.
    model_config = ConfigDict(extra="forbid")

    # Non-empty, because the validator refuses an empty one and the flow schema
    # publishes this model: a published `string` told a caller `""` was fine,
    # and `save_flow` then answered 400.
    name: str = Field(min_length=1)
    key: str = Field(min_length=1)


INSTRUCTIONS = f"""\
Drives a real Chrome or Firefox browser on Selenium Grid. The browser is \
persistent: it stays alive between tool calls and keeps its page, cookies and \
scroll position.

Lifecycle:
1. Call open_session to start a browser. It returns a session_id. Pass \
browser="firefox" for Firefox; the default is Chrome. Every other tool behaves \
identically on both.
2. Pass that session_id to the other calls.
3. Call end_browser when finished, including after a failure. Browsers are a \
scarce resource and an abandoned one holds a slot until the Grid reaps it. Your \
session survives it, so open_session picks up where you left off.

If this server can identify your client it will remember the browser for you \
and session_id becomes optional. When it cannot, the error tells you so; either \
pass session_id every time, or add ?{NAME_PARAM}=<name> to the MCP URL to name \
a session the server can hold on your behalf.

Elements are addressed by XPath or by CSS - pass one or the other, never \
both. xpath="//input[@name='q']" or css="input[name=q]".

Most actions take an optional url. It is not an assertion: if the browser is \
somewhere else it navigates there first, so you can jump straight to a page \
instead of clicking a path to it.

Prefer extract to read a page — it is far cheaper than a screenshot. Use \
execute_script for anything the other tools do not cover, scrolling included.
"""



def register(
    mcp: FastMCP, actions: Actions, sessions: SessionManager, catalogue=None
) -> None:
    """Register every action as an MCP tool on ``mcp``."""

    def run(
        session_id: str | None,
        call: Callable[[str], dict],
        *,
        reshapes: bool = False,
    ) -> dict:
        """Resolve the caller's browser, act, and remember where it ended up.

        The three steps every tool shares. ``touch`` is what lets a later reopen
        land on the right page, and keeps an in-use session from expiring out of
        the store. The resolved id is passed along so a stateless caller, which
        has no key, is still touched under the browser it is holding.

        ``reshapes`` is for the one action that changes a setting the record
        *stores* rather than just the page it is on. See ``sessions.reshape``.
        """
        key = sessions.key()
        resolved = sessions.resolve(key, session_id)
        result = call(resolved)
        if isinstance(result, dict):
            sessions.touch(key, result.get("url"), resolved)
            if reshapes:
                sessions.reshape(key, result, resolved)
        return result

    @mcp.tool(annotations=hints("Open browser session", destructive=True))
    def open_session(
        url: str | None = None,
        browser: str | None = None,
        width: int | None = None,
        height: int | None = None,
        page_load_timeout: int | None = None,
        script_timeout: int | None = None,
    ) -> dict:
        """Start a browser session. Do this first.

        This is the only place a browser is created, and the only place its
        settings can be chosen, so it is never done implicitly for you.

        Called with nothing, it carries on where this session left off: the same
        browser, the same window, back to the page it was last on. So after a
        browser is reaped or ended, a bare open_session() is usually right.

        Safe to call while you already have a browser: the one you are holding
        is ended for you first, so you never need to close before opening. That
        is how you switch browser — open_session(browser="firefox") — and the
        files the old browser had go with it, because the Grid keeps them per
        browser and deletes them with it.

        browser is "chrome" (the default) or "firefox". Every other tool works
        the same on either, so pick Firefox only when the task is about
        Firefox — checking a rendering difference, or a site that treats the two
        differently. A session cannot change browser later: open another one.

        Set width and height when layout matters — the headless default is
        narrow and varies between Grid nodes. page_load_timeout bounds how long
        a navigation may hang; without one a stuck page holds a scarce Grid slot
        until the Grid reaps it.

        The returned session_id is what a stateless caller passes to every later
        call. If this server is holding the browser for you, it is returned for
        information and you should NOT pass it back — read the session://current
        resource if you are unsure which of the two you are.
        """
        key = sessions.key()
        # What this flow session was last using. It sits between the client's
        # defaults and the explicit arguments: a caller that names nothing means
        # "carry on where I was", which is a stronger signal than a server-wide
        # default and a weaker one than an argument it just typed.
        previous = sessions.context(key)
        # Resolved BEFORE the browser you are holding is ended, because this
        # validates as well as merges: an explicit browser is checked strictly,
        # and doing it afterwards meant a typo in `browser=` quit a perfectly
        # good browser and then failed. A rejected argument must cost nothing.
        resolved = settings_module.resolve(
            {
                "browser": browser,
                "width": width,
                "height": height,
                "page_load_timeout": page_load_timeout,
                "script_timeout": script_timeout,
            },
            previous=previous.get("settings"),
        )
        # A flow session holds one browser. Opening a second without ending the
        # first leaves it on the Grid referenced by nothing, holding a slot
        # until the idle timeout — which switching browser did.
        sessions.end_browser(sessions.store_key(key))
        opened = actions.open_session(
            url=url or previous.get("url") or None, **resolved
        )
        sessions.remember(key, opened["session_id"], opened.get("url", ""), resolved)
        return opened

    @mcp.tool(annotations=hints("End browser", destructive=True, idempotent=True))
    def end_browser(session_id: str | None = None) -> dict:
        """Quit the browser and free its Grid slot. Do this when finished.

        Ends the *browser*, not your session. The session keeps the browser
        choice and the page you were on, so a later open_session() with no
        arguments picks up exactly where this left off — and any files the
        browser had are gone with it, because the Grid keeps them per browser.

        Call it on failure paths too. Browsers are scarce and an abandoned one
        holds a Grid slot until it is reaped. Omit session_id to end the one
        this client has been using.
        """
        key = sessions.key()
        resolved = sessions.resolve(key, session_id)
        sessions.end_browser(sessions.store_key(key, resolved), resolved)
        return {"success": True, "session_id": resolved}

    @mcp.tool(annotations=hints("Navigate to URL", idempotent=True))
    def navigate(url: str, session_id: str | None = None) -> dict:
        """Go to a URL. Returns the resulting URL and page title.

        Use this to move somewhere unconditionally. To act on a page in one
        step, prefer passing url to click, write, extract or screenshot.
        """
        return run(session_id, lambda s: actions.navigate(s, url))

    # The action list is interpolated so it cannot drift from the tuple the
    # action layer validates against.
    @mcp.tool(
        description=(
            "Perform a mouse action on an element.\n\n"
            f"action is one of: {', '.join(MOUSE_ACTIONS)}.\n\n"
            "click is the common case. hover opens menus that only appear on "
            "mouse-over. right_click opens context menus. scroll_to brings an "
            "off-screen element into view, which is often what a click on a "
            "long page needs first.\n\n"
            "Returns the URL and title *after* the action, so any navigation it "
            "caused is visible in the result.\n\n" + SELECTOR
        ),
        annotations=hints("Mouse action on an element", destructive=True),
    )
    def interact(
        action: str,
        xpath: str | None = None,
        css: str | None = None,
        session_id: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        return run(
            session_id,
            lambda s: actions.interact(
                s, action, xpath=xpath, css=css, url=url, wait_timeout=wait_timeout
            ),
        )

    @mcp.tool(
        description=(
            "Move into an iframe, or back out of it.\n\n"
            f"action is one of: {', '.join(FRAME_ACTIONS)}. Use switch with an "
            "xpath (or index) to go into a frame, parent to go up one level, and "
            "default to return to the main page.\n\n"
            "Selenium does not look inside frames: an element in one is "
            "invisible to every locator until you switch in. **The switch "
            "sticks** — every later call stays in that frame until you switch "
            "back, so if a locator that should work is failing, check "
            "session://current for in_frame.\n\nName the frame with xpath, "
            "css or index — one of the three, not two. parent and default take "
            "none of them."
        ),
        annotations=hints("Switch into or out of an iframe", idempotent=True),
    )
    def frame(
        action: str = "switch",
        xpath: str | None = None,
        css: str | None = None,
        index: int | None = None,
        session_id: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        return run(
            session_id,
            lambda s: actions.frame(
                s,
                action=action,
                xpath=xpath,
                css=css,
                index=index,
                wait_timeout=wait_timeout,
            ),
        )

    @mcp.tool(annotations=hints("Resize window", idempotent=True))
    def resize(
        width: int | None = None,
        height: int | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Resize the browser window.

        Use this when layout matters and the browser was opened for you, or
        when you need a different size partway through. The headless default is
        small and varies between Grid nodes, so set it explicitly before
        judging anything visual.

        The new size sticks to the session, so a browser the Grid reaps and
        reopens comes back the size you last set rather than the size it was
        opened at.
        """
        return run(
            session_id,
            lambda s: actions.resize(s, width=width, height=height),
            reshapes=True,
        )

    @mcp.tool(
        description=(
            "Answer a native alert, confirm or prompt dialog.\n\n"
            f"action is one of: {', '.join(DIALOG_ACTIONS)}. Use read to see the "
            "message without answering, accept to confirm, dismiss to cancel, "
            "and send_text with text to fill a prompt and accept it.\n\n"
            "An open dialog blocks every other command, so if a call fails "
            "complaining about an unexpected alert, this is how you clear it."
        ),
        annotations=hints("Answer a native dialog", destructive=True),
    )
    def dialog(
        action: str = "accept",
        text: str | None = None,
        session_id: str | None = None,
        wait_timeout: int = DIALOG_TIMEOUT,
    ) -> dict:
        return run(
            session_id,
            lambda s: actions.dialog(
                s, action=action, text=text, wait_timeout=wait_timeout
            ),
        )

    @mcp.tool(annotations=hints("Attach a file to a file input"))
    def upload_file(
        xpath: str | None = None,
        css: str | None = None,
        text: str | None = None,
        filename: str | None = None,
        mime_type: str | None = None,
        content: str | None = None,
        session_id: str | None = None,
        path: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        """Attach a file to a file input.

        For anything you wrote yourself — JSON, CSV, YAML, markdown, plain text
        — put it straight in `text` and give it a `filename`. There is no need
        to encode it; the server writes the real file and sends it to the
        browser, which runs on another machine.

        Use `content` (base64) only for binary, and `path` only for a file
        already on the server's filesystem. Pass exactly one of the three.

        The page reads the file's type from the **filename extension**, so name
        it `report.csv` rather than `report`. If you give a name without an
        extension, `mime_type` is used to pick one.

        Address the input with EITHER xpath OR css, never both and never
        neither.
        """
        return run(
            session_id,
            lambda s: actions.upload_file(
                s,
                xpath=xpath,
                css=css,
                text=text,
                content=content,
                filename=filename,
                mime_type=mime_type,
                path=path,
                url=url,
                wait_timeout=wait_timeout,
            ),
        )

    @mcp.tool(annotations=hints("Type text into a field"))
    def write(
        text: str | None = None,
        xpath: str | None = None,
        css: str | None = None,
        session_id: str | None = None,
        url: str | None = None,
        clear: bool = True,
        submit: bool = False,
        wait_timeout: int = WAIT_TIMEOUT,
        secret: SecretRef | None = None,
    ) -> dict:
        """Type text into an input, textarea or contenteditable.

        Set submit to press Enter afterwards, which fills and submits a search
        box in one call. Returns the field's value read back off the element, so
        you can confirm the text actually landed.

        Address the field with EITHER xpath OR css, never both and never
        neither.

        To type a secret, pass secret={"name": ..., "key": ...} instead of
        text. list_secrets shows what there is. You never see the value: the
        server reads it and types it, and the result comes back with
        value: null. A secret may only be used on the sites its owner allowed,
        checked against the page you are on, so navigate there first.
        """
        if secret is None and text is None:
            raise ValueError("write needs text, or a secret to supply it")
        if secret is None:
            return run(
                session_id,
                lambda s: actions.write(
                    s,
                    text,
                    xpath=xpath,
                    css=css,
                    url=url,
                    clear=clear,
                    submit=submit,
                    wait_timeout=wait_timeout,
                ),
            )

        # A bound write does not go through `run`, deliberately. `run` touches
        # the session with the URL the action returned, and `submit=True` can
        # land the browser on `?q=<what was typed>` — so the shared wrapper
        # would persist the credential into the session record before anything
        # had a chance to redact it.
        key = sessions.key()
        resolved = sessions.resolve(key, session_id)
        given, _guarded = secrets_module.prepare_write(
            catalogue,
            actions,
            resolved,
            {"text": text, "url": url, "secret": secret},
        )
        hidden = flowrun.hidden_forms([given["text"]])
        try:
            result = actions.write(
                resolved,
                given["text"],
                xpath=xpath,
                css=css,
                clear=clear,
                submit=submit,
                wait_timeout=wait_timeout,
                # Not read back at all, rather than read and then hidden.
                read_back=False,
            )
        except Exception as exc:  # noqa: BLE001 - rewrapped, never swallowed
            # An action puts its arguments in its error text.
            raise ValueError(flowrun.scrub(str(exc), hidden)) from None
        shown = flowrun.scrub_values({**result, "text_from": "secret"}, hidden)
        # Only remember a page the value never reached. A submitting write can
        # land on `?q=<what was typed>`; storing the scrubbed form would persist
        # a URL that does not exist, and a later reattach would navigate to it.
        #
        # Asked of the URL rather than by comparing it with its scrubbed form:
        # a secret whose value is the marker scrubs to itself, so equality would
        # have called the credential URL safe and stored it.
        #
        # Touched either way. Withholding the page must not also stop the clock:
        # `touch` slides the TTL, and skipping it entirely let a session expire
        # *because* its URL was correctly kept out of the store.
        safe = None if flowrun.taints(result.get("url"), hidden) else shown.get("url")
        sessions.touch(key, safe, resolved)
        return shown

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
            "to scroll.\n\nTo aim the key at an element, pass xpath or css; "
            "with neither, it goes wherever focus already is."
        ),
        annotations=hints("Press a named key", destructive=True),
    )
    def press_key(
        key: str,
        session_id: str | None = None,
        xpath: str | None = None,
        css: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        return run(
            session_id,
            lambda s: actions.press_key(
                s, key, xpath=xpath, css=css, url=url, wait_timeout=wait_timeout
            ),
        )

    @mcp.tool(annotations=hints("Read an element", read_only=True, idempotent=True))
    def extract(
        xpath: str | None = None,
        css: str | None = None,
        session_id: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        """Read an element's visible text and innerHTML.

        The primary way to read a page — prefer it over a screenshot, which
        costs far more. //body reads everything, but a narrower selector keeps
        the result small.

        Address the element with EITHER xpath OR css, never both and never
        neither.
        """
        return run(
            session_id,
            lambda s: actions.extract(
                s, xpath=xpath, css=css, url=url, wait_timeout=wait_timeout
            ),
        )

    @mcp.tool(annotations=hints("Run JavaScript in the page", destructive=True))
    def execute_script(
        script: str, session_id: str | None = None, url: str | None = None
    ) -> dict:
        """Run JavaScript in the page and return its result.

        The escape hatch for anything the other tools do not cover: scrolling
        (window.scrollTo(0, 2000)), drag and drop, computed styles, direct DOM
        access. Use `return` to send a value back.
        """
        return run(session_id, lambda s: actions.execute_script(s, script, url=url))

    @mcp.tool(annotations=hints("Capture a screenshot"))
    def screenshot(
        session_id: str | None = None,
        url: str | None = None,
        xpath: str | None = None,
        css: str | None = None,
        full_page: bool = False,
        width: int | None = None,
        height: int | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        save: bool = False,
        filename: str | None = None,
    ) -> Image | ToolResult:
        """Capture a PNG of the page and return it as an image you can see.

        Three modes: pass xpath (or css) for one element, full_page for the
        whole scrollable page, or none of them for the visible viewport.

        Only reach for this when the *visual* result matters — layout, styling,
        a rendered chart. To read content, extract is far cheaper.

        Set save to also keep it with the session's files, where it gets a URL
        that opens in a browser. Worth doing whenever a person will look at it:
        many clients cannot display an image returned by a tool, and every one
        of them can follow a link. Saving also returns the file's name, which is
        what keep_file and session_files take.
        """
        result = run(
            session_id,
            lambda s: actions.screenshot(
                s,
                url=url,
                xpath=xpath,
                css=css,
                full_page=full_page,
                width=width,
                height=height,
                wait_timeout=wait_timeout,
                save=save,
                filename=filename,
            ),
        )
        image = Image(data=base64.b64decode(result["image"]), format="png")
        entry = result.get("file")
        if entry is None:
            return image
        # A saved screenshot has a name, and the name is the whole point: it is
        # the argument keep_file takes. Chrome deduplicates, so `shot.png` can
        # land as `shot (1).png` and the caller cannot derive it — returning the
        # image alone left the one thing you have to know discoverable only by
        # listing the files and guessing which entry was yours. The HTTP surface
        # always returned it; this is the tool catching up.
        return ToolResult(
            content=[image.to_image_content()], structured_content={"file": entry}
        )

    @mcp.tool(annotations=hints("Save the page as PDF"))
    def save_pdf(
        session_id: str | None = None,
        url: str | None = None,
        filename: str | None = None,
    ) -> dict:
        """Print the current page to PDF and keep it with the session's files.

        This is the browser's own print output, so text stays selectable and the
        whole document is included rather than just the viewport. Returns the
        stored file; session_files gives it a link.
        """
        return run(
            session_id,
            lambda s: actions.save_pdf(s, url=url, filename=filename),
        )
