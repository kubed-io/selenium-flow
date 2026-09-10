"""The loose-input coercions.

These exist because callers send JSON by hand, through form encoders, and
through LLM tool calls. Each of the cases below has actually reached a handler.
"""

import pytest
from selenium.webdriver.common.by import By

from kubed.selenium_flow.actions import _safe_name
from kubed.selenium_flow.browser import (
    as_bool,
    as_int,
    locator,
    normalize_url,
    png_size,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        # The one that matters: every non-empty string is truthy in Python, so a
        # plain bool() call turns "false" into True.
        ("false", False),
        ("False", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        ("", False),
        (None, False),
    ],
)
def test_as_bool(value, expected):
    assert as_bool(value) is expected


def test_as_bool_default_is_used_only_for_none():
    assert as_bool(None, True) is True
    assert as_bool("", True) is False


@pytest.mark.parametrize(
    "value,expected",
    [
        (30, 30),
        ("30", 30),
        (" 30 ", 30),
        # An omitted optional parameter arrives as an empty string, and int("")
        # raises rather than returning anything.
        ("", 9),
        (None, 9),
        ("garbage", 9),
    ],
)
def test_as_int(value, expected):
    assert as_int(value, 9) == expected


@pytest.mark.parametrize(
    "a,b",
    [
        ("https://x.com/a", "https://x.com/a/"),
        ("https://x.com/a", "https://x.com/a#top"),
        ("https://x.com/a/", "https://x.com/a#top"),
    ],
)
def test_normalize_url_treats_these_as_one_page(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_keeps_query_strings_distinct():
    assert normalize_url("https://x.com/a?p=1") != normalize_url("https://x.com/a")


def test_png_size_reads_the_ihdr_header():
    # A 1x1 PNG. The header is the only part png_size looks at.
    import base64

    one_px = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    assert png_size(one_px) == (1, 1)
    assert base64.b64decode(one_px)[:8] == b"\x89PNG\r\n\x1a\n"


# ---- the name an uploaded file is given -------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("report.csv", "report.csv"),
        # Reduced to a basename: the caller chose this string and it is written
        # to disk here, so it must not be able to name a directory.
        ("/etc/passwd", "passwd"),
        ("../../etc/passwd", "passwd"),
        # Backslashes count too. os.path.basename does not treat one as a
        # separator on Linux, so this used to survive intact as a "basename".
        (r"..\..\etc\passwd", "passwd"),
        (r"C:\Users\me\report.csv", "report.csv"),
        # A leading dot would make a hidden file, and an empty name is not one.
        (".hidden", "hidden"),
        ("", "upload"),
        (None, "upload"),
        ("/", "upload"),
    ],
)
def test_safe_name_narrows_whatever_the_caller_sent(given, expected):
    assert _safe_name(given) == expected


def test_safe_name_adds_the_extension_the_page_reads_the_type_from():
    """The browser reports File.type from the EXTENSION, not the bytes, so a
    name without one arrives as an empty type and no sniffing happens."""
    assert _safe_name("data", mime_type="application/json") == "data.json"
    assert _safe_name("data", mime_type="text/yaml") == "data.yaml"
    # An extension already present is never second-guessed.
    assert _safe_name("data.csv", mime_type="application/json") == "data.csv"
    # Nothing to go on falls back to whatever the caller's surface defaults to.
    assert _safe_name("data", default_extension=".txt") == "data.txt"


def test_locator_resolves_each_strategy_to_the_pair_selenium_wants():
    assert locator(xpath="//button") == (By.XPATH, "//button")
    assert locator(css="button.go") == (By.CSS_SELECTOR, "button.go")


def test_locator_refuses_both_rather_than_picking_one():
    """The expensive failure this helper exists to prevent.

    Resolving a caller that named both would act on whichever element one of
    them found, silently, and a typo in the ignored one would never surface.
    """
    with pytest.raises(ValueError, match="not both"):
        locator(xpath="//button", css="button.go")


def test_locator_refuses_neither_and_says_how_to_fix_it():
    with pytest.raises(ValueError, match="pass xpath or css") as caught:
        locator()
    # The message carries an example of each, because this is the one new way
    # to get a call wrong and the model reads the error, not the docs.
    assert "//button" in str(caught.value)
    assert "button[type=submit]" in str(caught.value)


@pytest.mark.parametrize("empty", ["", None])
def test_an_empty_selector_counts_as_absent(empty):
    """An omitted optional parameter often arrives as "" rather than absent,
    and treating that as "the caller named an element" would wait 30s for an
    element whose selector is the empty string."""
    with pytest.raises(ValueError, match="pass xpath or css"):
        locator(xpath=empty, css=empty)
    assert locator(xpath=empty, css="a") == (By.CSS_SELECTOR, "a")
