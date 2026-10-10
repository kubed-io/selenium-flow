"""Masking happens once, at capture, for the buffer and the tail (ruling 13)."""

import pytest

from kubed.selenium_flow.capture import HEADER_LIMIT, HEADER_VALUE_CAP, MASK, TEXT_CAP
from kubed.selenium_flow.capture.masking import Masker, strip_query
from kubed.selenium_flow.capture.merge import console_entry

from .capture_events import log

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", ["Authorization", "proxy-authorization", "COOKIE", "Set-Cookie"])
def test_credential_headers_are_masked_in_any_case(name):
    assert Masker().headers([{"name": name, "value": {"type": "string", "value": "s3cr3t"}}]) == [
        {"name": name, "value": MASK}]


def test_a_bound_value_is_masked_everywhere():
    m = Masker(["hunter2", "hunter%32"])
    assert m.text("pw=hunter2 and hunter%32") == f"pw={MASK} and {MASK}"
    assert m.url("https://a.test/login?pw=hunter2", keep_query=True) == f"https://a.test/login?pw={MASK}"
    assert m.headers([{"name": "x-echo", "value": {"type": "string", "value": "hunter2"}}])[0]["value"] == MASK


def test_a_secret_straddling_the_text_cap_is_still_masked():
    m = Masker(["S" * 10])
    entry = console_entry(log("x" * (TEXT_CAP - 5) + "S" * 10)[1], m)
    assert "S" not in entry["text"] and len(entry["text"]) <= TEXT_CAP


def test_headers_are_capped_at_32_of_4_kb():
    raw = [{"name": f"h{i}", "value": {"type": "string", "value": "v" * 5000}} for i in range(40)]
    kept = Masker().headers(raw)
    assert len(kept) == HEADER_LIMIT and all(len(h["value"]) == HEADER_VALUE_CAP for h in kept)


def test_strip_query_keeps_origin_and_path():
    assert strip_query("https://a.test/p/q?code=abc#frag") == "https://a.test/p/q"


def test_the_longest_secret_is_masked_first():
    """A secret that contains another is masked whole, not as a fragment."""
    assert Masker(["abc", "abcdef"]).text("x abcdef y") == f"x {MASK} y"


def test_secrets_added_later_and_empty_ones_ignored():
    m = Masker([""])
    assert m.text("keep me") == "keep me"
    m.add(["me", ""])
    assert m.text("keep me") == f"keep {MASK}"


def test_url_drops_the_query_unless_kept_and_masks_first():
    m = Masker(["t0k"])
    assert m.url("https://a.test/t0k/p?q=1", keep_query=False) == f"https://a.test/{MASK}/p"


def test_a_binary_header_is_never_decoded():
    kept = Masker().headers([{"name": "x-bin", "value": {"type": "base64", "value": "AAEC"}}])
    assert kept == [{"name": "x-bin", "value": "(binary)"}]


def test_strip_query_survives_a_url_it_cannot_parse():
    assert strip_query("http://[::1/p?code=abc") == "http://[::1/p"
    assert strip_query("not a url?x=1") == "not a url"
