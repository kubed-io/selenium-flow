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
import mimetypes
import os
import shutil
import tempfile

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import browser
from .browser import Grid, as_bool, as_int

# Named keys a caller can press. Selenium's Keys members are unicode private-use
# characters, so a caller cannot reasonably type them into JSON by hand.
KEYS = {
    name.lower(): getattr(Keys, name)
    for name in dir(Keys)
    if name.isupper() and not name.startswith("_")
}


# Mouse gestures ``interact`` understands. hover and scroll_to are here rather
# than in their own tools because they take the same arguments as a click.
MOUSE_ACTIONS = ("click", "double_click", "right_click", "hover", "scroll_to")

# What can be done with a native dialog. "read" deliberately leaves it open.
DIALOG_ACTIONS = ("accept", "dismiss", "read", "send_text")

# Where a frame switch can go. "parent" matters for nested frames.
FRAME_ACTIONS = ("switch", "parent", "default")


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
    name = os.path.basename(str(filename or "")).strip().lstrip(".")
    if not name:
        name = "upload"
    if not os.path.splitext(name)[1]:
        name += _extension_for(mime_type) or default_extension
    return name


class Actions:
    """The browser operations, bound to one Grid."""

    def __init__(self, grid: Grid):
        self.grid = grid

    # ---- session lifecycle -------------------------------------------------

    def open_session(
        self,
        url=None,
        width=None,
        height=None,
        page_load_timeout=None,
        script_timeout=None,
    ) -> dict:
        """Start a browser session with the settings it should run under.

        This is the only place a browser is created, and the only place these
        settings can be chosen — window size can be changed later with
        ``resize``, but the timeouts are set here and then simply hold.
        """
        driver = self.grid.open()
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

        current_url, title = "about:blank", ""
        if url:
            driver.get(url)
            current_url, title = driver.current_url, driver.title

        size = driver.get_window_size()
        # Reported back so a caller can see what the cascade actually resolved
        # to, rather than assuming its argument won. Both surfaces get this from
        # here, so they cannot describe the same session differently.
        applied = {"width": size["width"], "height": size["height"]}
        if page_load_timeout:
            applied["page_load_timeout"] = as_int(page_load_timeout, 0)
        if script_timeout:
            applied["script_timeout"] = as_int(script_timeout, 0)
        return {
            "session_id": session_id,
            "url": current_url,
            "title": title,
            "width": size["width"],
            "height": size["height"],
            "settings": applied,
        }

    def close_session(self, session_id: str) -> dict:
        """Quit the session and free its Grid slot."""
        self.grid.quit(session_id)
        return {"success": True, "session_id": session_id}

    # ---- navigation --------------------------------------------------------

    def navigate(self, session_id: str, url: str) -> dict:
        """Go to a URL, unconditionally."""
        driver = self.grid.reconnect(session_id)
        driver.get(url)
        return {**browser.page_state(driver)}

    # ---- interaction -------------------------------------------------------

    def interact(
        self, session_id: str, action: str, xpath: str, url=None, wait_timeout=30
    ) -> dict:
        """Perform a mouse action on the element at ``xpath``.

        One action rather than five tools: they take identical arguments and
        differ only in which gesture is sent, so splitting them would be five
        near-identical schemas for a model to choose between.
        """
        resolved = str(action).strip().lower()
        if resolved not in MOUSE_ACTIONS:
            raise ValueError(
                f"unknown action {action!r}; known actions: "
                f"{', '.join(sorted(MOUSE_ACTIONS))}"
            )
        driver = self._at(session_id, url)
        timeout = as_int(wait_timeout, 30)

        # hover and scroll_to only need the element to exist. Requiring it to be
        # clickable would refuse exactly the off-screen element scroll_to is for.
        if resolved in ("hover", "scroll_to"):
            element = browser.wait_for_element(driver, xpath, timeout)
        else:
            element = browser.wait_for_clickable(driver, xpath, timeout)

        if resolved == "click":
            element.click()
        else:
            chain = ActionChains(driver)
            if resolved == "double_click":
                chain.double_click(element)
            elif resolved == "right_click":
                chain.context_click(element)
            elif resolved == "hover":
                chain.move_to_element(element)
            elif resolved == "scroll_to":
                chain.scroll_to_element(element)
            chain.perform()

        return {
            "action": resolved,
            **browser.page_state(driver),
        }

    def frame(
        self, session_id: str, action="switch", xpath=None, index=None, wait_timeout=30
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
        if resolved == "switch" and not xpath and index is None:
            raise ValueError("switch needs either xpath or index to say which frame")

        driver = self.grid.reconnect(session_id)
        if resolved == "default":
            driver.switch_to.default_content()
        elif resolved == "parent":
            driver.switch_to.parent_frame()
        elif xpath:
            driver.switch_to.frame(
                browser.wait_for_element(driver, xpath, as_int(wait_timeout, 30))
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
        self, session_id: str, action="accept", text=None, wait_timeout=10
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
        xpath: str,
        text=None,
        content=None,
        filename=None,
        mime_type=None,
        path=None,
        url=None,
        wait_timeout=30,
    ) -> dict:
        """Attach a file to the file input at ``xpath``.

        The file arrives one of three ways, and exactly one is required:

        - ``text`` — the file's content as plain text. This is the one to use
          for anything an agent produced itself: JSON, CSV, YAML, markdown.
          Base64-encoding text it just wrote is a wasted step it can get wrong.
        - ``content`` — base64, which binary needs and which is the only shape
          MCP tool arguments can carry.
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
            n for n, v in (("text", text), ("content", content), ("path", path)) if v
        ]
        if not sources:
            raise ValueError(
                "the file is required: pass text for a text file, content for "
                "base64 bytes, or path for a file on the server"
            )
        if len(sources) > 1:
            raise ValueError(
                f"pass only one of text, content or path; got {', '.join(sources)}"
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

        driver = self._at(session_id, url)
        browser.accept_local_files(driver)
        element = browser.wait_for_element(driver, xpath, as_int(wait_timeout, 30))

        temp_dir = None
        try:
            if raw is not None:
                temp_dir = tempfile.mkdtemp(prefix="selenium-flow-")
                local = os.path.join(temp_dir, name)
                with open(local, "wb") as handle:
                    handle.write(raw)
            else:
                local = str(path)
                if not os.path.isfile(local):
                    raise ValueError(f"no file at {local}")

            element.send_keys(local)
            size = os.path.getsize(local)
            name = os.path.basename(local)
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
        xpath: str,
        text: str,
        url=None,
        clear=True,
        submit=False,
        wait_timeout=30,
    ) -> dict:
        """Type ``text`` into the field at ``xpath``."""
        driver = self._at(session_id, url)
        element = browser.wait_for_clickable(driver, xpath, as_int(wait_timeout, 30))
        if as_bool(clear, True):
            element.clear()
        element.send_keys(str(text))
        # Read the value back before any submit: submitting navigates, which
        # makes the element reference stale.
        value = element.get_attribute("value")
        if as_bool(submit, False):
            element.send_keys(Keys.RETURN)
        return {"value": value, **browser.page_state(driver)}

    def press_key(
        self, session_id: str, key: str, xpath=None, url=None, wait_timeout=30
    ) -> dict:
        """Press a named key, at an element or wherever focus currently is.

        This is how you reach Tab, Escape, Enter and the arrows. It is *not* a
        reliable way to scroll: page_down only moves the page when focus happens
        to be on the scrollable container. Use ``execute_script`` to scroll.
        """
        resolved = KEYS.get(str(key).strip().lower())
        if resolved is None:
            raise ValueError(
                f"unknown key {key!r}; known keys: {', '.join(sorted(KEYS))}"
            )
        driver = self._at(session_id, url)
        if xpath:
            target = browser.wait_for_clickable(driver, xpath, as_int(wait_timeout, 30))
        else:
            target = driver.find_element(By.TAG_NAME, "body")
        target.send_keys(resolved)
        return {"key": key, **browser.page_state(driver)}

    def execute_script(self, session_id: str, script: str, url=None) -> dict:
        """Run JavaScript in the page and return its result.

        The escape hatch for everything the other actions do not cover —
        scrolling, drag and drop, reading computed styles, poking the DOM.
        ``return`` a value to get it back.
        """
        driver = self._at(session_id, url)
        result = driver.execute_script(script)
        return {"result": result, **browser.page_state(driver)}

    # ---- reading -----------------------------------------------------------

    def extract(self, session_id: str, xpath: str, url=None, wait_timeout=30) -> dict:
        """Read the text and HTML of the element at ``xpath``."""
        driver = self._at(session_id, url)
        element = browser.wait_for_element(driver, xpath, as_int(wait_timeout, 30))
        return {
            "html": element.get_attribute("innerHTML"),
            "text": element.text,
            **browser.page_state(driver),
        }

    def screenshot(
        self,
        session_id: str,
        url=None,
        xpath=None,
        full_page=False,
        width=None,
        height=None,
        wait_timeout=30,
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

        if xpath:
            element = browser.wait_for_element(driver, xpath, as_int(wait_timeout, 30))
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
        return {
            "image": image,
            "width": img_w,
            "height": img_h,
            "bytes": len(base64.b64decode(image)),
            **browser.page_state(driver),
        }

    # ---- internals ---------------------------------------------------------

    def _at(self, session_id: str, url=None):
        """Reconnect, and put the browser on ``url`` if it is not already there."""
        if not session_id:
            raise ValueError("session_id is required")
        driver = self.grid.reconnect(session_id)
        if url:
            browser.ensure_url(driver, url)
        return driver
