"""Masking happens once, at capture, for the buffer and the tail (ruling 13)."""

import time
import tracemalloc

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



def test_overlapping_secrets_leave_no_remainder():
    """Masked one after the other (or by a regex alternation), "abcd" would
    take the "cd" that "cdef" needs and leave its "ef" behind."""
    assert Masker(["abcd", "cdef"]).text("x abcdef y") == f"x {MASK} y"
    assert Masker(["aa"]).text("aaa") == MASK
    assert Masker(["ab", "cd"]).text("abcd ab") == f"{MASK} {MASK}"


@pytest.mark.parametrize("url, keep_query, expected", [
    ("https://user:pw@a.test/p?q=1", True, f"https://user:{MASK}@a.test/p?q=1"),
    ("https://user:pw@a.test/p?q=1", False, f"https://user:{MASK}@a.test/p"),
    ("https://user@a.test/", True, "https://user@a.test/"),
    ("https://:pw@a.test:8443/", True, f"https://:{MASK}@a.test:8443/"),
    ("https://a.test/at@sign?x=a:b@c", True, "https://a.test/at@sign?x=a:b@c"),
])
def test_a_password_in_a_url_is_masked(url, keep_query, expected):
    assert Masker().url(url, keep_query=keep_query) == expected


@pytest.mark.parametrize("name", ["Location", "referer", "CONTENT-LOCATION"])
def test_a_password_in_a_url_bearing_header_is_masked(name):
    raw = [{"name": name, "value": {"type": "string", "value": "https://u:pw@a.test/p?q=1"}},
           {"name": "X-Other", "value": {"type": "string", "value": "https://u:pw@a.test/"}}]
    assert Masker().headers(raw) == [
        {"name": name, "value": f"https://u:{MASK}@a.test/p?q=1"},
        {"name": "X-Other", "value": "https://u:pw@a.test/"}]


HUGE = 3_000_000


@pytest.mark.parametrize("read", [
    lambda m, v: m.text(v),
    lambda m, v: m.url("https://a.test/?q=" + v, keep_query=True),
    lambda m, v: m.headers([{"name": "x-" + v, "value": {"type": "string", "value": v}}])[0]["value"],
    lambda m, v: console_entry(log(v)[1], m)["text"],
], ids=["text", "url", "header", "console"])
def test_masking_a_huge_value_is_bounded_by_what_a_row_keeps(read):
    """A one-character secret and a page that repeats it for megabytes: the
    work and the memory are bounded by the cap, not by the page."""
    for value in ("a" * HUGE, "ab" * (HUGE // 2)):
        masker = Masker(["a"])
        started = time.perf_counter()
        tracemalloc.start()
        try:
            shown = read(masker, value)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert time.perf_counter() - started < 1.0
        assert peak < 2 * len(value) + 1_000_000      # a copy or two, never a tuple per "a"
        assert len(shown) <= TEXT_CAP and "a" not in shown.removeprefix("https://")


def test_a_secret_cut_off_where_masking_stops_is_still_masked():
    """Masks shorter than their secrets pull later text inside the cap: a
    secret that starts before masking stops and ends after it is masked too."""
    secret = "0123456789"
    value = secret * 409 + "x" * 10 + secret + "y" * 10_000
    shown = Masker([secret]).text(value)
    assert "012" not in shown and shown.startswith(f"{MASK}xxx")   # 409 touching: one mask


def test_text_is_cut_to_its_cap():
    assert Masker(["s"]).text("x" * 50, cap=10) == "x" * 10
    assert Masker().text("x" * (TEXT_CAP + 5)) == "x" * TEXT_CAP


def test_a_password_longer_than_the_cap_is_still_masked():
    """The cut falls inside the userinfo, where what is left would otherwise
    read as a host and a port; the host is past what a row keeps."""
    shown = Masker().url("https://u:" + "p" * (2 * TEXT_CAP) + "@a.test/", keep_query=True)
    assert shown == f"https://u:{MASK}"


def test_a_bound_secret_holding_the_userinfo_is_masked_whole():
    """Hiding the password first would leave "user:" for the secret to miss."""
    m = Masker(["user:pw"])
    assert m.url("https://user:pw@a.test/", keep_query=True) == f"https://{MASK}@a.test/"
    header = {"name": "Location", "value": {"type": "string", "value": "https://user:pw@a.test/"}}
    assert m.headers([header])[0]["value"] == f"https://{MASK}@a.test/"


@pytest.mark.parametrize("value, expected", [
    ("//user:pw@a.test/p", f"//user:{MASK}@a.test/p"),
    (" https://user:pw@a.test/p", f" https://user:{MASK}@a.test/p"),
    ("\thttp://user:pw@a.test", f"\thttp://user:{MASK}@a.test"),
    ("https:\\\\user:pw@a.test/", f"https:\\\\user:{MASK}@a.test/"),
])
def test_a_password_in_a_relative_or_padded_url_is_masked(value, expected):
    header = {"name": "Location", "value": {"type": "string", "value": value}}
    assert Masker().headers([header])[0]["value"] == expected
