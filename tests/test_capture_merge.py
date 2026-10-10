"""Rows from events: the resource type, what is kept, what is hinted, the key."""

import pytest

from kubed.selenium_flow.capture.masking import Masker
from kubed.selenium_flow.capture.merge import (
    console_entry,
    counts_in_hint,
    entry_key,
    is_static_success,
    resource_type,
)

from .capture_events import log

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("request_, mime, navigation, kind", [
    ({"destination": "style"}, "", False, "stylesheet"),
    ({"destination": "image"}, "", False, "image"),
    ({"destination": "font"}, "", False, "font"),
    ({"destination": "script"}, "", False, "script"),
    ({"destination": "video"}, "", False, "media"),
    ({"destination": "", "initiatorType": "fetch"}, "", False, "fetch"),
    ({"destination": "", "initiatorType": "xmlhttprequest"}, "", False, "xhr"),
    ({"destination": "document"}, "", False, "document"),
    ({}, "", True, "document"),
    ({}, "text/css", False, "stylesheet"),
    ({}, "image/png", False, "image"),
    ({}, "application/json", False, "other"),
    ({"destination": "iframe"}, "", False, "document"),
    ({"destination": "serviceworker"}, "", False, "script"),
    ({"destination": "track"}, "", False, "media"),
    ({}, "font/woff2", False, "font"),
    ({}, "application/font-woff", False, "font"),
    ({}, "text/javascript; charset=utf-8", False, "script"),
    ({}, "audio/ogg", False, "media"),
    ({}, "text/html", False, "document"),
    ({"destination": None, "initiatorType": None}, None, False, "other"),
])
def test_resource_type(request_, mime, navigation, kind):
    assert resource_type(request_, mime, navigation) == kind


@pytest.mark.parametrize("row, static, hinted", [
    ({"type": "image", "failed": False, "status": 200}, True, False),
    ({"type": "font", "failed": False, "status": 304}, True, False),
    ({"type": "image", "failed": False, "status": 404}, False, False),   # kept, not hinted (R10)
    ({"type": "image", "failed": True, "status": None}, False, False),
    ({"type": "fetch", "failed": False, "status": 500}, False, True),
    ({"type": "xhr", "failed": True, "status": None}, False, True),
    ({"type": "document", "failed": False, "status": 404}, False, True),
    ({"type": "fetch", "failed": False, "status": 200}, False, False),
])
def test_what_is_kept_and_what_is_hinted(row, static, hinted):
    assert is_static_success(row) is static and counts_in_hint(row) is hinted


def test_the_entry_key_is_the_six_fields_of_ruling_9():
    _, p = log("boom", level="error", ts=1700.0, realm="r9", frame=("https://a.test/a.js", 4, 2))
    assert entry_key(p) == ("r9", 1700.0, "console", "error", "boom", "https://a.test/a.js:4:2")


def test_an_entry_with_no_stack_has_an_empty_frame():
    _, p = log("boom", frame=None)
    assert entry_key(p)[-1] == "" and console_entry(p, Masker())["source"] is None


def test_a_console_entry_keeps_no_args_and_counts_lines_from_one():
    _, p = log("hello", ts=1500.0, frame=("https://a.test/a.js?v=1", 9, 0))
    entry = console_entry(p, Masker())
    assert entry == {
        "level": "info",
        "type": "console",
        "text": "hello",
        "source": {"url": "https://a.test/a.js?v=1", "line": 10},
        "context": "top-1",
        "at": 1.5,
    }
