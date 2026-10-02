"""What the server can actually do, as plain functions.

This is the single source of truth for behaviour. ``tools.py`` exposes these to
MCP clients and ``routes.py`` exposes the same functions over HTTP, so the two
surfaces cannot drift: adding a capability here adds it to both.

Every function takes a ``session_id`` and returns a JSON-safe dict. Nothing is
cached between calls — the browser state lives on the Grid, not in this process.
"""

from __future__ import annotations

import base64
import logging
import shutil
import tempfile
import time
from pathlib import Path

from selenium.common.exceptions import ElementClickInterceptedException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.print_page_options import PrintOptions

from ..errors import GONE, UNAVAILABLE
from ..names import FILES_DIR, SCREENSHOTS_DIR
from ..site_data import snapshot as site_data_snapshot
from ..site_data import transfer as site_data_transfer
from ..urls import allowed_navigation
from . import browser, pointer, probe
from .assertion import Assertion, window
from .browser import Grid
from .coerce import as_bool, as_int
from .defaults import normalize_browser
from .keys import SUBMIT_KEYS, resolve_key
from .naming import _decode, _generated_name, _why_unsaved, safe_name
from .recipe import CLICKABLE, DIALOG_TIMEOUT, PRESENCE, WAIT_TIMEOUT, Recipe

# What a failed pointer move must never be mistaken for. See `_move_onto`.
INFRASTRUCTURE = (*GONE, *UNAVAILABLE)

log = logging.getLogger(__name__)

INSECURE_SKIP = "an insecure browser gets no saved site data"

# Mouse gestures ``interact`` understands. hover and scroll_to are here rather
# than in their own tools because they take the same arguments as a click.
MOUSE_ACTIONS = ("click", "double_click", "right_click", "hover", "scroll_to")

# The ones that put the pointer somewhere. `scroll_to` moves the PAGE, not the
# pointer, so it is excluded and `glide` is meaningless for it. Every one of
# these now moves first and acts second, which is what makes Dr K's rule true:
# after a click the pointer is on what was clicked, the way a person's would be
# (saga §F2.3).
POINTER_ACTIONS = ("click", "double_click", "right_click", "hover")

# What `print` can make of a page. `pdf` is the browser's own print; `html` is
# the document as it stands now, after its scripts ran. MHTML — the page with
# its images in one file — is Chrome-only, and every tool here behaves the same
# on both browsers, so it waits for someone who needs it.
PRINT_FORMATS = ("pdf", "html")

# What can be done with a native dialog. "read" deliberately leaves it open.
DIALOG_ACTIONS = ("accept", "dismiss", "read", "send_text")

# Where a frame switch can go. "parent" matters for nested frames.
FRAME_ACTIONS = ("switch", "parent", "default")


class Actions:
    """The browser operations, bound to one Grid."""

    def __init__(self, grid: Grid, keep=None, pointers=None, read_file=None):
        self.grid = grid
        # How bytes this server made — a screenshot, a print — are kept with the
        # caller's session: `keep(name, data, folder)` writes them and returns
        # the file as every listing describes it, link included. A function
        # rather than the store and the token, because which session owns the
        # file is a question about the caller, and this layer should hand out a
        # link without holding the key that signs one (§F2.9). Absent, nothing
        # can be kept, and a save says so.
        self.keep = keep
        # Where the pointer is in each browser, keyed by the Grid's session id.
        # Injected so a deployment can share it between replicas, and defaulted
        # so this class is still usable on its own.
        self.pointers = pointers if pointers is not None else pointer.MemoryPointers()
        # Reads a file by its uri, for `upload_file(file=...)`. A function for
        # the same reason as `describe_file`: which flow session owns a file
        # is a question about the *caller*, and this layer deliberately cannot
        # see one. Absent, naming a file is refused with a reason.
        self.read_file = read_file
        # Where the caller's session has been, newest first, so one save reads
        # every site's localStorage and not only the page's. A function for
        # the reason `keep` is one: which session is calling is not this
        # layer's to see. Absent, a save reads only the page it is on.
        self.visited = None

    def _kept(self, name: str, data: bytes, folder=FILES_DIR) -> dict:
        """Keep bytes this server made with the caller's session.

        Straight to the session's files, never through the browser. They used to
        be handed back to the page as a download so they would land in the
        Grid's store beside the site's own downloads, and every refusal Chrome
        has for a download became a lost screenshot: plain-http pages, pages
        with no origin, a second download from `about:blank` (§F3.8).
        """
        if self.keep is None:
            raise ValueError(
                "keeping files is not enabled on this server: it was started "
                "with no FLOW_DATA_DIR, so there is nowhere to save one"
            )
        return self.keep(name, data, folder)

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
        insecure=None,
        site_data=None,
    ) -> dict:
        """Start a browser session with the settings it should run under.

        This is the only place a browser is created, and the only place these
        settings can be chosen — window size can be changed later with
        ``resize``, but the browser and the timeouts are set here and then
        simply hold. There is no switching a live session to another browser:
        that is a different browser, so it is a different session.

        A session's saved site data is restored here — cookies, every origin's
        storage, the saved sessionStorage — before the first page loads, except
        into an ``insecure`` browser, which gets none.
        """
        name = normalize_browser(browser)
        insecure = as_bool(insecure, False)
        driver = self.grid.open(name, insecure=insecure)
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

        report = None
        if site_data_snapshot.restorable(site_data or {}):
            if insecure:
                # It accepts any certificate, so anyone in the middle would get
                # every saved cookie. Nothing is set, and nothing is deleted.
                report = {
                    "restored": [], "skipped": [{"reason": INSECURE_SKIP}],
                    "uri": site_data_snapshot.LIST_URI,
                }
            else:
                with self.grid.bidi(session_id) as bidi:
                    report = site_data_transfer.restore(bidi, site_data, time.time())

        current_url, title = "about:blank", ""
        if url:
            driver.get(url)
            current_url, title = driver.current_url, driver.title
        elif report is not None and driver.current_url != "about:blank":
            # The restore sends the tab back from its stand-in page over BiDi,
            # and swallows a failure; the classic driver makes the reported
            # about:blank true (Copilot, #51).
            driver.get("about:blank")

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
        if insecure:
            applied["insecure"] = True
        result = {
            "session_id": session_id,
            "browser": name,
            "url": current_url,
            "title": title,
            "width": size["width"],
            "height": size["height"],
            "settings": applied,
        }
        if report and (report["restored"] or report["skipped"]):
            result["site_data"] = report
        return result

    def save_site_data(self, session_id: str, url=None) -> dict:
        """Capture the browser's cookies, the page's storage, and the
        localStorage of every other origin the session has been to.

        The capture rides back under a private key; the session manager stores
        it and no caller sees it.
        """

        def capture(at):
            origins = self.visited() if self.visited is not None else []
            with self.grid.bidi(session_id) as bidi:
                captured = site_data_transfer.capture(bidi, at.driver, origins)
            return {site_data_snapshot.CAPTURED: captured}

        return self._recipe().run(session_id, capture, url=url)

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
        """Go to a URL, unconditionally.

        The ``url`` is the body's, not the recipe's: the recipe skips a page the
        browser is already on, and going there again is what this is for. So it
        is checked here, as the recipe checks its own.
        """
        allowed_navigation(url)

        def go(at):
            at.driver.get(url)
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

        def gesture(at):
            """The whole act, so a retry re-does the move as well as the click."""
            driver, element = at.driver, at.element
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
                    why = probe.explain(driver, at.target)
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
            return {"action": resolved, **self._pointer_report(moved, glide)}

        # hover and scroll_to only need the element to exist. Requiring it to be
        # clickable would refuse exactly the off-screen element scroll_to is for.
        wait = PRESENCE if resolved in ("hover", "scroll_to") else CLICKABLE
        return self._recipe(wait, retry_stale=True).run(
            session_id, gesture, url=url, selector=selector, wait_timeout=wait_timeout
        )

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
        # Resolved before the browser is touched, like every other locator
        # mistake: an impossible drag should cost a 400, not a page load. The
        # source first, as it always was, so a call wrong twice hears about the
        # source; the recipe resolves it again on the way in.
        browser.locator(selector)
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

        wanted = as_bool(glide, True)
        clamp = {}

        def travel(at):
            driver = at.driver
            # The approach is always a jump: `glide` is about the travel with
            # the button down, which is the part a drag library is watching.
            moved = self._move_onto(session_id, driver, at.element, False)
            if moved is None:
                # A ValueError, so this is a 400. It describes a geometry the
                # caller can fix - resize the window, scroll, name a smaller
                # handle - and a 500 would tell an n8n node with Retry-On-Fail
                # to send the identical drag again (Copilot, #31).
                raise ValueError(
                    "the pointer could not be put on the element to drag it. It "
                    "may be larger than the window, or outside it in a way "
                    "scrolling does not fix. Try resize, or drag a smaller "
                    "handle inside it"
                )
            start = moved["at"]

            if to_target is not None:
                # Read now rather than when the step was written: the approach
                # may have scrolled, and every rect on the page moved with it
                # (§F2.3).
                aimed = pointer.aim(
                    driver, browser.wait_for_element(driver, to_target, at.timeout)
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
                # A clamp changes where the drop landed, so it cannot be
                # silent: a slider dragged to the window edge instead of to
                # +400 looks like the site ignoring the drag.
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
        # After the page state, where it has always been.
        result.update(clamp)
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

        # A selector matters only to a switch, and only a switch by selector
        # waits for its frame; the other actions never looked at one.
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
        """Resize the window of a session that is already open.

        Window size is one of the few things WebDriver lets you change after
        creation, which is why this is a separate action rather than an argument
        to ``open_session`` alone — a caller whose browser was opened for them
        can still set it.
        """

        def size(at):
            driver = at.driver
            current = driver.get_window_size()
            driver.set_window_size(
                as_int(width, current["width"]), as_int(height, current["height"])
            )
            now = driver.get_window_size()
            return {"width": now["width"], "height": now["height"]}

        return self._reattached().run(session_id, size)

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


        def answer(at):
            # Not the recipe's wait: that one is for elements, at their timeout.
            alert = browser.wait_for_alert(
                at.driver, as_int(wait_timeout, DIALOG_TIMEOUT)
            )
            # Read before answering: the dialog is gone once accepted or
            # dismissed.
            message = alert.text

            if resolved == "send_text":
                alert.send_keys(str(text))
                alert.accept()
            elif resolved == "accept":
                alert.accept()
            elif resolved == "dismiss":
                alert.dismiss()
            # "read" leaves it open, so a caller can decide what to do about it.
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
        path=None,
        url=None,
        wait_timeout=WAIT_TIMEOUT,
        file=None,
        session=None,
    ) -> dict:
        """Attach a file to a file input.

        ``session`` names which library ``file`` is read from, for a **flow
        run** only — ``flows/run.py`` injects it via
        ``capabilities.LIBRARY_ARG`` so a step reads the library the flow
        itself belongs to. Neither the MCP
        tool nor the HTTP dispatcher exposes it as a field a caller can set:
        `routes._add` excludes it from the accepted body, so a request naming
        another session here is dropped like any other unknown field rather
        than honoured (Copilot, #41). Passed as anything but that internal
        injection, it is ignored and the calling session answers instead.

        The file arrives one of four ways, and exactly one is required:

        - ``text`` — the file's content as plain text. This is the one to use
          for anything an agent produced itself: JSON, CSV, YAML, markdown.
          Base64-encoding text it just wrote is a wasted step it can get wrong.
        - ``content`` — base64, which binary needs and which is the only shape
          MCP tool arguments can carry.
        - ``file`` — any file this session has, by its ``session://files`` uri
          — a screenshot, a download, or a file in Files — from the library
          ``session`` names. This closes the loop the file store never had: a
          browser could download a file or take a screenshot and there was no
          way to give it back to a page. Now a flow can download an export and
          upload it somewhere else, without the bytes ever passing through a
          model's context (§F1.41, §F4.7).
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
                ("file", file),
                ("path", path),
            )
            if v
        ]
        if not sources:
            raise ValueError(
                "the file is required: pass text for a text file, content for "
                "base64 bytes, file for any file this session has, by its "
                "session://files uri, or path for a file on the server"
            )
        if len(sources) > 1:
            raise ValueError(
                f"pass only one of text, content, file or path; "
                f"got {', '.join(sources)}"
            )

        # Resolved before connecting: unusable input is a 400 about the input,
        # and there is no point opening anything to discover it.
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
                    "Pass text, content or path instead"
                )
            # `session` names WHICH library, and is not `session_id`, which
            # names the browser. Both appear on `/files/list` for the same
            # reason: a file store outlives the browser that filled it, so the
            # two are different questions. Neither an MCP caller nor an HTTP
            # caller can pass this one - its key answers the first and the
            # server the second; only a flow run's own injection does.
            name, raw = self.read_file(str(file), str(session) if session else None)
            # The file's own name is the default, because its extension is
            # what the page reads the type from and a caller uploading
            # `export.csv` back should not have to say so twice.
            name = safe_name(filename or name, mime_type)

        def at_with_local_files(session_id, url):
            # Part of reattaching, for this action alone: the file input is
            # found by a driver that already knows to ship a local path.
            driver = self._at(session_id, url)
            browser.accept_local_files(driver)
            return driver

        def attach(at):
            temp_dir = None
            try:
                if raw is not None:
                    try:
                        temp_dir = tempfile.mkdtemp(prefix="selenium-flow-")
                    except OSError as exc:
                        # The image runs read-only as an unprivileged user, so
                        # this is a deployment problem rather than a caller's:
                        # there has to be one writable directory to stage a
                        # file in before Selenium can ship it to the Grid node.
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

                # send_keys wants a string path, and Selenium's
                # LocalFileDetector reads it off the filesystem to ship the
                # bytes to the Grid node.
                at.element.send_keys(str(staged))
                return {"filename": staged.name, "bytes": staged.stat().st_size}
            finally:
                # The bytes live on the Grid node now; this copy has done its
                # job.
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

        ``read_back`` is normally true and is what lets a caller confirm the
        text landed. It is turned off for a value the caller must never be shown
        again — a bound secret — and turning it off means the read is **not
        performed**, not that its answer is dropped later. Redacting afterwards
        still puts the credential in a local variable, in a traceback if one
        fires between the two, and in whatever the action returned before
        anything wrapped it.
        """

        def typing(at):
            driver, element = at.driver, at.element
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
            return {"value": read}

        return self._recipe(CLICKABLE, retry_stale=True).run(
            session_id, typing, url=url, selector=selector, wait_timeout=wait_timeout
        )

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

        def press(at):
            driver, element = at.driver, at.element
            if element is None:
                # No selector: wherever focus is, which the page's body reaches.
                element = driver.find_element(By.TAG_NAME, "body")
            element.send_keys(resolved)
            # Only the keys that can submit a form. Tab, Escape and the arrows
            # never navigate, and making every one of them wait to find that out
            # would tax the common case for nothing. See `browser.settled`.
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
        """What is on the page, with a selector for each and whether it works.

        The answer an agent otherwise assembles out of DOM dumps: the first
        pilot report spent three hand-written scripts finding one sidebar link,
        then clicked it and failed anyway, because the element existed and its
        ancestor was `display: none`. Both halves are here - the selector and
        the verdict - so the failure does not have to teach it.
        """

        def map_(at):
            # Both coerced here rather than in the page: an HTTP caller can send
            # a number for `text`, which reaches JavaScript as one and dies on
            # `.toLowerCase()`, and a negative `limit` would bound nothing.
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
                # More than `count` is the map saying it was cut short, which an
                # unscoped call on a real application usually is.
                "total": total,
            }

        # Scoped to the selector's element when there is one, the whole page
        # when there is not.
        return self._recipe(PRESENCE if selector else None).run(
            session_id, map_, url=url, selector=selector, wait_timeout=wait_timeout
        )

    def execute_script(self, session_id: str, script: str, url=None) -> dict:
        """Run JavaScript in the page and return its result.

        The escape hatch for everything the other actions do not cover —
        scrolling, drag and drop, reading computed styles, poking the DOM.
        ``return`` a value to get it back.
        """
        return self._recipe().run(
            session_id,
            lambda at: {"result": at.driver.execute_script(script)},
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
        timeout, hold = window(wait_timeout, stable_for, WAIT_TIMEOUT)

        def poll(at):
            # `at.state` rather than a reading of its own: the page as it was
            # when the answer held is the page state this reports.
            Assertion(
                lambda: at.driver.execute_script(script),
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
                "html": at.element.get_attribute("innerHTML"),
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

        Plain W3C WebDriver throughout, so this works on any browser the Grid
        runs. Chrome has no W3C full-page command, hence the window resize.
        """

        shot = {}

        def capture(at):
            driver = at.driver
            if width or height:
                current = driver.get_window_size()
                driver.set_window_size(
                    as_int(width, current["width"]), as_int(height, current["height"])
                )

            if at.target is not None:
                # Its own wait rather than the recipe's: the window is sized
                # first, so the element is found in the layout it is shot in.
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
        # Saved by default. It used to be opt-in, which made the *agent* decide
        # whether a person would ever want to look at this one — and the answer
        # is usually no, so an operator watching the admin UI saw nothing and
        # had nothing to open. So the cheap thing is to keep them all (§F2.9).
        if as_bool(save, True):
            try:
                result["file"] = self._kept(
                    _generated_name(filename or "screenshot", ".png"),
                    shot["raw"],
                    SCREENSHOTS_DIR,
                )
            except Exception as exc:  # noqa: BLE001 - the picture outranks the file
                # It must never be able to take the capture away: a full disk or
                # a server keeping nothing costs the file, said out loud.
                result["file_error"] = _why_unsaved(exc)
        return result

    # ---- internals ---------------------------------------------------------

    def print_(
        self,
        session_id: str,
        url=None,
        format="pdf",
        filename=None,
        landscape=False,
        background=False,
    ) -> dict:
        """Print the page into the session's files, as a PDF or as HTML.

        A PDF is W3C ``print``, the rendering a person gets from Ctrl+P: text
        stays selectable and the whole document is included. HTML is the
        document as the browser holds it now, scripts and all already run.
        ``landscape`` and ``background`` shape a PDF and mean nothing to HTML.
        """
        # No default here: an omitted format arrives as "pdf" already, and a
        # `null` or `""` over HTTP is refused, as the tool's enum refuses it.
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
                # Off by default in every browser's print, which is why a PDF of
                # a page built on coloured panels comes out as bare text.
                options.background = as_bool(background, False)
                data = base64.b64decode(at.driver.print_page(options))
            else:
                data = at.driver.page_source.encode("utf-8")
            try:
                kept = self._kept(
                    _generated_name(filename or "page", f".{kind}"), data, FILES_DIR
                )
            except OSError as exc:
                # A full disk or a permission names a path under FLOW_DATA_DIR,
                # which is nobody's business but the log's. Still a server fault.
                log.error("a print could not be kept", exc_info=exc)
                raise RuntimeError(
                    f"the print could not be kept ({type(exc).__name__})"
                ) from None
            return {"file": kept, "format": kind, "bytes": len(data)}

        return self._recipe().run(session_id, render, url=url)

    def page(self, session_id: str) -> dict:
        """Where the browser is, without touching it.

        Not a capability and so not a tool: `session://current` already answers
        this for a caller. It exists because a secret's leash is checked against
        the page about to receive the keystroke, and that check has to read the
        page rather than trust what the caller said about it.
        """
        return browser.page_state(self.grid.reconnect(session_id))

    def _recipe(self, wait=None, retry_stale=False) -> Recipe:
        """The road for an action that takes ``url``: through `_at`.

        Built per call, so `_at` is read when the action runs - which is what a
        test that reattaches through it relies on.
        """
        return Recipe(self._at, wait, retry_stale)

    def _reattached(self, wait=None) -> Recipe:
        """The road for an action that takes no ``url``: a bare reattach.

        Not `_at`: these never refused an empty ``session_id``, and moving them
        onto the recipe is not where they start to.
        """
        return Recipe(lambda session_id, _url: self.grid.reconnect(session_id), wait)

    def _at(self, session_id: str, url=None):
        """Reconnect, and put the browser on ``url`` if it is not already there.

        The first step of every `Recipe` an action with a ``url`` runs.
        """
        if not session_id:
            raise ValueError("session_id is required")
        driver = self.grid.reconnect(session_id)
        if url:
            browser.ensure_url(driver, url)
        return driver
