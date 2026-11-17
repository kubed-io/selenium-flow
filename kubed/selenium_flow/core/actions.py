"""The browser actions, as plain functions.

``tools.py`` exposes them over MCP and ``routes.py`` over HTTP, so adding a
capability here adds it to both. Every action takes a ``session_id`` and returns
a JSON-safe dict; the browser state lives on the Grid, not in this process.
"""

from __future__ import annotations

import base64
import functools
import logging
import shutil
import tempfile
import time
from pathlib import Path

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    SessionNotCreatedException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.print_page_options import PrintOptions

from ..bidi.connection import BidiError, MessageTooBig
from ..capture import NETWORK_URI
from ..errors import GONE, UNAVAILABLE
from ..faults import BidiUnavailable, NotFound, ScriptTimedOut, formatted
from ..faults import message as failure_text
from ..names import FILES_DIR, SCREENSHOTS_DIR
from ..redaction import clip
from ..site_data import snapshot as site_data_snapshot
from ..site_data import transfer as site_data_transfer
from ..urls import allowed_navigation
from . import browser, js, pointer, probe, session_caps
from .assertion import Assertion, window
from .browser import Grid
from .coerce import as_bool, as_int
from .defaults import normalize_browser
from .keys import SUBMIT_KEYS, resolve_key
from .naming import _decode, _generated_name, _why_unsaved, safe_name
from .recipe import (
    CLICKABLE,
    DIALOG_TIMEOUT,
    PRESENCE,
    SHOWN,
    WAIT_TIMEOUT,
    Recipe,
    node_answering,
)

# What a failed pointer move must never be mistaken for. See `_move_onto`.
INFRASTRUCTURE = (*GONE, *UNAVAILABLE)

log = logging.getLogger(__name__)

# `network_body`: text is cut at this many bytes; an id is a short string.
BODY_CAP = 100_000
# `outline`: a name is cut at this many characters, and ends in `…` when it was.
NAME_CAP = 80
CUT_MARK = "\u2026"
# The largest text body asked of the browser, by the size its row says: a cheap
# refusal before asking. Chrome answers `network.getData` whole and decoded, so
# a compressed body can still pass the connection's limit; see `network_body`.
BODY_FETCH_LIMIT = 8_000_000
# A body of any other MIME type is answered from its row, never fetched. An
# unknown type is asked.
TEXT_TYPES = ("text/",)
TEXT_SUBTYPES = (
    "json",
    "xml",
    "javascript",
    "x-javascript",
    "ecmascript",
    "x-ecmascript",
    "x-www-form-urlencoded",
)
ID_LIMIT = 64
BODY_GONE = (
    "the browser no longer holds this body: it keeps them while it lives, "
    "and drops the oldest past about 200 MB"
)
# A body asked for once the workspace's session has ended.
BODY_CLOSED = (
    "no session is open in this workspace; a body lives only as long as its browser"
)
BODY_ANSWERS = {
    "no such network data": BODY_GONE,
    "unavailable network data": (
        "the browser did not keep this body: it keeps bodies up to 10 MB"
    ),
    "no such network collector": (
        "this body is no longer collectable: the browser stopped keeping bodies"
    ),
}


def run_script(driver, script: str):
    """``driver.execute_script``, raising `ScriptTimedOut` when the script ran
    out of time.

    Keyed on the command, because the browsers word it differently: Chrome says
    `script timeout` and Firefox `Timed out after N ms`.
    """
    try:
        return driver.execute_script(script)
    except TimeoutException:
        try:
            # One more command, so it must never hide the timeout.
            seconds = driver.timeouts.script
        except Exception:  # noqa: BLE001 - any failure here only costs the number
            seconds = None
        raise ScriptTimedOut(seconds) from None


def _is_text(mime: str) -> bool:
    """Whether a body of type ``mime`` is text: ``text/*``, a JSON, XML or
    JavaScript type (``+json`` and ``+xml`` suffixes included), or a form."""
    kind = mime.split(";", 1)[0].strip().lower()
    subtype = kind.partition("/")[2]
    return kind.startswith(TEXT_TYPES) or any(
        subtype == t or subtype.endswith("+" + t) for t in TEXT_SUBTYPES
    )


def _decoded_length(value: str) -> int:
    try:
        return len(base64.b64decode(value, validate=False))
    except ValueError:
        return 0


def cut_body(result):
    """`network_body`'s ``text`` cut to `BODY_CAP` bytes, ``truncated`` saying
    whether it was. For a result already scrubbed (`CUTS`)."""
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        result["text"], result["truncated"] = clip(
            result["text"], BODY_CAP, unit="bytes"
        )
    return result


def cut_names(result):
    """Each of `outline`'s names past `NAME_CAP` characters cut to it and ended
    in `…`. For a result already scrubbed (`CUTS`)."""
    elements = result.get("elements") if isinstance(result, dict) else None
    for element in elements if isinstance(elements, list) else ():
        name = element.get("name") if isinstance(element, dict) else None
        if isinstance(name, str) and len(name) > NAME_CAP:
            element["name"] = clip(name, NAME_CAP)[0] + CUT_MARK
    return result


# Where the server cuts a text, by tool. `Workspaces._scrubbed` applies it after
# the scrub, so a value the cut would go through is masked whole first.
CUTS = {"network_body": cut_body, "outline": cut_names}


def _body_ended(id: str) -> str:
    return f"the browser that made request {id} has ended, and its body went with it"


INSECURE_SKIP = "an insecure browser gets no saved site data"

# Elements that can host a nested document. Keys sent to one may land in that
# document, whose origin the leash never read: it checks the selected context.
# WebDriver can switch into only iframe and frame, so the rest are refused too.
FRAME_ELEMENTS = ("iframe", "frame", "object", "embed", "fencedframe", "portal")
# A viewer's paste is typed with WebDriver at the element the person focused,
# because VNC keystrokes cannot carry non-ASCII text.
PASTE_CAP = 10_000
PASTE_DEPTH = 10  # frames followed to the focused element
IN_A_FRAME = (
    "the agent is working inside a frame, and a paste would move it out of it: "
    "type this in Control instead"
)
NOTHING_FOCUSED = (
    "nothing in the browser has focus: click where the text goes, then paste"
)
TOO_DEEP = "the focused element is nested too many frames deep to paste into"
CANNOT_TYPE = "this browser cannot type some of these characters (emoji, for one)"
# ChromeDriver's refusal of a character outside the Basic Multilingual Plane.
BMP_REFUSAL = "only supports characters in the BMP"
FOCUSED = js.read("focused.js")

# An element's HTML with no password input's value in it.
EXTRACT_JS = js.read("extract.js")
FRAME_REFUSAL = (
    "a secret cannot be typed into an element that hosts another document; "
    "switch into the frame first"
)

MOUSE_ACTIONS = ("click", "double_click", "right_click", "hover", "scroll_to")

# The gestures that move the pointer onto the element first. `scroll_to` moves
# the page, so `glide` means nothing for it.
POINTER_ACTIONS = ("click", "double_click", "right_click", "hover")

# No MHTML: it is Chrome-only, and every tool behaves the same on both browsers.
PRINT_FORMATS = ("pdf", "html")

# "read" leaves the dialog open.
DIALOG_ACTIONS = ("accept", "dismiss", "read", "send_text")

FRAME_ACTIONS = ("switch", "parent", "default")


# What `open_session`'s result carries for the workspace and never for a
# caller: the Grid's id, what was sent, and what the Grid says ran.
OPEN_PRIVATE = ("session_id", "sent", "ran")


def _resize(driver, width, height) -> None:
    """Set the window to ``width`` by ``height``, keeping a side not given."""
    current = driver.get_window_size()
    driver.set_window_size(
        as_int(width, current["width"]), as_int(height, current["height"])
    )


def _ran(driver) -> dict:
    """What the Grid says it started: version, node and live view, each None
    when the returned capabilities do not say."""
    caps = getattr(driver, "caps", None) or {}
    version, node = caps.get("browserVersion"), caps.get("se:containerName")
    live = caps.get("se:vncEnabled")
    return {
        "version": version if isinstance(version, str) and version else None,
        "node": node if isinstance(node, str) and node else None,
        "live_view": live if isinstance(live, bool) else None,
    }


class Actions:
    """The browser operations, bound to one Grid."""

    def __init__(self, grid: Grid, keep=None, pointers=None, read_file=None):
        self.grid = grid
        # `keep`, `read_file` and `visited` are functions because which
        # workspace is calling is not this layer's to see.
        # `keep(name, data, folder)` keeps bytes this server made (a screenshot,
        # a print) with the caller's workspace and returns the file as every
        # listing describes it, link included. Absent, a save says it could not.
        self.keep = keep
        # Where the pointer is in each browser, keyed by the Grid's session id.
        # Injected so replicas can share it.
        self.pointers = pointers if pointers is not None else pointer.MemoryPointers()
        # `read_file(uri, workspace)` reads a file for `upload_file(file=...)`.
        # Absent, naming a file is refused.
        self.read_file = read_file
        # `visited()` lists the origins the caller's workspace has been to,
        # newest first, so one save reads every site's localStorage. Absent, a
        # save reads only the page it is on.
        self.visited = None
        # The rest are wired by the server.
        # Opens a BiDi channel to a session's browser (`bidi.Channels`).
        self.channels = None
        # `(session_id, context) -> block`: inside it, capture ignores that
        # browsing context, so a save's spare tab never reaches a caller's log.
        self.hide = None
        # The operator's `session.capabilities`, sent under the caller's at
        # every open: its own source, never a layer of the cascade.
        self.config_capabilities: dict = {}
        # The kept console and network (`capture.Capture`); None when capture is
        # not set up.
        self.capture = None
        # `refusal(session_id, tool) -> Controlled | None` (`live.control.Controls`):
        # a read whose url would navigate, or a screenshot that would resize, is
        # refused while a person holds the browser. Duck-typed because core
        # imports no live/. None: never refused.
        self.control = None

    def _refuse(self, session_id: str, tool: str) -> None:
        refusal = self.control and self.control.refusal(session_id, tool)
        if refusal:
            raise refusal

    def _kept(self, name: str, data: bytes, folder=FILES_DIR) -> dict:
        """Keep bytes this server made with the caller's workspace.

        Straight to the workspace's files, never through the browser as a
        download, which Chrome refuses on plain-http pages, pages with no
        origin, and a second download from `about:blank`.
        """
        if self.keep is None:
            raise ValueError(
                "keeping files is not enabled on this server: it was started "
                "with no DATA_DIR, so there is nowhere to save one"
            )
        return self.keep(name, data, folder)

    def _pointer(self, session_id: str):
        """Where the pointer is in this browser, or None if we never sent it."""
        try:
            return self.pointers.get(session_id)
        except Exception as exc:  # noqa: BLE001 - not knowing is a state here, not a failure
            log.debug("could not read the pointer:\n%s", formatted(exc))
            return None

    def _moved(self, session_id: str, at) -> None:
        """Record where a move we sent left the pointer."""
        try:
            if at is None:
                self.pointers.forget(session_id)
            else:
                self.pointers.set(session_id, at[0], at[1])
        except Exception as exc:  # noqa: BLE001 - see `_pointer`
            log.debug("could not record the pointer:\n%s", formatted(exc))

    # ---- session lifecycle -------------------------------------------------

    def open_session(
        self,
        url=None,
        browser=None,
        version=None,
        width=None,
        height=None,
        page_load_timeout=None,
        script_timeout=None,
        insecure=None,
        record=None,
        capture=None,
        video_name=None,
        site_data=None,
        capabilities=None,
        headless=None,
        *,
        version_from: str = "call",
        on_created=None,
        on_ready=None,
        on_quit=None,
    ) -> dict:
        """Start a browser session with the settings it runs under.

        The only place a browser is created. The browser and the timeouts hold
        for the session's life; only the window size can change later
        (``resize``).

        A workspace's saved site data (cookies, every origin's storage, the
        saved sessionStorage) is restored before the first page loads, except
        into an ``insecure`` browser, which gets none. ``record`` asks the Grid
        to film this browser's whole life.

        ``version_from`` says whose ``version`` is (see ``session_caps.build``);
        a node's refusal blames the caller for it unless it is the config's.

        ``on_created`` is called with the Grid's id the moment the browser
        exists, before anything here that can fail, so a browser left running
        is still accounted for (its video, for one). One that raises fails the
        open.

        ``on_ready`` is called with the Grid's id once everything but the first
        page is done (the window, the timeouts, the restore) and before ``url``
        loads, so a listener it starts hears that page from its first request.
        ``capture`` only says the browser is being captured; what listens is
        ``on_ready``'s.

        A browser this open made is never left behind: anything that fails once
        it exists quits it, and the open fails with that failure, not the
        quit's. ``on_quit`` is called with the Grid's id once the quit is done;
        a quit that failed may leave the browser running, so it is not called.
        """
        name = normalize_browser(browser)
        insecure = as_bool(insecure, False)
        headless = as_bool(headless, False)
        record = as_bool(record, False)
        capture = as_bool(capture, False)
        # Built before anything opens: an owned key is a 400 that costs no browser.
        built = session_caps.build(
            name,
            version=version,
            insecure=insecure,
            record=record,
            video_name=video_name,
            config=self.config_capabilities,
            raw=capabilities,
            option_args=[session_caps.HEADLESS_ARGS[name]] if headless else (),
            version_from=version_from,
        )
        try:
            driver = self.grid.open(built)
        except SessionNotCreatedException as exc:
            # No browser exists, so there is nothing to quit. A queue timeout
            # or a node's refusal says what it means; the Grid's own failures
            # stay as they were.
            fault = session_caps.not_created(failure_text(exc), built)
            if fault is None:
                raise
            raise fault from exc
        session_id = driver.session_id
        try:
            return self._set_up(
                driver,
                name,
                built=built,
                asked_version=version,
                capabilities=capabilities,
                url=url,
                width=width,
                height=height,
                page_load_timeout=page_load_timeout,
                script_timeout=script_timeout,
                insecure=insecure,
                headless=headless,
                record=record,
                capture=capture,
                site_data=site_data,
                on_created=on_created,
                on_ready=on_ready,
            )
        except BaseException:
            # Nobody was handed it, so quit it rather than hold a Grid slot until
            # the idle timeout. The id stays out of the log.
            try:
                self.grid.quit(session_id)
            except Exception as exc:  # noqa: BLE001 - the failure matters more
                log.warning(
                    "could not quit a browser whose open failed: %s",
                    type(exc).__name__,
                )
            else:
                if on_quit is not None:
                    on_quit(session_id)
            raise

    def _set_up(
        self,
        driver,
        name: str,
        *,
        built: session_caps.Built,
        asked_version,
        capabilities,
        url,
        width,
        height,
        page_load_timeout,
        script_timeout,
        insecure: bool,
        headless: bool,
        record: bool,
        capture: bool,
        site_data,
        on_created,
        on_ready,
    ) -> dict:
        """Everything `open_session` does once the browser exists."""
        session_id = driver.session_id
        if on_created is not None:
            on_created(session_id)

        if width or height:
            _resize(driver, width, height)

        # Unbounded by default, which lets one hanging page hold a Grid slot for
        # the whole idle timeout. Set here so it survives every later reconnect.
        if page_load_timeout:
            driver.set_page_load_timeout(as_int(page_load_timeout, 300))
        if script_timeout:
            driver.set_script_timeout(as_int(script_timeout, 30))

        # A new browser's pointer is somewhere this server never sent it.
        self._moved(session_id, None)

        report = None
        if site_data_snapshot.restorable(site_data or {}):
            if insecure:
                # It accepts any certificate, so anyone in the middle would get
                # every saved cookie.
                report = {
                    "restored": [],
                    "skipped": [{"reason": INSECURE_SKIP}],
                    "uri": site_data_snapshot.LIST_URI,
                }
            else:
                try:
                    with self.channels.open_channel(session_id) as channel:
                        report = site_data_transfer.restore(
                            channel, site_data, time.time()
                        )
                except BidiUnavailable as e:
                    # Restore is best effort: a channel that will not open is
                    # a report, never a failed open.
                    report = {
                        "restored": [],
                        "skipped": [{"reason": failure_text(e)}],
                        "uri": site_data_snapshot.LIST_URI,
                    }

        if not url and report is not None and driver.current_url != "about:blank":
            # The restore sends the tab back from its stand-in page over BiDi
            # and swallows a failure; this makes the reported about:blank true.
            # Before `on_ready`, so capture never hears the stand-in.
            driver.get("about:blank")

        if on_ready is not None:
            on_ready(session_id)

        current_url, title = "about:blank", ""
        if url:
            browser.go(driver, url)
            current_url, title = driver.current_url, driver.title

        size = driver.get_window_size()
        # What the cascade resolved to. `browser` is always present: a refresh
        # replays it to reopen the same browser rather than the default one.
        applied = {
            "browser": name,
            "width": size["width"],
            "height": size["height"],
        }
        if page_load_timeout:
            applied["page_load_timeout"] = as_int(page_load_timeout, 0)
        if script_timeout:
            applied["script_timeout"] = as_int(script_timeout, 0)
        if insecure:
            applied["insecure"] = True
        if headless:
            applied["headless"] = True
        if record:
            applied["record"] = True
        if capture:
            applied["capture"] = True
        if asked_version is not None and str(asked_version).strip():
            # As asked, `latest` included: the version that ran is beside it.
            applied["version"] = str(asked_version).strip()
        if capabilities:
            applied["capabilities"] = capabilities
        ran = _ran(driver)
        result = {
            "session_id": session_id,
            "browser": name,
            "version": ran["version"],
            "url": current_url,
            "title": title,
            "width": size["width"],
            "height": size["height"],
            "settings": applied,
        }
        if built.ignored:
            result["ignored"] = list(built.ignored)
        if report and (report["restored"] or report["skipped"]):
            result["site_data"] = report
        # For the workspace record and the admin only (`OPEN_PRIVATE`).
        result["sent"] = built.sent
        result["ran"] = ran
        return result

    def save_site_data(self, session_id: str, url=None) -> dict:
        """Capture the browser's cookies, the page's storage, and the
        localStorage of every other origin the workspace has been to.

        The capture rides back under a private key; the workspace manager stores
        it and no caller sees it.
        """

        def capture(at):
            origins = self.visited() if self.visited is not None else []
            hidden = functools.partial(self.hide, session_id) if self.hide else None
            with self.channels.open_channel(session_id) as channel:
                captured = site_data_transfer.capture(
                    channel, at.driver, origins, hidden=hidden
                )
            return {site_data_snapshot.CAPTURED: captured}

        return self._recipe().run(session_id, capture, url=url)

    def end_browser(self, session_id: str) -> dict:
        """Quit the browser and free its Grid slot; the workspace survives it."""
        self.grid.quit(session_id)
        self._moved(session_id, None)
        return {"success": True, "session_id": session_id}

    # ---- navigation --------------------------------------------------------

    def navigate(self, session_id: str, url: str) -> dict:
        """Go to a URL, even the one the browser is on.

        The recipe skips a page the browser is already on, so ``url`` is the
        body's, and is checked here as the recipe checks its own.
        """
        allowed_navigation(url)

        def go(at):
            browser.go(at.driver, url)
            return {}

        return self._reattached().run(session_id, go)

    # ---- interaction -------------------------------------------------------

    def interact(
        self,
        session_id: str,
        action: str,
        selector=None,
        url=None,
        wait_timeout=WAIT_TIMEOUT,
        glide=False,
    ) -> dict:
        """Perform a mouse action on an element.

        Every gesture that involves the pointer moves it first (a jump unless
        ``glide``) and acts second, so afterwards the pointer is on what was
        acted on. The click is WebDriver's element click, which refuses a
        covered target, where a pointer-action click would press whatever is on
        top.
        """
        resolved = str(action).strip().lower()
        if resolved not in MOUSE_ACTIONS:
            raise ValueError(
                f"unknown action {action!r}; known actions: "
                f"{', '.join(sorted(MOUSE_ACTIONS))}"
            )

        def gesture(at):
            """The whole act, so a retry re-does the move as well as the click."""
            driver, element = at.driver, at.element
            moved = None
            if resolved in POINTER_ACTIONS:
                moved = self._move_onto(session_id, driver, element, glide)
            # What the pointer is about to press, read after the move.
            presses = resolved in ("click", "double_click", "right_click")
            hit = probe.received(driver, element) if presses else {}

            if resolved == "click":
                try:
                    element.click()
                except ElementClickInterceptedException as exc:
                    # `element_to_be_clickable` passes a covered element, so
                    # it fails here, and the driver's message names the element
                    # rather than what covers it. The probe asks about the
                    # element clicked, which may not be the selector's first
                    # match.
                    why = probe.explain(driver, at.target, element=element)
                    first = (getattr(exc, "msg", "") or "").strip().splitlines()
                    found = at.found
                    which = (
                        f" The selector matched {found.matched}; this was "
                        f"[{found.index}], the first that could be used."
                        if found is not None and found.matched > 1
                        else ""
                    )
                    raise ElementClickInterceptedException(
                        (first[0] if first else "element click intercepted")
                        + which
                        + (f" {why}" if why else "")
                    ) from exc
            elif resolved == "hover":
                # The move is the hover, unless it could not be sent.
                if moved is None:
                    ActionChains(driver).move_to_element(element).perform()
            else:
                chain = ActionChains(driver)
                if resolved == "double_click":
                    chain.double_click(element)
                elif resolved == "right_click":
                    chain.context_click(element)
                elif resolved == "scroll_to":
                    chain.scroll_to_element(element)
                chain.perform()
            return {
                "action": resolved,
                **self._pointer_report(moved, glide),
                **(probe.receiver_report(hit, resolved) if presses else {}),
            }

        # hover and scroll_to aim at the first displayed match, else the first:
        # requiring it clickable would refuse the off-screen element scroll_to
        # is for.
        wait = SHOWN if resolved in ("hover", "scroll_to") else CLICKABLE
        return self._recipe(wait, retry_stale=True).run(
            session_id, gesture, url=url, selector=selector, wait_timeout=wait_timeout
        )

    def _move_onto(self, session_id: str, driver, element, glide) -> dict | None:
        """Put the pointer on ``element`` before the gesture, if it can.

        A destination WebDriver will not move to (usually an element taller than
        the viewport) costs the pointer's position and nothing else.
        """
        try:
            moved = pointer.move(
                driver,
                element,
                start=self._pointer(session_id),
                glide=as_bool(glide, False),
            )
        except INFRASTRUCTURE:
            # A dead browser or an unreachable Grid keeps its own status and
            # fix in `errors.py`; swallowed, `drag` would blame the geometry.
            raise
        except Exception as exc:  # noqa: BLE001 - the gesture outranks the move
            log.debug("could not move the pointer onto the target:\n%s", formatted(exc))
            # A move that failed part-way leaves the pointer somewhere unknown,
            # and a glide plotted from a wrong origin crosses the wrong elements.
            self._moved(session_id, None)
            return None
        self._moved(session_id, moved["at"])
        return moved

    @staticmethod
    def _pointer_report(moved: dict | None, glide) -> dict:
        """What the result says about how the pointer got there."""
        if moved is None:
            return {}
        report = {"glided": moved["glided"]}
        if moved["nudged"]:
            # The pointer was already inside the target, so the move started by
            # leaving it; this explains a hover that fired this time and not last.
            report["nudged"] = True
        if moved["unknown_start"]:
            report["glide_note"] = (
                "the pointer's position in this browser was not known, so this "
                "was a jump; a glide from here on has a start to plot from"
            )
        elif moved.get("unglideable"):
            report["glide_note"] = (
                "the element is not somewhere a path can be plotted to - it "
                "does not fit in the window even scrolled to the middle - so "
                "this was a jump"
            )
        elif as_bool(glide, False) and not moved["glided"]:
            report["glide_note"] = "this was a jump"
        return report

    def drag(
        self,
        session_id: str,
        selector=None,
        to=None,
        by_x=None,
        by_y=None,
        url=None,
        wait_timeout=WAIT_TIMEOUT,
        glide=True,
    ) -> dict:
        """Drag one element onto another, or by an offset.

        Press, travel, release, with a short hold either side of the travel:
        several drag libraries arm on a delay or a distance, and a press and
        release in the same frame reads as a click.

        ``glide`` defaults to true, unlike `interact`: a sortable list or a
        slider watching for `pointermove` sees a teleport otherwise.
        """
        # Resolved before the browser is touched, so an impossible drag costs a
        # 400 and no page load. The source first: a call wrong twice hears about
        # the source.
        browser.locator(selector)
        to_target = None
        offset = None
        if to is not None:
            if by_x is not None or by_y is not None:
                raise ValueError(
                    "give the destination as `to` OR as a by_x/by_y offset, never both"
                )
            to_target = browser.locator(to)
        elif by_x is not None or by_y is not None:
            offset = (as_int(by_x, 0), as_int(by_y, 0))
            if offset == (0, 0):
                raise ValueError(
                    "by_x and by_y are both zero, which is a drag to where it "
                    "already is; give a distance, or name a destination element"
                )
        else:
            raise ValueError(
                "the destination is required: name it with `to`, "
                "or give a by_x/by_y offset in pixels"
            )

        wanted = as_bool(glide, True)
        clamp = {}

        def travel(at):
            driver = at.driver
            # The approach is always a jump: `glide` is for the travel with the
            # button down.
            moved = self._move_onto(session_id, driver, at.element, False)
            if moved is None:
                # A 400: the caller can fix the geometry, and a 500 would make a
                # retrying client send the identical drag again.
                raise ValueError(
                    "the pointer could not be put on the element to drag it. It "
                    "may be larger than the window, or outside it in a way "
                    "scrolling does not fix. Try resize, or drag a smaller "
                    "handle inside it"
                )
            start = moved["at"]

            if to_target is not None:
                # Read after the approach, which may have scrolled. The first
                # displayed match: a hidden duplicate's rect is at 0,0.
                aimed = pointer.aim(
                    driver,
                    browser.wait_for_shown(driver, to_target, at.timeout).element,
                )
                end, size = aimed["at"], aimed["viewport"]
            else:
                # The window's size came back with the approach's landing.
                end = (start[0] + offset[0], start[1] + offset[1])
                size = moved["viewport"]
            inside = pointer.clamped(end, size)

            pointer.drag_to(driver, start, inside, glide=wanted)
            self._moved(session_id, inside)
            if inside != end:
                # Said, because a slider dragged to the window edge instead of
                # to +400 looks like the site ignoring the drag.
                clamp["clamped"] = (
                    f"the destination was outside the window, so the drag "
                    f"stopped at its edge ({round(inside[0])}, {round(inside[1])})"
                )
            return {
                "from": {"x": round(start[0]), "y": round(start[1])},
                "to": {"x": round(inside[0]), "y": round(inside[1])},
                "glided": wanted,
            }

        result = self._recipe(CLICKABLE).run(
            session_id, travel, url=url, selector=selector, wait_timeout=wait_timeout
        )
        result.update(clamp)  # after the page state
        return result

    def frame(
        self,
        session_id: str,
        action="switch",
        selector=None,
        index=None,
        wait_timeout=WAIT_TIMEOUT,
    ) -> dict:
        """Move the session into an iframe, or back out of it.

        The switch is session state on the Grid, so it holds across calls until
        something switches back.
        """
        resolved = str(action).strip().lower()
        if resolved not in FRAME_ACTIONS:
            raise ValueError(
                f"unknown action {action!r}; known actions: "
                f"{', '.join(sorted(FRAME_ACTIONS))}"
            )
        if resolved == "switch" and not selector and index is None:
            raise ValueError("switch needs a selector or an index to say which frame")

        # Only a switch by selector waits for its frame.
        by_selector = resolved == "switch" and bool(selector)

        def switch(at):
            driver = at.driver
            if resolved == "default":
                driver.switch_to.default_content()
            elif resolved == "parent":
                driver.switch_to.parent_frame()
            elif by_selector:
                driver.switch_to.frame(at.element)
            else:
                driver.switch_to.frame(as_int(index, 0))
            return {"action": resolved, "in_frame": browser.in_frame(driver)}

        return self._reattached(PRESENCE if by_selector else None).run(
            session_id,
            switch,
            selector=selector if by_selector else None,
            wait_timeout=wait_timeout,
        )

    def resize(self, session_id: str, width=None, height=None) -> dict:
        """Resize the window of a session that is already open."""

        def size(at):
            driver = at.driver
            _resize(driver, width, height)
            now = driver.get_window_size()
            return {"width": now["width"], "height": now["height"]}

        return self._reattached().run(session_id, size)

    def dialog(
        self, session_id: str, action="accept", text=None, wait_timeout=DIALOG_TIMEOUT
    ) -> dict:
        """Answer a native alert, confirm or prompt; one blocks every other
        command until answered."""
        resolved = str(action).strip().lower()
        if resolved not in DIALOG_ACTIONS:
            raise ValueError(
                f"unknown action {action!r}; known actions: "
                f"{', '.join(sorted(DIALOG_ACTIONS))}"
            )
        # Checked before connecting: a 400 about the argument, not a Grid fault.
        if resolved == "send_text" and text is None:
            raise ValueError("text is required for the send_text action")

        def answer(at):
            # Not the recipe's wait: that one is for elements, at their timeout.
            alert = browser.wait_for_alert(
                at.driver, as_int(wait_timeout, DIALOG_TIMEOUT)
            )
            message = alert.text  # gone once answered

            if resolved == "send_text":
                alert.send_keys(str(text))
                alert.accept()
            elif resolved == "accept":
                alert.accept()
            elif resolved == "dismiss":
                alert.dismiss()
            return {"action": resolved, "message": message}

        return self._reattached().run(session_id, answer)

    def upload_file(
        self,
        session_id: str,
        selector=None,
        text=None,
        content=None,
        filename=None,
        mime_type=None,
        url=None,
        wait_timeout=WAIT_TIMEOUT,
        file=None,
        workspace=None,
    ) -> dict:
        """Attach a file to a file input.

        The file arrives one of three ways, and exactly one is required:
        ``text`` (plain text), ``content`` (base64, for binary) or ``file`` (any
        file this workspace has, by its ``workspace://files`` or
        ``session://downloads`` uri). Never a path on this server, which would
        let a caller upload the server's config and mounted secrets.

        ``workspace`` names which library ``file`` is read from. Only the flow
        engine sets it (``capabilities.LIBRARY_ARG``), so a step reads its own
        flow's library; `routes._add` keeps it out of the HTTP body and the MCP
        tool has no such field.

        The bytes are staged in a temporary file and shipped to the Grid node by
        Selenium, because the browser runs in another container.

        ``filename`` is what the page sees, and the browser derives the MIME
        type from its extension, so ``mime_type`` only supplies an extension the
        filename lacks.
        """
        sources = [
            n
            for n, v in (
                ("text", text),
                ("content", content),
                ("file", file),
            )
            if v
        ]
        if not sources:
            raise ValueError(
                "the file is required: pass text for a text file, content for "
                "base64 bytes, or file for any file this workspace has, by its "
                "workspace://files or session://downloads uri"
            )
        if len(sources) > 1:
            raise ValueError(
                f"pass only one of text, content or file; got {', '.join(sources)}"
            )

        # Resolved before connecting: unusable input is a 400 about the input.
        raw = None
        if text is not None:
            raw = str(text).encode("utf-8")
            name = safe_name(filename, mime_type, default_extension=".txt")
        elif content is not None:
            raw = content if isinstance(content, bytes) else _decode(content)
            name = safe_name(filename, mime_type)
        elif file is not None:
            if self.read_file is None:
                raise ValueError(
                    "file is not available on this server: the flow store is "
                    "off, so there is nowhere for keep_file to have kept one. "
                    "Pass text or content instead"
                )
            name, raw = self.read_file(str(file), str(workspace) if workspace else None)
            # The file's own name is the default: its extension is the type.
            name = safe_name(filename or name, mime_type)

        def at_with_local_files(session_id, url, look=None):
            # The file input is found by a driver that ships a local path.
            driver = self._at(session_id, url, look)
            browser.accept_local_files(driver)
            return driver

        def attach(at):
            temp_dir = None
            try:
                try:
                    temp_dir = tempfile.mkdtemp(prefix="selenium-flow-")
                except OSError as exc:
                    # The image runs read-only as an unprivileged user: a
                    # deployment problem, not the caller's.
                    raise RuntimeError(
                        "cannot stage the upload: no writable temporary "
                        f"directory ({exc}). Mount one at /tmp (an emptyDir "
                        "volume) or set TMPDIR to a writable path."
                    ) from exc
                staged = Path(temp_dir) / name
                staged.write_bytes(raw)

                # Selenium's LocalFileDetector reads the path to ship the bytes.
                at.element.send_keys(str(staged))
                return {"filename": staged.name, "bytes": staged.stat().st_size}
            finally:
                if temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)

        return Recipe(at_with_local_files, PRESENCE).run(
            session_id, attach, url=url, selector=selector, wait_timeout=wait_timeout
        )

    def write(
        self,
        session_id: str,
        text: str,
        selector=None,
        url=None,
        clear=True,
        submit=False,
        wait_timeout=WAIT_TIMEOUT,
        read_back=True,
    ) -> dict:
        """Type ``text`` into a field.

        ``read_back`` false is for a bound secret: the value is never read, so
        the credential is never in a local variable, a traceback or a result.
        """

        bound = not as_bool(read_back, True)

        def typing(at):
            driver, element = at.driver, at.element
            # Keys sent to a frame element land in the frame's document, whose
            # origin the leash never checked. Only a bound value has a leash:
            # literal text into a rich-text editor's iframe stays legal.
            if bound and element.tag_name.lower() in FRAME_ELEMENTS:
                raise ValueError(FRAME_REFUSAL)
            if as_bool(clear, True):
                element.clear()
            element.send_keys(str(text))
            # Read before any submit, which can make the element stale. A
            # password field's value is never read back.
            password = (element.get_attribute("type") or "").lower() == "password"
            read = None if bound or password else element.get_attribute("value")
            if as_bool(submit, False):
                element.send_keys(Keys.RETURN)
                # Wait for the navigation, or the page state is the old page's.
                browser.settled(driver, element)
            return {"value": read}

        return self._recipe(CLICKABLE, retry_stale=True).run(
            session_id, typing, url=url, selector=selector, wait_timeout=wait_timeout
        )

    def paste(self, session_id: str, text: str) -> dict:
        """Type ``text`` at the element a person focused, at the caret.

        `press_key` stops at a focused frame or shadow host; this follows the
        focus into both, and with no ``clear()``. The agent is always left in
        the top document, so a paste is refused while it has a frame selected.

        One trailing newline (a terminal copy's leftover, which would submit a
        form) is dropped before the cap. Answers a count and no page state, so
        the text never comes back.
        """
        text = str(text)
        text = text.removesuffix("\r\n" if text.endswith("\r\n") else "\n")
        text = text[:PASTE_CAP]

        def typing(at):
            driver = at.driver
            if driver.execute_script("return window !== window.top;"):
                raise ValueError(IN_A_FRAME)
            switched = False
            try:
                found = driver.execute_script(FOCUSED)

                def hosts(element) -> bool:
                    return element.tag_name.lower() in FRAME_ELEMENTS

                for _ in range(PASTE_DEPTH):
                    if found is None or not hosts(found):
                        break
                    driver.switch_to.frame(found)
                    switched = True
                    found = driver.execute_script(FOCUSED)
                if found is None:
                    raise ValueError(NOTHING_FOCUSED)
                if hosts(found):
                    raise ValueError(TOO_DEEP)
                try:
                    if text:
                        found.send_keys(text)
                except WebDriverException as exc:
                    if BMP_REFUSAL in str(exc):
                        raise ValueError(CANNOT_TYPE) from None
                    raise
            finally:
                if switched:
                    driver.switch_to.default_content()
            return {"typed": len(text)}

        return self._recipe().run(session_id, typing)

    def press_key(
        self,
        session_id: str,
        key: str,
        selector=None,
        url=None,
        wait_timeout=WAIT_TIMEOUT,
    ) -> dict:
        """Press a key or combination, at an element or wherever focus is."""
        # Resolved before the browser is touched: a typo costs nothing.
        resolved = resolve_key(key)

        def press(at):
            driver, element = at.driver, at.element
            if element is None:
                # Element Send Keys focuses its target first, so sending to
                # <body> would take focus away; the active element is <body>
                # when nothing is focused.
                element = driver.switch_to.active_element
            element.send_keys(resolved)
            # Only the keys that can submit a form wait for a navigation.
            if any(submit in resolved for submit in SUBMIT_KEYS):
                browser.settled(driver, element)
            return {"key": key}

        recipe = (
            self._recipe(CLICKABLE, retry_stale=True) if selector else self._recipe()
        )
        return recipe.run(
            session_id, press, url=url, selector=selector, wait_timeout=wait_timeout
        )

    def outline(
        self,
        session_id: str,
        selector=None,
        text=None,
        limit=probe.DEFAULT_LIMIT,
        interactive=True,
        url=None,
        wait_timeout=WAIT_TIMEOUT,
    ) -> dict:
        """What is on the page, with a selector for each and whether it works."""

        def map_(at):
            # Coerced here: a number for `text` dies on `.toLowerCase()` in the
            # page, and a negative `limit` would bound nothing.
            found, total = probe.outline(
                at.driver,
                at.element,
                "" if text is None else str(text),
                max(as_int(limit, probe.DEFAULT_LIMIT), 0),
                as_bool(interactive, True),
            )
            return {
                "elements": found,
                "count": len(found),
                "total": total,  # more than `count` when cut short
            }

        return self._recipe(PRESENCE if selector else None).run(
            session_id, map_, url=url, selector=selector, wait_timeout=wait_timeout
        )

    def execute_script(self, session_id: str, script: str, url=None) -> dict:
        """Run JavaScript in the page and return its result."""
        return self._recipe().run(
            session_id,
            lambda at: {"result": run_script(at.driver, script)},
            url=url,
        )

    def assert_(
        self,
        session_id: str,
        script: str,
        message=None,
        wait_timeout=WAIT_TIMEOUT,
        url=None,
        stable_for=0,
    ) -> dict:
        """Evaluate JavaScript that must come back true, a boolean (see
        `Assertion`).

        False is asked again until ``wait_timeout`` (0 asks once). With
        ``stable_for`` the answer must still be true that many seconds later,
        or the clock starts again: a guard needs it, because an app can paint
        its signed-in shell for a moment before redirecting to the login page.
        """
        timeout, hold = window(wait_timeout, stable_for, WAIT_TIMEOUT)

        def poll(at):
            # The page state reported is the page as it was when the answer held.
            Assertion(
                lambda: run_script(at.driver, script),
                at.state,
                timeout,
                hold,
                message,
            ).run()
            return {"asserted": True, "script": script}

        result = self._recipe().run(session_id, poll, url=url)
        if hold:
            result["stable_for"] = hold
        return result

    # ---- reading -----------------------------------------------------------

    def extract(
        self, session_id: str, selector=None, url=None, wait_timeout=WAIT_TIMEOUT
    ) -> dict:
        """Read the text and HTML of an element."""

        def read(at):
            return {
                "html": at.driver.execute_script(EXTRACT_JS, at.element),
                "text": at.element.text,
            }

        return self._recipe(PRESENCE).run(
            session_id, read, url=url, selector=selector, wait_timeout=wait_timeout
        )

    def screenshot(
        self,
        session_id: str,
        url=None,
        selector=None,
        full_page=False,
        width=None,
        height=None,
        wait_timeout=WAIT_TIMEOUT,
        save=True,
        filename=None,
    ) -> dict:
        """Capture a PNG and return it base64-encoded.

        Plain W3C WebDriver, so it works on any browser the Grid runs; a full
        page resizes the window, because Chrome has no W3C full-page command.
        """

        shot = {}

        def capture(at):
            driver = at.driver
            if width or height or as_bool(full_page, False):
                self._refuse(session_id, "resize")
            if width or height:
                _resize(driver, width, height)

            if at.target is not None:
                # Its own wait, after the resize, so the element is found in
                # the layout it is shot in.
                element = browser.wait_for_element(driver, at.target, at.timeout)
                image = element.screenshot_as_base64
            elif as_bool(full_page, False):
                before = driver.get_window_size()
                doc_w, doc_h = browser.full_page_size(driver)
                driver.set_window_size(max(doc_w, before["width"]), doc_h)
                try:
                    image = driver.get_screenshot_as_base64()
                finally:
                    driver.set_window_size(before["width"], before["height"])
            else:
                image = driver.get_screenshot_as_base64()

            img_w, img_h = browser.png_size(image)
            shot["raw"] = base64.b64decode(image)
            return {
                "image": image,
                "width": img_w,
                "height": img_h,
                "bytes": len(shot["raw"]),
            }

        result = self._recipe().run(
            session_id, capture, url=url, selector=selector, wait_timeout=wait_timeout
        )
        if as_bool(save, True):
            try:
                result["file"] = self._kept(
                    _generated_name(filename or "screenshot", ".png"),
                    shot["raw"],
                    SCREENSHOTS_DIR,
                )
            except Exception as exc:  # noqa: BLE001 - the picture outranks the file
                # A full disk or a server keeping nothing costs the file only.
                result["file_error"] = _why_unsaved(exc)
        return result

    def print_(
        self,
        session_id: str,
        url=None,
        format="pdf",
        filename=None,
        landscape=False,
        background=False,
    ) -> dict:
        """Print the page into the workspace's files, as a PDF (W3C ``print``)
        or as HTML (the document as the browser holds it now).

        ``landscape`` and ``background`` shape a PDF and mean nothing to HTML.
        """
        # A `null` or `""` over HTTP is refused, as the tool's enum refuses it.
        kind = str(format).strip().lower()
        if kind not in PRINT_FORMATS:
            raise ValueError(
                f"format must be one of {', '.join(PRINT_FORMATS)}, not {format!r}"
            )

        def render(at):
            if kind == "pdf":
                options = PrintOptions()
                if as_bool(landscape, False):
                    options.orientation = "landscape"
                # Off by default in every browser's print.
                options.background = as_bool(background, False)
                data = base64.b64decode(at.driver.print_page(options))
            else:
                data = at.driver.page_source.encode("utf-8")
            try:
                kept = self._kept(
                    _generated_name(filename or "page", f".{kind}"), data, FILES_DIR
                )
            except OSError as exc:
                # The error names a path under DATA_DIR: for the log only.
                log.error("a print could not be kept:\n%s", formatted(exc))
                raise RuntimeError(
                    f"the print could not be kept ({type(exc).__name__})"
                ) from None
            return {"file": kept, "format": kind, "bytes": len(data)}

        return self._recipe().run(session_id, render, url=url)

    def page(self, session_id: str) -> dict:
        """Where the browser is, for a secret's leash check. Not a tool.

        ``frame_origin`` is the origin of the document a keystroke would reach
        (the frame's, in a frame). It is read every time, because a frame can
        forge an in-frame check.
        """
        with node_answering():
            driver = self.grid.reconnect(session_id)
            return {
                **browser.page_state(driver),
                "frame_origin": browser.frame_origin(driver),
            }

    def network_body(self, session_id: str, id: str) -> dict:
        """One captured request's response body, asked of the browser that made
        it and of no other. ``id`` is checked before any lookup, and nothing is
        asked of a browser until the request is known to be this one's.

        A binary body is answered from its row, never fetched, and a text body
        over `BODY_FETCH_LIMIT` is refused unasked. Chrome answers
        `network.getData` in one frame whatever its size, so the body is asked
        on a connection of its own: a frame too large closes only that one, and
        is a 400. The text is masked, never logged, and comes back whole: the
        caller scrubs it, then cuts it (`cut_body`).
        """
        if not isinstance(id, str) or not 0 < len(id) <= ID_LIMIT:
            raise ValueError(f"id is a request id from {NETWORK_URI}, like 3.7")
        located = (
            None if self.capture is None else self.capture.request_for(session_id, id)
        )
        if located is None:
            raise ValueError(f"no captured request {id!r}: {NETWORK_URI} lists them")
        if located.session_id != session_id:
            raise NotFound(_body_ended(id))
        answer = {
            "id": id,
            "uri": f"{NETWORK_URI}/{id}",
            "mime": located.mime,
            "size": located.size,
            "binary": True,
            "text": None,
            "truncated": False,
        }
        if located.mime and not _is_text(located.mime):
            return answer  # its type and size are all a binary body gives
        if located.size is not None and located.size > BODY_FETCH_LIMIT:
            raise ValueError(
                f"request {id}'s body is {located.size / 1e6:.1f} MB, too large to "
                f"fetch: the browser holds it, but bodies over "
                f"{BODY_FETCH_LIMIT // 1_000_000} MB are not read from it. "
                f"{NETWORK_URI}/{id} has its headers and size"
            )
        try:
            with self.channels.open_channel(session_id, private=True) as channel:
                reply = channel.call(
                    "network.getData",
                    {"dataType": "response", "request": located.request},
                )
        except MessageTooBig:
            # On Firefox the frame may have been an event fanned out to every
            # socket, not this reply.
            raise ValueError(
                f"request {id}'s body could not be read: the browser sent a "
                f"message too large to read while fetching this body. "
                f"{NETWORK_URI}/{id} has its headers and size"
            ) from None
        except BidiError as exc:
            # The browser's own words are never passed on: they can carry paths.
            if exc.error in BODY_ANSWERS:
                raise NotFound(BODY_ANSWERS[exc.error]) from None
            raise RuntimeError("the browser could not return this body") from None
        data = (reply or {}).get("bytes") or {}
        value = data.get("value") or ""
        if data.get("type") == "base64":
            return {**answer, "size": _decoded_length(value)}
        # Masked whole, uncut: a marker can be longer than its value. The size
        # is what the browser sent.
        size = len(value.encode("utf-8", "surrogatepass"))
        masked = self.capture.mask(session_id, value, None)
        if masked is None:  # capture forgot this browser while it was asked
            raise NotFound(_body_ended(id))
        return {**answer, "size": size, "binary": False, "text": masked}

    def network_body_closed(self, workspace: str, id) -> None:
        """`network_body` in a workspace with no session open: a request
        capture kept is a 404 naming why, because its body went with the
        browser and a new one cannot return it. Anything else is left to the
        usual refusal (a 400: call open_session)."""
        if (
            self.capture is not None
            and isinstance(id, str)
            and self.capture.known(workspace, id)
        ):
            raise NotFound(BODY_CLOSED)

    def _recipe(self, wait=None, retry_stale=False) -> Recipe:
        """The road for an action that takes ``url``: through `_at`.

        Built per call, so a test that patches `_at` is heard.
        """
        return Recipe(self._at, wait, retry_stale)

    def _reattached(self, wait=None) -> Recipe:
        """The road for an action that takes no ``url``: a bare reattach, which
        does not refuse an empty ``session_id`` as `_at` does."""

        def reattach(session_id, _url, look=None):
            driver = self.grid.reconnect(session_id)
            if look:
                look(driver)
            return driver

        return Recipe(reattach, wait)

    def _at(self, session_id: str, url=None, look=None):
        """Reconnect, and put the browser on ``url`` if it is not already there.

        ``look`` sees the browser between the two, before ``url`` moves it.
        """
        if not session_id:
            raise ValueError("session_id is required")
        driver = self.grid.reconnect(session_id)
        if look:
            look(driver)
        if url:
            browser.ensure_url(
                driver,
                url,
                before_going=lambda: self._refuse(session_id, "navigate"),
            )
        return driver
