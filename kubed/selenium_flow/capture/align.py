"""Aligning Chrome's replay with what was already kept."""

from __future__ import annotations

from collections.abc import Sequence


def overlap(seen: Sequence, replay: Sequence) -> int:
    """How many of ``replay``'s first entries are the last of ``seen``.

    Chrome replays the last 100 entries per browsing context to a new
    subscriber, in order: the tail of what was already seen, then whatever
    arrived while nobody listened. Matched as a sequence, never as a set:
    identical tuples are real entries.
    """
    for j in range(min(len(seen), len(replay)), 0, -1):
        if list(replay[:j]) == list(seen[-j:]):
            return j
    return 0
