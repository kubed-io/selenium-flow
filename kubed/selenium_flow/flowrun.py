"""Running a saved flow: every step, server-side, on one clearance.

The saving is not where it looks. `Grid.reconnect` deliberately skips
`start_session`, so binding to a running browser is local object construction
rather than a call to the Grid — batching saves nothing there, and each step
sends the same WebDriver commands it would have sent alone. What a run collapses
is everything *around* the steps (saga §F1.1):

| Per step, called one at a time | Per run |
|---|---|
| a model inference — the dominant cost | 1 |
| an MCP round trip | 1 |
| an `is_alive` GET, from `sessions.resolve` | 1 |
| a session-store read and write | 1 |

So this module resolves the browser **once** and then calls the ordinary action
methods in a loop. It deliberately does *not* thread one driver through them:
that would be a micro-optimisation on the one cost that is already near zero,
paid for by making every action take a driver it does not otherwise need.

**A parameter is text; a secret is structural** (§F1.38). `${name}` is
substituted into any argument, anywhere, and `args.secret` is not — a secret
goes straight into the keyword arguments and never through a string.

Three rules make the text half safe, and `substitute` below is where they live:

- **single pass**, so a value a caller supplied is never rescanned and passing
  ``${admin_token}`` as a value yields that text;
- **only names the caller supplied** are replaced, so a script holding a
  JavaScript template literal — ``return `${window.scrollY}px` `` — survives
  rather than being silently blanked;
- **`$${` is a literal `${`**, matched by the same expression as a reference so
  an escape can never be read as one.

**A run is not a program.** Steps execute in order, once each. No branching, no
loops, no step reading another's output — that last one is Chapter 2. A flow is
a wizard, not a language.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote, quote_plus

from . import secrets
from .flowdoc import ARGS, NOT_STEPS, PARAM_REFERENCE, SECRET_ARG, listed
from .routes import ENDPOINTS

# The only attributes a step may dispatch to. `getattr(actions, tool)` alone
# accepts any callable on the object — `clear_files` would wipe the session's
# downloads and `__init__` would re-point it at another Grid — and saving
# validates the name but a file edited on disk never passed through saving.
# ENDPOINTS is the canonical list of browser actions and is already held to the
# tool surface by test_surfaces.py.
RUNNABLE = frozenset(ENDPOINTS.values()) - NOT_STEPS

log = logging.getLogger(__name__)

# How long a whole run may take. Checked between steps rather than enforced
# inside one: a Selenium call blocks, and the honest bound is "we will not start
# another step after this". Each step still has its own wait_timeout.
RUN_TIMEOUT = 300

# Parameter values that may appear in a step's summary. Everything else is
# omitted rather than redacted, which is the structural version of not leaking:
# the summary is built from what is safe, so a value can never reach it by
# being missed. A selector and a URL are how you tell which step ran; `text` is
# the one an agent most wants to see and the one most likely to be a password.
SAFE_IN_SUMMARY = ("url", "xpath", "css", "action", "key", "filename", "index")

# Dropped from every step result in a report. A full-page screenshot is over a
# megabyte of base64, and a run that returned three of them would cost more than
# the twelve tool calls it replaced. Use screenshot(save=True) and read the file
# list instead — the run report says when one was kept.
HEAVY_FIELDS = ("image",)

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

# Actions that can be told not to read their value back off the page.
READ_BACK_OFF = {"write"}


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


HIDDEN = "<hidden>"


def scrub(text: str, values) -> str:
    """``text`` with every guarded value replaced.

    String replacement, which is the one place this module does any — and it is
    the opposite direction from templating. Templating scans an input for
    something that might be a reference; this scans an *output* for values we
    already know, on the way to a report and a log.

    It is needed because an action puts its arguments in its error text:
    ``upload_file`` bound to a guarded path raises ``no file at <path>``, and a
    bad URL comes back from Selenium with the URL in it.

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


class FlowError(ValueError):
    """A run that could not start, or a step that could not be resolved.

    Distinct from a step *failing*, which is an ordinary outcome and is reported
    rather than raised.

    A **ValueError**, deliberately: every one of these is something the caller
    got wrong and can fix — a missing parameter, a name the flow does not take,
    a source this server cannot resolve. As a RuntimeError it reached
    `errors.status_for` as a 500, telling an n8n node with Retry-On-Fail to
    replay a request that was never going to work.
    """


def _properties(document: dict) -> dict:
    """A flow's declared parameters, or nothing if the document is malformed.

    Defensive because this reads a stored file: `parameters: []` and a scalar
    `properties` are both things a hand edit produces, and neither may become a
    crash in a preflight whose job is to refuse documents cleanly.
    """
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    properties = parameters.get("properties")
    return properties if isinstance(properties, dict) else {}


def _refused(document: dict, name: str, why: str) -> dict:
    """A run that was stopped before step one, reported as a run.

    Preflighted rather than caught mid-loop: a bad document found at step nine
    would otherwise have run the first eight and then reported `steps_run: 0`,
    which both half-runs a flow the message says was refused and misstates what
    happened.
    """
    total = len(document.get("steps") or [])
    return {
        "flow": name,
        "status": "failed",
        "steps_run": 0,
        "steps_total": total,
        "steps": [{"n": 1, "ok": False, "error": why}] if total else [],
    }


def required_params(document: dict) -> list[str]:
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        return []
    required = parameters.get("required")
    return list(required) if isinstance(required, list) else []


def check_params(document: dict, params: dict) -> None:
    """Refuse before step one rather than at step two with half a form filled."""
    missing = [name for name in required_params(document) if name not in (params or {})]
    if missing:
        raise FlowError(
            f"{document.get('name', 'this flow')} needs "
            f"{listed(missing)}: pass them in params"
        )
    declared = set(_properties(document))
    unknown = set(params or {}) - declared
    if unknown:
        known = listed(declared) or "it takes none"
        raise FlowError(
            f"{document.get('name', 'this flow')} does not take "
            f"{listed(unknown)}. Takes: {known}"
        )


def substitute(value, params: dict):
    """``value`` with every ``${name}`` replaced by the parameter it names.

    **Single pass.** `re.sub` never revisits what it wrote, and an escape is
    matched by the same expression as a reference, so a parameter whose *value*
    contains ``${admin_token}`` yields that text and resolves nothing. This is
    the rule that keeps "a payload can never collide with a reference" true now
    that strings are scanned at all (§F1.38).

    A string that is **exactly** one reference takes the parameter's value with
    its type intact, so an integer parameter stays an integer and reaches a
    tool argument that wants one. Anywhere else the value is interpolated as
    text, because the result is a string by construction.

    Walks lists and mappings, because an argument is not always a flat string —
    a selector inside a list is as good a place for a parameter as any.
    """
    if isinstance(value, str):
        whole = PARAM_REFERENCE.fullmatch(value)
        if whole is not None and whole.group(1) in params:
            return params[whole.group(1)]

        def one(match):
            if match.group(1) is None:
                return "${"
            # **Only what the caller supplied.** Anything else is left exactly
            # as written, because it is not ours to interpret: a script holding
            # a JavaScript template literal — `return `${window.scrollY}px`` —
            # is an ordinary payload, and blanking it would corrupt the step
            # silently. Saving refuses an undeclared name, so a flow that went
            # through validation cannot reach here with one; a hand-edited one
            # keeps its text rather than losing it.
            if match.group(1) not in params:
                return match.group(0)
            return str(params[match.group(1)])

        return PARAM_REFERENCE.sub(one, value)
    if isinstance(value, dict):
        return {key: substitute(item, params) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, params) for item in value]
    return value


def resolve_step(
    step: dict,
    params: dict,
    catalogue=None,
    page: str = "",
) -> tuple[dict, set]:
    """A step's keyword arguments, and **which of them** must not be echoed.

    Two mechanisms, deliberately unlike each other (§F1.38).

    A **parameter** is text. It is substituted into the arguments wherever it
    appears, and nothing is guarded: a parameter is non-secret by definition,
    which is what makes substituting it anywhere safe. There is no `writeOnly`.

    A **secret** is structural and never becomes part of a string. It arrives as
    `args.secret`, an argument only `write` has, and the value goes straight
    into `text` — so the credential exists only as one keyword argument, and the
    guard set names that argument for the redaction that follows.

    The guard is a set rather than a flag because the *result* redaction keys
    off argument names, and a second guarded argument should not require
    rewriting it.
    """
    kwargs = dict(step.get(ARGS) or {})
    guarded: set[str] = set()
    # Substituted first, and the secret lifted out before it — so a parameter
    # can never reach the secret's name or key. Saving refuses that too; this
    # is the same rule applied to a document that may never have been saved.
    reference = kwargs.pop(SECRET_ARG, None)
    kwargs = substitute(kwargs, params)
    if reference is None:
        return kwargs, guarded

    tool = step.get("tool", "")
    label = step.get("id") or tool
    # Saving refuses this, and saving is not the only way a document gets here:
    # `LocalFlowStore` reads YAML somebody may have written by hand. Without
    # the check the literal was silently discarded and the credential typed in
    # its place — a step saying two things quietly becoming a step saying one,
    # which is the shape every other surface refuses by name.
    if kwargs.get("text") is not None:
        raise FlowError(
            f"step {label}: text is given literally and by a secret — one "
            "value, one place"
        )
    if kwargs.get("url"):
        raise FlowError(
            f"step {label}: a step that types a secret may not also navigate — "
            "the secret's allowed sites are checked against the page the "
            "browser is on, and this would type it on a page that was never "
            "checked"
        )
    # The one place a run reads a credential. `page` is where the browser
    # actually is, so the secret's leash is checked against the page about to
    # receive the keystroke rather than wherever the flow started.
    try:
        kwargs["text"] = secrets.bind(catalogue, reference, page, tool=tool)
    except secrets.Refused as exc:
        raise FlowError(f"step {label}: {exc}") from exc
    guarded.add("text")
    return kwargs, guarded


def summarise(tool: str, kwargs: dict, guarded: set) -> str:
    """One short line saying what a step did, built from what is safe to say.

    Two filters, not one. The whitelist decides which *fields* could ever be
    printed; `guarded` then hides the ones whose value came from somewhere it
    must not come back from, whatever field they landed in.
    """
    parts = []
    for key in SAFE_IN_SUMMARY:
        if not kwargs.get(key):
            continue
        parts.append(f"{key}=<hidden>" if key in guarded else f"{key}={kwargs[key]!r}")
    if "text" in kwargs:
        value = kwargs["text"]
        parts.append(
            "text=<hidden>"
            if "text" in guarded
            else f"text={len(value) if isinstance(value, str) else '?'} chars"
        )
    return f"{tool} {' '.join(parts)}".strip()


def _clean(result, guarded: set, hidden=()) -> dict:
    """A step's result, without the parts a report must not carry."""
    if not isinstance(result, dict):
        result = {"result": result}
    cleaned = {k: v for k, v in result.items() if k not in HEAVY_FIELDS}
    for field in redacted_fields(guarded):
        if field in cleaned:
            cleaned[field] = None
    return scrub_values(cleaned, hidden) if hidden else cleaned


def _page_state(actions, session_id: str) -> dict:
    """Where the browser actually is, best effort.

    A step that carries `url` navigates *before* it waits for its element, so a
    failed wait leaves the browser on the new page while the last successful
    step's URL is the newest one recorded. Reporting that stale URL is
    misleading; letting `sessions.touch` store it is worse, because a later
    reopen would land on the wrong page.

    Goes through `actions.page` rather than reaching for the Grid itself, so
    that "where is the browser" has one implementation. The binding check reads
    the page the same way, and a secret's leash being checked against a
    different notion of "here" than the failure report uses would be a subtle
    and unpleasant divergence.

    Wrapped because it runs while reporting a failure and must never turn one
    failure into two.
    """
    try:
        return actions.page(session_id)
    except Exception:  # noqa: BLE001 - a best-effort read, on an error path
        return {}


def run(
    actions,
    document: dict,
    session_id: str,
    params: dict | None = None,
    verbose: bool = False,
    timeout: int = RUN_TIMEOUT,
    after_step=None,
    catalogue=None,
) -> dict:
    """Run every step of ``document`` against the browser ``session_id``.

    The browser is the caller's and is resolved before this is called — a flow
    never opens or ends one, which is what lets the same flow run on Chrome and
    then on Firefox unedited (§F1.9).

    ``after_step`` is called with ``(tool, result)`` after each step that
    succeeds. It exists because a step can change something the *session record*
    stores rather than just the page: `resize` is the one, and a flow that
    resized without telling the session would come back the old size the next
    time the Grid reaped the browser — exactly the silent shape change
    `sessions.reshape` was written to prevent.
    """
    params = dict(params or {})
    check_params(document, params)
    name = document.get("name", "flow")

    reports: list[dict] = []
    seen: set = set()
    redacted_url = False
    last: dict = {}
    status = "ok"
    # `is None`, not `or`: an explicit 0 means "no budget" and must not be read
    # as "unset" and silently given the full five minutes.
    budget = RUN_TIMEOUT if timeout is None else max(int(timeout), 0)
    deadline = time.monotonic() + budget

    # A document the validator would refuse, reaching here anyway because
    # `LocalFlowStore` reads YAML that may never have been saved through it.
    # `writeOnly` is the one that matters: it used to hide a parameter from the
    # report and now hides nothing, so an author who trusted it — and a
    # hand-edited file is exactly where that marker survives a migration —
    # would have the value echoed back by any `return: true` step. Refusing the
    # run is the same answer saving gives, in the only other place a document
    # can arrive (§F1.38).
    marked = sorted(
        (
            str(param)
            for param, schema in _properties(document).items()
            if isinstance(schema, dict) and schema.get("writeOnly")
        ),
    )
    if marked:
        return _refused(
            document,
            name,
            f"this flow marks {listed(marked)} writeOnly, which no longer "
            "hides anything — a parameter is text and may appear in the "
            "report. A value nobody may see is a secret: give write an "
            "args.secret instead.",
        )

    stale = [
        number
        for number, step in enumerate(document.get("steps") or [], start=1)
        if isinstance(step, dict) and ("valueFrom" in step or "params" in step)
    ]
    if stale:
        # Preflighted, not caught mid-loop: a stale key on step nine would
        # otherwise have run the first eight and then reported `steps_run: 0`,
        # which both half-runs a flow the message says was refused and misstates
        # what happened.
        return {
            "flow": name,
            "status": "failed",
            "steps_run": 0,
            "steps_total": len(document.get("steps") or []),
            "steps": [
                {
                    "n": number,
                    "ok": False,
                    "error": (
                        "this flow was saved in an older format: a step's "
                        "arguments are 'args' now, not 'params', and a secret "
                        "is 'args.secret' rather than 'value_from'. A flow "
                        "parameter is written ${name} in any argument. Save it "
                        "again in the new shape."
                    ),
                }
                for number in stale
            ],
        }

    for number, step in enumerate(document.get("steps") or [], start=1):
        tool = step.get("tool")
        label = step.get("id") or tool
        entry = {"n": number, "tool": tool}
        if step.get("id"):
            entry["id"] = step["id"]
        if step.get("note"):
            entry["note"] = step["note"]

        if time.monotonic() >= deadline:
            entry.update(
                ok=False,
                error=f"the run passed its {timeout}s budget before this step",
            )
            reports.append(entry)
            status = "failed"
            break

        method = getattr(actions, tool, None) if tool in RUNNABLE else None
        if method is None:
            # Saving validates the name, so reaching this means the document was
            # written before a tool was renamed — or edited on disk, which never
            # passed through saving at all. Hence the allowlist rather than a
            # callable check: `clear_files` and `_at` are both callable.
            entry.update(ok=False, error=f"there is no action called {tool!r}")
            reports.append(entry)
            status = "failed"
            break

        try:
            # Only read the page when a step actually binds a secret: it costs a
            # WebDriver round trip, and every other step has no leash to check.
            page = ""
            if (step.get(ARGS) or {}).get(SECRET_ARG) is not None:
                page = _page_state(actions, session_id).get("url", "")
            kwargs, guarded = resolve_step(step, params, catalogue, page)
        except FlowError as exc:
            entry.update(ok=False, error=str(exc))
            reports.append(entry)
            status = "failed"
            break

        entry["summary"] = summarise(tool, kwargs, guarded)
        # Accumulated across the run, not scoped to this step. A submitting
        # bound write in step two leaves the value in the browser's URL, and
        # step five's page state would have carried it back out with `hidden`
        # recomputed as empty. Once a value has been typed, nothing later in
        # this run may echo it.
        seen |= hidden_forms(kwargs.get(name) for name in guarded)
        hidden = seen
        try:
            if guarded and tool in READ_BACK_OFF:
                # The read must not HAPPEN for a bound value, not merely be
                # redacted afterwards. The direct tool and the HTTP endpoint
                # both did this; the flow path — the main one — did not, and
                # the end-to-end test missed it because its action is a double.
                kwargs = {**kwargs, "read_back": False}
            raw = method(session_id, **kwargs)
            result = _clean(raw, guarded, hidden)
            entry["ok"] = True
            # Recorded rather than inferred later by searching the string for
            # the marker, and asked of the raw URL rather than by comparing it
            # with its scrubbed form: a secret whose value happens to BE the
            # marker survives both of those tests unchanged.
            #
            # Set per page rather than left sticky, because it describes the
            # page this run *reports* — the last one — not the run's history. A
            # flow that types a password and then navigates away ends somewhere
            # perfectly ordinary, and a sticky flag threw that page away and
            # left the session pointing at whatever it knew before.
            redacted_url = isinstance(raw, dict) and taints(raw.get("url"), hidden)
            last = result
            if after_step is not None:
                after_step(tool, raw)
            if verbose or step.get("return"):
                entry["result"] = result
        except Exception as exc:  # noqa: BLE001 - a failing step is an outcome
            entry["ok"] = False
            # An action puts its arguments in its error text, so the message is
            # scrubbed before it reaches either the report or the log.
            entry["error"] = scrub(str(exc), hidden)
            # Where it actually failed, not where the last step succeeded.
            # Swept, because the page can carry the value back: typing into a
            # search box lands you on `?q=<what you typed>`, and the URL of the
            # page a bound write failed on is exactly the kind of place a
            # credential turns up without anyone putting it there.
            page = _page_state(actions, session_id)
            landed = scrub_values(page, hidden)
            if landed.get("url") and "url" not in guarded:
                entry["url"] = landed["url"]
                # The same fact on the branch that had not recorded it: a step
                # can fail *after* the value reached the page, so the URL it
                # failed on is exactly as unsafe to store as one a step
                # succeeded on. Set where `last` changes, so the flag always
                # describes the page the report ends up carrying.
                redacted_url = taints(page.get("url"), hidden)
                last = {**last, **landed}
            log.info(
                "flow %s step %s (%s) failed: %s", name, number, label, entry["error"]
            )
            reports.append(entry)
            if step.get("onError") == "continue":
                continue
            status = "failed"
            break
        reports.append(entry)

    report = {
        "flow": name,
        "status": status,
        "steps_run": len(reports),
        "steps_total": len(document.get("steps") or []),
        "steps": reports,
    }
    # Where the browser ended up. Taken from the last step that produced it
    # rather than asked for again: a run that failed should report the page it
    # failed on, and asking now would report whatever it drifted to since.
    for key in ("url", "title"):
        if last.get(key):
            report[key] = last[key]
    if redacted_url:
        # Says the reported page is not the page: a caller must not store it as
        # somewhere to navigate back to.
        report["url_redacted"] = True
    # No top-level `result`. It used to carry the last step's, which meant a
    # flow ending in `extract` with `return: true` — the shape every example
    # taught — reported the same object twice, once under its step and once
    # here. Worse, it was a second way to say what a run answers with: `return`
    # is the explicit one, and two mechanisms for one job is how they drift.
    #
    # A step now says whether its result is part of the flow's answer, any
    # number of steps may say so, and a caller running somebody else's flow
    # that marks none can still pass `verbose`. `url` and `title` stay, because
    # where the browser ended up is a fact about the *run* rather than a step's
    # output — and it is the thing a caller needs to carry on from.
    return report
