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
    kept([{"name": "Cookie", "value": {"type": "string", "value": "sid=session-0123-abcdef; theme=dark-mode-on-please"}}], m)
    kept([{"name": "set-cookie", "value": {"type": "string", "value": "tok=set-cookie-77-xyz12; Path=/; HttpOnly"}}], m)
    echoed = ('{"cookies": {"sid": "session-0123-abcdef", "theme": "dark-mode-on-please", '
              '"tok": "set-cookie-77-xyz12"}}')
    assert m.text(echoed) == f'{{"cookies": {{"sid": "{MASK}", "theme": "{MASK}", "tok": "{MASK}"}}}}'
    assert m.text("Path=/; HttpOnly") == "Path=/; HttpOnly"      # attributes are not credentials


def cookie(m, value):
    m.cookies([{"name": "c", "value": {"type": "string", "value": value}}])


def test_a_request_cookie_value_is_masked():
    m = Masker()
    cookie(m, "COOKIE-VALUE-0123")
    assert m.text("saw COOKIE-VALUE-0123") == f"saw {MASK}"


def test_a_short_credential_masks_nothing():
    """A value like ``1`` would mask every digit of a page."""
    m = Masker()
    kept([{"name": "Cookie", "value": {"type": "string", "value": "a=1; b=short"}}], m)
    cookie(m, "7")
    assert m.text("1 short 7 a=1") == "1 short 7 a=1"


def test_the_forms_are_capped_per_session():
    """R1-1, R1-2: what a session masks is a forms budget, not a count of
    values: each form costs every text a scan, whatever value it came from.
    Which values a full budget holds is round 4's (below)."""
    from kubed.selenium_flow.capture import COOKIE_FORMS

    m = Masker()
    values = [f"cookie-value-{i:04}" for i in range(COOKIE_FORMS + 10)]  # one form each
    for value in values:
        cookie(m, value)
    assert len(m.secrets) <= COOKIE_FORMS
    assert sum(m.text(v) == MASK for v in values) == COOKIE_FORMS


def test_a_bound_secret_is_never_crowded_out_by_credentials():
    from kubed.selenium_flow.capture import COOKIE_FORMS

    m = Masker(["hunter2"])
    for i in range(COOKIE_FORMS + 1):
        cookie(m, f"cookie-value-{i:04}")
    assert m.text("hunter2") == MASK


def test_a_carried_credential_stays_a_credential():
    """The session a reopen replaces hands its credentials on, still under the
    cap: carried over and over, they would otherwise grow without bound."""
    from kubed.selenium_flow.capture import COOKIE_FORMS

    old = Masker(["hunter2"])
    cookie(old, "carried-cookie-0000")                       # ranks below what follows
    new = Masker()
    new.carry(old)
    assert new.text("carried-cookie-0000 hunter2") == f"{MASK} {MASK}"
    for i in range(COOKIE_FORMS):
        cookie(new, f"cookie-value-{i:04}")
    assert new.text("carried-cookie-0000 hunter2") == f"carried-cookie-0000 {MASK}"


# ---- fix round 1 ------------------------------------------------------------------


def test_a_text_masked_while_credentials_change_masks_with_one_state(monkeypatch):
    """R1-3: `network_body` masks on a worker thread while the loop learns and
    evicts. A text must read the forms and their lengths as one state: read
    apart, an eviction between them shifts the forms under an index taken
    before it, and the slice skips the bound token."""
    from kubed.selenium_flow.capture import masking

    token = "Tk-bound-secret-token-0123456789"                   # 32: fits the text
    m = Masker([token])
    m.cookies([{"name": "c", "value": {"type": "string", "value": "L" * 30 + "-cookie-0123456789-abcdefghij"}}])
    real, fired = masking.bisect.bisect_left, []

    def meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)                         # skips the long cookie
        if not fired:
            fired.append(1)                                   # the loop, between two reads:
            for i in range(600):                              # churn evicts the long cookie
                m.cookies([{"name": "c", "value": {"type": "string", "value": f"ck-{i:04}-abcdefgh"}}])
        return found

    monkeypatch.setattr(masking.bisect, "bisect_left", meanwhile)
    assert m.text(f"auth {token}") == f"auth {MASK}"


def bearer(m, value):
    kept([{"name": "Authorization", "value": {"type": "string", "value": value}}], m)


def test_cookie_churn_never_evicts_an_authorization_value():
    """R1-2: network_body masks at fetch time, so a token evicted by a page's
    cookies would come back in clear in an earlier body that echoed it."""
    from kubed.selenium_flow.capture import COOKIE_FORMS

    m = Masker()
    bearer(m, "Bearer eyJhbGciOi.token-0123456789")
    for i in range(COOKIE_FORMS * 4):
        cookie(m, f"churning-cookie-{i:05}")
    assert m.text("eyJhbGciOi.token-0123456789") == MASK


def test_a_huge_credential_is_not_learned_and_is_counted():
    """R1-2: each learned value is kept with four or five spellings; a page
    choosing huge distinct values would grow memory without bound."""
    from kubed.selenium_flow.capture import CREDENTIAL_MAX

    m = Masker()
    huge = "Zz9-" * (CREDENTIAL_MAX // 4 + 1)
    cookie(m, huge)
    bearer(m, "Bearer " + huge)
    assert m.secrets == () and m.skipped == 2        # the cookie, and the token with its header


def test_learned_forms_are_bounded_in_characters_too():
    from kubed.selenium_flow.capture import CREDENTIAL_MAX, FORM_CHARS

    m = Masker()
    for i in range(50):
        bearer(m, f"Bearer {i:04}-" + "a1" * (CREDENTIAL_MAX // 2 - 8))
        cookie(m, f"{i:04}-" + "b2" * (CREDENTIAL_MAX // 2 - 8))
    # Two tiers, each within its budget but for the newest value it keeps.
    assert sum(map(len, m.secrets)) <= 2 * (FORM_CHARS + 8 * CREDENTIAL_MAX)


def test_a_credential_holding_a_nul_is_not_learned():
    """A row's header values are scanned joined on NUL (R1-1)."""
    m = Masker()
    cookie(m, "abc\x00def-0123456789-xyz")
    assert m.secrets == ()


PROBE_COOKIES = "consent=accepted; ab=undefined; ts=1728600000; sid=s%3Aa8F2kQ9xZ1mP0rT7vW3yB5"


def test_the_probes_console_stays_readable_and_its_session_token_is_masked():
    """R1-5: with an 8-character minimum these cookies masked "undefined",
    a timestamp and "accepted" across the whole page's console."""
    m = Masker()
    kept([{"name": "Cookie", "value": {"type": "string", "value": PROBE_COOKIES}}], m)
    said = ("TypeError: Cannot read properties of undefined (reading 'x') "
            "at 1728600000; cookies accepted NaN null true false")
    assert m.text(said) == said
    assert m.text("sid s%3Aa8F2kQ9xZ1mP0rT7vW3yB5") == f"sid {MASK}"


@pytest.mark.parametrize("value", ["acceptedcookiesyes", "1728600000123456", "undefinedundefined"])
def test_a_cookie_of_only_letters_or_only_digits_is_not_learned(value):
    m = Masker()
    cookie(m, value)
    assert m.secrets == ()


def test_a_cookie_under_sixteen_characters_is_not_learned():
    m = Masker()
    cookie(m, "abc-123-xyz-99")                        # 14, mixed
    assert m.secrets == ()


@pytest.mark.parametrize("value", ["Negotiate", "Bearer", "Basic ", "Bearer undefined"])
def test_an_authorization_without_a_token_learns_nothing(value):
    m = Masker()
    bearer(m, value)
    assert m.secrets == ()


# ---- what masking costs (R1-1) ----------------------------------------------------


def worst_masker():
    """A session at its budgets, every value spelt as many ways as an echo can
    (eleven) and every spelling ASCII: a form with a character the text cannot
    hold is refused by `str.find` at once, so it would understate the cost."""
    from kubed.selenium_flow.capture import AUTH_FORMS, COOKIE_FORMS

    m = Masker()
    for i in range(AUTH_FORMS):
        bearer(m, f"Bearer t/{i:04}+&'<x>\"" + "q" * 40)
    for i in range(COOKIE_FORMS):
        cookie(m, f"c/{i:04}+&'<x>\"" + "z" * 40)
    return m


def rows(n=32, size=600):
    return [{"name": f"x-header-{i}", "value": {"type": "string", "value": f"{i}-" + "v" * size}}
            for i in range(n)]


def test_one_side_of_a_request_is_scanned_once_not_once_per_header(monkeypatch):
    """Each scan costs a pass per form: a row's names and values are joined and
    scanned together (on NUL, which neither holds), not one header at a time."""
    from kubed.selenium_flow.capture import masking

    m = worst_masker()
    scans, real = [], masking._scan
    monkeypatch.setattr(masking, "_scan", lambda *a: scans.append(1) or real(*a))
    m.headers(rows(size=100))
    assert len(scans) == 1


def test_joined_scanning_masks_exactly_as_one_at_a_time():
    secret = "hunter2-secret-0123"
    m = Masker([secret, "hunter2"])
    raw = [
        {"name": "a", "value": {"type": "string", "value": f"x {secret} y"}},
        {"name": f"n-{secret}", "value": {"type": "string", "value": "hunter2"}},
        {"name": "referer", "value": {"type": "string", "value": "https://u:pw@h/p"}},
        {"name": "Authorization", "value": {"type": "string", "value": "Bearer zz"}},
        {"name": "big", "value": {"type": "string", "value": "v" * (HEADER_VALUE_CAP - 3) + secret}},
    ]
    shown, dropped = m.headers(raw)
    assert shown[0]["value"] == f"x {MASK} y"
    assert shown[1] == {"name": f"n-{MASK}", "value": MASK}
    assert shown[2]["value"] == f"https://u:{MASK}@h/p"
    assert shown[3]["value"] == MASK
    assert "hun" not in shown[4]["value"] and len(shown[4]["value"]) <= HEADER_VALUE_CAP
    assert dropped == 0


def test_a_bound_secret_holding_a_nul_is_still_masked_in_a_header():
    m = Masker(["a\x00b-secret"])
    [header], _ = m.headers([{"name": "x", "value": {"type": "string", "value": "1 a\x00b-secret 2"}}])
    assert header["value"] == f"1 {MASK} 2"


def test_the_worst_case_request_event_stays_cheap():
    """The pin: a request event at the forms budget, its 32 headers of 600
    characters and a 2 kB URL, runs on the server's main loop. Measured at
    about 0.6 ms on the machine that set the budget (16 + 48 forms); the
    bound is generous for a slow runner, and still fails at the 256 forms the
    budget replaced (3.3 ms there)."""
    import time as clock

    from kubed.selenium_flow.capture.store import Capture

    from .capture_events import nav_started, req

    capture = Capture()
    capture.attached("ws", "s1", "chrome", "top-1", again=False)
    capture.ingest("ws", "s1", *nav_started("https://a.test/"))
    held = capture._sessions["s1"]
    held.masker = worst_masker()
    headers = [(f"x-header-{i}", f"{i}-" + "v" * 600) for i in range(32)]
    url = "https://a.test/p?" + "q" * 2000
    times = []
    for i in range(30):
        event = req(f"r{i}", url, headers=headers)
        started = clock.perf_counter()
        capture.ingest("ws", "s1", *event)
        times.append(clock.perf_counter() - started)
    assert sorted(times)[len(times) // 2] < 0.002


# ---- fix round 2 ------------------------------------------------------------------


def standard_base64(length):
    """A token as an OAuth server issues one: standard base64, `+`, `/` and
    `=` included, so each of its spellings differs (four forms)."""
    import base64

    raw = bytes((i * 151 + 7) % 256 for i in range(length))
    return base64.b64encode(raw).decode()[:length - 1] + "="


@pytest.mark.parametrize("length", [3600, 3868, 4000, 4096])
def test_a_large_bearer_token_echoed_alone_is_masked(length):
    """R2-1: learnt as two entries, the token and then its whole header, a
    token past about 3.5 kB was evicted by its own header's forms, and an
    OAuth token response echoing it came back in clear."""
    token = standard_base64(length)
    m = Masker()
    bearer(m, f"Bearer {token}")
    assert m.text(f'{{"access_token":"{token}"}}', cap=10_000) == f'{{"access_token":"{MASK}"}}'
    escaped = token.replace("/", "\\/")                       # PHP's json_encode
    assert m.text(f'"{escaped}"', cap=10_000) == f'"{MASK}"'


def test_a_whole_authorization_value_holding_a_nul_is_not_learned():
    """R2-2: only the token passed the NUL rule; the whole value did not, and
    one form with a NUL turns the joined header scan off."""
    m = Masker()
    bearer(m, "Be\x00arer validtoken-0123456789")
    assert m.text("validtoken-0123456789") == MASK
    assert not any("\x00" in form for form in m.secrets) and m._state[2]


def test_a_cookie_is_masked_in_its_decoded_spelling_too():
    """R2-3: a server echoes the cookie it parsed (`s:a8F2…`), not the
    percent-encoded one the browser sent (`s%3Aa8F2…`)."""
    m = Masker()
    kept([{"name": "Cookie", "value": {"type": "string", "value": "sid=s%3Aa8F2kQ9xZ1mP0rT7vW3yB5"}}], m)
    cookie(m, "v%2Bk3Yq8LmN5pQ7rS9tU1")                        # a request cookie's value
    assert m.text('{"sid": "s:a8F2kQ9xZ1mP0rT7vW3yB5"}') == f'{{"sid": "{MASK}"}}'
    assert m.text("s%3Aa8F2kQ9xZ1mP0rT7vW3yB5") == MASK
    assert m.text("tok=v+k3Yq8LmN5pQ7rS9tU1") == f"tok={MASK}"


# ---- fix round 3: what learning costs once it has learned ---------------------------


def b64(n, seed):
    import base64

    return base64.b64encode(bytes((i * 151 + seed) % 256 for i in range(n))).decode()[:n]


def send(m, pairs, auth=None):
    """What one request event asks of a masker: its BiDi cookies, then its
    headers, for ``(name, value)`` cookie pairs. Returns how long it took."""
    import time as clock

    raw = [{"name": n, "value": {"type": "string", "value": v}} for n, v in pairs]
    sent = "; ".join(f"{n}={v}" for n, v in pairs)
    headers = [{"name": "Cookie", "value": {"type": "string", "value": sent}}]
    if auth:
        headers.append({"name": "Authorization", "value": {"type": "string", "value": "Bearer " + auth}})
    started = clock.perf_counter()
    m.cookies(raw)
    m.headers(headers)
    return clock.perf_counter() - started


def request_event(m, cookies, auth=None):
    return send(m, [(f"c{i}", v) for i, v in enumerate(cookies)], auth)


STEADY = ([b64(120, i) for i in range(6)], b64(3000, 99))   # 6 cookies, a 3 kB JWT
BIG_COOKIES = [b64(4000, i) for i in range(8)]                # past the characters
MANY_COOKIES = [("%2F" + b64(61, i))[:64] for i in range(60)]  # past the forms


# Two hosts' requests interleaved, each sending its own 25 cookies: 50 values
# past the forms budget, every event a different half.
HOST_A = [("%2F" + b64(61, i))[:64] for i in range(25)]
HOST_B = [("%2F" + b64(61, 100 + i))[:64] for i in range(25)]
STEADY_CASES = pytest.mark.parametrize(
    "sets",
    [[STEADY], [(BIG_COOKIES, None)], [(MANY_COOKIES, None)], [(HOST_A, None), (HOST_B, None)]],
    ids=["known", "8x4kB", "60 small", "two hosts"],
)


def events(m, sets, rounds):
    """``rounds`` passes over ``sets``, each one request event; their times."""
    return [request_event(m, cookies, auth) for _ in range(rounds) for cookies, auth in sets]


@STEADY_CASES
def test_once_learned_an_event_builds_no_forms(monkeypatch, sets):
    """R3: a credential already known costs a lookup, and one a full tier
    will not take this event costs no more; neither spells out its forms
    again, which is what each request event used to pay. Two hosts taking
    turns settle too: what is held does not depend on which one spoke last."""
    from kubed.selenium_flow.capture import masking

    m = Masker()
    events(m, sets, 3)
    built, real = [], masking.hidden_forms
    monkeypatch.setattr(masking, "hidden_forms", lambda v: built.append(1) or real(v))
    sorted_, real_sort = [], m._sort
    monkeypatch.setattr(m, "_sort", lambda: sorted_.append(1) or real_sort())
    events(m, sets, 5)
    assert built == [] and sorted_ == []


@pytest.mark.parametrize(
    ("sets", "bound"),
    [([STEADY], 0.05), ([(BIG_COOKIES, None)], 0.2), ([(MANY_COOKIES, None)], 0.2),
     ([(HOST_A, None), (HOST_B, None)], 0.1)],
    ids=["known", "8x4kB", "60 small", "two hosts"],
)
def test_a_learned_or_full_tier_costs_little_per_event(sets, bound):
    """The pin, the median of 100 events in ms, with a generous bound for a
    slow runner. Measured on the machine that wrote it: 0.010, 0.027, 0.045
    and 0.019 ms, where the code each replaced cost 0.27 (known, e3ab0c2),
    1.70 (8x4kB), 1.74 (60 small) and 0.14 (two hosts, 09f981b). The bound
    fails all but the last clearly; for two hosts the guard that does is
    `test_once_learned_an_event_builds_no_forms`. 8x4kB hashes 32 kB of
    cookies an event, so its bound stays round 3's."""
    m = Masker()
    events(m, sets, 3)
    times = sorted(events(m, sets, 100 // len(sets)))
    assert times[len(times) // 2] * 1000 < bound


# ---- fix round 4: past the budget, the cookies that matter most are held -----------

# A page heavy with analytics: 30 cookies spelt as Google Analytics spells
# them (one form each) and 8 opaque ones (three each), past the forms budget.
TRACKERS = [(f"_t{i}", f"GA1.1.{1000000000 + i * 7919}.1728600{i:03d}") for i in range(30)]
OPAQUE = [(f"_o{i}", b64(40, i)) for i in range(8)]
PAGE = TRACKERS + OPAQUE
SID = ("sid", "s%3A" + b64(32, 77))


def test_a_session_cookie_set_after_login_is_masked_on_a_tracker_heavy_page(monkeypatch):
    """R4: a browser sends the oldest cookies first; a tier that kept the
    first ones it saw held the trackers and never learned the session."""
    from kubed.selenium_flow.capture import masking

    m = Masker()
    for _ in range(3):
        send(m, PAGE)
    send(m, [*PAGE, SID])                                   # logged in
    assert m.text(SID[1]) == MASK
    built, real = [], masking.hidden_forms
    monkeypatch.setattr(masking, "hidden_forms", lambda v: built.append(1) or real(v))
    sorted_, real_sort = [], m._sort
    monkeypatch.setattr(m, "_sort", lambda: sorted_.append(1) or real_sort())
    for _ in range(5):
        send(m, [*PAGE, SID])
    assert m.text(SID[1]) == MASK
    assert built == [] and sorted_ == []                    # settled at once


def test_a_rotated_session_cookie_is_masked_at_once_and_the_old_one_ages_out():
    from kubed.selenium_flow.capture import COOKIE_STALE

    old, new = ("sid", "s%3A" + b64(32, 1)), ("sid", "s%3A" + b64(32, 2))
    m = Masker()
    for _ in range(3):
        send(m, [*PAGE, old])
    send(m, [*PAGE, new])
    assert m.text(new[1]) == MASK                           # at once
    for _ in range(COOKIE_STALE):                           # two events each
        send(m, [*PAGE, new])
    assert m.text(new[1]) == MASK
    assert m.text(old[1]) == old[1]                         # gone once no longer sent


def test_the_same_cookies_in_any_order_hold_the_same_set():
    import random

    held = []
    for seed in range(5):
        order = [*PAGE, SID]
        random.Random(seed).shuffle(order)
        m = Masker()
        for _ in range(3):
            send(m, order)
        held.append({v for _, v in order if m.text(v) == MASK})
    assert held[0] and all(h == held[0] for h in held)


# Neutral names, strong values (some 30 distinct characters each), past the budget.
FILLER = [(f"pref{i}", b64(40, 200 + i)) for i in range(20)]


@pytest.mark.parametrize(
    ("name", "masked"),
    [
        ("sid", True), ("XSRF-TOKEN", True), ("PHPSESSID", True),
        ("__Host-next-auth.session-token", True), ("refresh_jwt", True),
        ("_ga", False), ("_gid", False), ("_fbp", False), ("__utmz", False),
        ("_hjSessionUser_1", False), ("AMP_TOKEN", False), ("ajs_user_id", False),
    ],
)
def test_a_cookies_name_ranks_it_when_the_budget_is_full(name, masked):
    """A session-like name outranks a stronger value; a tracker's name ranks
    below every other, however strong its value, and wins over a session-like
    word in it (Hotjar's ``_hjSessionUser``)."""
    value = "abcdef0123456789" if masked else b64(60, 99)  # weak, or strong
    # Last in the header when it should win, first when it should lose: the
    # order a browser sends them in decides nothing.
    order = [*FILLER, (name, value)] if masked else [(name, value), *FILLER]
    m = Masker()
    for _ in range(3):
        send(m, order)
    assert (m.text(value) == MASK) is masked


def test_cookie_overflow_leaves_the_authorization_tier_alone():
    m = Masker()
    token = b64(3000, 5)
    for _ in range(3):
        send(m, [*PAGE, SID], auth=token)
    held = dict(m._auth.values)
    for _ in range(5):
        send(m, PAGE + FILLER + [SID], auth=token)
    assert m.text(token) == MASK and m._auth.values == held


def test_a_full_tier_takes_a_new_cookie_once_the_old_ones_go_stale():
    """Past the budget the tier holds the highest-ranked cookies. A weaker
    one the page sends once those stop is taken when they go stale."""
    from kubed.selenium_flow.capture import COOKIE_STALE

    m = Masker()
    for _ in range(3):
        request_event(m, MANY_COOKIES)
    weak = "fresh-session-cookie-0123456789"                # fewer distinct characters
    request_event(m, [weak])                                # the page now sends only this
    assert m.text(weak) == weak                             # the held still rank above it
    for _ in range(COOKIE_STALE):
        request_event(m, [weak])
    assert m.text(weak) == MASK


def test_of_two_large_credentials_in_one_request_the_authorization_is_kept():
    """Each fills the tier's characters alone, so one event cannot keep both.
    A proxy strips Proxy-Authorization before the origin sees it, so no page
    can echo it: Authorization is the one kept, whichever comes first."""
    a, p = b64(4000, 1), b64(4000, 2)
    m = Masker()
    for _ in range(2):
        kept([{"name": "Proxy-Authorization", "value": {"type": "string", "value": "Bearer " + p}},
              {"name": "Authorization", "value": {"type": "string", "value": "Bearer " + a}}], m)
    assert m.text(a) == MASK


def test_a_whole_authorization_value_past_the_limit_is_not_a_skip_when_its_token_was_learned():
    """R3-3: the token was learned; only the whole value was too long."""
    m = Masker()
    token = b64(4090, 3)
    bearer(m, f"Bearer {token}")
    assert m.text(token) == MASK and m.skipped == 0
