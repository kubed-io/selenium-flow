"""What a run says about itself: a line per step, a cleaned result, a hint.

A step's summary takes only the fields `SAFE_IN_SUMMARY` names; its result loses
the heavy fields and every guarded value; a failed run says where to read about
the failure. `flows.api` hands in how a hint names a page (`refer_with`).
"""

from __future__ import annotations

from collections.abc import Callable

from ..binding import after
from ..redaction import Forms, Id, marker
from .document import ASSERTION
from .shape import Shape

# The prompt a person picks to repair a failed flow; an agent cannot invoke one.
REPAIR_PROMPT = "repair_flow"

# How a run that ran out of budget reports it; `hint_for` reads it back.
OUT_OF_TIME = "budget before this step"

# The arguments a step's summary may show. Everything else is omitted, not
# redacted; `text` shows only its length, or a secret's marker.
SAFE_IN_SUMMARY = ("url", "selector", "to", "action", "key", "filename", "index")

# Dropped from every step result: a full-page screenshot is over a megabyte of
# base64. The step's line names a kept file instead.
HEAVY_FIELDS = ("image",)

# A reference page's file name -> the URI it is read at. Without one, a hint
# names its prompt and no page.
_pointer: Callable[[str], str] | None = None


def refer_with(pointer: Callable[[str], str] | None) -> None:
    """Say how a hint names a reference page."""
    global _pointer
    _pointer = pointer


def hint_for(step: dict, flow: str, skill_available: bool = True) -> dict:
    """Where to look, decided by what failed rather than guessed."""
    error = str(step.get("error") or "")
    # Before the assertion case: running out of time before an `assert` did
    # not fail it.
    if OUT_OF_TIME in error:
        page, section = "FLOWS.md", "how-long-a-run-may-take"
    elif step.get("tool") == ASSERTION:
        page, section = "FLOWS.md", "say-what-must-be-true"
    elif "matched" in error:
        # A locator found nothing, or nothing usable.
        page, section = "FLOWS.md", "when-a-flow-fails"
    else:
        page, section = "TROUBLESHOOTING.md", ""

    hint = {
        "prompt": REPAIR_PROMPT,
        "arguments": {"flow": flow, "step": str(step.get("n") or "")},
    }
    # `read` is a resource URI, so the section goes beside it, never as an
    # anchor on it. Only when the skill is served.
    if skill_available and _pointer is not None:
        hint["read"] = _pointer(page)
        if section:
            hint["section"] = section
    return hint


def with_hint(report: dict, skill_available: bool = True) -> dict:
    """Attach the hint to a failed report, wherever it was built."""
    steps = report.get("steps") or []
    if report.get("status") == "failed" and steps:
        report["hint"] = hint_for(steps[-1], report.get("flow", ""), skill_available)
    return report


def refused(document: dict, name: str, why: str, skill_available: bool = True) -> dict:
    """A run that was stopped before step one, reported as a run.

    Its steps are counted by `Shape`, since the document may be malformed, and
    the reason is a step line even when there are no steps.
    """
    return with_hint(
        {
            "flow": name,
            "status": "failed",
            "steps_run": 0,
            "steps_total": Shape(document).step_count,
            "steps": [{"n": 1, "ok": False, "error": why}],
        },
        skill_available,
    )


def summarise(tool: str, kwargs: dict, guarded: dict[str, Id]) -> str:
    """One short line saying what a step did, built from what is safe to say.

    `SAFE_IN_SUMMARY` decides which fields may be shown, and `guarded` hides a
    secret's value whatever field it is in.
    """
    parts = []
    for key in SAFE_IN_SUMMARY:
        value = kwargs.get(key)
        if not value:
            continue
        if key in guarded:
            parts.append(f"{key}={marker([guarded[key]])}")
            continue
        # A selector reads `css='button.go'`.
        if isinstance(value, dict):
            parts += [f"{k}={v!r}" for k, v in value.items() if v]
            continue
        parts.append(f"{key}={value!r}")
    if "text" in kwargs:
        value = kwargs["text"]
        parts.append(
            f"text={marker([guarded['text']])}"
            if "text" in guarded
            else f"text={len(value) if isinstance(value, str) else '?'} chars"
        )
    return f"{tool} {' '.join(parts)}".strip()


def clean(result, guarded: dict[str, Id], hidden: Forms | None = None) -> dict:
    """A step's result, without the parts a report must not carry."""
    if not isinstance(result, dict):
        result = {"result": result}
    cleaned = {k: v for k, v in result.items() if k not in HEAVY_FIELDS}
    return after(cleaned, guarded, hidden or Forms())
