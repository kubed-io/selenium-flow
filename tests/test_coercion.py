"""The loose-input coercions.

These exist because callers send JSON by hand, through form encoders, and
through LLM tool calls. Each of the cases below has actually reached a handler.
"""

import pytest

from kubed.selenium_flow.browser import as_bool, as_int, normalize_url, png_size

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
