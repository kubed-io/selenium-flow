"""What the server can actually do, as plain functions.

This is the single source of truth for behaviour. ``tools.py`` exposes these to
MCP clients and ``routes.py`` exposes the same functions over HTTP, so the two
surfaces cannot drift: adding a capability here adds it to both.

Every function takes a ``session_id`` and returns a JSON-safe dict. Nothing is
cached between calls — the browser state lives on the Grid, not in this process.
"""

from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
import re
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import browser, pointer, probe
from .browser import Grid, as_bool, as_int, normalize_browser
from ..errors import GONE, UNAVAILABLE, AssertionFailed

# What a failed pointer move must never be mistaken for. See `_move_onto`.
INFRASTRUCTURE = (*GONE, *UNAVAILABLE)

log = logging.getLogger(__name__)

# Named keys a caller can press. Selenium's Keys members are unicode private-use
# characters, so a caller cannot reasonably type them into JSON by hand.
KEYS = {
    name.lower(): getattr(Keys, name)
    for name in dir(Keys)
    if name.isupper() and not name.startswith("_")
}

# Selenium spells 60 keys with 73 names: LEFT and ARROW_LEFT, BACK_SPACE and
# BACKSPACE, four for Meta. Every spelling still works, since KEYS keeps them
# all; this is the one name per key that is *offered*, so a listing is a list of
# keys rather than a quiz of synonyms. Where there is a choice, the name matching
# the browser's own KeyboardEvent.key wins.
_PREFERRED = frozenset({
    "alt", "arrow_down", "arrow_left", "arrow_right", "arrow_up", "backspace",
    "control", "meta", "right_alt", "shift",
})


def _offered_names() -> tuple[str, ...]:
    spellings: dict[str, list[str]] = {}
    for name in sorted(KEYS):
        spellings.setdefault(KEYS[name], []).append(name)
    chosen = []
    for names in spellings.values():
        preferred = [name for name in names if name in _PREFERRED]
        chosen.append((preferred or names)[0])
    return tuple(sorted(chosen))


KEY_NAMES = _offered_names()


def _squash(name: str) -> str:
    """One spelling for comparison: `ArrowLeft`, `arrow_left` and `arrow-left`
    are the same key, and so are `PageDown` and `page_down`."""
    return re.sub(r"[\s_-]", "", name).lower()


_BY_SPELLING = {_squash(name): value for name, value in KEYS.items()}

# `Control+a`, the way browsers and Playwright write a combination. A `+` that
# follows another `+` is the plus key itself, so `Control++` is Control and plus.
_COMBINATION = re.compile(r"\+(?=.)")


def _why_unsaved(exc: BaseException) -> str:
    """Why a capture could not be stored, without quoting the Grid at it.

    Storing goes through the Grid's HTTP API, and `requests` puts the whole URL
    into its message - a URL this deployment is allowed to put credentials in
    (`GRID_URL`). The one message worth repeating is ours, which names the file
    and says it never arrived; everything else is reported by type, and the
    detail stays in the log where it belongs.
    """
    if isinstance(exc, TimeoutError):
        return str(exc)
    return f"the capture could not be stored ({type(exc).__name__})"


def _shape(value) -> str:
    """What came back, without saying what was in it.

    The refusal is returned to the caller and logged, and an assertion can
    return anything the page holds — `document.cookie`, an innerHTML, a token
    in a data attribute. The author needs to know their expression answered
    with a string rather than a comparison; nobody needs the string.
    """
    if value is None:
        return "null"
    if isinstance(value, str):
        return f"a string of {len(value)} characters"
    if isinstance(value, list):
        return f"an array of {len(value)} items"
    if isinstance(value, dict):
        return f"an object with {len(value)} keys"
    if isinstance(value, (int, float)):
        return f"a number ({type(value).__name__})"
    return f"a {type(value).__name__}"


def _seconds(value, default: float, name: str = "value") -> float:
    """A duration in seconds, from JSON that may have sent it as a string.

    `as_int` cannot serve here: half a second is a sensible stability window and
    `int("0.5")` raises. Wider type, and deliberately **less** forgiving.

    "Coerce, don't trust" is the rule everywhere else because a fallback there
    is harmless - a `wait_timeout` that cannot be read becomes the default wait,
    and the call still waits. This one is different in kind: the fallback is
    zero, and zero means *the stability check does not happen*. A typo would
    quietly take away the guard the argument exists to add, and the assertion
    would then pass on the transient it was written to reject (Copilot, #31).
    """
    if value is None or value == "":
        return default
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must be a number of seconds; got {value!r}"
        ) from None
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be a finite number of seconds")
    if seconds < 0:
        raise ValueError(f"{name} cannot be negative; got {seconds}")
    return seconds


def resolve_key(key) -> str:
    """What to send for ``key``: a name, one character, or a combination.

    A string rather than an enum, deliberately, because the set is open — any
    character is a key. Names are matched in either spelling (§F2.1). A
    combination is sent as one sequence, and WebDriver holds each modifier for
    the keys after it and releases them all at the end.
    """
    text = "" if key is None else str(key)
    if len(text) > 1:
        text = text.strip()
    parts = [text] if len(text) <= 1 else _COMBINATION.split(text)
    return "".join(_one_key(part, text) for part in parts)


def _one_key(part: str, whole: str) -> str:
    if len(part) == 1:
        return part
    value = _BY_SPELLING.get(_squash(part)) if part else None
    if value is None:
        within = "" if part == whole else f" in {whole!r}"
        raise ValueError(
            f"unknown key {part!r}{within}; give one character, a combination "
            f"such as Control+a, or a name: {', '.join(KEY_NAMES)}"
        )
    return value


# The keys that can submit a form, and so are the only ones worth waiting on a
# navigation for. Selenium spells RETURN and ENTER as different characters, so
# both are here; every other key navigates nowhere by itself.
SUBMIT_KEYS = frozenset({Keys.RETURN, Keys.ENTER})


# Mouse gestures ``interact`` understands. hover and scroll_to are here rather
# than in their own tools because they take the same arguments as a click.
MOUSE_ACTIONS = ("click", "double_click", "right_click", "hover", "scroll_to")

# The ones that put the pointer somewhere. `scroll_to` moves the PAGE, not the
# pointer, so it is excluded and `glide` is meaningless for it. Every one of
# these now moves first and acts second, which is what makes Dr K's rule true:
# after a click the pointer is on what was clicked, the way a person's would be
# (saga §F2.3).
POINTER_ACTIONS = ("click", "double_click", "right_click", "hover")

# How often `assert_` asks the page again. Short enough to catch a route change
# in the frame after it lands, long enough not to spin the Grid on a wait that
# is going to take seconds.
ASSERT_POLL = 0.2

# How long to wait for a screenshot to appear in the download store. Shorter
# than `save_to_downloads`'s own default, which is right for a file the caller
# asked for and wrong for a save that happens on every capture: a flow taking
# thirty frames on a page that cannot download would otherwise spend thirty full
# timeouts discovering the same thing.
SAVE_TIMEOUT = 5

# Pages the browser will not download from at all, so there is nothing to wait
# for. Chrome refuses a data: URL outright - which is exactly what a synthetic
# test page is - and about: pages have no origin to download to.
UNDOWNLOADABLE = ("data:", "about:")

# What can be done with a native dialog. "read" deliberately leaves it open.
DIALOG_ACTIONS = ("accept", "dismiss", "read", "send_text")

# Where a frame switch can go. "parent" matters for nested frames.
FRAME_ACTIONS = ("switch", "parent", "default")

# How long a locator waits for its element before giving up. Named because the
# same number is the default on BOTH surfaces — every tool in tools.py and every
# endpoint in routes.py derives its default from here. Two copies of a literal
# 30 across two files is exactly how the surfaces come to disagree about what an
# omitted argument means, which is the drift this project is built to prevent.
WAIT_TIMEOUT = 30

# Dialogs get less. A native dialog is either already open or it is not — there
# is nothing to render and nothing to load — so a caller that guessed wrong
# should find out in ten seconds rather than thirty.
DIALOG_TIMEOUT = 10


def _decode(content) -> bytes:
    """Base64 file content as bytes, with a legible error if it is not base64."""
    try:
        return base64.b64decode(str(content), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            "content must be base64-encoded file bytes; "
            "use the multipart form of the HTTP endpoint to send a raw file"
        ) from exc


# The browser reads File.type from the file's EXTENSION — verified: data.json
# arrives as application/json, an extensionless file as "". So mime_type cannot
# override the type; all it can do is choose the extension. These are the
# formats an agent is likely to hand over, where the stdlib is wrong (it guesses
# .xsl for application/xml) or silent (yaml, ndjson).
EXTENSIONS = {
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "text/yaml": ".yaml",
    "application/yaml": ".yaml",
    "application/x-yaml": ".yaml",
    "application/x-ndjson": ".ndjson",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "text/plain": ".txt",
    "text/html": ".html",
}


def _extension_for(mime_type) -> str:
    """The extension that makes a browser report ``mime_type``, or ""."""
    if not mime_type:
        return ""
    key = str(mime_type).split(";")[0].strip().lower()
    return EXTENSIONS.get(key) or mimetypes.guess_extension(key) or ""


def _safe_name(filename, mime_type=None, default_extension="") -> str:
    """The name the page will see for an uploaded file.

    Reduced to a basename because the caller chooses it and it is written to
    disk here. An extension is appended when there is none, since without one
    the page reports an empty File.type and content sniffing does not happen.
    """
    # Backslashes are folded to "/" first so a Windows-style name is reduced to
    # its basename too. os.path.basename does not do that on Linux, which left
    # r"..\..\etc\passwd" intact as a "basename" — harmless as the staged
    # filename it becomes, but it is the caller's string and this is the one
    # place it is narrowed before being written to disk.
    raw_name = str(filename or "").replace("\\", "/")
    name = PurePosixPath(raw_name).name.strip().lstrip(".")
    if not name:
        name = "upload"
    if not PurePosixPath(name).suffix:
        name += _extension_for(mime_type) or default_extension
    return name


def _generated_name(filename, extension: str) -> str:
    """A name for bytes this server made, whose type it already knows.

    `_safe_name` keeps whatever suffix the caller wrote, which is right for an
    upload: there the caller has the file and names its type. Here the bytes are
    ours. A screenshot called `chart.pdf` is still a PNG, and the file store
    guesses the served type from the name - so the wrong suffix hands a browser
    a PNG labelled `application/pdf`, which it will not open.
    """
    name = _safe_name(filename, None, extension)
    if PurePosixPath(name).suffix.lower() != extension:
        name += extension
    return name


class Actions:
    """The browser operations, bound to one Grid."""

    def __init__(self, grid: Grid, describe_file=None, pointers=None, read_kept=None):
        self.grid = grid
        # How a stored file is described on the way out: the server injects a
        # function that signs a URL for it. A function rather than the token,
        # because this layer should be able to hand out a link without ever
        # holding the key that makes one (§F2.9). Absent - an open server with
        # no public base - a file is reported exactly as the Grid lists it.
        self.describe_file = describe_file
        # Where the pointer is in each browser, keyed by the Grid's session id.
        # Injected so a deployment can share it between replicas, and defaulted
        # so this class is still usable on its own.
        self.pointers = pointers if pointers is not None else pointer.MemoryPointers()
        # Reads a kept file by name, for `upload_file(kept=...)`. A function for
        # the same reason as `describe_file`: which flow session owns a kept
        # file is a question about the *caller*, and this layer deliberately
        # cannot see one. Absent, naming a kept file is refused with a reason.
        self.read_kept = read_kept

    def _stored(self, session_id: str, entry: dict) -> dict:
        """One saved file, described the same way wherever it was saved."""
        return self.describe_file(session_id, entry) if self.describe_file else entry

    def _pointer(self, session_id: str):
        """Where the pointer is in this browser, or None if we never sent it."""
        try:
            return self.pointers.get(session_id)
        except Exception:  # not knowing is a state here, not a failure
            log.debug("could not read the pointer for %s", session_id, exc_info=True)
            return None

    def _moved(self, session_id: str, at) -> None:
        """Record where a move we sent left the pointer."""
        try:
            if at is None:
                self.pointers.forget(session_id)
            else:
                self.pointers.set(session_id, at[0], at[1])
        except Exception:  # see `_pointer`
            log.debug("could not record the pointer for %s", session_id, exc_info=True)

    # ---- session lifecycle -------------------------------------------------

    def open_session(
        self,
        url=None,
        browser=None,
        width=None,
        height=None,
        page_load_timeout=None,
        script_timeout=None,
    ) -> dict:
        """Start a browser session with the settings it should run under.

        This is the only place a browser is created, and the only place these
        settings can be chosen — window size can be changed later with
        ``resize``, but the browser and the timeouts are set here and then
        simply hold. There is no switching a live session to another browser:
        that is a different browser, so it is a different session.
        """
        name = normalize_browser(browser)
        driver = self.grid.open(name)
        session_id = driver.session_id

        if width or height:
            current = driver.get_window_size()
            driver.set_window_size(
                as_int(width, current["width"]), as_int(height, current["height"])
            )

        # Unbounded by default, which lets one hanging page hold a Grid slot for
        # the whole idle timeout. Set here so it survives every later reconnect.
        if page_load_timeout:
            driver.set_page_load_timeout(as_int(page_load_timeout, 300))
        if script_timeout:
            driver.set_script_timeout(as_int(script_timeout, 30))

        # A new browser's pointer starts at (0,0) and nothing this server sent
        # put it there, so whatever was remembered for a previous browser must
        # not be inherited. Grid ids are not reused, but forgetting is what
        # makes that a fact rather than an assumption.
        self._moved(session_id, None)

        current_url, title = "about:blank", ""
        if url:
            driver.get(url)
            current_url, title = driver.current_url, driver.title

        size = driver.get_window_size()
        # Reported back so a caller can see what the cascade actually resolved
        # to, rather than assuming its argument won. Both surfaces get this from
        # here, so they cannot describe the same session differently.
        #
        # `browser` is always present, unlike the timeouts: it is never unset,
        # only defaulted, and it is what a refresh has to replay to reopen the
        # same browser rather than the default one.
        applied = {
            "browser": name,
            "width": size["width"],
            "height": size["height"],
        }
        if page_load_timeout:
            applied["page_load_timeout"] = as_int(page_load_timeout, 0)
        if script_timeout:
            applied["script_timeout"] = as_int(script_timeout, 0)
        return {
            "session_id": session_id,
            "browser": name,
            "url": current_url,
            "title": title,
            "width": size["width"],
            "height": size["height"],
            "settings": applied,
        }

    def end_browser(self, session_id: str) -> dict:
        """Quit the browser and free its Grid slot.

        The browser, not the session. A flow session survives its browser and
        keeps the context the next open inherits — see ``SessionManager``.
        """
        self.grid.quit(session_id)
        self._moved(session_id, None)
        return {"success": True, "session_id": session_id}

    # ---- navigation --------------------------------------------------------

    def navigate(self, session_id: str, url: str) -> dict:
        """Go to a URL, unconditionally."""
        driver = self.grid.reconnect(session_id)
        driver.get(url)
        return {**browser.page_state(driver)}

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

        One action rather than five tools: they take identical arguments and
        differ only in which gesture is sent, so splitting them would be five
        near-identical schemas for a model to choose between.

        Every gesture that involves the pointer **moves it first and acts
        second**. A jump unless ``glide`` asks otherwise, so nothing that worked
        before behaves differently; what changes is that afterwards the pointer
        is on what was acted on, which is where a person's would be (§F2.3).
        The click itself stays WebDriver's element click, deliberately: it is
        the one that refuses a covered target loudly, where a pointer-action
        click would silently press whatever is on top.
        """
        resolved = str(action).strip().lower()
        if resolved not in MOUSE_ACTIONS:
            raise ValueError(
                f"unknown action {action!r}; known actions: "
                f"{', '.join(sorted(MOUSE_ACTIONS))}"
            )
        # Resolved before the browser is touched: a selector naming both xpath
        # and css is a mistake, and finding that out after a reconnect and a
        # navigation costs a page load to learn nothing.
        target = browser.locator(selector)
        driver = self._at(session_id, url)
        timeout = as_int(wait_timeout, 30)

        def gesture(element):
            """The whole act, so a retry re-does the move as well as the click."""
            moved = None
            if resolved in POINTER_ACTIONS:
                moved = self._move_onto(session_id, driver, element, glide)

            if resolved == "click":
                try:
                    element.click()
                except ElementClickInterceptedException as exc:
                    # This is where a covered element actually surfaces.
                    # Selenium's `element_to_be_clickable` considers one
                    # clickable, so the wait above passes and the failure lands
                    # here - and the driver's message names the element it was
                    # asked for, not the thing on top of it. The probe knows
                    # which (saga §F2.8).
                    why = probe.explain(driver, target)
                    first = (getattr(exc, "msg", "") or "").strip().splitlines()
                    raise ElementClickInterceptedException(
                        (first[0] if first else "element click intercepted")
                        + (f" {why}" if why else "")
                    ) from exc
            elif resolved == "hover":
                # The move IS the hover. Only when it could not be sent does
                # this fall back to the gesture this package has always used.
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
            return moved

        # hover and scroll_to only need the element to exist. Requiring it to be
        # clickable would refuse exactly the off-screen element scroll_to is for.
        moved = self._acting_on(
            driver, target, timeout, resolved not in ("hover", "scroll_to"), gesture
        )

        return {
            "action": resolved,
            **self._pointer_report(moved, glide),
            **browser.page_state(driver),
        }

    def _acting_on(self, driver, target, timeout: int, clickable: bool, act):
        """Find the element and act on it, once more if it goes stale first.

        A page that repaints replaces the element between the wait and the act,
        and WebDriver reports that as a stale reference. It is not a mistake by
        the caller and there is nothing to fix in the selector: the element it
        found is simply not the one on the page any more. Found by the admin UI,
        whose session list repaints on a two-second poll — a click on a row was
        racy on every page that refreshes itself, which is a great many of them.

        Retried **once**, and the wait is part of the retry: retrying the act
        alone would reuse the same dead reference. Once rather than until it
        works, because a page that replaces an element faster than we can act on
        it is a real finding, and a loop would bury it as a slow call.
        """
        find = browser.wait_for_clickable if clickable else browser.wait_for_element
        try:
            return act(find(driver, target, timeout))
        except StaleElementReferenceException:
            log.info("element went stale before it could be used; finding it again")
            return act(find(driver, target, timeout))

    def _move_onto(self, session_id: str, driver, element, glide) -> dict | None:
        """Put the pointer on ``element`` before the gesture, if it can.

        Wrapped, because this is new work in front of gestures that already
        worked: a destination WebDriver will not move to - an element taller
        than the viewport is the usual one - must cost the pointer's position
        and nothing else. The gesture then runs exactly as it did before.
        """
        try:
            moved = pointer.move(
                driver,
                element,
                start=self._pointer(session_id),
                glide=as_bool(glide, False),
            )
        except INFRASTRUCTURE:
            # A dead browser or an unreachable Grid is not something the
            # gesture can carry on without, and `errors.py` has a specific
            # status and a specific fix for each - 404 "call /browser/open",
            # 503 "the Grid is unavailable". Swallowing them here turned both
            # into "the pointer could not be moved", which for `drag` then
            # became a 400 telling the caller their geometry was wrong
            # (Copilot, #31).
            raise
        except Exception:  # the gesture outranks the move
            log.debug("could not move the pointer onto the target", exc_info=True)
            # Forgotten rather than left stale: a move that failed part-way
            # leaves the pointer somewhere we cannot name, and a glide plotted
            # from a wrong origin crosses the wrong elements (§F2.3).
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
            # Said out loud because it explains a hover that worked this time
            # and did nothing last time: the pointer was already inside the
            # target, so the move had to start by leaving it (§F2.10).
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

        Its own tool rather than a sixth `interact` action, because it is the
        one gesture that needs a **source and a destination** — which is the
        premise that put five gestures into one tool in the first place (§F2.4).

        Press, travel, release, with a short hold either side of the travel:
        several drag libraries arm on a delay or a distance rather than on the
        press, and a press and release in the same frame reads as a click.

        ``glide`` defaults to **true** here, the opposite of `interact`.
        Incremental movement is most of what a drag is for — a sortable list or
        a slider watching for `pointermove` sees a teleport otherwise.
        """
        target = browser.locator(selector)
        # Resolved before the browser is touched, like every other locator
        # mistake: an impossible drag should cost a 400, not a page load.
        to_target = None
        offset = None
        if to is not None:
            if by_x is not None or by_y is not None:
                raise ValueError(
                    "give the destination as `to` OR as a by_x/by_y "
                    "offset, never both"
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

        driver = self._at(session_id, url)
        timeout = as_int(wait_timeout, 30)
        element = browser.wait_for_clickable(driver, target, timeout)

        # The approach is always a jump: `glide` is about the travel with the
        # button down, which is the part a drag library is watching.
        moved = self._move_onto(session_id, driver, element, False)
        if moved is None:
            # A ValueError, so this is a 400. It describes a geometry the
            # caller can fix - resize the window, scroll, name a smaller
            # handle - and a 500 would tell an n8n node with Retry-On-Fail to
            # send the identical drag again (Copilot, #31).
            raise ValueError(
                "the pointer could not be put on the element to drag it. It may "
                "be larger than the window, or outside it in a way scrolling "
                "does not fix. Try resize, or drag a smaller handle inside it"
            )
        start = moved["at"]

        if to_target is not None:
            # Read now rather than when the step was written: the approach may
            # have scrolled, and every rect on the page moved with it (§F2.3).
            end = pointer.center(
                driver, browser.wait_for_element(driver, to_target, timeout)
            )
        else:
            end = (start[0] + offset[0], start[1] + offset[1])
        inside = pointer.clamped(end, pointer.viewport(driver))

        wanted = as_bool(glide, True)
        pointer.drag_to(driver, start, inside, glide=wanted)
        self._moved(session_id, inside)

        result = {
            "from": {"x": round(start[0]), "y": round(start[1])},
            "to": {"x": round(inside[0]), "y": round(inside[1])},
            "glided": wanted,
            **browser.page_state(driver),
        }
        if inside != end:
            # A clamp changes where the drop landed, so it cannot be silent: a
            # slider dragged to the window edge instead of to +400 looks like
            # the site ignoring the drag.
            result["clamped"] = (
                f"the destination was outside the window, so the drag stopped "
                f"at its edge ({round(inside[0])}, {round(inside[1])})"
            )
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

        Selenium does not look inside frames: an element in one is invisible to
        every locator until the session is switched into it. That switch is
        **session state on the Grid**, not something this process holds, so it
        persists across calls — and keeps applying until something switches
        back. That is why ``default`` exists and why the session resource
        reports whether you are in a frame.
        """
        resolved = str(action).strip().lower()
        if resolved not in FRAME_ACTIONS:
            raise ValueError(
                f"unknown action {action!r}; known actions: "
                f"{', '.join(sorted(FRAME_ACTIONS))}"
            )
        if resolved == "switch" and not selector and index is None:
            raise ValueError(
                "switch needs a selector or an index to say which frame"
            )

        driver = self.grid.reconnect(session_id)
        if resolved == "default":
            driver.switch_to.default_content()
        elif resolved == "parent":
            driver.switch_to.parent_frame()
        elif selector:
            driver.switch_to.frame(
                browser.wait_for_element(
                    driver, browser.locator(selector), as_int(wait_timeout, 30)
                )
            )
        else:
            driver.switch_to.frame(as_int(index, 0))

        return {
            "action": resolved,
            "in_frame": browser.in_frame(driver),
            **browser.page_state(driver),
        }

    def resize(self, session_id: str, width=None, height=None) -> dict:
        """Resize the window of a session that is already open.

        Window size is one of the few things WebDriver lets you change after
        creation, which is why this is a separate action rather than an argument
        to ``open_session`` alone — a caller whose browser was opened for them
        can still set it.
        """
        driver = self.grid.reconnect(session_id)
        current = driver.get_window_size()
        driver.set_window_size(
            as_int(width, current["width"]), as_int(height, current["height"])
        )
        size = driver.get_window_size()
        return {
            "width": size["width"],
            "height": size["height"],
            **browser.page_state(driver),
        }

    def dialog(
        self, session_id: str, action="accept", text=None, wait_timeout=DIALOG_TIMEOUT
    ) -> dict:
        """Answer a native alert, confirm or prompt.

        An open dialog blocks every other command, so without this one
        ``confirm()`` makes a session unusable until it is reaped.
        """
        resolved = str(action).strip().lower()
        if resolved not in DIALOG_ACTIONS:
            raise ValueError(
                f"unknown action {action!r}; known actions: "
                f"{', '.join(sorted(DIALOG_ACTIONS))}"
            )
        # Checked before connecting: a caller's mistake should be a 400 about the
        # argument, not a 500 about the Grid it never needed to reach.
        if resolved == "send_text" and text is None:
            raise ValueError("text is required for the send_text action")

        driver = self.grid.reconnect(session_id)
        alert = browser.wait_for_alert(driver, as_int(wait_timeout, 10))
        # Read before answering: the dialog is gone once accepted or dismissed.
        message = alert.text

        if resolved == "send_text":
            alert.send_keys(str(text))
            alert.accept()
        elif resolved == "accept":
            alert.accept()
        elif resolved == "dismiss":
            alert.dismiss()
        # "read" leaves it open, so a caller can decide what to do about it.

        return {
            "action": resolved,
            "message": message,
            **browser.page_state(driver),
        }

    def upload_file(
        self,
        session_id: str,
        selector=None,
        text=None,
        content=None,
        filename=None,
        mime_type=None,
        path=None,
        url=None,
        wait_timeout=WAIT_TIMEOUT,
        kept=None,
        session=None,
    ) -> dict:
        """Attach a file to a file input.

        The file arrives one of four ways, and exactly one is required:

        - ``text`` — the file's content as plain text. This is the one to use
          for anything an agent produced itself: JSON, CSV, YAML, markdown.
          Base64-encoding text it just wrote is a wasted step it can get wrong.
        - ``content`` — base64, which binary needs and which is the only shape
          MCP tool arguments can carry.
        - ``kept`` — the name of a file `keep_file` already kept, from the
          library ``session`` names. This closes
          the loop the file store never had: a browser could download a file
          and keep it, and there was no way to give it back to a page. Now a
          flow can download an export and upload it somewhere else, without the
          bytes ever passing through a model's context (§F1.41).
        - ``path`` — a file already on this server's filesystem.

        Whichever it is, the bytes are written to a temporary file here and
        shipped to the Grid node by Selenium, because the browser runs in
        another container and cannot see this filesystem.

        ``filename`` is what the page sees, and its extension is what decides
        the MIME type the page reports — the browser derives that from the name,
        not from anything we can send. ``mime_type`` is therefore used to supply
        an extension when the filename lacks one, rather than to override it.
        """
        sources = [
            n
            for n, v in (
                ("text", text),
                ("content", content),
                ("kept", kept),
                ("path", path),
            )
            if v
        ]
        if not sources:
            raise ValueError(
                "the file is required: pass text for a text file, content for "
                "base64 bytes, kept for a file keep_file has kept, or path for "
                "a file on the server"
            )
        if len(sources) > 1:
            raise ValueError(
                f"pass only one of text, content, kept or path; "
                f"got {', '.join(sources)}"
            )

        # Resolved before connecting: unusable input is a 400 about the input,
        # and there is no point opening anything to discover it.
        raw = None
        if text is not None:
            raw = str(text).encode("utf-8")
            name = _safe_name(filename, mime_type, default_extension=".txt")
        elif content is not None:
            raw = content if isinstance(content, bytes) else _decode(content)
            name = _safe_name(filename, mime_type)
        elif kept is not None:
            if self.read_kept is None:
                raise ValueError(
                    "kept files are not available on this server: the flow "
                    "store is off, so there is nowhere for keep_file to keep "
                    "one. Pass text, content or path instead"
                )
            # `session` names WHICH library, and is not `session_id`, which
            # names the browser. Both appear on `/files/list` for the same
            # reason: a file store outlives the browser that filled it, so the
            # two are different questions. An MCP caller passes neither - its
            # key answers the first and the server the second.
            raw = self.read_kept(str(kept), str(session) if session else None)
            # The kept name is the default, because its extension is what the
            # page reads the type from and a caller that kept `export.csv`
            # should not have to say so twice.
            name = _safe_name(filename or str(kept), mime_type)

        driver = self._at(session_id, url)
        browser.accept_local_files(driver)
        element = browser.wait_for_element(
            driver, browser.locator(selector), as_int(wait_timeout, 30)
        )

        temp_dir = None
        try:
            if raw is not None:
                try:
                    temp_dir = tempfile.mkdtemp(prefix="selenium-flow-")
                except OSError as exc:
                    # The image runs read-only as an unprivileged user, so this
                    # is a deployment problem rather than a caller's: there has
                    # to be one writable directory to stage a file in before
                    # Selenium can ship it to the Grid node.
                    raise RuntimeError(
                        "cannot stage the upload: no writable temporary "
                        f"directory ({exc}). Mount one at /tmp (an emptyDir "
                        "volume) or set TMPDIR to a writable path."
                    ) from exc
                staged = Path(temp_dir) / name
                staged.write_bytes(raw)
            else:
                staged = Path(str(path))
                if not staged.is_file():
                    raise ValueError(f"no file at {staged}")

            # send_keys wants a string path, and Selenium's LocalFileDetector
            # reads it off the filesystem to ship the bytes to the Grid node.
            element.send_keys(str(staged))
            size = staged.stat().st_size
            name = staged.name
        finally:
            # The bytes live on the Grid node now; this copy has done its job.
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

        return {
            "filename": name,
            "bytes": size,
            **browser.page_state(driver),
        }

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

        ``read_back`` is normally true and is what lets a caller confirm the
        text landed. It is turned off for a value the caller must never be shown
        again — a bound secret — and turning it off means the read is **not
        performed**, not that its answer is dropped later. Redacting afterwards
        still puts the credential in a local variable, in a traceback if one
        fires between the two, and in whatever the action returned before
        anything wrapped it.
        """
        target = browser.locator(selector)
        driver = self._at(session_id, url)

        def typing(element):
            if as_bool(clear, True):
                element.clear()
            element.send_keys(str(text))
            # Read the value back before any submit: submitting navigates, which
            # makes the element reference stale.
            read = element.get_attribute("value") if as_bool(read_back, True) else None
            if as_bool(submit, False):
                element.send_keys(Keys.RETURN)
                # And then wait for the navigation it may have caused, or the
                # state below describes the page we just left. See
                # `browser.settled`.
                browser.settled(driver, element)
            return read

        value = self._acting_on(
            driver, target, as_int(wait_timeout, 30), True, typing
        )
        return {"value": value, **browser.page_state(driver)}

    def press_key(
        self,
        session_id: str,
        key: str,
        selector=None,
        url=None,
        wait_timeout=WAIT_TIMEOUT,
    ) -> dict:
        """Press a key or combination, at an element or wherever focus is.

        This is how you reach Tab, Escape, Enter and the arrows. It is *not* a
        reliable way to scroll: page_down only moves the page when focus happens
        to be on the scrollable container. Use ``execute_script`` to scroll.
        """
        # Resolved before the browser is touched: a typo costs nothing.
        resolved = resolve_key(key)
        driver = self._at(session_id, url)
        def press(element):
            element.send_keys(resolved)
            # Only the keys that can submit a form. Tab, Escape and the arrows
            # never navigate, and making every one of them wait to find that out
            # would tax the common case for nothing. See `browser.settled`.
            if any(submit in resolved for submit in SUBMIT_KEYS):
                browser.settled(driver, element)

        if selector:
            self._acting_on(
                driver,
                browser.locator(selector),
                as_int(wait_timeout, 30),
                True,
                press,
            )
        else:
            press(driver.find_element(By.TAG_NAME, "body"))
        return {"key": key, **browser.page_state(driver)}

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
        """What is on the page, with a selector for each and whether it works.

        The answer an agent otherwise assembles out of DOM dumps: the first
        pilot report spent three hand-written scripts finding one sidebar link,
        then clicked it and failed anyway, because the element existed and its
        ancestor was `display: none`. Both halves are here - the selector and
        the verdict - so the failure does not have to teach it.
        """
        driver = self._at(session_id, url)
        scope = None
        if selector:
            scope = browser.wait_for_element(
                driver, browser.locator(selector), as_int(wait_timeout, 30)
            )
        # Both coerced here rather than in the page: an HTTP caller can send a
        # number for `text`, which reaches JavaScript as one and dies on
        # `.toLowerCase()`, and a negative `limit` would bound nothing.
        found = probe.outline(
            driver,
            scope,
            "" if text is None else str(text),
            max(as_int(limit, probe.DEFAULT_LIMIT), 0),
            as_bool(interactive, True),
        )
        return {
            "elements": found,
            "count": len(found),
            **browser.page_state(driver),
        }

    def execute_script(self, session_id: str, script: str, url=None) -> dict:
        """Run JavaScript in the page and return its result.

        The escape hatch for everything the other actions do not cover —
        scrolling, drag and drop, reading computed styles, poking the DOM.
        ``return`` a value to get it back.
        """
        driver = self._at(session_id, url)
        result = driver.execute_script(script)
        return {"result": result, **browser.page_state(driver)}

    def assert_(
        self,
        session_id: str,
        script: str,
        message=None,
        wait_timeout=WAIT_TIMEOUT,
        url=None,
        stable_for=0,
    ) -> dict:
        """Evaluate JavaScript that must come back true.

        ``execute_script`` with one difference, and the difference is the point:
        the answer has to be a **boolean**. A flow had no way to say what must be
        true, so one reported eight passing steps while sitting on the page
        before the one it meant to reach.

        Truthiness is refused rather than accepted, because it is how an
        assertion passes by accident: ``return document.querySelector('#x')``
        reads correctly and is correct by luck, until the day the expression
        answers ``0`` or ``[]``.

        False is not final until the timeout. The expression is asked again,
        the way every wait here works — a single-page app lands its route a few
        frames after the click that caused it, and an assertion that looked
        once would be that same race moved one step later. Nothing sleeps
        waiting for a fixed duration; ``wait_timeout=0`` asks exactly once.

        ``stable_for`` is the other half, and it is what a **guard** needs.
        Poll-until-true means *eventually* true, which is right after a click
        and wrong before one: an app that paints its signed-in shell for a
        moment before redirecting to the login page satisfies "am I signed in"
        during that moment, and a guard written that way passed while signed
        out. With ``stable_for`` the answer has to still be true that many
        seconds later, or the clock starts again (§F2.10).
        """
        # Resolved before the browser is touched, like every other argument
        # mistake here: `_at` reconnects and may NAVIGATE, so validating after
        # it means an impossible request moves the caller's browser and then
        # answers 400. A rejected argument must cost nothing (Copilot, #31).
        timeout = max(as_int(wait_timeout, WAIT_TIMEOUT), 0)
        hold = _seconds(stable_for, 0.0, "stable_for")
        # `>=`, not `>`. Verifying a hold needs at least one poll AFTER the
        # answer first came back true, and the deadline stops that poll at
        # `wait_timeout` - so a hold exactly equal to the timeout can never be
        # confirmed and every such assertion failed with "did not hold",
        # whatever the page did (Copilot, #31).
        if hold and hold >= timeout:
            # Refused rather than silently impossible, and the message names the
            # unit: `stable_for` and `wait_timeout` are both seconds, and a
            # caller who read one of them as milliseconds finds out here rather
            # than from an assertion that can never pass.
            raise ValueError(
                f"stable_for ({hold}s) leaves no room inside wait_timeout "
                f"({timeout}s): the answer has to be asked again AFTER it has "
                "held, so the wait must be longer than the hold. Both are in "
                "seconds; raise wait_timeout, or lower stable_for"
            )
        driver = self._at(session_id, url)
        deadline = time.monotonic() + timeout
        true_since = None
        ever_true = False
        first = True
        while True:
            answer = driver.execute_script(script)
            if not isinstance(answer, bool):
                raise ValueError(
                    f"assert must return true or false; this returned "
                    f"{_shape(answer)}. Compare, rather than returning the "
                    "thing itself - return !!document.querySelector('#x')"
                )
            now = time.monotonic()
            # The script itself can run past the deadline - a blocking
            # expression, a page that stops responding - and an answer that
            # arrived after the caller stopped waiting is not an answer to
            # `wait_timeout` (Copilot, #31). The FIRST evaluation is exempt,
            # because `wait_timeout=0` promises exactly one look and any script
            # takes longer than nothing.
            if answer and not first and now > deadline:
                answer = False
            first = False
            if answer:
                ever_true = True
                if true_since is None:
                    true_since = now
                if now - true_since >= hold:
                    result = {
                        "asserted": True,
                        "script": script,
                        **browser.page_state(driver),
                    }
                    if hold:
                        result["stable_for"] = hold
                    return result
            else:
                # The clock restarts, it does not pause. A transient that
                # flickers true, false, true has not held true for anything.
                true_since = None
            # Bounded by what is left, and re-checked before the next
            # evaluation: a fixed pause here could carry the call past
            # `wait_timeout` and then report an answer that arrived after the
            # caller had stopped waiting for it.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(ASSERT_POLL, remaining))
            if time.monotonic() > deadline:
                # A sleep can wake late. Without this, the answer that arrived
                # after the caller stopped waiting would still be accepted, so
                # a slow page could pass an assertion it had already failed.
                # The first evaluation is above the loop's exits, so
                # wait_timeout=0 still asks exactly once.
                break

        state = browser.page_state(driver)
        if hold and ever_true:
            # A different failure and worth saying so: the page DID answer true,
            # it just would not stay that way. Told apart from "never true",
            # because the fixes are opposite - one is a wrong assertion, the
            # other is a page still settling.
            raise AssertionFailed(
                message
                or f"the assertion became true on {state.get('url')!r} but did "
                f"not hold for {hold}s. Give the step a message to say what "
                "should have been true"
            )
        # The script is not echoed. It is the author's text rather than the
        # page's, but it can carry a literal a run report must not: a token
        # compared inline, a serialised request body. This package already keeps
        # `script` out of run summaries (`SAFE_IN_SUMMARY`) for that reason, and
        # a failure message is read in more places than a summary is - the HTTP
        # error, the flow report, the log. So: where it was false, and a nudge
        # to write the sentence that would have said what should have been true.
        raise AssertionFailed(
            message
            or f"assertion failed after {timeout}s on {state.get('url')!r}. "
            "Give the step a message to say what should have been true"
        )

    # ---- reading -----------------------------------------------------------

    def extract(
        self, session_id: str, selector=None, url=None, wait_timeout=WAIT_TIMEOUT
    ) -> dict:
        """Read the text and HTML of an element."""
        target = browser.locator(selector)
        driver = self._at(session_id, url)
        element = browser.wait_for_element(driver, target, as_int(wait_timeout, 30))
        return {
            "html": element.get_attribute("innerHTML"),
            "text": element.text,
            **browser.page_state(driver),
        }

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

        Plain W3C WebDriver throughout, so this works on any browser the Grid
        runs. Chrome has no W3C full-page command, hence the window resize.
        """
        driver = self._at(session_id, url)

        if width or height:
            current = driver.get_window_size()
            driver.set_window_size(
                as_int(width, current["width"]), as_int(height, current["height"])
            )

        if selector:
            element = browser.wait_for_element(
                driver, browser.locator(selector), as_int(wait_timeout, 30)
            )
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
        raw = base64.b64decode(image)
        result = {
            "image": image,
            "width": img_w,
            "height": img_h,
            "bytes": len(raw),
            **browser.page_state(driver),
        }
        # Saved by default. It used to be opt-in, which made the *agent* decide
        # whether a person would ever want to look at this one — and the answer
        # is usually no, so an operator watching the admin UI saw nothing and
        # had nothing to open. A session's files die with the browser and cost
        # nothing while it lives, so the cheap thing is to keep them all and let
        # `keep_file` be the only decision anybody makes (§F2.9).
        if as_bool(save, True):
            name = _generated_name(filename or "screenshot", ".png")
            page = result.get("url") or ""
            try:
                if page.startswith(UNDOWNLOADABLE):
                    scheme = page.split(":", 1)[0]
                    raise TimeoutError(
                        f"the browser does not download from {scheme}: pages, so "
                        f"{name} was not stored"
                    )
                result["file"] = self._stored(
                    session_id,
                    browser.save_to_downloads(
                        self.grid,
                        driver,
                        name,
                        raw,
                        "image/png",
                        timeout=SAVE_TIMEOUT,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - the picture outranks the file
                # Saving happens on every screenshot now, so it must never be
                # able to take one away. A page whose policy blocks a download,
                # or a Grid that never lists the file, costs the file and not
                # the capture — said out loud rather than silently.
                result["file_error"] = _why_unsaved(exc)
        return result

    # ---- internals ---------------------------------------------------------

    def files(self, session_id: str) -> dict:
        """List what this session has downloaded.

        Covers both kinds of file in one place: anything the *site* served to a
        download, and anything this server saved there itself. They are not
        distinguished because to the caller they are the same thing — files this
        browsing session produced.
        """
        if not session_id:
            raise ValueError("session_id is required")
        return {"session_id": session_id, "files": self.grid.files(session_id)}

    def clear_files(self, session_id: str) -> dict:
        """Delete the session's downloads without closing the browser."""
        if not session_id:
            raise ValueError("session_id is required")
        self.grid.clear_files(session_id)
        return {"success": True, "session_id": session_id}

    def save_pdf(self, session_id: str, url=None, filename=None) -> dict:
        """Print the current page to PDF and keep it with the session's files.

        W3C ``print``, not a Chrome-only DevTools call, so this is the same
        rendering a user gets from Ctrl+P rather than a screenshot of the
        viewport — text stays selectable and the whole document is included.
        """
        name = _generated_name(filename or "page", ".pdf")
        driver = self._at(session_id, url)
        data = base64.b64decode(driver.print_page())
        entry = browser.save_to_downloads(
            self.grid, driver, name, data, "application/pdf"
        )
        return {
            "file": self._stored(session_id, entry),
            "bytes": len(data),
            **browser.page_state(driver),
        }

    def page(self, session_id: str) -> dict:
        """Where the browser is, without touching it.

        Not a capability and so not a tool: `session://current` already answers
        this for a caller. It exists because a secret's leash is checked against
        the page about to receive the keystroke, and that check has to read the
        page rather than trust what the caller said about it.
        """
        return browser.page_state(self.grid.reconnect(session_id))

    def _at(self, session_id: str, url=None):
        """Reconnect, and put the browser on ``url`` if it is not already there."""
        if not session_id:
            raise ValueError("session_id is required")
        driver = self.grid.reconnect(session_id)
        if url:
            browser.ensure_url(driver, url)
        return driver
