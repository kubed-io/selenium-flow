"""One set of forms, one marker, one scanner (spec 2026-11-14-secret-scope)."""

import base64
import html
import json
from urllib.parse import quote, quote_plus

import pytest
from selenium.common.exceptions import TimeoutException

from kubed.selenium_flow.redaction import (
    Forms,
    Unscrubbed,
    marker,
    render,
    scrub,
    scrub_error,
    scrub_values,
    taints,
)

pytestmark = pytest.mark.unit

PW = "pa ss@&<\"'é9"
ID = ("ldap", "password")
F = Forms.of(PW, ID)
M = "[secret:ldap/password]"

def golden(value: str) -> list[tuple[str, str]]:
    """One row per encoding of ``value`` (Dr K's table, 11-13 22:15).

    A function so `test_scrub_coverage` spells its own value the same ways.
    """
    return [
        ("raw", value), ("html", html.escape(value)), ("query", quote(value, safe="")),
        ("form", quote_plus(value)), ("json", json.dumps(value)[1:-1]),
        ("base64", base64.b64encode(value.encode()).decode()),
        ("base64 url-safe", base64.urlsafe_b64encode(value.encode()).decode()),
        ("hex", value.encode().hex()), ("HEX", value.encode().hex().upper()),
        ("upper", value.upper()), ("reversed", value[::-1]), ("repr", repr(value)[1:-1]),
    ]


GOLDEN = golden(PW)


@pytest.mark.parametrize("name,spelt", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_every_encoding_comes_back_as_the_marker(name, spelt):
    assert scrub(f"<p>{spelt}</p>", F) == f"<p>{M}</p>"


@pytest.mark.parametrize("prefix", ["user:", "user1:", "user12:"])  # alignments 2, 0, 1
def test_a_value_inside_a_longer_base64_string_is_found(prefix):
    blob = base64.b64encode((prefix + PW).encode()).decode()
    assert PW not in scrub(f"Authorization: Basic {blob}", F)
    assert M in scrub(f"Authorization: Basic {blob}", F)


def test_two_secrets_with_one_value_are_both_named():                    # ruling 3
    both = Forms.of("hunter22", ("a", "password")) | Forms.of("hunter22", ("b", "password"))
    assert scrub("x hunter22 y", both) == "x [secret:a/password|b/password] y"


def test_overlapping_forms_become_one_marker():
    forms = Forms.of("abcdef", ("s", "k")) | Forms.of("defghi", ("t", "k"))
    assert scrub("abcdefghi", forms) == "[secret:s/k|t/k]"


def test_a_value_inside_its_own_marker_is_not_scrubbed_twice():          # ruling 20
    forms = Forms.of("password", ("ldap", "password"))
    once = scrub("the password", forms)
    assert scrub(once, forms) == once == "the [secret:ldap/password]"


def test_a_forged_marker_does_not_shelter_the_value():                    # Review Focus 4
    forms = Forms.of("hunter22", ("ldap", "password"))
    assert "hunter22" not in scrub("[secret:x/y]hunter22 [secret:hunter22/k]", forms)


def test_a_short_value_is_masked_only_where_it_stands_alone():            # ruling 4
    forms = Forms.of("abc", ("s", "k"))
    assert scrub("abc, labcd", forms) == "[secret:s/k], labcd"


def test_folding_keeps_spans_aligned_after_a_wider_lowercase():          # ruling 18
    assert scrub("İ HUNTER22", Forms.of("hunter22", ID)) == f"İ {M}"


def test_base64_is_matched_exactly():
    core = base64.b64encode(b"hunter22").decode()
    assert scrub(core.swapcase(), Forms.of("hunter22", ID)) == core.swapcase()


def test_an_error_is_scrubbed_in_place_and_keeps_its_type():             # ruling 2
    try:
        try:
            raise TimeoutException(msg=f"waited on ?q={PW}")
        except TimeoutException as inner:
            raise ValueError(f"no {PW}") from inner
    except ValueError as exc:
        exc.add_note(f"note {PW}")
        scrub_error(exc, F)
        assert type(exc) is ValueError and PW not in str(exc) and PW not in exc.__notes__[0]
        assert PW not in exc.__cause__.msg


def test_a_value_that_is_its_own_marker_still_taints():
    own = Forms.of(M, ID)
    assert scrub(f"q={M}", own) == f"q={M}"
    assert taints(f"https://nc/?q={M}", own) is True


def test_an_error_whose_state_cannot_be_reached_raises_unscrubbed():
    exc = FileNotFoundError(2, "No such file", "/tmp/hunter22")
    forms = Forms.of("hunter22", ID)
    with pytest.raises(Unscrubbed) as caught:
        scrub_error(exc, forms)
    assert "hunter22" not in str(caught.value) and "hunter22" not in repr(caught.value)
    assert caught.value.__suppress_context__ and caught.value.__cause__ is None


def test_a_value_inside_its_own_marker_is_not_residue():
    exc = RuntimeError("bad password here")
    scrub_error(exc, Forms.of("password", ("nextcloud", "password")))
    assert str(exc) == "bad [secret:nextcloud/password] here"


def test_a_clean_scrub_does_not_raise_unscrubbed():
    exc = ValueError("no hunter22")
    scrub_error(exc, Forms.of("hunter22", ID))
    assert "hunter22" not in str(exc)


def test_an_unlabelled_form_renders_the_mask():
    assert render("a secret b", [(2, 8, frozenset())], "•••") == "a ••• b"


def test_the_marker_names_its_ids_sorted():
    assert marker({("b", "k"), ("a", "z")}) == "[secret:a/z|b/k]"


def test_taints_asks_whether_a_value_is_there():
    forms = Forms.of("hunter2", ID)
    assert taints("https://x.test/?q=hunter2", forms) is True
    assert taints("https://x.test/", forms) is False
    assert taints(None, forms) is False
    assert taints(f"https://x.test/?q={M}", forms) is False


def test_an_empty_forms_changes_nothing():
    assert not Forms() and scrub("anything", Forms()) == "anything"
    assert not Forms.of("", ID)


def test_scrub_values_sweeps_keys_below_the_top_only():
    forms = Forms.of("s3cr3t", ID)
    page = {"a s3cr3t": 1, "kept": {"x s3cr3t": [{"s3cr3t": 3}]}}
    assert scrub_values({"s3cr3t": page}, forms) == {
        "s3cr3t": {f"a {M}": 1, "kept": {f"x {M}": [{M: 3}]}}
    }


def test_scrub_values_reaches_into_tuples_and_sets_keeping_their_type():
    from typing import NamedTuple

    class Pair(NamedTuple):
        left: str
        right: object

    forms = Forms.of("s3cr3t", ID)
    shown = scrub_values(
        {
            "t": ("a s3cr3t", ["s3cr3t"]),
            "s": {"s3cr3t", "b"},
            "f": frozenset({"s3cr3t"}),
            "n": Pair("s3cr3t", ("s3cr3t",)),
            "k": {("s3cr3t", 1): "v"},
        },
        forms,
    )
    assert shown["t"] == (f"a {M}", [M]) and type(shown["t"]) is tuple
    assert shown["s"] == {M, "b"} and type(shown["s"]) is set
    assert shown["f"] == frozenset({M}) and type(shown["f"]) is frozenset
    assert shown["n"] == Pair(M, (M,)) and type(shown["n"]) is Pair
    assert shown["k"] == {(M, 1): "v"}
    assert scrub_values(("s3cr3t",), forms) == (M,)


# --- a text cut short (final review 3) ---------------------------------------

TOKEN = "ghp_0123456789abcdefABCDEF0123456789abcdef"
TOKEN_ID = ("github", "token")
TF = Forms.of(TOKEN, TOKEN_ID)
TM = "[secret:github/token]"


def test_a_cut_text_masks_what_is_left_of_a_value_at_its_end():
    text = "Your personal access token is " + TOKEN[:20]
    assert scrub(text, TF, cut=True) == "Your personal access token is " + TM


def test_a_text_ending_in_an_ellipsis_is_read_as_cut():
    """outline marks a name it cut with `…`; the scrub needs no other word."""
    assert scrub("token: " + TOKEN[:12] + "…", TF) == "token: " + TM + "…"


def test_a_cut_tail_matches_case_blind_like_the_rest():
    assert scrub("x " + TOKEN.upper()[:10], TF, cut=True) == "x " + TM


def test_a_tail_shorter_than_a_short_value_is_left_alone():
    """Under `SHORT` characters a prefix is anybody's word."""
    assert scrub("price: ghp", TF, cut=True) == "price: ghp"


def test_a_text_not_cut_keeps_its_tail():
    assert scrub("Your token is " + TOKEN[:20], TF) == "Your token is " + TOKEN[:20]


def test_a_cut_tail_is_found_beside_whole_values():
    text = TOKEN + " and " + TOKEN[:8]
    assert scrub(text, TF, cut=True) == TM + " and " + TM


def test_scrub_values_reads_an_ellipsis_as_cut_anywhere_it_sits():
    value = {"elements": [{"name": "Token " + TOKEN[:30] + "…"}]}
    assert scrub_values(value, TF) == {"elements": [{"name": "Token " + TM + "…"}]}
