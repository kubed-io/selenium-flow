"""A URL whose host does not resolve is the caller's mistake, said as one.

A live smoke test opened `https://nonexistent.invalid/` and got a 500 with the
browser's own words — `unknown error: net::ERR_NAME_NOT_RESOLVED` from Chrome,
`Reached error page: about:neterror?e=dnsNotFound…` from Firefox — and the pod
logged a full Selenium traceback for it. The host is the one the caller named,
and the same request will fail the same way on a retry, so it is a 400 naming
that host. Only DNS-not-found is recognised: a refused connection, a timeout or
a bad certificate may be the network's or the site's, and stays what it was.
"""

from __future__ import annotations

import logging

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from selenium.common.exceptions import WebDriverException
from starlette.testclient import TestClient

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core import browser
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import calling_as
from .fakes import ScriptedDriver

pytestmark = pytest.mark.unit

HOST = "nonexistent.invalid"
URL = f"https://{HOST}/"
# Hedged, because the browser does not say which lookup failed: a host that
# resolves and redirects to one that does not fails in exactly the same words.
SAID = f"the host {HOST}, or a host it redirected to, did not resolve: check the URL"

# What each browser raises from `driver.get` for a host with no address, as
# the smoke test saw them: the driver's `msg`, Chrome's session furniture and
# Firefox's error-page URL included.
CHROME_DNS = (
    "unknown error: net::ERR_NAME_NOT_RESOLVED\n"
    "  (Session info: chrome=141.0.7390.54)"
)
FIREFOX_DNS = (
    "Reached error page: about:neterror?e=dnsNotFound&u=https%3A//nonexistent.invalid/"
    "&c=UTF-8&d=We%20can%E2%80%99t%20connect%20to%20the%20server%20at%20nonexistent.invalid."
)
DNS = {"chrome": CHROME_DNS, "firefox": FIREFOX_DNS}

# Other ways a page fails to load. Not the caller's by any rule this server can
# check, so they stay exactly as they were.
OTHER_NET_ERRORS = {
    "chrome refused": "unknown error: net::ERR_CONNECTION_REFUSED\n  (Session info: chrome=141)",
    "chrome timed out": "unknown error: net::ERR_CONNECTION_TIMED_OUT",
    "chrome tls": "unknown error: net::ERR_CERT_AUTHORITY_INVALID",
    "firefox refused": "Reached error page: about:neterror?e=connectionFailure&u=https%3A//a.test/",
    "firefox timed out": "Reached error page: about:neterror?e=netTimeout&u=https%3A//a.test/",
    "firefox tls": "Reached error page: about:neterror?e=nssFailure2&u=https%3A//a.test/",
}


class Unreachable(ScriptedDriver):
    """A driver whose every page but `about:blank` fails as ``failure``."""

    def __init__(self, failure: str, **settings):
        super().__init__(**settings)
        self.__dict__["failure"] = failure

    def get(self, url):
        self.log.append(("get", url))
        if url != "about:blank":
            raise WebDriverException(self.failure)
        self.__dict__["_url"] = url


# ---- the classification --------------------------------------------------------


@pytest.mark.parametrize("name", sorted(DNS))
def test_each_browsers_dns_failure_is_recognised(name):
    assert faults.unresolved(WebDriverException(DNS[name]))


@pytest.mark.parametrize("name", sorted(OTHER_NET_ERRORS))
def test_no_other_net_error_is_read_as_dns(name):
    assert not faults.unresolved(WebDriverException(OTHER_NET_ERRORS[name]))


def test_only_a_driver_failure_is_read_as_dns():
    """Our own words quoting the marker — a flow's assertion message, say —
    are not the browser reporting a failed lookup."""
    assert not faults.unresolved(ValueError("net::ERR_NAME_NOT_RESOLVED"))


@pytest.mark.parametrize("name", sorted(DNS))
def test_a_page_whose_host_does_not_resolve_is_a_400_naming_the_host(name):
    with pytest.raises(faults.Unresolved) as caught:
        browser.go(Unreachable(DNS[name]), URL)
    assert faults.message(caught.value) == SAID
    assert errors.status_for(caught.value) == 400


@pytest.mark.parametrize("name", sorted(DNS))
def test_the_refusal_says_the_host_and_nothing_else_of_the_url_or_the_error(name):
    """The browser's text and the URL can both carry paths, query strings and
    credentials; the host and a fixed reason are all that is said."""
    url = "https://user:hunter2@nonexistent.invalid:8443/private/path?token=s3cret#frag"
    with pytest.raises(faults.Unresolved) as caught:
        browser.go(Unreachable(DNS[name]), url)
    said = faults.message(caught.value)
    assert said == SAID
    for leaked in ("hunter2", "user", "private", "s3cret", "frag", "8443", "neterror", "net::"):
        assert leaked not in said
    # Nor anywhere a traceback could reach: the browser's exception is dropped.
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@pytest.mark.parametrize("name", sorted(DNS))
def test_a_redirect_to_a_host_that_does_not_resolve_does_not_blame_the_callers_alone(name):
    """`fine.test` resolves and redirects to a host that does not; Chrome says
    ERR_NAME_NOT_RESOLVED all the same. Still a 400 - the retry fails the same
    way - but the caller is not told their own host failed when it did not."""
    with pytest.raises(faults.Unresolved) as caught:
        browser.go(Unreachable(DNS[name]), "https://fine.test/login")
    said = faults.message(caught.value)
    assert errors.status_for(caught.value) == 400
    assert said != "the host fine.test did not resolve: check the URL"
    assert "fine.test, or a host it redirected to," in said


# URLs `urlsplit` reads oddly, or refuses: whatever the shape, the answer is the
# 400 and its fixed words, never a parse error and never the browser's text.
AWKWARD_URLS = {
    "http://[::1": "the URL's host",
    "https://bücher.invalid/": "the host bücher.invalid",
    "http://[::1]:8080/": "the host ::1",
    "https://nonexistent.invalid:8443/": f"the host {HOST}",
    "https://user:hunter2@nonexistent.invalid/": f"the host {HOST}",
    "nonexistent.invalid/path": "the URL's host",
    "https:///nothing": "the URL's host",
}


@pytest.mark.parametrize("url", sorted(AWKWARD_URLS))
@pytest.mark.parametrize("name", sorted(DNS))
def test_any_url_shape_is_the_400_and_nothing_else(name, url):
    with pytest.raises(faults.Unresolved) as caught:
        browser.go(Unreachable(DNS[name]), url)
    said = faults.message(caught.value)
    assert said.startswith(AWKWARD_URLS[url] + ", or a host it redirected to,")
    assert errors.status_for(caught.value) == 400
    for leaked in ("hunter2", "8443", "8080", "path", "nothing", "neterror", "net::"):
        assert leaked not in said
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert caught.value.__context__ is None or caught.value.__suppress_context__


def test_the_browser_layer_does_not_reach_for_the_status_table():
    """`errors` imports `core.cancel`; `core.browser` reading it closed a loop
    through the kernel. The match is in `faults`, which imports no driver."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(browser.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
    assert not any(name.split(".")[-1] == "errors" for name in imported), imported


@pytest.mark.parametrize("name", sorted(OTHER_NET_ERRORS))
def test_any_other_net_error_is_raised_as_it_was(name):
    with pytest.raises(WebDriverException) as caught:
        browser.go(Unreachable(OTHER_NET_ERRORS[name]), "https://a.test/")
    assert not isinstance(caught.value, faults.Unresolved)
    assert errors.status_for(caught.value) == 500


# ---- every action that goes to a caller's URL ----------------------------------


@pytest.mark.parametrize("name", sorted(DNS))
def test_navigate_says_the_host_did_not_resolve(actions, monkeypatch, name):
    driver = Unreachable(DNS[name])
    monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: driver)
    with pytest.raises(faults.Unresolved, match=SAID):
        actions.navigate("abc", URL)


@pytest.mark.parametrize("name", sorted(DNS))
def test_an_action_given_a_url_says_it_too(actions, monkeypatch, name):
    """The recipe's own navigation, which every action with a `url` takes."""
    driver = Unreachable(DNS[name])
    monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: driver)
    with pytest.raises(faults.Unresolved, match=SAID):
        actions.extract("abc", selector={"css": "#x"}, url=URL)


@pytest.mark.parametrize("name", sorted(DNS))
def test_an_open_whose_first_page_does_not_resolve_leaves_no_browser(
    actions, monkeypatch, name
):
    """Spotless #74: a browser nobody was handed is quit. The 400 is that same
    failure, so it takes that same road."""
    driver = Unreachable(DNS[name])
    quit = []
    monkeypatch.setattr(actions.grid, "open", lambda *a, **k: driver)
    monkeypatch.setattr(actions.grid, "quit", quit.append)
    ended = []
    with pytest.raises(faults.Unresolved, match=SAID):
        actions.open_session(url=URL, on_quit=ended.append)
    assert quit == [driver.session_id] and ended == [driver.session_id]


# ---- each surface: the status, the message, and one log line -------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, data={"dir": str(tmp_path)})
    )
    quit = []
    monkeypatch.setattr(server.actions.grid, "quit", quit.append)
    server.quit = quit
    return server


def opens_to(server, monkeypatch, failure: str):
    driver = Unreachable(failure)
    monkeypatch.setattr(server.actions.grid, "open", lambda *a, **k: driver)
    return driver


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[tuple[int, str, bool]] = []

    def emit(self, record):
        self.records.append(
            (record.levelno, self.format(record), record.exc_info is not None)
        )


@pytest.fixture
def mcp_log():
    """FastMCP's logger does not propagate, so it is listened to directly."""
    from kubed.selenium_flow.mcp.failures import FASTMCP_LOGGER

    handler = Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(FASTMCP_LOGGER)
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)


@pytest.fixture
def routes_log():
    handler = Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("kubed.selenium_flow.routes")
    level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)
    logger.setLevel(level)


async def open_over_mcp(server, monkeypatch) -> str:
    calling_as(monkeypatch, "dns")
    async with Client(server.mcp) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("open_session", {"url": URL})
    return str(caught.value)


def open_over_http(server):
    client = TestClient(server.mcp.http_app(), headers={"X-Workspace": "dns"})
    return client.post("/browser", json={"url": URL})


@pytest.mark.parametrize("name", sorted(DNS))
async def test_over_mcp_the_caller_reads_the_host_and_the_log_one_line(
    server, monkeypatch, mcp_log, name
):
    driver = opens_to(server, monkeypatch, DNS[name])
    said = await open_over_mcp(server, monkeypatch)
    assert said == f"Error calling tool 'open_session': {SAID}"
    assert server.quit == [driver.session_id]
    calls = [r for r in mcp_log if "Error calling tool" in r[1]]
    assert len(calls) == 1, calls
    level, text, has_traceback = calls[0]
    assert level == logging.WARNING and not has_traceback
    assert "Traceback" not in text and "\n" not in text
    assert text.endswith(SAID)


@pytest.mark.parametrize("name", sorted(DNS))
def test_over_http_the_caller_gets_a_400_and_the_log_one_line(
    server, monkeypatch, routes_log, name
):
    driver = opens_to(server, monkeypatch, DNS[name])
    response = open_over_http(server)
    assert response.status_code == 400
    assert response.json() == {"error": SAID}
    assert server.quit == [driver.session_id]
    assert len(routes_log) == 1, routes_log
    level, text, has_traceback = routes_log[0]
    assert level < logging.WARNING and not has_traceback
    assert "Traceback" not in text and "\n" not in text
    assert text.endswith(SAID)


async def test_over_mcp_a_net_error_that_is_ours_keeps_its_traceback(
    server, monkeypatch, mcp_log
):
    opens_to(server, monkeypatch, OTHER_NET_ERRORS["chrome refused"])
    said = await open_over_mcp(server, monkeypatch)
    assert said == "Error calling tool 'open_session': unknown error: net::ERR_CONNECTION_REFUSED"
    calls = [r for r in mcp_log if "Error calling tool" in r[1]]
    assert len(calls) == 1, calls
    level, text, _ = calls[0]
    assert level == logging.ERROR and "Traceback" in text


def test_over_http_a_net_error_that_is_ours_is_a_500_with_its_traceback(
    server, monkeypatch, routes_log
):
    opens_to(server, monkeypatch, OTHER_NET_ERRORS["chrome refused"])
    response = open_over_http(server)
    assert response.status_code == 500
    assert response.json() == {"error": "unknown error: net::ERR_CONNECTION_REFUSED"}
    assert len(routes_log) == 1, routes_log
    level, text, _ = routes_log[0]
    assert level == logging.ERROR and "Traceback" in text
