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

**There is no templating.** A step that needs a value it was not given names a
source in `valueFrom`, and this module puts the resolved value straight into the
call's keyword arguments. Nothing scans a payload, so a script containing
``${...}`` or a password containing ``{{`` is just a string (§F1.7).

**A run is not a program.** Steps execute in order, once each. No branching, no
loops, no step reading another's output — that last one is Chapter 2. A flow is
a wizard, not a language.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote, quote_plus

from . import secrets
from .flowdoc import FILLS, NOT_STEPS, VALUE_FROM, NoSoleSource, sole_source
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
        # Coerced, not skipped: `Actions.write` does `str(text)`, so a numeric
        # or boolean writeOnly parameter really is typed into the page — and
        # skipping non-strings here meant it came back unscrubbed.
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


def required_params(document: dict) -> list[str]:
    return list((document.get("parameters") or {}).get("required") or [])


def sensitive_params(document: dict) -> set[str]:
    """Parameters marked `writeOnly` in the flow's own schema.

    Standard JSON Schema for "supplied but not returned", so it is not a keyword
    we invented. It is a marker and not encryption: the value still arrives in
    the call. What it buys is that it does not go back out again.
    """
    properties = (document.get("parameters") or {}).get("properties") or {}
    return {
        name
        for name, schema in properties.items()
        if isinstance(schema, dict) and schema.get("writeOnly")
    }


def check_params(document: dict, params: dict) -> None:
    """Refuse before step one rather than at step two with half a form filled."""
    missing = [name for name in required_params(document) if name not in (params or {})]
    if missing:
        raise FlowError(
            f"{document.get('name', 'this flow')} needs "
            f"{', '.join(sorted(missing))}: pass them in params"
        )
    declared = set((document.get("parameters") or {}).get("properties") or {})
    unknown = sorted(set(params or {}) - declared)
    if unknown:
        known = ", ".join(sorted(declared)) or "it takes none"
        raise FlowError(
            f"{document.get('name', 'this flow')} does not take "
            f"{', '.join(unknown)}. Takes: {known}"
        )


def resolve_step(
    step: dict,
    params: dict,
    sensitive: set[str],
    catalogue=None,
    page: str = "",
) -> tuple[dict, set]:
    """A step's keyword arguments, and **which of them** must not be echoed.

    Structural: `valueFrom` names a source and the value goes straight into the
    kwargs. No string is inspected for placeholders, which is why a payload can
    never collide with a reference.

    `value_from` is an ordinary parameter of the action, so a step's `params`
    is exactly the arguments of the call and a step is literally the call a
    caller would make directly. Which argument it fills is the action's own
    business — `write` fills `text` — so nothing repeats a name, the way
    Kubernetes never repeats an env var's name inside its `valueFrom`.

    The guard is a set of argument names rather than one flag. It holds at most
    one today, and stays a set because the *result* redaction keys off argument
    names and a second bindable argument should not require rewriting that.
    """
    kwargs = dict(step.get("params") or {})
    guarded: set[str] = set()
    source = kwargs.pop(VALUE_FROM, None)
    if source is None:
        return kwargs, guarded
    tool = step.get("tool", "")
    label = step.get("id") or tool
    # `is None` above, not truthiness: an empty mapping is a *malformed*
    # binding, and save-time validation rejects it. Treating it as absent let a
    # hand-edited stored flow fall through to a literal `text` sitting beside it
    # — quietly running the step with the wrong value instead of refusing.
    #
    # The same rule the validator applies, applied again here: a stored document
    # may never have been through it. Dispatching on `kind` rather than testing
    # the sources in order is what stops two of them silently becoming one.
    try:
        kind = sole_source(source)
    except NoSoleSource as exc:
        raise FlowError(f"step {label}: {exc}") from exc
    target = FILLS.get(tool)
    if target is None:
        raise FlowError(f"step {label}: {tool} does not take {VALUE_FROM}")

    # Saving checks this, and saving is not the only way a document gets here:
    # `LocalFlowStore` reads YAML somebody may have written by hand. Every rule
    # that protects a secret is therefore checked again at the moment it is
    # used, where the document's provenance no longer matters.
    if kind == "secret" and kwargs.get("url"):
        raise FlowError(
            f"step {label}: a step that binds a secret may not also navigate — "
            "the secret's allowed sites are checked against the page the "
            "browser is on, and this would type it on a page that was never "
            "checked"
        )

    if kind == "param":
        reference = source["param"]
        kwargs[target] = params.get(reference)
        if reference in sensitive:
            guarded.add(target)
    elif kind == "secret":
        # The one place a run reads a credential. `page` is where the browser
        # actually is, so the secret's leash is checked against the page about
        # to receive the keystroke rather than wherever the flow started.
        try:
            kwargs[target] = secrets.bind(catalogue, source, page, tool=tool)
        except secrets.Refused as exc:
            raise FlowError(f"step {label}: {exc}") from exc
        guarded.add(target)
    elif kind == "config":
        raise FlowError(
            f"step {label}: config values are not available on this server yet"
        )
    else:  # pragma: no cover - only reachable if SOURCES grows and this does not
        raise FlowError(f"step {label}: this server cannot resolve a {kind}")
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
    sensitive = sensitive_params(document)
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

    stale = [
        number
        for number, step in enumerate(document.get("steps") or [], start=1)
        if isinstance(step, dict) and "valueFrom" in step
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
                        "this flow was saved in an older format: valueFrom is a "
                        "parameter now, so move it inside params as value_from "
                        "and save it again"
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
            if "secret" in ((step.get("params") or {}).get(VALUE_FROM) or {}):
                page = _page_state(actions, session_id).get("url", "")
            kwargs, guarded = resolve_step(step, params, sensitive, catalogue, page)
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
    if last:
        report["result"] = last
    return report
