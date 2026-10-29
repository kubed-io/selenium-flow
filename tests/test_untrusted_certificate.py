"""A certificate the browser does not trust is the caller's to answer, said as one.

Chrome and Firefox both raise `InsecureCertificateException` for it, and its
text quotes the URL, credentials included. It is a 400 that says to open with
`insecure=true`, and nothing of the browser's words. A plain `WebDriverException`
quoting `net::ERR_CERT_*` is still a 500 (`test_unresolved_host.py` pins it).
"""

from __future__ import annotations

import pytest
from selenium.common.exceptions import InsecureCertificateException
from starlette.testclient import TestClient

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core import browser
from kubed.selenium_flow.server import SeleniumMCP

from .fakes import ScriptedDriver

pytestmark = pytest.mark.unit

UNTRUSTED = (
    "the site's certificate is not trusted: for a site with a self-signed "
    "certificate, open_session(insecure=true); an insecure browser gets no saved site data"
)
URL = "https://u:hunter2@a.test/x"


class Untrusted(ScriptedDriver):
    """A driver whose every page but `about:blank` has an untrusted certificate."""

    def get(self, url):
        self.log.append(("get", url))
        if url != "about:blank":
            raise InsecureCertificateException(f"insecure certificate {URL}")
        self.__dict__["_url"] = url


def test_go_says_the_sentence_and_nothing_of_the_browsers_text():
    with pytest.raises(faults.Untrusted) as caught:
        browser.go(Untrusted(), URL)
    assert faults.message(caught.value) == UNTRUSTED and errors.status_for(caught.value) == 400
    assert "hunter2" not in str(caught.value) and caught.value.__suppress_context__


def test_a_bare_insecure_certificate_exception_is_a_400():
    assert errors.status_for(InsecureCertificateException("x")) == 400


def test_navigate_says_it(actions, monkeypatch):
    driver = Untrusted()
    monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: driver)
    with pytest.raises(faults.Untrusted):
        actions.navigate("abc", URL)


def test_an_open_whose_first_page_is_untrusted_leaves_no_browser(actions, monkeypatch):
    driver = Untrusted()
    quit = []
    monkeypatch.setattr(actions.grid, "open", lambda *a, **k: driver)
    monkeypatch.setattr(actions.grid, "quit", quit.append)
    with pytest.raises(faults.Untrusted):
        actions.open_session(url=URL)
    assert quit == [driver.session_id]


def test_over_http_open_session_is_a_400_with_the_sentence(tmp_path, monkeypatch):
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, data={"dir": str(tmp_path)})
    )
    driver = Untrusted()
    monkeypatch.setattr(server.actions.grid, "open", lambda *a, **k: driver)
    monkeypatch.setattr(server.actions.grid, "quit", lambda session_id: None)
    client = TestClient(server.mcp.http_app(), headers={"Sf-Workspace": "cert"})
    response = client.post("/browser", json={"url": URL})
    assert response.status_code == 400 and response.json() == {"error": UNTRUSTED}
