"""The key vocabulary: what a caller may name, and what that sends.

Rules over Selenium's `Keys` table and nothing else: no driver, no session. A key
is a name, one character or a combination, and this is the one place that says
which.
"""

from __future__ import annotations

import re

from selenium.webdriver.common.keys import Keys

# Named keys a caller can press. Selenium's Keys members are unicode private-use
# characters, so a caller cannot reasonably type them into JSON by hand.
KEYS = {
    name.lower(): getattr(Keys, name)
    for name in dir(Keys)
    if name.isupper() and not name.startswith("_")
}

# Selenium spells 60 keys with 73 names: LEFT and ARROW_LEFT, BACK_SPACE and
# BACKSPACE, four for Meta. Every spelling still works, since KEYS keeps them
# all; this is the one name per key that is *offered*, so a listing is a list of
# keys rather than a quiz of synonyms. Where there is a choice, the name matching
# the browser's own KeyboardEvent.key wins.
_PREFERRED = frozenset({
    "alt", "arrow_down", "arrow_left", "arrow_right", "arrow_up", "backspace",
    "control", "meta", "right_alt", "shift",
})


def _offered_names() -> tuple[str, ...]:
    spellings: dict[str, list[str]] = {}
    for name in sorted(KEYS):
        spellings.setdefault(KEYS[name], []).append(name)
    chosen = []
    for names in spellings.values():
        preferred = [name for name in names if name in _PREFERRED]
        chosen.append((preferred or names)[0])
    return tuple(sorted(chosen))


KEY_NAMES = _offered_names()


def _squash(name: str) -> str:
    """One spelling for comparison: `ArrowLeft`, `arrow_left` and `arrow-left`
    are the same key, and so are `PageDown` and `page_down`."""
    return re.sub(r"[\s_-]", "", name).lower()


_BY_SPELLING = {_squash(name): value for name, value in KEYS.items()}

# `Control+a`, the way browsers and Playwright write a combination. A `+` that
# follows another `+` is the plus key itself, so `Control++` is Control and plus.
_COMBINATION = re.compile(r"\+(?=.)")


def resolve_key(key) -> str:
    """What to send for ``key``: a name, one character, or a combination.

    A string rather than an enum, deliberately, because the set is open — any
    character is a key. Names are matched in either spelling (§F2.1). A
    combination is sent as one sequence, and WebDriver holds each modifier for
    the keys after it and releases them all at the end.
    """
    text = "" if key is None else str(key)
    if len(text) > 1:
        text = text.strip()
    parts = [text] if len(text) <= 1 else _COMBINATION.split(text)
    return "".join(_one_key(part, text) for part in parts)


def _one_key(part: str, whole: str) -> str:
    if len(part) == 1:
        return part
    value = _BY_SPELLING.get(_squash(part)) if part else None
    if value is None:
        within = "" if part == whole else f" in {whole!r}"
        raise ValueError(
            f"unknown key {part!r}{within}; give one character, a combination "
            f"such as Control+a, or a name: {', '.join(KEY_NAMES)}"
        )
    return value


# The keys that can submit a form, and so are the only ones worth waiting on a
# navigation for. Selenium spells RETURN and ENTER as different characters, so
# both are here; every other key navigates nowhere by itself.
SUBMIT_KEYS = frozenset({Keys.RETURN, Keys.ENTER})
