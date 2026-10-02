"""The MCP tool surface.

Thin wrappers over ``actions.py``. Each tool here is a declaration — a signature
and a docstring — and its name, annotations and body come from its row in
``core/capabilities.py``, the table the HTTP surface is mounted from too. Type
hints here are not decoration: FastMCP turns each signature into the tool's JSON
schema, so the parameter names, types and defaults written below are exactly
what a model sees and fills in.

Docstrings are prompt. They are written for a model deciding whether to call the
tool, not for a developer reading the source.

No tool takes a ``session_id``. A caller names its session — ``?session=`` or
``X-Session-Key`` — and ``sessions.py`` turns that name into the browser it
holds. The Grid's own id is never a parameter and never a result (§F2.12).
"""

from __future__ import annotations

import base64
import functools
import inspect
import json
from collections.abc import Callable
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .. import secrets as secrets_module
from ..core.actions import (
    DIALOG_ACTIONS,
    FRAME_ACTIONS,
    MOUSE_ACTIONS,
    PRINT_FORMATS,
    Actions,
)
from ..core.capabilities import CAPABILITIES, Capability, capability
from ..core.defaults import BROWSERS
from ..core.probe import DEFAULT_LIMIT as OUTLINE_LIMIT
from ..core.recipe import DIALOG_TIMEOUT, WAIT_TIMEOUT
from ..session.sessions import NAME_PARAM, SessionManager
from . import clients


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
PrintFormat = Annotated[Literal[PRINT_FORMATS], BeforeValidator(_lowered)]
Browser = Annotated[Literal[BROWSERS] | None, BeforeValidator(_blank_is_unset)]

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


# One object rather than two flat arguments because they are one choice
# (§F2.14): every tool that takes one takes the other, for the same element,
# under the same rule — which used to be written out in a dozen descriptions
# and is now said once, here.
#
# The docstring below is NOT a note for this file's reader. Pydantic publishes
# it as the schema's description in every tool that takes a selector, and a
# refusal quotes it, so it is written for a model filling the argument.
class Selector(BaseModel):
    """Which element: {"css": "button.go"} or {"xpath": "//button[@type='submit']"} - exactly one of the two."""  # noqa: E501 - one line, as a schema description

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

"""

# Appended only when the skill is actually being served. With
# `--mcp-skill false` (or package data missing) nothing registers those
# resources, and telling a client to read a URI that cannot be read is worse
# than saying nothing (Copilot, #36).
SKILL_POINTER = """
How to drive this well — when to screenshot rather than extract, what a timeout \
on a good XPath usually means, how to write a flow — is at \
skill://selenium-flow/SKILL.md. Read it{how} before your first call; it ships \
with this server, so it describes this version of it.
"""

# For a client whose model cannot read resources (§F3.2). Everything to read is
# still named by URI, so it is told how to read one rather than handed a
# different vocabulary.
READING_POINTER = """
Everything this server has to read is a URI — session://current, flow://flows, \
secret://secrets and the like, wherever a hint or an error names one. Read one \
with read_resource(uri); list_resources shows them all.
"""


def instructions(skill_available: bool = True, reads_resources: bool = True) -> str:
    """What a client reads at connect, for the server it got and what it can read."""
    text = INSTRUCTIONS
    if not reads_resources:
        text += READING_POINTER
    if skill_available:
        how = "" if reads_resources else " with read_resource"
        text += SKILL_POINTER.format(how=how)
    return text


def first_instructions(skill_available: bool = True) -> str:
    """What the server object starts with, before any handshake has named a client.

    The text `InstructionsFor` gives a client it cannot tell: the same function
    and the same reading of "can it read resources", so the constructor and the
    handshake cannot drift into two defaults.
    """
    unknown = clients.reads_resources(clients.Caller.stdio(client=""))
    return instructions(skill_available, unknown)


class InstructionsFor(Middleware):
    """Answer the handshake with the instructions for the client it came from.

    One server object serves every client, so the text is chosen per handshake
    rather than fixed at construction: a client that cannot read resources is
    told to use read_resource, which a client that can would only be confused by.
    """

    def __init__(self, skill_available: bool):
        self.skill_available = skill_available

    def _text(self, context) -> str:
        caller = clients.caller(clients.named_in(context.message))
        return instructions(self.skill_available, clients.reads_resources(caller))

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is not None:
            result.instructions = self._text(context)
        return result

    async def on_discover(self, context, call_next):
        result = await call_next(context)
        if isinstance(result, dict):
            result["instructions"] = self._text(context)
        elif result is not None:
            result.instructions = self._text(context)
        return result


def _as_image(result: dict) -> Image | ToolResult:
    """A screenshot as an MCP image block, which is what a tool caller can use.

    The one sanctioned difference between the surfaces (AGENTS.md): an HTTP
    caller gets the same capture as base64 JSON.
    """
    image = Image(data=base64.b64decode(result["image"]), format="png")
    # Beside the image, each only when present:
    # - `file`: a saved screenshot has a name the caller cannot derive (a
    #   second `shot.png` is kept as `shot (1).png`);
    # - `file_error`: the capture survived and the file did not, and an MCP
    #   caller given only the image would look for a name never coming;
    # - `site_data`: the first call after a silent reopen says what came
    #   back (``sessions.settle``) — dropped here, it is never said at all.
    told = {
        k: result[k] for k in ("file", "file_error", "site_data")
        if result.get(k) is not None
    }
    if not told:
        return image
    return ToolResult(content=[image.to_image_content()], structured_content=told)


def register(
    mcp: FastMCP, actions: Actions, sessions: SessionManager, catalogue=None
) -> None:
    """Register every capability as an MCP tool on ``mcp``.

    Each tool below is a declaration: its signature is the schema and its
    docstring (or ``description=``) the prompt, both read by FastMCP and both
    written out by hand. Its name, its annotations and what it does come from
    its row in ``core.capabilities``: ``action`` gives a declaration the one
    body every action shares, and ``own`` publishes the two whose body is their
    own. A row left undeclared stops the server here.
    """
    declared: set[str] = set()

    def publish(fn, name: str, description: str | None):
        row = capability(name)
        declared.add(row.name)
        return mcp.tool(
            name=row.name, description=description, annotations=row.annotations
        )(fn)

    def act(row: Capability, arguments: dict):
        """Whose browser this is, then act on it. See ``sessions.act``, which
        the HTTP surface calls too so the two cannot drift."""
        if "secret" in arguments:
            # A capability that can type a secret (`write`) types one through
            # the catalogue, and with neither a secret nor text has nothing to
            # type at all.
            if arguments["secret"] is None and arguments.get("text") is None:
                raise ValueError(f"{row.name} needs text, or a secret to supply it")
            if arguments["secret"] is not None:
                return secrets_module.perform_write(
                    catalogue, actions, sessions, clients.caller().name, arguments
                )
            del arguments["secret"]
        return sessions.act(
            clients.caller(),
            lambda s: getattr(actions, row.method)(s, **arguments),
            reshapes=row.reshapes,
        )

    def action(
        name: str,
        *,
        description: str | None = None,
        shape: Callable[[dict], object] | None = None,
    ):
        """Publish a declaration whose body is ``act``, shaped by ``shape``."""
        row = capability(name)

        def bind(declaration):
            signature = inspect.signature(declaration)

            @functools.wraps(declaration)
            def tool(*args, **kwargs):
                given = signature.bind(*args, **kwargs)
                given.apply_defaults()
                result = act(row, dict(given.arguments))
                return shape(result) if shape else result

            return publish(tool, name, description)

        return bind

    def own(name: str, *, description: str | None = None):
        """Publish a declaration whose body is its own."""
        return lambda fn: publish(fn, name, description)

    @own("open_session")
    def open_session(
        url: str | None = None,
        browser: Browser = None,
        width: int | None = None,
        height: int | None = None,
        page_load_timeout: int | None = None,
        script_timeout: int | None = None,
        insecure: bool | None = None,
        fresh: bool = False,
        restore_site_data: bool = True,
    ) -> dict:
        """Start this session's browser, or come back to the one it had. Call it before
        anything else: nothing opens a browser for you.

        With no arguments it returns to the same browser, window and page, which is
        the right call after a browser was reaped or ended. Calling it while you
        hold a browser ends that one first, which is how you switch:
        open_session(browser="firefox"). Files that browser had and you did not keep
        go with it.

        Set width and height when layout matters; the headless default is narrow.
        fresh=true starts on about:blank. Saved site data comes back by default;
        restore_site_data=false opens without it and deletes it, which is how you
        start as a new user. page_load_timeout bounds a navigation that
        hangs. insecure=true accepts a self-signed certificate; use it only for a site
        you know has one. An insecure browser gets no saved site data.
        """
        return sessions.open_browser(
            clients.caller(),
            url=url,
            fresh=fresh,
            restore_site_data=restore_site_data,
            browser=browser,
            width=width,
            height=height,
            page_load_timeout=page_load_timeout,
            script_timeout=script_timeout,
            insecure=insecure,
        )

    @action("save_site_data")
    def save_site_data(url: str | None = None) -> dict:
        """Save this session's site data, so a new browser comes back signed in.

        It keeps every cookie the browser holds, the localStorage of the sites it has
        been to, and the sessionStorage of the page it is on. Call it right after a
        sign-in is confirmed, and after changing a setting you want kept; never before
        checking you landed, or you keep a failed sign-in.

        One save covers every site a call ended on, while the session keeps its
        history (a day by default, 100 sites): one passed through inside a call is not
        read, and one last visited longer ago loses its storage at the next save, its
        cookies staying. Each save replaces the last, so a save after signing out
        saves you signed out. Every browser opened for this session has it back before
        open_session returns, the one that replaces a reaped browser included. Values
        are never returned; session://site-data lists what is saved.
        """

    @own("end_browser")
    def end_browser() -> dict:
        """Quit this session's browser and free its Grid slot.

        Call it when finished, and on failure paths too: browsers are scarce, and an
        abandoned one holds its slot until the Grid reaps it. The session survives,
        so a later open_session() comes back to the same page. Files the browser had
        and you did not keep go with it.
        """
        caller = clients.caller()
        sessions.end_browser(caller)
        return {"success": True, "session": caller.name}

    @action("navigate")
    def navigate(url: str) -> dict:
        """Go to a URL. Returns the URL and title the browser ended on.

        Every other action also takes url, and navigates there first only if the
        browser is somewhere else, so you rarely need this as a separate call.
        """

    # The action list is interpolated so it cannot drift from the tuple the
    # action layer validates against.
    @action(
        "interact",
        description=(
            f"A mouse action on an element: {', '.join(MOUSE_ACTIONS[:-1])} or "
            f"{MOUSE_ACTIONS[-1]}.\n\n"
            "hover leaves the pointer where it put it, so a menu that opens "
            "on hover stays open for the next call: hover it, then click "
            "inside. scroll_to brings an off-screen element into view.\n\n"
            "The pointer jumps onto the element unless glide=true, which "
            "moves it in small steps, for interfaces that react to movement: "
            "sliders, sortable lists, menus that track direction. To drag "
            "something, use drag.\n\n"
            "Returns the URL and title after the action, so a navigation it "
            "caused shows."
        ),
    )
    def interact(
        action: MouseAction,
        selector: SelectorArg = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        glide: bool = False,
    ) -> dict: ...

    @action(
        "drag",
        description=(
            "Drag an element onto another element (to), or by an offset in "
            "pixels (by_x, by_y); give one or the other. A range slider is "
            "by_x; a card into a column is to.\n\n"
            "The pointer presses, glides and releases with short holds at "
            "each end, which drag libraries need. On Chrome this also "
            "completes native HTML5 drag-and-drop; on Firefox it does not, so "
            "a draggable=true element there may need the page's own fallback."
        ),
    )
    def drag(
        selector: SelectorArg = None,
        to: SelectorArg = None,
        by_x: int | None = None,
        by_y: int | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        glide: bool = True,
    ) -> dict: ...

    @action(
        "frame",
        description=(
            "Move into an iframe (switch, with a selector or an index), up "
            "one level (parent), or back to the page (default).\n\n"
            "Elements inside a frame are invisible to every tool until you "
            "switch in, and the switch sticks for every later call. If a "
            "selector that should work keeps failing, read session://current: "
            "in_frame says where you are."
        ),
    )
    def frame(
        action: FrameAction = "switch",
        selector: SelectorArg = None,
        index: int | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict: ...

    @action("resize")
    def resize(
        width: int | None = None,
        height: int | None = None,
    ) -> dict:
        """Resize the browser window.

        Set a size before judging anything visual: the headless default is small and
        varies between Grid nodes. The size sticks to the session, so a browser
        reopened after the Grid reaps it comes back at this size.
        """

    @action(
        "dialog",
        description=(
            "Answer a native alert, confirm or prompt: accept, dismiss, read "
            "(see the message without answering), or send_text (fill a prompt "
            "with text and accept it).\n\n"
            "An open dialog blocks every other command, so if a call fails on "
            "an unexpected alert, clear it here."
        ),
    )
    def dialog(
        action: DialogAction = "accept",
        text: str | None = None,
        wait_timeout: int = DIALOG_TIMEOUT,
    ) -> dict: ...

    @action("upload_file")
    def upload_file(
        selector: SelectorArg = None,
        text: str | None = None,
        filename: str | None = None,
        mime_type: str | None = None,
        content: str | None = None,
        path: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        file: str | None = None,
    ) -> dict:
        """Attach a file to a file input (selector). Give exactly one source:

        - text, for anything you wrote (JSON, CSV, markdown), with a filename;
        - file, any file this session has, by its uri from session://files — a
          screenshot, a download or a file in Files — without its bytes passing
          through you;
        - content, base64, for other binary;
        - path, a file already on the server.

        The page reads the type from the filename's extension, so name it
        report.csv, not report; without one, mime_type picks it.
        """

    @action("write")
    def write(
        text: str | None = None,
        selector: SelectorArg = None,
        url: str | None = None,
        clear: bool = True,
        submit: bool = False,
        wait_timeout: int = WAIT_TIMEOUT,
        secret: SecretRef | None = None,
    ) -> dict:
        """Type into an input, textarea or contenteditable, and read the value back so
        you can see it landed. clear (default true) replaces what is there;
        submit=true presses Enter afterwards.

        To type a secret, pass secret={"name": ..., "key": ...} instead of text;
        secret://secrets lists them. The server types it and you never see it: value
        comes back null. A secret works only on the sites it allows, checked against
        the page you are on, so navigate there first.
        """

    # The key names are not listed here: a name that is not one is refused with
    # every name there is, generated from the mapping itself, which is where a
    # model that guessed wrong is looking anyway (§F3.5).
    @action(
        "press_key",
        description=(
            "Press a key or a combination, at an element (selector) or "
            "wherever focus already is.\n\n"
            "key is a name, a single character, or keys joined with +: Enter, "
            "Tab, Escape, ArrowLeft, PageDown, F5, Control+a, Shift+Tab. The "
            "browser's spelling (ArrowLeft) and Selenium's (arrow_left) both "
            "work, in any case.\n\n"
            "Not a reliable way to scroll: PageDown moves the page only when "
            "focus is on the thing that scrolls. Use execute_script to "
            "scroll."
        ),
    )
    def press_key(
        key: str,
        selector: SelectorArg = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict: ...

    @action("extract")
    def extract(
        selector: SelectorArg = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict:
        """Read an element's visible text and HTML.

        The cheapest way to read a page; prefer it to a screenshot. //body reads
        everything, and a narrower selector keeps the result small. To find a
        selector, use outline.
        """

    @action(
        "outline",
        description=(
            "Map the page: the elements worth acting on, each with a selector "
            "checked to match exactly one element, and whether it can be used "
            "now.\n\n"
            "Use it to find selectors instead of reading HTML. When visible "
            "is false, reason says why: hidden (often a closed menu; "
            "revealed_by and open_with say what opens it), covered "
            "(blocked_by names what is on top), zero_size, offscreen or "
            "disabled.\n\n"
            "Content is listed before navigation, header, footer and "
            "sidebars, and region names each entry's landmark. A total above "
            "count means the list was cut: scope it with selector, filter by "
            "text, or raise limit. interactive=false lists every element."
        ),
    )
    def outline(
        selector: SelectorArg = None,
        text: str | None = None,
        limit: int = OUTLINE_LIMIT,
        interactive: bool = True,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
    ) -> dict: ...

    @action("execute_script")
    def execute_script(
        script: str, url: str | None = None
    ) -> dict:
        """Run JavaScript in the page and return what it returns.

        Check the other tools first: interact, write, extract and outline cover most
        needs, and only interact's hover opens a menu that appears on :hover, which
        script events never trigger. Use this for what is left: scrolling
        (window.scrollTo), computed styles, reading many values at once, direct DOM
        work. Not for drag and drop; drag uses real pointer input.
        """

    # Declared as `assert_page` because `assert` is a Python keyword and cannot
    # be a function name; the row's `method` is the other half of that.
    @action(
        "assert",
        description=(
            "Check the page with JavaScript that must return true; otherwise "
            "the call fails. Return a boolean, not the thing itself: return "
            "!!document.querySelector('#total').\n\n"
            "It asks again until the answer is true or wait_timeout passes (0 "
            "asks once), so it can follow a click without knowing how long "
            "the page takes. For a guard that must hold before acting, set "
            "stable_for: the answer has to stay true that many seconds, which "
            "catches a page that is true for a moment before it redirects.\n\n"
            "message is the sentence a failure shows, and in a flow it "
            "becomes the failing step's error."
        ),
    )
    def assert_page(
        script: str,
        message: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        stable_for: float = 0,
    ) -> dict: ...

    @action("screenshot", shape=_as_image)
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
        """Capture a PNG of the viewport, of one element (selector), or of the whole
        page (full_page), returned as an image. Use it only when the look is the
        answer; extract reads content far more cheaply.

        By default the capture is also kept with the session's files. To show a
        person what you saw, give them the file's url: it opens in any
        browser without a token, and ![](url) works. Do not describe the
        image instead.

        save=false stores nothing; file_error says why a save failed, and the image
        still comes back.
        """

    @action("print")
    def print_page(
        url: str | None = None,
        format: PrintFormat = "pdf",
        filename: str | None = None,
        landscape: bool = False,
        background: bool = False,
    ) -> dict:
        """Print the page into the session's files, kept past the browser.

        format="pdf" is the browser's own print: selectable text, the whole
        document. landscape turns the page and background keeps colours and images
        a print drops. format="html" is the page as it stands now, after its
        scripts ran.

        To give it to a person, give them the file's url: it opens in any browser
        without a token. A server with no public address configured returns it
        relative instead.
        """

    undeclared = {row.name for row in CAPABILITIES} - declared
    if undeclared:
        raise RuntimeError(
            "capabilities with no tool declared in mcp/tools.py: "
            + ", ".join(sorted(undeclared))
        )
