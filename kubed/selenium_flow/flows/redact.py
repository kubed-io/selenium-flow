"""Keeping a value nobody may see out of everything a run says.

A secret is typed into the page and must come back out nowhere: not in a step's
result, its error, the page it landed on, the run's own URL, or a log line. This
is the sweep, and it is a security primitive rather than a part of running a
flow — `secrets.perform_write` uses it for a single bound write as well.

String replacement, which is the opposite direction from templating. Templating
scans an input for something that might be a reference; this scans an *output*
for values already known, on the way to a report and a log.
"""

from __future__ import annotations

from urllib.parse import quote, quote_plus

HIDDEN = "<hidden>"

# A result field carries an argument's value back out. Most do it by having the
# same name — `navigate(url=...)` answers with the page state, whose `url` is
# the one it was given — and one does it under a different name: `write` reads
# the field back and returns it as `value`, deliberately, so a caller can
# confirm the text landed.
#
# Both routes have to be closed. Redacting only `value` left a magic-link token
# bound to `url` coming straight back in the result, in the run's top-level
# `url`, and — worst of the three — in `sessions.touch`, which persists it to
# Redis as the page a later reopen should return to.
RESULT_FROM_ARGUMENT = {"text": "value", "script": "result"}


def redacted_fields(guarded: set) -> set:
    """Result fields that would carry a guarded argument's value back out."""
    fields = set(guarded)
    for argument, field in RESULT_FROM_ARGUMENT.items():
        if argument in guarded:
            fields.add(field)
    return fields


def scrub_values(obj, values):
    """``obj`` with every guarded value replaced, however deep it sits.

    The named-field map above is precise and cheap, and it is a list somebody
    has to remember to extend — `script` -> `result` was missing from it, which
    is how a guarded script came back under a name nobody had mapped. So the
    map handles the fields we know and this handles the ones we do not: for a
    guarded step, and only a guarded step, every string in the result is swept.

    Only reached when a step actually bound something, so the cost lands on the
    rare call rather than on `extract` returning a page of HTML.
    """
    if isinstance(obj, str):
        return scrub(obj, values)
    if isinstance(obj, dict):
        return {key: scrub_values(value, values) for key, value in obj.items()}
    if isinstance(obj, list):
        return [scrub_values(value, values) for value in obj]
    return obj


def hidden_forms(values) -> set:
    """Every spelling a guarded value can come back in.

    A submitting write lands the browser on `?q=<what was typed>`, and the
    browser percent-encodes it on the way — so `a/b` comes back as `a%2Fb` and a
    literal replacement misses it entirely. Both quoting styles are covered
    because a form submission uses `+` for spaces and a path does not.
    """
    forms = set()
    for value in values:
        if value is None:
            continue
        # Coerced, not skipped: `Actions.write` does `str(text)`, so a secret
        # whose stored value is not a string really is typed into the page —
        # and skipping non-strings here meant it came back unscrubbed.
        text = value if isinstance(value, str) else str(value)
        if not text:
            continue
        forms.add(text)
        forms.add(quote(text, safe=""))
        forms.add(quote_plus(text))
    return forms


def scrub(text: str, values) -> str:
    """``text`` with every guarded value replaced.

    It is needed because an action puts its arguments in its error text:
    ``upload_file`` naming a kept file that is not there raises ``no file at
    <uri>``, and a bad URL comes back from Selenium with the URL in it.

    A very short guarded value makes the message noisy, which is the right way
    round: a mangled error beats a leaked credential, and a two-character secret
    is a problem with the secret.
    """
    for value in values:
        if isinstance(value, str) and value:
            text = text.replace(value, HIDDEN)
    return text


def taints(text, values) -> bool:
    """Whether any guarded value is actually present in ``text``.

    Asked directly, rather than inferred by comparing a string with its scrubbed
    form. Three places had made that comparison and all three were wrong the
    same way: a value that is exactly ``HIDDEN`` scrubs to itself, so equality
    holds while the credential is still there. A marker is evidence of nothing —
    the question is whether the value is in the text, so ask that.
    """
    if not isinstance(text, str) or not text:
        return False
    return any(isinstance(value, str) and value and value in text for value in values)
