"""What a failure says, and what it must never say.

`errors` decides what a failure means for both surfaces — the status an HTTP
caller gets and the text an agent reads — so a credential that reaches this
module reaches a log, a tool result and whatever scrapes either.
"""

from __future__ import annotations

import pytest
import requests

from kubed.selenium_flow import errors

pytestmark = pytest.mark.unit

GRID = "http://user:hunter2@grid.internal:4444"


def test_a_refused_grid_request_does_not_quote_the_url():
    """`raise_for_status` formats the whole URL into its message."""
    response = requests.Response()
    response.status_code = 404
    response.url = f"{GRID}/session/abc"
    exc = requests.HTTPError("404 Client Error: Not Found for url: " + response.url)
    said = errors.message(exc)
    assert "hunter2" not in said
    assert "404" in said, "the useful half has to survive"


def test_a_connection_failure_does_not_quote_the_url_either():
    """The one that was leaking. `status_for` calls a ConnectionError 503, and
    nothing truncated its text — so the credential travelled into the log line
    the shared HTTP answer writes for every unavailable Grid (Copilot, #36).
    """
    exc = requests.ConnectionError(
        f"HTTPConnectionPool(host='grid.internal', port=4444): "
        f"Max retries exceeded with url: {GRID}/status"
    )
    said = errors.message(exc)
    assert "hunter2" not in said, "a credential reached the log"
    assert "Max retries" in said, "the diagnosis has to survive the scrub"


def test_the_scrub_keeps_a_message_that_carries_no_url():
    """Nothing to strip, nothing changed."""
    assert errors.message(ValueError("no such flow")) == "no such flow"


def test_an_error_with_no_text_still_says_something():
    """An empty message is worse than a class name."""
    assert errors.message(ValueError("")) == "ValueError"


@pytest.mark.parametrize(
    "xpath",
    [
        "//input[@name='q']",
        "//a[@href]",
        "//div[@id='x']//span[@class='y']",
        "//button[@type='submit' and @disabled]",
    ],
)
def test_an_xpath_in_a_message_survives_the_credential_scrub(xpath):
    """`//` then `@` is an XPath attribute test as well as a URL's userinfo, and
    the scrub read it as the second: every timeout quoting one reached the
    caller with its `tag[@` cut out, naming a selector nobody wrote."""
    said = errors.message(ValueError(f"no element matched {xpath!r}"))
    assert xpath in said


def test_a_url_and_an_xpath_in_one_message_lose_only_the_credential():
    said = errors.message(
        ValueError(f"{GRID}/session failed looking for //input[@name='q']")
    )
    assert "hunter2" not in said and "user:" not in said
    assert "http://grid.internal:4444/session" in said
    assert "//input[@name='q']" in said
