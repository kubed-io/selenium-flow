"""What the server can actually do, as plain functions.

This is the single source of truth for behaviour. ``tools.py`` exposes these to
MCP clients and ``routes.py`` exposes the same functions over HTTP, so the two
surfaces cannot drift: adding a capability here adds it to both.

Every function takes a ``session_id`` and returns a JSON-safe dict. Nothing is
cached between calls — the browser state lives on the Grid, not in this process.
"""

from __future__ import annotations

import base64

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


class Actions:
    """The browser operations, bound to one Grid."""

    def __init__(self, grid: Grid):
        self.grid = grid

    # ---- session lifecycle -------------------------------------------------

    def open_session(self, url=None, width=None, height=None) -> dict:
        """Start a browser session, optionally at a URL and window size."""
        driver = self.grid.open()
        session_id = driver.session_id

        if width or height:
            current = driver.get_window_size()
            driver.set_window_size(
                as_int(width, current["width"]), as_int(height, current["height"])
            )

        current_url, title = "about:blank", ""
        if url:
            driver.get(url)
            current_url, title = driver.current_url, driver.title

        size = driver.get_window_size()
        return {
            "session_id": session_id,
            "url": current_url,
            "title": title,
            "width": size["width"],
            "height": size["height"],
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
        return {"url": driver.current_url, "title": driver.title}

    # ---- interaction -------------------------------------------------------

    def click(self, session_id: str, xpath: str, url=None, wait_timeout=30) -> dict:
        """Click the element at ``xpath``."""
        driver = self._at(session_id, url)
        browser.wait_for_clickable(driver, xpath, as_int(wait_timeout, 30)).click()
        return {"url": driver.current_url, "title": driver.title}

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
        return {"value": value, "url": driver.current_url, "title": driver.title}

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
        return {"key": key, "url": driver.current_url, "title": driver.title}

    def execute_script(self, session_id: str, script: str, url=None) -> dict:
        """Run JavaScript in the page and return its result.

        The escape hatch for everything the other actions do not cover —
        scrolling, drag and drop, reading computed styles, poking the DOM.
        ``return`` a value to get it back.
        """
        driver = self._at(session_id, url)
        result = driver.execute_script(script)
        return {"result": result, "url": driver.current_url, "title": driver.title}

    # ---- reading -----------------------------------------------------------

    def extract(self, session_id: str, xpath: str, url=None, wait_timeout=30) -> dict:
        """Read the text and HTML of the element at ``xpath``."""
        driver = self._at(session_id, url)
        element = browser.wait_for_element(driver, xpath, as_int(wait_timeout, 30))
        return {
            "html": element.get_attribute("innerHTML"),
            "text": element.text,
            "url": driver.current_url,
            "title": driver.title,
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
            "url": driver.current_url,
            "title": driver.title,
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
