"""The MCP tool surface.

Thin wrappers over ``actions.py``. Type hints here are not decoration: FastMCP
turns each signature into the tool's JSON schema, so the parameter names, types
and defaults written below are exactly what a model sees and fills in.

Docstrings are prompt. They are written for a model deciding whether to call the
tool, not for a developer reading the source.

No tool takes a ``session_id``. A caller names its session — ``?session=`` or
``X-Session-Key`` — and ``sessions.py`` turns that name into the browser it
holds. The Grid's own id is never a parameter and never a result (§F2.12).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .. import secrets as secrets_module
from ..core.actions import (
    DIALOG_ACTIONS,
    DIALOG_TIMEOUT,
    FRAME_ACTIONS,
    KEY_NAMES,
    MOUSE_ACTIONS,
    WAIT_TIMEOUT,
    Actions,
)
from ..core.browser import BROWSERS
from ..core.probe import DEFAULT_LIMIT as OUTLINE_LIMIT
from ..session.sessions import NAME_PARAM, SessionManager
from .annotations import hints


def _lowered(value):
    return value.strip().lower() if isinstance(value, str) else value


# A closed set, published as an enum so a model planning against the schema can
# see every choice — the first agent to fly a real app never found `hover`
# because `action` read as an open string (saga §F2.1). Lowercased BEFORE the
# check, because these were plain strings the action layer lowercased: `Hover`
# worked over MCP and still works over HTTP and in a flow, and publishing the
# list must not start refusing a caller for writing what used to be fine.
def _blank_is_unset(value):
    """A blank optional choice means "not given", as it did before the enum.

    `normalize_browser` has always treated an empty or whitespace-only browser
    as "carry on with the default", and a client that encodes an omitted
    optional as "" relied on it. A bare enum refuses that before the tool runs,
    so the blank is turned back into None here and the default decides.
    """
    resolved = _lowered(value)
    return None if resolved == "" else resolved


MouseAction = Annotated[Literal[MOUSE_ACTIONS], BeforeValidator(_lowered)]
DialogAction = Annotated[Literal[DIALOG_ACTIONS], BeforeValidator(_lowered)]
FrameAction = Annotated[Literal[FRAME_ACTIONS], BeforeValidator(_lowered)]
Browser = Annotated[Literal[BROWSERS] | None, BeforeValidator(_blank_is_unset)]

# Said the same way everywhere, because the one new way to get a call wrong is
# to pass both selectors or neither, and the fix has to be in front of the model
# at the point it is choosing.
SELECTOR = (
    "Address the element with a selector: {\"xpath\": \"//button[@type='submit']\"} "
    "or {\"css\": \"button[type=submit]\"} - exactly one of the two, never both "
    "and never neither."
)


def _as_selector(value):
    """A selector sent as a JSON string, turned back into an object.

    Some clients stringify object arguments, and pydantic refuses a string
    where a model is expected with a message about dictionaries — which tells
    the model nothing it can act on. The enums above take the same treatment
    for the same reason.

    A string that is not JSON is handed on unchanged, so the error a caller
    sees is the selector's own rather than one about parsing.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


class Selector(BaseModel):
    """Which element to act on: xpath or css, exactly one.

    One object rather than two flat arguments because they are one choice
    (§F2.14): every tool that takes one takes the other, for the same element,
    under the same rule — which used to be written out in a dozen descriptions
    and is now said once, here.
    """

    # Refused rather than dropped, as `SecretRef` does: an unknown key is a
    # caller's mistake, and ignoring it silently runs a different request.
    model_config = ConfigDict(extra="forbid")

    xpath: str | None = Field(
        None, description="An XPath expression, e.g. //button[@type='submit']"
    )
    css: str | None = Field(
        None, description="A CSS selector, e.g. button[type=submit]"
    )

    @model_validator(mode="after")
    def _exactly_one(self):
        """Never a fallback from one to the other: a typo in the first would
        become a click on whatever the second found, and the run would report
        `ok` while it drifted (§F2.14)."""
        if bool(self.xpath) == bool(self.css):
            raise ValueError(
                "a selector takes xpath or css - exactly one, not both and not "
                "neither"
            )
        return self


SelectorArg = Annotated[Selector | None, BeforeValidator(_as_selector)]

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

Name your session first: add ?{NAME_PARAM}=<name> to the MCP URL, or send an \
X-Session-Key header. Every call is then about that session, and no call takes \
a session id — calling again with the same name is how you get the same browser \
back, after a reconnect or a restart.

Lifecycle:
1. Call open_session to start a browser. Pass browser="firefox" for Firefox; \
the default is Chrome. Every other tool behaves identically on both.
2. Call the other tools. They act on your session's browser.
3. Call end_browser when finished, including after a failure. Browsers are a \
scarce resource and an abandoned one holds a slot until the Grid reaps it. Your \
session survives it, so open_session picks up where you left off.

Elements are addressed by XPath or by CSS - pass one or the other, never \
both: selector={{"xpath": "//input[@name='q']"}} or \
selector={{"css": "input[name=q]"}}.

Most actions take an optional url. It is not an assertion: if the browser is \
somewhere else it navigates there first, so you can jump straight to a page \
instead of clicking a path to it.

Prefer extract to read a page — it is far cheaper than a screenshot. Use \
execute_script for anything the other tools do not cover, scrolling included.

How to drive this well — when to screenshot rather than extract, what a timeout \
on a good XPath usually means, how to write a flow — is at \
skill://selenium-flow/SKILL.md. Read it before your first call; it ships with \
this server, so it describes this version of it.
"""



def register(
    mcp: FastMCP, actions: Actions, sessions: SessionManager, catalogue=None
) -> None:
    """Register every action as an MCP tool on ``mcp``."""

    def run(
        call: Callable[[str], dict],
        *,
        reshapes: bool = False,
    ) -> dict:
        """Whose browser this is, then act on it. See ``sessions.act``, which
        the HTTP surface calls too so the two cannot drift."""
        return sessions.act(sessions.name(), call, reshapes=reshapes)

    @mcp.tool(annotations=hints("Open browser session", destructive=True))
    def open_session(
        url: str | None = None,
        browser: Browser = None,
        width: int | None = None,
        height: int | None = None,
        page_load_timeout: int | None = None,
        script_timeout: int | None = None,
        fresh: bool = False,
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

        fresh=true opens on about:blank instead of going back to the page this
        session was last on. Use it to run something from a known start — a
        login flow you want to exercise signed out. It keeps the browser and
        window this session was using; only the page is dropped.

        Returns the session name and the settings the browser was opened with.
        There is no browser id to keep: every later call is about this session
        because of how you named yourself, not because of anything you pass.
        """
        return sessions.open_browser(
            sessions.name(),
            url=url,
            fresh=fresh,
            browser=browser,
            width=width,
            height=height,
            page_load_timeout=page_load_timeout,
            script_timeout=script_timeout,
        )

    @mcp.tool(annotations=hints("End browser", destructive=True, idempotent=True))
    def end_browser() -> dict:
        """Quit the browser and free its Grid slot. Do this when finished.

        Ends the *browser*, not your session. The session keeps the browser
        choice and the page you were on, so a later open_session() with no
        arguments picks up exactly where this left off — and any files the
        browser had are gone with it, because the Grid keeps them per browser.

        Call it on failure paths too. Browsers are scarce and an abandoned one
        holds a Grid slot until it is reaped.
        """
        name = sessions.name()
        sessions.end_browser(name)
        return {"success": True, "session": name}

    @mcp.tool(annotations=hints("Navigate to URL", idempotent=True))
    def navigate(url: str) -> dict:
        """Go to a URL. Returns the resulting URL and page title.

        Use this to move somewhere unconditionally. To act on a page in one
        step, prefer passing url to click, write, extract or screenshot.
        """
        return run(lambda s: actions.navigate(s, url))

    # The action list is interpolated so it cannot drift from the tuple the
    # action layer validates against.
    @mcp.tool(
        description=(
            "Perform a mouse action on an element.\n\n"
            f"action is one of: {', '.join(MOUSE_ACTIONS)}.\n\n"
            "click is the common case. hover opens menus that only appear on "
            "mouse-over, and leaves the pointer there, so such a menu stays open "
            "for the next call: hover it, then click the item inside. right_click "
            "opens context menus. scroll_to brings an "
            "off-screen element into view, which is often what a click on a "
            "long page needs first.\n\n"
            "The pointer moves onto the element first, so after a click it is "
            "on what you clicked, the way a person's would be. That move is an "
            "instant jump unless you set glide=true, which sends many small "
            "moves instead - set it for an interface that watches movement "
            "rather than arrival: sliders, sortable lists, drag thresholds, and "
            "menus that track which way the pointer came from. To drag "
            "something, use drag.\n\n"
            "Returns the URL and title *after* the action, so any navigation it "
            "caused is visible in the result.\n\n" + SELECTOR
        ),
        annotations=hints("Mouse action on an element", destructive=True),
    )
    def interact(
        action: MouseAction,
        selector: SelectorArg = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        glide: bool = False,
    ) -> dict:
        return run(
            lambda s: actions.interact(
                s,
                action,
                selector=selector,
                url=url,
                wait_timeout=wait_timeout,
                glide=glide,
            ),
        )

    @mcp.tool(
        description=(
            "Drag one element onto another, or by an offset in pixels.\n\n"
            "Address the thing being dragged with selector. Say "
            "where it goes with EITHER to - a destination element "
            "- OR by_x and by_y, a distance from where it started. A range "
            "slider is the by_x case; a card into a column is the element "
            "case.\n\n"
            "The pointer presses, travels and releases, holding briefly at "
            "each end because several drag libraries arm on a delay rather than "
            "on the press. glide defaults to true here: incremental movement is "
            "most of what a drag is for, and a library watching pointermove "
            "sees nothing without it.\n\n"
            "This drives pointer events, and on Chrome that is also enough for "
            "native HTML5 drag-and-drop (dragstart through drop). On Firefox "
            "the native drop does not complete - measured, not assumed - so a "
            "draggable=true element there may need the page's own fallback."
        ),
        annotations=hints("Drag an element", destructive=True),
    )
    def drag(
        selector: SelectorArg = None,
        to: SelectorArg = None,
        by_x: int | None = None,
        by_y: int | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        glide: bool = True,
    ) -> dict:
        return run(
            lambda s: actions.drag(
                s,
                selector=selector,
                to=to,
                by_x=by_x,
                by_y=by_y,
                url=url,
                wait_timeout=wait_timeout,
                glide=glide,
            ),
        )

    @mcp.tool(
        description=(
            "Move into an iframe, or back out of it.\n\n"
            f"action is one of: {', '.join(FRAME_ACTIONS)}. Use switch with an "
            "a selector (or index) to go into a frame, parent to go up one level, and "
            "default to return to the main page.\n\n"
            "Selenium does not look inside frames: an element in one is "
            "invisible to every locator until you switch in. **The switch "
            "sticks** — every later call stays in that frame until you switch "
            "back, so if a locator that should work is failing, check "
            "session://current for in_frame.\n\nName the frame with a selector "
            "or an index — one of the two, not both. parent and default take "
            "none of them."
        ),
        annotations=hints("Switch into or out of an iframe", idempotent=True),
    )
    def frame(
        action: FrameAction = "switch",
        selector: SelectorArg = None,
        index: int | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        return run(
            lambda s: actions.frame(
                s,
                action=action,
                selector=selector,
                index=index,
                wait_timeout=wait_timeout,
            ),
        )

    @mcp.tool(annotations=hints("Resize window", idempotent=True))
    def resize(
        width: int | None = None,
        height: int | None = None,
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
        action: DialogAction = "accept",
        text: str | None = None,
        wait_timeout: int = DIALOG_TIMEOUT,
    ) -> dict:
        return run(
            lambda s: actions.dialog(
                s, action=action, text=text, wait_timeout=wait_timeout
            ),
        )

    @mcp.tool(annotations=hints("Attach a file to a file input"))
    def upload_file(
        selector: SelectorArg = None,
        text: str | None = None,
        filename: str | None = None,
        mime_type: str | None = None,
        content: str | None = None,
        path: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        kept: str | None = None,
    ) -> dict:
        """Attach a file to a file input.

        For anything you wrote yourself — JSON, CSV, YAML, markdown, plain text
        — put it straight in `text` and give it a `filename`. There is no need
        to encode it; the server writes the real file and sends it to the
        browser, which runs on another machine.

        `kept` takes the name of a file keep_file has kept, which is how you
        give a page back something a browser downloaded — an export from one
        site uploaded to another, without the bytes passing through you.
        session_files lists what is there.

        Use `content` (base64) only for binary, and `path` only for a file
        already on the server's filesystem. Pass exactly one of the four.

        The page reads the file's type from the **filename extension**, so name
        it `report.csv` rather than `report`. If you give a name without an
        extension, `mime_type` is used to pick one.

        Address the input with a selector: exactly one of xpath or css.
        """
        return run(
            lambda s: actions.upload_file(
                s,
                selector=selector,
                text=text,
                content=content,
                filename=filename,
                mime_type=mime_type,
                path=path,
                url=url,
                wait_timeout=wait_timeout,
                kept=kept,
            ),
        )

    @mcp.tool(annotations=hints("Type text into a field"))
    def write(
        text: str | None = None,
        selector: SelectorArg = None,
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

        Address the field with a selector: exactly one of xpath or css.

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
                    lambda s: actions.write(
                    s,
                    text,
                    selector=selector,
                    url=url,
                    clear=clear,
                    submit=submit,
                    wait_timeout=wait_timeout,
                ),
            )

        return secrets_module.perform_write(
            catalogue,
            actions,
            sessions,
            sessions.name(),
            {
                "text": text,
                "url": url,
                "secret": secret,
                "selector": selector,
                "clear": clear,
                "submit": submit,
                "wait_timeout": wait_timeout,
            },
        )

    # The description is passed rather than left as a docstring so the real key
    # list is interpolated in — a model guessing key names gets a 400, and the
    # list cannot drift from the mapping it is generated from.
    @mcp.tool(
        description=(
            "Press a key, at an element or wherever focus currently is.\n\n"
            "key is a name, one character, or a combination joined with +. "
            "Names take the browser's spelling or Selenium's, in any case: "
            "Enter, Tab, Escape, Backspace, ArrowLeft, PageDown, F5 - or enter, "
            "arrow_left, page_down. A combination holds each modifier for the "
            "keys after it: Control+a, Shift+Tab. Every name: "
            f"{', '.join(KEY_NAMES)}.\n\n"
            "Not a reliable way to scroll - page_down only moves the page when "
            "focus happens to be on the scrollable container. Use execute_script "
            "to scroll.\n\nTo aim the key at an element, pass a selector; "
            "with neither, it goes wherever focus already is."
        ),
        annotations=hints("Press a named key", destructive=True),
    )
    def press_key(
        key: str,
        selector: SelectorArg = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        return run(
            lambda s: actions.press_key(
                s, key, selector=selector, url=url, wait_timeout=wait_timeout
            ),
        )

    @mcp.tool(annotations=hints("Read an element", read_only=True, idempotent=True))
    def extract(
        selector: SelectorArg = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        """Read an element's visible text and innerHTML.

        The primary way to read a page — prefer it over a screenshot, which
        costs far more. //body reads everything, but a narrower selector keeps
        the result small.

        Address the element with a selector: exactly one of xpath or css.
        """
        return run(
            lambda s: actions.extract(
                s, selector=selector, url=url, wait_timeout=wait_timeout
            ),
        )

    @mcp.tool(
        description=(
            "Map what is on the page: every element worth acting on, with a "
            "selector for it and whether it can actually be used.\n\nThis is "
            "how you find selectors - not by reading HTML with extract, and "
            "not by writing a script to walk the DOM. Each entry carries one "
            "selector, checked to match exactly one element, as css where the "
            "page gives something stable and xpath by text where it does "
            "not.\n\nvisible says whether it can be used now, and reason says "
            "what is in the way when it cannot: hidden (an ancestor is "
            "display:none - often a menu that opens on hover), covered "
            "(blocked_by names what is on top), zero_size, offscreen, "
            "disabled.\n\nScope it with a selector to one part of the page, "
            "filter by text to find one thing by its label, and raise limit "
            "when 50 entries are not enough. interactive=false includes every "
            "element rather than only the ones you can act on.\n\nRead it "
            "before acting, and again after a page changes under a flow you "
            "are repairing."
        ),
        annotations=hints("Map the page's elements", read_only=True, idempotent=True),
    )
    def outline(
        selector: SelectorArg = None,
        text: str | None = None,
        limit: int = OUTLINE_LIMIT,
        interactive: bool = True,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        return run(
            lambda s: actions.outline(
                s,
                selector=selector,
                text=text,
                limit=limit,
                interactive=interactive,
                url=url,
                wait_timeout=wait_timeout,
            ),
        )

    @mcp.tool(annotations=hints("Run JavaScript in the page", destructive=True))
    def execute_script(
        script: str, url: str | None = None
    ) -> dict:
        """Run JavaScript in the page and return its result.

        Check the other tools first: reaching for a script usually means a
        cheaper one exists. interact clicks, double-clicks, right-clicks, hovers
        and scrolls to an element - and only its hover opens a menu that appears
        on :hover, because a script's synthetic events never set :hover. Every
        tool that names an element already waits for it.

        What is left is this: scrolling the page (window.scrollTo(0, 2000)),
        computed styles, reading many things at once, direct DOM access,
        setting a value on an input `write` cannot reach. Use `return` to send
        a value back.

        Not drag and drop - `drag` does that with real pointer input, which a
        script cannot produce.
        """
        return run(lambda s: actions.execute_script(s, script, url=url))

    # Named through `name=` because `assert` is a Python keyword and cannot be a
    # function name. `routes.METHOD_ALIASES` is the other half of that.
    @mcp.tool(
        name="assert",
        description=(
            "Assert that the page is what you expect, with JavaScript that must "
            "return true.\n\nLike execute_script, except the answer has to be a "
            "boolean: return a comparison, not the thing itself - "
            "`return !!document.querySelector('#total')`, not the element. "
            "Anything else is refused.\n\nIt asks again until the answer is "
            "true or wait_timeout passes, so an assertion straight after a click "
            "does not have to know how long a route change takes. "
            "wait_timeout=0 asks once.\n\n"
            "**For a guard - something that must be true BEFORE the flow acts "
            "- set stable_for.** Asking until true means EVENTUALLY true, and "
            "an app that paints its signed-in shell for a moment before "
            "redirecting to the login page satisfies 'am I signed in' during "
            "that moment. stable_for=1 makes the answer hold for a second "
            "before it counts. Both it and wait_timeout are in seconds.\n\n"
            "Give message the sentence whoever "
            "reads the failure should see - in a flow it becomes the failing "
            "step's error, and a flow cannot continue past one. Without a "
            "message the failure names only the page it was false on.\n\n"
            "Use it to make a flow say what must be "
            "true: the page it landed on, that a form saved, or that it should "
            "not run at all because you are already signed in."
        ),
        annotations=hints("Assert the page is what you expect", destructive=True),
    )
    def assert_page(
        script: str,
        message: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        stable_for: float = 0,
    ) -> dict:
        return run(
            lambda s: actions.assert_(
                s,
                script,
                message=message,
                url=url,
                wait_timeout=wait_timeout,
                stable_for=stable_for,
            ),
        )

    @mcp.tool(annotations=hints("Capture a screenshot"))
    def screenshot(
        url: str | None = None,
        selector: SelectorArg = None,
        full_page: bool = False,
        width: int | None = None,
        height: int | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        save: bool = True,
        filename: str | None = None,
    ) -> Image | ToolResult:
        """Capture a PNG of the page and return it as an image you can see.

        Three modes: pass a selector for one element, full_page for the
        whole scrollable page, or none of them for the visible viewport.

        Only reach for this when the *visual* result matters — layout, styling,
        a rendered chart. To read content, extract is far cheaper.

        By default it is also kept with the session's files, where it has a URL
        that opens in a browser and shows up in the admin page - so a person can
        see what you saw, whether or not your client can display an image. The
        result then carries the file's name, which is what keep_file and
        session_files take.

        When it could not be stored - a page the browser will not download from,
        say - the result carries file_error instead of file, and the image still
        comes back. There is no name to keep in that case.

        **To show a person what you saw, give them the file's absolute_url.**
        It opens in any browser, needs no bearer token, and is the only form of
        this they can actually look at - do not paste the image back into your
        reply and do not describe it instead. Markdown works: ![](absolute_url).
        The link is signed and time-limited when this server has a token; with
        authentication off there is nothing to sign and the plain path is the
        answer.

        A server that has not been told its public address has no absolute_url
        to give: the file carries a relative url instead, which needs the
        address you reached this server on.

        Those files die with the browser. keep_file(name) is what makes one
        outlive it. Pass save=false for a capture nobody should even be able to
        look at later - a flow taking thirty frames it will never reopen.
        """
        result = run(
            lambda s: actions.screenshot(
                s,
                url=url,
                selector=selector,
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
        unsaved = result.get("file_error")
        if entry is None and unsaved is None:
            return image
        if entry is None:
            # The capture survived and the file did not. The HTTP surface says
            # why; an MCP caller that got only the image would be told nothing
            # and would look for a name that is never coming.
            return ToolResult(
                content=[image.to_image_content()],
                structured_content={"file_error": unsaved},
            )
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
        url: str | None = None,
        filename: str | None = None,
    ) -> dict:
        """Print the current page to PDF and keep it with the session's files.

        This is the browser's own print output, so text stays selectable and the
        whole document is included rather than just the viewport.

        Returns the stored file. **Give a person its absolute_url** - a link
        that opens in any browser and needs no bearer token, signed and
        time-limited when this server has a token to sign with. That is how
        somebody reads the PDF; nothing else in this result is any use to them.
        Without a public address configured the file carries a relative url.
        """
        return run(
            lambda s: actions.save_pdf(s, url=url, filename=filename),
        )
