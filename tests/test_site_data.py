"""Site data's groundwork: origins as `location.origin` spells them, the
record field, the page's storage read, and the BiDi socket. A save, a
restore and the views each have their own module."""

import json

import pytest

from kubed.selenium_flow import urls
from kubed.selenium_flow.session.store import SessionRecord
from kubed.selenium_flow.site_data import snapshot as sd
from kubed.selenium_flow.site_data import transfer

pytestmark = pytest.mark.unit

NOW = 1_790_800_000.0


def cookie(name, domain, value="v", http_only=False, expiry=None, secure=True):
    return {
        "name": name, "value": value, "value_type": "string", "domain": domain,
        "path": "/", "http_only": http_only, "secure": secure,
        "same_site": "lax", "expiry": expiry,
    }


def test_origin_of_matches_location_origin():
    assert urls.origin_of("https://App.Example.com:443/x?y#z") == "https://app.example.com"
    assert urls.origin_of("http://localhost:3000/a") == "http://localhost:3000"
    assert urls.origin_of("http://x.test:80/") == "http://x.test"
    assert urls.origin_of("about:blank") == ""
    assert urls.origin_of("") == ""


def test_an_ipv6_origin_keeps_its_brackets():
    assert urls.origin_of("http://[::1]:3000/") == "http://[::1]:3000"
    assert urls.origin_of("https://[2001:db8::1]/x") == "https://[2001:db8::1]"


def test_expired_cookies_are_dropped_and_session_cookies_kept():
    kept = sd.live_cookies(
        [cookie("old", "a.test", expiry=int(NOW) - 1), cookie("new", "a.test", expiry=int(NOW) + 60),
         cookie("session", "a.test", expiry=None)],
        NOW,
    )
    assert [c["name"] for c in kept] == ["new", "session"]


def test_a_record_written_before_site_data_reads_as_empty():
    assert SessionRecord.from_json('{"session_id": "s", "url": "u"}').site_data == {}
    assert SessionRecord.from_json('{"session_id": "s", "site_data": [1]}').site_data == {}


# ---- BiDi ------------------------------------------------------------------


def test_grid_bidi_derives_the_socket_from_the_grid_url():
    from kubed.selenium_flow.core.browser import Grid
    with Grid("https://grid.example:4444/").bidi("abc") as driver:
        assert driver.caps["webSocketUrl"] == (
            "wss://grid.example:4444/session/abc/se/bidi")
        assert driver.session_id == "abc"


def test_every_browser_is_opened_with_bidi():
    from kubed.selenium_flow.core.browser import Grid
    for name in ("chrome", "firefox"):
        assert Grid()._options(name).to_capabilities().get("webSocketUrl") is True


# ---- what the live Grid taught (2026-09-30) ---------------------------------


def test_read_storage_reaches_each_store_inside_its_try():
    assert "dump(() => localStorage)" in transfer.READ_STORAGE
    assert "dump(() => sessionStorage)" in transfer.READ_STORAGE


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="needs node")
def test_read_storage_on_a_page_with_no_storage_returns_the_empty_shape(tmp_path):
    """about:blank and data: throw SecurityError on merely naming localStorage."""
    import subprocess
    path = tmp_path / "read.js"
    path.write_text(
        "const location = {origin: 'null'};\n"
        "Object.defineProperty(globalThis, 'localStorage', {get() { throw new Error('SecurityError') }});\n"
        "Object.defineProperty(globalThis, 'sessionStorage', {get() { throw new Error('SecurityError') }});\n"
        "console.log(JSON.stringify((function () {" + transfer.READ_STORAGE + "})()));\n",
        encoding="utf-8",
    )
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"origin": "", "local": {}, "session": {}}


def test_the_bidi_socket_gives_up_in_seconds_not_thirty():
    from kubed.selenium_flow.core.browser import BIDI_TIMEOUT, Grid
    with Grid("http://grid.example:4444").bidi("abc") as driver:
        assert driver.command_executor.client_config.websocket_timeout == BIDI_TIMEOUT
    assert BIDI_TIMEOUT <= 5


def test_the_wire_loggers_never_log_at_debug(monkeypatch):
    """LOG_LEVEL=DEBUG would otherwise write every BiDi frame, cookie values
    included. Through the real entry point."""
    import logging
    from types import SimpleNamespace

    from kubed.selenium_flow import main as entry
    from kubed.selenium_flow.config import Settings

    settings = Settings(grid={"url": "http://grid.invalid:4444"}, log_level="DEBUG")
    monkeypatch.setattr(entry.config, "load",
                        lambda argv: SimpleNamespace(settings=settings, sources={}))

    class Server:
        auth_token = skill = flows = secrets = collector = None
        sessions = SimpleNamespace(kind="memory")

        def __init__(self, *a, **kw):
            pass

        def run(self, **kw):
            pass

    monkeypatch.setattr(entry, "SeleniumMCP", Server)
    root = logging.getLogger()
    monkeypatch.setattr(logging, "basicConfig", lambda level: root.setLevel(level))
    names = ("selenium.webdriver.remote.websocket_connection",
             "selenium.webdriver.remote.remote_connection")
    before = root.level, [logging.getLogger(n).level for n in names]
    try:
        entry.main([])
        assert root.isEnabledFor(logging.DEBUG)
        for name in names:
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
    finally:
        root.setLevel(before[0])
        for name, level in zip(names, before[1], strict=True):
            logging.getLogger(name).setLevel(level)


# ---- Grid.bidi --------------------------------------------------------------


class FakeSocket:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("fails", [False, True])
def test_grid_bidi_closes_a_socket_it_opened(fails):
    from contextlib import nullcontext

    from kubed.selenium_flow.core.browser import Grid

    socket = FakeSocket()
    raised = pytest.raises(RuntimeError) if fails else nullcontext()
    with raised, Grid("http://grid.example:4444").bidi("abc") as driver:
        driver._websocket_connection = socket
        if fails:
            raise RuntimeError("mid-call")
    assert socket.closed == 1
