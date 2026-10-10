"""Chrome's replay, aligned as a sequence (ruling 9)."""

import pytest

from kubed.selenium_flow.capture.align import overlap

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("seen, replay, expected", [
    ([], ["a", "b"], 0),
    (["a", "b", "c"], ["a", "b", "c"], 3),             # nothing new
    (["a", "b", "c"], ["b", "c", "d"], 2),             # one new, the oldest scrolled off
    (["x", "a", "a"], ["a", "a", "a"], 2),             # identical tuples are real entries
    (["a", "b"], ["c", "d"], 0),
])
def test_overlap_is_the_longest_prefix_of_the_replay_that_ends_what_was_seen(seen, replay, expected):
    assert overlap(seen, replay) == expected
