"""What an open passed, and whether it differs from the live browser's
settings (`settings.changed`, WS-85): only what the call passed is compared,
as `sourced` resolves it."""

from __future__ import annotations

import pytest

from kubed.selenium_flow.workspace.settings import changed, sourced

pytestmark = pytest.mark.unit


def test_changed_compares_only_what_the_call_passed():
    assert changed(
        sourced({"width": 1400}, client={"browser": "firefox"}),
        {"browser": "chrome", "width": 1400},
    ) == []
    assert changed(sourced({"browser": "firefox"}), {"browser": "chrome"}) == [
        "browser"
    ]
    assert changed(sourced({"version": "latest"}), {"browser": "chrome"}) == []
    assert changed(sourced({"capabilities": {}}), {"browser": "chrome"}) == []
    assert changed(sourced({"record": False}), {"browser": "chrome"}) == []


def test_changed_names_the_keys_in_the_order_of_the_settings():
    passed = sourced({"width": 1024, "headless": "true", "browser": "Firefox"})
    assert changed(passed, {"browser": "chrome", "width": 1400}) == [
        "browser", "width", "headless",
    ]


def test_passed_is_what_the_explicit_layer_set():
    resolved = sourced(
        {"width": "1400", "height": "", "browser": None},
        defaults={"browser": "chrome"},
        client={"height": 900},
    )
    assert resolved.passed == frozenset({"width"})
