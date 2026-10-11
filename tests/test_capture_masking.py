"""Masking happens once, at capture, for the buffer and the tail (ruling 13)."""

import time
import tracemalloc

import pytest

from kubed.selenium_flow.capture import (
    HEADER_BYTES,
    HEADER_LIMIT,
    HEADER_NAME_CAP,
    HEADER_VALUE_CAP,
    MASK,
    TEXT_CAP,
)
from kubed.selenium_flow.capture.masking import Masker, strip_query
from kubed.selenium_flow.capture.merge import console_entry

from .capture_events import log

pytestmark = pytest.mark.unit


def kept(raw, masker=None):
    """The headers a row keeps, without the count of those it dropped."""
    return (masker or Masker()).headers(raw)[0]


@pytest.mark.parametrize("name", ["Authorization", "proxy-authorization", "COOKIE", "Set-Cookie"])
def test_credential_headers_are_masked_in_any_case(name):
    assert kept([{"name": name, "value": {"type": "string", "value": "s3cr3t"}}]) == [
        {"name": name, "value": MASK}]


def test_a_bound_value_is_masked_everywhere():
    m = Masker(["hunter2", "hunter%32"])
    assert m.text("pw=hunter2 and hunter%32") == f"pw={MASK} and {MASK}"
    assert m.url("https://a.test/login?pw=hunter2", keep_query=True) == f"https://a.test/login?pw={MASK}"
    assert kept([{"name": "x-echo", "value": {"type": "string", "value": "hunter2"}}], m)[0]["value"] == MASK


def test_a_secret_straddling_the_text_cap_is_still_masked():
    m = Masker(["S" * 10])
    entry = console_entry(log("x" * (TEXT_CAP - 5) + "S" * 10)[1], m)
    assert "S" not in entry["text"] and len(entry["text"]) <= TEXT_CAP


def test_headers_are_capped_at_32_and_each_value_at_4_kb():
    raw = [{"name": f"h{i}", "value": {"type": "string", "value": "v" * 300}} for i in range(40)]
    shown, dropped = Masker().headers(raw)
    assert len(shown) == HEADER_LIMIT and dropped == 40 - HEADER_LIMIT
    [big], _ = Masker().headers([{"name": "h", "value": {"type": "string", "value": "v" * 5000}}])
    assert len(big["value"]) == HEADER_VALUE_CAP


def test_a_names_cap_is_256():
    [header], _ = Masker().headers([{"name": "x" * 5000, "value": {"type": "string", "value": "v"}}])
    assert header["name"] == "x" * HEADER_NAME_CAP


def test_one_sides_headers_stop_at_16_kb_and_the_rest_are_counted():
    """A page decides how many large headers it sends, and its server how many
    it answers with: 32 of 4 kB a side would be 256 kB a row, 1 000 rows a
    workspace. Past the budget, later headers are dropped and counted."""
    raw = [{"name": f"h{i}", "value": {"type": "string", "value": "v" * 5000}} for i in range(40)]
    shown, dropped = Masker().headers(raw)
    assert len(shown) == 3 and dropped == 37
    assert sum(len(h["name"].encode()) + len(h["value"].encode()) for h in shown) <= HEADER_BYTES
    small = [{"name": "a", "value": {"type": "string", "value": "1"}}]
    assert Masker().headers(raw[:3] + small)[0][-1] == {"name": "a", "value": "1"}
    assert Masker().headers(raw[:4] + small) == (shown, 2)   # later ones go, even small


def test_the_budget_counts_bytes_not_characters():
    raw = [{"name": f"h{i}", "value": {"type": "string", "value": "é" * 4000}} for i in range(4)]
    shown, dropped = Masker().headers(raw)       # 8 000 bytes each
    assert (len(shown), dropped) == (2, 2)


def test_a_secret_straddling_the_header_value_cap_is_still_masked():
    m = Masker(["S" * 10])
    [header], _ = m.headers([{"name": "x-echo", "value": {"type": "string",
                                                         "value": "x" * (HEADER_VALUE_CAP - 5) + "S" * 10}}])
    assert "S" not in header["value"] and len(header["value"]) <= HEADER_VALUE_CAP


def test_a_secret_straddling_a_urls_cap_is_still_masked():
    m = Masker(["S" * 10])
    url = "https://a.test/?q=" + "x" * (TEXT_CAP - 23) + "S" * 10
    shown = m.url(url, keep_query=True)
    assert "S" not in shown and len(shown) <= TEXT_CAP


def test_a_secret_in_a_urls_fragment_is_masked():
    m = Masker(["hunter2"])
    assert m.url("https://a.test/p#token=hunter2", keep_query=True) == f"https://a.test/p#token={MASK}"
    assert m.url("https://a.test/p#token=hunter2", keep_query=False) == "https://a.test/p"


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
    assert kept([{"name": "x-bin", "value": {"type": "base64", "value": "AAEC"}}]) == [
        {"name": "x-bin", "value": "(binary)"}]


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
    assert kept(raw) == [
        {"name": name, "value": f"https://u:{MASK}@a.test/p?q=1"},
        {"name": "X-Other", "value": "https://u:pw@a.test/"}]


HUGE = 3_000_000


@pytest.mark.parametrize("read", [
    lambda m, v: m.text(v),
    lambda m, v: m.url("https://a.test/?q=" + v, keep_query=True),
    lambda m, v: kept([{"name": "x-" + v, "value": {"type": "string", "value": v}}], m)[0]["value"],
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
    assert kept([header], m)[0]["value"] == f"https://{MASK}@a.test/"


@pytest.mark.parametrize("value, expected", [
    ("//user:pw@a.test/p", f"//user:{MASK}@a.test/p"),
    (" https://user:pw@a.test/p", f" https://user:{MASK}@a.test/p"),
    ("\thttp://user:pw@a.test", f"\thttp://user:{MASK}@a.test"),
    ("https:\\\\user:pw@a.test/", f"https:\\\\user:{MASK}@a.test/"),
])
def test_a_password_in_a_relative_or_padded_url_is_masked(value, expected):
    header = {"name": "Location", "value": {"type": "string", "value": value}}
    assert kept([header])[0]["value"] == expected


# ---- a credential header's value is masked wherever it is echoed (spotless 2) --


def test_a_masked_credential_is_masked_wherever_it_is_echoed():
    """The headers view masks ``Authorization`` by name; a page that echoes it
    (httpbin's ``/anything``) must not hand the value back in its text."""
    m = Masker()
    kept([{"name": "Authorization", "value": {"type": "string", "value": "Bearer abcdefgh12345"}}], m)
    assert m.text('{"Authorization": "Bearer abcdefgh12345"}') == f'{{"Authorization": "{MASK}"}}'
    assert m.text("token=abcdefgh12345") == f"token={MASK}"     # the credential without its scheme
    assert m.url("https://a.test/?t=abcdefgh12345", keep_query=True) == f"https://a.test/?t={MASK}"


def test_each_cookie_of_a_cookie_header_is_masked_on_its_own():
    m = Masker()
    kept([{"name": "Cookie", "value": {"type": "string", "value": "sid=session-0123; theme=dark-mode-on"}}], m)
    kept([{"name": "set-cookie", "value": {"type": "string", "value": "tok=set-cookie-77; Path=/; HttpOnly"}}], m)
    echoed = '{"cookies": {"sid": "session-0123", "theme": "dark-mode-on", "tok": "set-cookie-77"}}'
    assert m.text(echoed) == f'{{"cookies": {{"sid": "{MASK}", "theme": "{MASK}", "tok": "{MASK}"}}}}'
    assert m.text("Path=/; HttpOnly") == "Path=/; HttpOnly"      # attributes are not credentials


def test_a_request_cookie_value_is_masked():
    m = Masker()
    m.cookies([{"name": "sid", "value": {"type": "string", "value": "COOKIE-VALUE"}}])
    assert m.text("saw COOKIE-VALUE") == f"saw {MASK}"


def test_a_short_credential_masks_nothing():
    """A value like ``1`` would mask every digit of a page."""
    m = Masker()
    kept([{"name": "Cookie", "value": {"type": "string", "value": "a=1; b=short"}}], m)
    m.cookies([{"name": "c", "value": {"type": "string", "value": "7"}}])
    assert m.text("1 short 7 a=1") == "1 short 7 a=1"


def test_credentials_are_capped_per_session_and_the_newest_kept():
    from kubed.selenium_flow.capture import CREDENTIALS

    m = Masker()
    values = [f"cookie-value-{i:04}" for i in range(CREDENTIALS + 10)]
    for value in values:
        m.cookies([{"name": "c", "value": {"type": "string", "value": value}}])
    assert m.text(values[0]) == values[0]                     # the oldest went
    assert m.text(values[-1]) == MASK
    assert m.text(values[10]) == MASK                         # the oldest kept
    m.cookies([{"name": "c", "value": {"type": "string", "value": values[10]}}])
    m.cookies([{"name": "c", "value": {"type": "string", "value": "cookie-value-new"}}])
    assert m.text(values[10]) == MASK                         # seen again: newest again
    assert m.text(values[11]) == values[11]


def test_a_bound_secret_is_never_crowded_out_by_credentials():
    from kubed.selenium_flow.capture import CREDENTIALS

    m = Masker(["hunter2"])
    for i in range(CREDENTIALS + 1):
        m.cookies([{"name": "c", "value": {"type": "string", "value": f"cookie-value-{i:04}"}}])
    assert m.text("hunter2") == MASK


def test_a_carried_credential_stays_a_credential():
    """The session a reopen replaces hands its credentials on, still under the
    cap: carried over and over, they would otherwise grow without bound."""
    from kubed.selenium_flow.capture import CREDENTIALS

    old = Masker(["hunter2"])
    old.cookies([{"name": "c", "value": {"type": "string", "value": "carried-cookie"}}])
    new = Masker()
    new.carry(old)
    assert new.text("carried-cookie hunter2") == f"{MASK} {MASK}"
    for i in range(CREDENTIALS):
        new.cookies([{"name": "c", "value": {"type": "string", "value": f"cookie-value-{i:04}"}}])
    assert new.text("carried-cookie hunter2") == f"carried-cookie {MASK}"
