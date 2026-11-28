"""The MCP tool surface.

Each tool is a declaration, a signature and a docstring, and its name,
annotations and body come from its row in ``core/capabilities.py``, the table
the HTTP surface is mounted from too. FastMCP turns each signature into the
tool's JSON schema and each docstring into its description, so both are what a
model reads: the docstrings are written for a model deciding whether to call
the tool.

No tool takes a session id. A caller names its workspace (``?workspace=`` or
``Sf-Workspace``) and ``Workspaces`` finds the browser it holds. Every tool
that acts on a browser takes ``session``, which names one of the workspace's
sessions (`Workspaces.session_of`); it is the tool's, never the action's.
"""

from __future__ import annotations

import base64
import functools
import inspect
import json
from collections.abc import Callable
from typing import Annotated, Literal, get_args, get_origin, get_type_hints

import anyio
from kubed.mcp import Context, Depends, Image, Inject, QuickMCP, ToolResult
from kubed.mcp.protocol import AppConfig
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from .. import secrets as secrets_module
from ..config import LiveSettings
from ..core.actions import (
    DIALOG_ACTIONS,
    FRAME_ACTIONS,
    ID_LIMIT,
    MOUSE_ACTIONS,
    PRINT_FORMATS,
    TAB_ACTIONS,
    Actions,
)
from ..core.capabilities import CAPABILITIES, PAGE_STATE, Capability, capability
from ..core.defaults import BROWSERS
from ..core.probe import DEFAULT_LIMIT as OUTLINE_LIMIT
from ..core.recipe import DIALOG_TIMEOUT, WAIT_TIMEOUT
from ..live import control as control_module
from ..workspace.workspaces import Workspaces
from . import clients, timing, waiting


def _lowered(value):
    return value.strip().lower() if isinstance(value, str) else value


def _blank_is_unset(value):
    """A blank optional choice means "not given".

    `normalize_browser` takes a blank browser as the default, and a client that
    sends an omitted optional as "" relies on it, which a bare enum would refuse.
    """
    resolved = _lowered(value)
    return None if resolved == "" else resolved


# Closed sets are published as enums, so a model sees every choice. Each is
# lowercased before the check: the action layer lowercases them over HTTP and
# in a flow, so `Hover` works on every surface.
MouseAction = Annotated[Literal[MOUSE_ACTIONS], BeforeValidator(_lowered)]
DialogAction = Annotated[Literal[DIALOG_ACTIONS], BeforeValidator(_lowered)]
FrameAction = Annotated[Literal[FRAME_ACTIONS], BeforeValidator(_lowered)]
TabAction = Annotated[Literal[TAB_ACTIONS], BeforeValidator(_lowered)]
PrintFormat = Annotated[Literal[PRINT_FORMATS], BeforeValidator(_lowered)]
Browser = Annotated[Literal[BROWSERS] | None, BeforeValidator(_blank_is_unset)]
RequestId = Annotated[str, Field(min_length=1, max_length=ID_LIMIT)]
# Which of the workspace's sessions a call acts in (WS-49), on every tool that
# acts on a browser: the only thing on a request that tells two callers on one
# connection apart (SURF-46).
Session = Annotated[
    str | None,
    Field(
        description=(
            "Which session to act in, by name. Leave it out to act in your own, "
            "named after you. Only open_session opens one. A named session's state "
            "is session://<name>/…; workspace:// is your own."
        )
    ),
]


def _as_selector(value):
    """A selector sent as a JSON string, turned back into an object.

    Some clients stringify object arguments, and pydantic's refusal of a string
    speaks of dictionaries. A string that is not JSON is handed on unchanged, so
    the caller sees the selector's own error.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# Pydantic publishes the docstring below as the schema's description in every
# tool that takes a selector, and a refusal quotes it: it is written for a model.
class Selector(BaseModel):
    """Which element: {"css": "button.go"} or {"xpath": "//button[@type='submit']"} - exactly one of the two."""  # noqa: E501 - one line, as a schema description

    # An unknown key is refused: ignored, it would run a different request.
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
        become a click on whatever the second found, reported as `ok`."""
        if bool(self.xpath) == bool(self.css):
            raise ValueError(
                "a selector takes xpath or css - exactly one, not both and not neither"
            )
        return self


SelectorArg = Annotated[Selector | None, BeforeValidator(_as_selector)]


class SecretRef(BaseModel):
    """Which secret, and which key inside it."""

    model_config = ConfigDict(extra="forbid")

    # The flow schema publishes this model, and the flow validator refuses "".
    name: str = Field(min_length=1)
    key: str = Field(min_length=1)


# What an MCP screenshot keeps beside its image block, when present: every
# field of the screenshot's published response except the capture itself and
# the page's `url` and `title`. `site_data`, `reopened` and `flows_here` are
# said once, so one dropped here is never said at all.
_THE_CAPTURE = frozenset({"image", "width", "height", "bytes", *PAGE_STATE})
_BESIDE_THE_IMAGE = tuple(
    k for k in capability("screenshot").response["properties"] if k not in _THE_CAPTURE
)


def _as_image(result: dict) -> Image | ToolResult:
    """A screenshot as an MCP image block.

    The one difference between the surfaces: an HTTP caller gets the capture as
    base64 JSON.
    """
    image = Image(data=base64.b64decode(result["image"]), format="png")
    told = {k: result[k] for k in _BESIDE_THE_IMAGE if result.get(k) is not None}
    if not told:
        return image
    return ToolResult(content=[image.to_image_content()], structured_content=told)


def _forwarding(body: Callable[[dict], object]):
    """A declaration's body replaced by ``body(arguments)``.

    The arguments are every parameter by name, defaults applied, so a tool's
    signature is written once, as its schema.
    """

    def wrap(declaration):
        signature = inspect.signature(declaration)

        @functools.wraps(declaration)
        def tool(*args, **kwargs):
            given = signature.bind(*args, **kwargs)
            given.apply_defaults()
            return body(dict(given.arguments))

        return tool

    return wrap


# What the facade's ``Depends`` makes, so a parameter's metadata can be told to be
# a dependency without naming the class behind it.
_DEPENDENCY = type(Depends(lambda: None))


def _require_injected_actions(name: str, declaration) -> None:
    """Refuse an action declared without ``actions: Inject[Actions]``.

    ``act`` takes the Actions the container hands the tool: one declared without
    the parameter would register and then fail on every call, and a bare
    ``actions: Actions`` would put the parameter in the tool's schema. Which
    instance it gets is the container's (``singleton``), tested through a call.
    """
    hint = None
    if "actions" in inspect.signature(declaration).parameters:
        hint = get_type_hints(declaration, include_extras=True)["actions"]
    injected = (
        get_origin(hint) is Annotated
        and get_args(hint)[0] is Actions
        and any(isinstance(meta, _DEPENDENCY) for meta in hint.__metadata__)
    )
    if not injected:
        raise TypeError(f"{name}: an action takes actions: Inject[Actions]")


def register(
    mcp: QuickMCP,
    workspaces: Workspaces,
    catalogue=None,
    *,
    live: LiveSettings | None = None,
) -> None:
    """Register every capability as an MCP tool on ``mcp``.

    ``action`` gives a declaration the body every action shares, and ``own``
    publishes one whose body is its own. A capability left undeclared stops the
    server here. ``live.await_cap`` bounds a blocking `await_control`.
    """
    declared: set[str] = set()
    live = live if live is not None else LiveSettings()

    def publish(fn, name: str, description: str | None, task=None):
        row = capability(name)
        declared.add(row.name)
        app = AppConfig(visibility=list(row.visibility)) if row.visibility else None
        return mcp.tool(
            name=row.name,
            description=description,
            annotations=row.annotations,
            app=app,
            task=task,
        )(fn)

    def act(row: Capability, arguments: dict):
        """Whose browser this is, then act on it through ``Workspaces.act``."""
        # The tool's, never the action's: which session it acts in, and the
        # `Actions` it acts through.
        session = arguments.pop("session", None)
        actions = arguments.pop("actions")
        if "secret" in arguments:
            # `write` types a secret through the catalogue.
            if arguments["secret"] is None and arguments.get("text") is None:
                raise ValueError(f"{row.name} needs text, or a secret to supply it")
            if arguments["secret"] is not None:
                caller = clients.caller()
                key = workspaces.session_of(caller, session)
                return secrets_module.perform_write(
                    catalogue,
                    actions,
                    workspaces,
                    key,
                    arguments,
                    surface="mcp",
                    own=workspaces.own(caller, session, key),
                )
            del arguments["secret"]
        return workspaces.act(
            clients.caller(),
            lambda s: getattr(actions, row.method)(s, **arguments),
            session=session,
            reshapes=row.reshapes,
            tool=row.name,
            surface="mcp",
            closed=row.closed
            and (lambda name: getattr(actions, row.closed)(name, **arguments)),
        )

    def action(
        name: str,
        *,
        description: str | None = None,
        shape: Callable[[dict], object] | None = None,
    ):
        """Publish a declaration whose body is ``act``, shaped by ``shape``."""
        row = capability(name)

        def body(arguments: dict):
            result = act(row, arguments)
            return shape(result) if shape else result

        def wrap(declaration):
            _require_injected_actions(name, declaration)
            return publish(_forwarding(body)(declaration), name, description)

        return wrap

    def own(name: str, *, description: str | None = None, task=None):
        """Publish a declaration whose body is its own; ``task`` for one that
        may run as an MCP task (`waiting.TASK`)."""
        return lambda fn: publish(fn, name, description, task)

    @own("open_session")
    @_forwarding(
        lambda arguments: workspaces.open_browser(
            clients.caller(), surface="mcp", **arguments
        )
    )
    def open_session(
        url: str | None = None,
        browser: Browser = None,
        version: str | None = None,
        width: int | None = None,
        height: int | None = None,
        page_load_timeout: int | None = None,
        script_timeout: int | None = None,
        insecure: bool | None = None,
        headless: bool | None = None,
        record: bool | None = None,
        capture: bool | None = None,
        capabilities: dict[str, JsonValue] | None = None,
        fresh: bool = False,
        restore_site_data: bool = True,
        session: Session = None,
    ) -> dict:
        """Opens your own session, or joins it when its browser is live; that ends
        nothing. session="x" opens or joins x instead. The first session in the
        workspace's list is its main one; that is only its place in the list. Call it
        before anything else: nothing opens a browser for you.

        With no arguments it opens a browser from the server's and your connection's
        defaults, plus the options you passed before, and goes back to the page you were
        on: the right call after a browser was reaped or ended. While your browser is
        live it joins it instead and answers joined: true; a url then navigates there.
        Only an option you pass that differs from the live browser's, or fresh=true,
        ends it and opens a new one, which is how you switch:
        open_session(browser="firefox"). Files that browser had and you did not keep
        go with it. When another session has taken the head of the list from yours,
        your next result says so once, in main_moved beside main: false; carry on in
        your own session.

        The options:
        - browser is chrome (default) or firefox. version="152" asks the Grid for
          that version, and the result's version says which one ran;
          version="latest" forgets a remembered one, and switching browser forgets
          it too.
        - width and height set the window when layout matters; the default
          window is narrow. page_load_timeout bounds a navigation that hangs,
          script_timeout a script. insecure=true accepts a self-signed certificate;
          use it only for a site you know has one, and an insecure browser gets no
          saved site data. Everything above is remembered when you pass it; one you
          leave out comes from the server's and your connection's defaults, read at
          each open.
        - headless=true opens a browser that draws nothing on the node's screen, which
          is quicker. It cannot be recorded and nobody can watch it live, and sites'
          bot checks spot it more easily, so leave it off for real sites. Remembered
          like the options above until you pass headless=false.
        - record=true films this browser's whole life as a video; it appears under
          workspace://files/recordings shortly after it ends. A person watches it,
          so only when one will — it costs the Grid. capture=true keeps what the
          page logs and fetches — workspace://console and workspace://network,
          session://<name>/console and /network for a named session — and a call
          that caused a page error says so in its result. A new browser opened
          without capture=true forgets what was captured, so read those first when
          you are debugging; a join keeps it. Neither is kept: ask again for the
          next browser.
        - fresh=true starts on about:blank. Saved site data comes back by default;
          restore_site_data=false opens without it and deletes it, which is how you
          start as a new user. On a live session, fresh=true is how you get a clean
          browser or your saved site data back: a join ignores restore_site_data.
        - session="tester" opens a session of its own, starting empty, that another
          call reaches with the same session; its state is session://tester/…. It
          never ends another session's browser; others lists them.

        capabilities is the last resort, only for something no option above covers:
        extra W3C capabilities sent to the Grid as given, best effort, and
        remembered whole; {} forgets them. A key an option controls is refused
        with the option to use. Inside goog:chromeOptions or moz:firefoxOptions
        your args and prefs are added to selenium-flow's, and the result's ignored
        lists any of yours that lost.
        """

    @action("network_body")
    def network_body(
        id: RequestId, session: Session = None, *, actions: Inject[Actions]
    ) -> dict:
        """Fetch the response body of one request the page made, by its id from
        workspace://network, or from session://<name>/network with that same
        session (an id looks like 3.7). Text comes back up to 100 kB,
        with truncated=true beyond that; for an image or other binary you get its
        type and size, not its bytes. The browser keeps bodies only while it lives,
        so read one soon after the request; it needs open_session(capture=true).
        """

    @action("save_site_data")
    def save_site_data(
        url: str | None = None, session: Session = None, *, actions: Inject[Actions]
    ) -> dict:
        """Save this session's site data, so a new browser comes back signed in.

        It keeps every cookie the browser holds, the localStorage of the sites it has
        been to, and the sessionStorage of the page it is on. Call it right after a
        sign-in is confirmed, and after changing a setting you want kept; never before
        checking you landed, or you keep a failed sign-in.

        One save covers every site a call ended on, while the workspace keeps its
        history (a day by default, 100 sites): one passed through inside a call is not
        read, and one last visited longer ago loses its storage at the next save, its
        cookies staying. Each save replaces the last, so a save after signing out
        saves you signed out. Every browser this session opens has it back before
        open_session returns, the one that replaces a reaped browser included. Values
        are never returned; the result's uri lists what is saved: workspace://site-data,
        or session://<name>/site-data for a named session.
        """

    @own("end_browser")
    def end_browser(session: Session = None) -> dict:
        """Quit this workspace's browser and free its Grid slot.

        Call it when finished, and on failure paths too: browsers are scarce, and an
        abandoned one holds its slot until the Grid reaps it. The workspace survives,
        so a later open_session() comes back to the same page. Files the browser had
        and you did not keep go with it.
        """
        caller = clients.caller()
        workspaces.end_browser(caller, session=session, surface="mcp")
        return {"success": True, "workspace": caller.name}

    @own("await_control", task=waiting.TASK)
    @timing.timed(workspaces, "await_control")
    async def await_control(ctx: Context, session: Session = None) -> dict:
        """Wait for the person who took control of this browser to hand it back.

        A person can take control of your browser from the admin's live view, to
        sign in, solve a captcha or fix something by hand. While they hold it, every
        call that would change the browser is refused with a message naming this
        tool; reads still answer. Call this then, rather than retrying the refused
        call.

        It returns the moment they hand back, saying who had control and for how
        long, the page the browser is on now, every page they reached (visited,
        oldest first) and an outline of that page. Read it before carrying on: the
        page may not be where you left it. With nobody in control it returns at
        once. ended=true means the browser ended while they had it: call
        open_session.

        On a client without background tasks it returns after about 90 seconds with
        handed_back=false, saying to call again; do, until it hands back.

        A person's control covers one session: pass the session a refused call named
        (or leave it out for your own) and this waits for that one only.
        """
        caller = clients.caller()
        # A read of the store, which may be a network call: off the loop.
        name = await anyio.to_thread.run_sync(workspaces.reading, caller, session)
        own = await anyio.to_thread.run_sync(workspaces.own, caller, session, name)
        controls = workspaces.control
        return await waiting.waited(
            ctx,
            lambda cap: control_module.await_control(
                controls, workspaces, name, cap=cap, surface="mcp", own=own
            ),
            cap=live.await_cap,
            message=lambda: controls.waiting(name),
        )

    @action("navigate")
    def navigate(
        url: str, session: Session = None, *, actions: Inject[Actions]
    ) -> dict:
        """Go to a URL. Returns the URL and title the browser ended on.

        Every other action also takes url, and navigates there first only if the
        browser is somewhere else, so you rarely need this as a separate call.
        """

    # The action list is interpolated from the tuple the action layer checks.
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
            "When the selector matches several elements, the first that can "
            "be used is acted on, and matched and index say which. click, "
            "double_click and right_click answer received_by, the element at "
            "the point they pressed, with a warning when that is probably not "
            "what you meant. double_click and right_click press whatever is "
            "on top, so read it.\n\n"
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
        session: Session = None,
        *,
        actions: Inject[Actions],
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
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict: ...

    @action(
        "frame",
        description=(
            "Move into an iframe (switch, with a selector or an index), up "
            "one level (parent), or back to the page (default).\n\n"
            "Elements inside a frame are invisible to every tool until you "
            "switch in, and the switch sticks for every later call. If a "
            "selector that should work keeps failing, read workspace://current "
            "(session://<name> for a named session): in_frame says where you are."
        ),
    )
    def frame(
        action: FrameAction = "switch",
        selector: SelectorArg = None,
        index: int | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict: ...

    @action(
        "tab",
        description=(
            "List the browser's tabs (the default), open one (loading url in "
            "it), switch to one (by id; by url, the newest tab whose URL "
            "contains that text; or, with neither, the newest), or close one "
            "(id, or the current tab).\n\n"
            "Every other tool acts on the current tab. A popup a page opens "
            "becomes the current tab by itself, and when the current tab "
            "closes the session goes back to the tab it came from; a result "
            "says so with tab, opened and closed. Only the current tab is in "
            "front: Chrome does not render a tab that is not, and Firefox "
            "slows its timers."
        ),
    )
    def tab(
        action: TabAction = "list",
        id: str | None = None,
        url: str | None = None,
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict: ...

    @action("resize")
    def resize(
        width: int | None = None,
        height: int | None = None,
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict:
        """Resize the browser window.

        Set a size before judging anything visual: the default window is small and
        varies between Grid nodes. The size sticks to the workspace, so a browser
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
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict: ...

    @action("upload_file")
    def upload_file(
        selector: SelectorArg = None,
        text: str | None = None,
        filename: str | None = None,
        mime_type: str | None = None,
        content: str | None = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        file: str | None = None,
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict:
        """Attach a file to a file input (selector). Give exactly one source:

        - text, for anything you wrote (JSON, CSV, markdown), with a filename;
        - file, by its uri: a file in workspace://files or its folders, or a
          download from session://downloads (the session this call acts in) or
          session://<name>/downloads — without its bytes passing through you;
        - content, base64, for other binary.

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
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict:
        """Type into an input, textarea or contenteditable, and read the value back so
        you can see it landed. clear (default true) replaces what is there;
        submit=true presses Enter afterwards.

        To type a secret, pass secret={"name": ..., "key": ...} instead of text;
        secret://secrets lists them. The server types it and you never see it: value
        comes back null. A secret works only on the sites it allows, checked against
        the page you are on, so navigate there first. A value typed from a secret, or
        any value of a secret allowed on this site, comes back as [secret:name/key] in
        every later read here.
        """

    # The key names are not listed: a wrong name is refused with all of them.
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
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict: ...

    @action("extract")
    def extract(
        selector: SelectorArg = None,
        url: str | None = None,
        wait_timeout: int = WAIT_TIMEOUT,
        session: Session = None,
        *,
        actions: Inject[Actions],
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
            "(blocked_by names what is on top), unpainted (it paints nothing "
            "at its centre), invisible_cover (an invisible element on top, "
            "named in blocked_by, takes the click; clicking it, or something "
            "inside it, may be what you want), zero_size, offscreen or "
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
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict: ...

    @action("execute_script")
    def execute_script(
        script: str,
        url: str | None = None,
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict:
        """Run JavaScript in the page and return what it returns.

        Check the other tools first: interact, write, extract and outline cover most
        needs, and only interact's hover opens a menu that appears on :hover, which
        script events never trigger. Use this for what is left: scrolling
        (window.scrollTo), computed styles, reading many values at once, direct DOM
        work. Not for drag and drop; drag uses real pointer input.
        """

    # `assert` is a Python keyword, so the declaration is `assert_page`.
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
        session: Session = None,
        *,
        actions: Inject[Actions],
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
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> Image | ToolResult:
        """Capture a PNG of the viewport, of one element (selector), or of the whole
        page (full_page), returned as an image. Use it only when the look is the
        answer; extract reads content far more cheaply.

        By default the capture is also kept with the workspace's files. To show a
        person what you saw, give them the file's url: it opens in any
        browser without a token, and ![](url) works. Do not describe the
        image instead.

        Pixels cannot be scrubbed: a page showing a secret's value in a field that is
        not a password field shows it here.

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
        session: Session = None,
        *,
        actions: Inject[Actions],
    ) -> dict:
        """Print the page into the workspace's files, kept past the browser.

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
