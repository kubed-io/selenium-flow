"""A flow's tags, and the filter that picks flows by them."""

import re

import pytest

from kubed.selenium_flow.flows import tags
from kubed.selenium_flow.flows.tags import Filter

pytestmark = pytest.mark.unit


def test_a_tag_is_read_trimmed_and_lowercased_in_the_order_given():
    assert tags.read([" Auth:LDAP ", "smoke", "a/b.c-d_e"]) == [
        "auth:ldap",
        "smoke",
        "a/b.c-d_e",
    ]


def test_a_repeat_after_folding_is_dropped_and_the_first_kept():
    assert tags.read(["Smoke", "login", "smoke ", "LOGIN"]) == ["smoke", "login"]


def test_the_code_never_splits_a_tag_on_its_punctuation():
    assert tags.read(["foo:bar", "foo/bar"]) == ["foo:bar", "foo/bar"]


def test_a_tag_may_be_fifty_characters_and_no_more():
    assert tags.read(["a" * 50]) == ["a" * 50]
    with pytest.raises(ValueError, match="a" * 51):
        tags.read(["a" * 51])


def test_twenty_tags_and_no_more():
    twenty = [f"t{n}" for n in range(20)]
    assert tags.read(twenty) == twenty
    with pytest.raises(ValueError, match="at most 20"):
        tags.read([*twenty, "one-more"])


def test_repeats_do_not_count_toward_the_twenty():
    assert len(tags.read([f"t{n}" for n in range(20)] + ["T0"])) == 20


@pytest.mark.parametrize(
    "bad", ["", "  ", "-lead", ":lead", "has space", "comma,s", "ünï", "a#b", "a$"]
)
def test_a_bad_tag_is_refused_naming_it_and_the_rule(bad):
    with pytest.raises(ValueError) as caught:
        tags.read(["fine", bad])
    message = str(caught.value)
    assert repr(bad) in message
    assert "starting with a letter or a digit" in message


def test_a_trailing_newline_is_not_a_tag_character():
    """`$` would match before a final newline; the whole tag must match."""
    assert tags.read(["ok\n"]) == ["ok"]
    with pytest.raises(ValueError):
        tags.read(["o\nk"])


@pytest.mark.parametrize("bad", ["smoke", {"a": 1}, None])
def test_tags_must_be_a_list(bad):
    with pytest.raises(ValueError, match="tags must be a list"):
        tags.read(bad)


@pytest.mark.parametrize("bad", [1, None, ["a"]])
def test_a_tag_that_is_not_a_string_is_refused_naming_it(bad):
    with pytest.raises(ValueError, match=re.escape(f"tag {bad!r} is refused")):
        tags.read(["fine", bad])


def test_no_tags_is_an_empty_list():
    assert tags.read([]) == []


# ---- the filter -------------------------------------------------------------


def test_a_flow_passes_when_it_carries_every_tag_of_some_row():
    rows = Filter.parse(["foo:bar,baz", "dud"])
    assert rows.passes(["baz", "foo:bar"])
    assert rows.passes(["dud"])
    assert rows.passes(["x", "dud", "y"])
    assert not rows.passes(["foo:bar"])
    assert not rows.passes(["baz"])
    assert not rows.passes([])


def test_a_row_is_read_as_a_tag_is():
    assert Filter.parse([" Foo:Bar , BAZ "]).passes(["foo:bar", "baz"])


def test_an_empty_filter_passes_no_flow():
    assert not Filter.parse([]).passes(["anything"])
    assert not Filter.parse([]).passes([])


@pytest.mark.parametrize("row", ["", "  ", "a,,b", "a,", "a,b c"])
def test_an_empty_row_or_one_holding_a_non_tag_is_refused_naming_it(row):
    with pytest.raises(ValueError) as caught:
        Filter.parse(["fine", row])
    assert repr(row) in str(caught.value)


@pytest.mark.parametrize("rows", ["foo", [1], [["foo"]], None])
def test_a_filter_is_a_list_of_strings(rows):
    with pytest.raises(ValueError):
        Filter.parse(rows)
