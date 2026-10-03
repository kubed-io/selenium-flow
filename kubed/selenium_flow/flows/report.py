"""What a run says about itself: a line per step, a cleaned result, a hint.

The report is as much of the feature as the running is, and it is built from
what is safe to say rather than from everything with the unsafe parts removed.
A step's summary takes only the fields `SAFE_IN_SUMMARY` names; its result loses
the heavy fields and every guarded value; a failed run says where to read about
the failure.

**Where to read is handed in.** A hint names a reference page by its URI, and
which URIs exist is the MCP layer's business (`core.guidance.pointer`) rather
than this module's: `flows.api` hands the function in through `refer_with`, so
a run imports nothing that knows how the skill is served.
"""

from __future__ import annotations

from collections.abc import Callable

from ..binding import after
from .document import ASSERTION
from .shape import Shape

# Where to read about a failure, and which prompt repairs it. The reference is
# for the agent, which reads resources when it decides to; the prompt is for a
# person, because an agent cannot invoke one - it can only say which to pick
# (§F2.6).
REPAIR_PROMPT = "repair_flow"

# How a run that ran out of budget reports it; `hint_for` reads it back.
OUT_OF_TIME = "budget before this step"

# Parameter values that may appear in a step's summary. Everything else is
# omitted rather than redacted, which is the structural version of not leaking:
# the summary is built from what is safe, so a value can never reach it by
# being missed. A selector and a URL are how you tell which step ran; `text` is
# the one an agent most wants to see and the one most likely to be a password.
SAFE_IN_SUMMARY = ("url", "selector", "to", "action", "key", "filename", "index")

# Dropped from every step result in a report. A full-page screenshot is over a
# megabyte of base64, and a run that returned three of them would cost more than
# the twelve tool calls it replaced. Use screenshot(save=True) and read the file
# list instead — the run report says when one was kept.
HEAVY_FIELDS = ("image",)

# A reference page's file name -> the URI it is read at. None until `flows.api`
# hands one in; a hint without it names its prompt and no page, which is what
# it says when the skill is not served at all.
_pointer: Callable[[str], str] | None = None


def refer_with(pointer: Callable[[str], str] | None) -> None:
    """Say how a hint names a reference page. See the module docstring."""
    global _pointer
    _pointer = pointer


def hint_for(step: dict, flow: str, skill_available: bool = True) -> dict:
    """Where to look, decided by what failed rather than guessed."""
    error = str(step.get("error") or "")
    # Before the assertion case: a run that ran out of time before an `assert`
    # did not fail that assertion, and pointing at how to write one would send
    # the reader to fix something that never ran (Copilot, #38).
    if OUT_OF_TIME in error:
        # Not a broken page: the flow took longer than it was allowed. Either
        # it means to wait and should say so, or the step before stalled.
        page, section = "FLOWS.md", "how-long-a-run-may-take"
    elif step.get("tool") == ASSERTION:
        # The assertion did its job. What to do next is in the message its
        # author wrote; the reference explains why the run stopped there.
        page, section = "FLOWS.md", "say-what-must-be-true"
    elif "matched" in error:
        # A locator that found nothing, or found something that cannot be used:
        # the page has moved under the flow.
        page, section = "FLOWS.md", "when-a-flow-fails"
    else:
        page, section = "TROUBLESHOOTING.md", ""

    hint = {
        "prompt": REPAIR_PROMPT,
        "arguments": {"flow": flow, "step": str(step.get("n") or "")},
    }
    # `read` is a resource URI and nothing else: everything after
    # `skill://selenium-flow/` is the file path, so an anchor glued on the end
    # names a file that does not exist. The section travels beside it.
    #
    # And it is only there when the skill is actually being served: with
    # `--mcp-skill false` nothing registers those resources, and a URI
    # that cannot be read is worse than no URI at all.
    if skill_available and _pointer is not None:
        hint["read"] = _pointer(page)
        if section:
            hint["section"] = section
    return hint


def with_hint(report: dict, skill_available: bool = True) -> dict:
    """Attach the hint to a failed report, wherever it was built.

    One place, because a run can fail three ways — a refused document, a stale
    format, or a step — and two of them used to return before the hint existed.
    """
    steps = report.get("steps") or []
    if report.get("status") == "failed" and steps:
        report["hint"] = hint_for(steps[-1], report.get("flow", ""), skill_available)
    return report


def refused(document: dict, name: str, why: str, skill_available: bool = True) -> dict:
    """A run that was stopped before step one, reported as a run.

    Preflighted rather than caught mid-loop: a bad document found at step nine
    would otherwise have run the first eight and then reported `steps_run: 0`,
    which both half-runs a flow the message says was refused and misstates what
    happened.

    The document is the one being refused, so its steps are counted by `Shape`:
    `steps: 1` is a reason to refuse, not a TypeError on the way to saying so.
    And the reason is reported even when there are no steps to count, or the
    refusal would say nothing at all.
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


def summarise(tool: str, kwargs: dict, guarded: set) -> str:
    """One short line saying what a step did, built from what is safe to say.

    Two filters, not one. The whitelist decides which *fields* could ever be
    printed; `guarded` then hides the ones whose value came from somewhere it
    must not come back from, whatever field they landed in.
    """
    parts = []
    for key in SAFE_IN_SUMMARY:
        value = kwargs.get(key)
        if not value:
            continue
        if key in guarded:
            parts.append(f"{key}=<hidden>")
            continue
        # A selector is an object with one key in it, so the line reads
        # `css='button.go'` rather than `selector={'css': 'button.go'}`.
        if isinstance(value, dict):
            parts += [f"{k}={v!r}" for k, v in value.items() if v]
            continue
        parts.append(f"{key}={value!r}")
    if "text" in kwargs:
        value = kwargs["text"]
        parts.append(
            "text=<hidden>"
            if "text" in guarded
            else f"text={len(value) if isinstance(value, str) else '?'} chars"
        )
    return f"{tool} {' '.join(parts)}".strip()


def clean(result, guarded: set, hidden=()) -> dict:
    """A step's result, without the parts a report must not carry."""
    if not isinstance(result, dict):
        result = {"result": result}
    cleaned = {k: v for k, v in result.items() if k not in HEAVY_FIELDS}
    return after(cleaned, guarded, hidden)
