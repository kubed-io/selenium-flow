"""The step loop: every step of a flow, in order, once each.

The caller resolves the browser once, and this calls the ordinary action
methods in a loop. Binding to a running browser costs no Grid call
(`Grid.reconnect` skips `start_session`), so threading one driver through the
actions would save nothing.

Which tools a step may name (`Toolbox`), what to tell the code around the run
(`Hooks`) and the clock are handed in. Nothing here imports the route table,
the MCP layer or Selenium.

The store also reads flows edited on disk, which never went through saving, so
each check saving makes is made again here: the tool allowlist, the secret
binding, the timeout, `writeOnly`, and never continuing past an assert.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .. import binding, faults, secrets
from ..binding import SECRET_ARG
from ..core import cancel
from ..redaction import Forms, Id
from ..workspace import locks
from .document import ARGS, ASSERTION, declared_timeout
from .redact import scrub, scrub_values, taints
from .report import OUT_OF_TIME, clean, refused, summarise, with_hint
from .shape import Shape
from .template import (
    FlowError,
    check_params,
    listed,
    substitute,
    with_defaults,
)

# The runner's old logger name: operators filter logs by it.
log = logging.getLogger("kubed.selenium_flow.flows.run")

# The step that reads every site in the workspace's history (`before_save`).
SAVE_SITE_DATA = "save_site_data"


@dataclass(frozen=True)
class Toolbox:
    """The tools a step may name, and where each one is served.

    ``runnable`` is the only set a step may dispatch to: `getattr(actions,
    tool)` alone would reach any callable, such as `_at` or `__init__`.

    ``library_arg`` names, per tool, the argument that carries the caller's own
    library.
    """

    runnable: frozenset[str]
    method_for: Callable[[str], str]
    library_arg: Mapping[str, str]


def _ignored(*_args) -> None:
    return None


def _nothing_in_scope(_result) -> Forms:
    # Secrets off, or a run nobody wired.
    return Forms()


@dataclass(frozen=True)
class Hooks:
    """What a run tells the code around it, step by step. Each is optional.

    ``before_step(entry, total)`` as each step is about to act; ``entry`` has
    its number, tool, id and scrubbed summary. It must not change anything.

    ``after_step(tool, result)`` after each step that succeeds: `resize`
    changes the workspace record, which would otherwise reopen at the old size.

    ``before_save(pages)`` just before a ``save_site_data`` step, with the step
    URLs the report shows so far, in order: a save reads every site in the
    history, which is otherwise written once, after the run.

    ``bound(forms)`` before a step that binds a secret acts, with every
    spelling of its values, so capture masks them from the first keystroke.

    ``step_done(tool, error)`` after every step `before_step` began, still
    inside its turn on the browser; ``error`` is what it raised, or None. A
    step refused before it began is never done.

    ``in_scope(result)`` after each step, still inside its turn: the forms of
    the secrets allowed where the step ran. ``result`` is the step's raw
    result, or None for a step that raised. The step's line and the run's URL
    are scrubbed with them and with every value the run typed. One that raises
    fails closed: the step fails with `faults.SCRUB_FAILED` and the run stops.
    """

    before_step: Callable[[dict, int], None] = _ignored
    after_step: Callable[[str, object], None] = _ignored
    before_save: Callable[[list[str]], None] = _ignored
    bound: Callable[[Forms], None] = _ignored
    step_done: Callable[[str, BaseException | None], None] = _ignored
    in_scope: Callable[[dict | None], Forms] = _nothing_in_scope

    @classmethod
    def of(cls, **hooks) -> Hooks:
        """Hooks by name, where None means absent."""
        return cls(**{name: hook for name, hook in hooks.items() if hook is not None})


_UNSET = object()


@dataclass
class StepOutcome:
    """One step as its report line will read, filled in as the step goes.

    A field is in the line only once it is set: a step with no `url` did not
    move the page, and one with no `file` kept none.
    """

    n: int
    tool: object
    id: object = None
    note: object = None
    summary: str | None = None
    ok: bool | None = None
    error: str | None = None
    url: str | None = None
    file: object = None
    result: object = _UNSET

    @property
    def label(self):
        """How the log names the step: its id, else its tool."""
        return self.id or self.tool

    def entry(self) -> dict:
        line = {"n": self.n, "tool": self.tool}
        for key in ("id", "note", "summary", "ok", "error", "url", "file"):
            value = getattr(self, key)
            if value is not None:
                line[key] = value
        if self.result is not _UNSET:
            line["result"] = self.result
        return line


@dataclass
class Run:
    """One run's state: its budget, what it has seen and where it has been.

    ``seen`` is every spelling of every value typed from a secret so far, kept
    for the whole run: a bound write in step two can leave the value in the URL
    that step five reports. Each step is scrubbed with it and with the secrets
    in scope where it ran (`Hooks.in_scope`), together ``forms``.

    ``pages`` are the pages the report shows, in order, for `before_save`.
    ``was_at`` is the page the previous step ended on; it starts unset, so
    step one reports where the flow began.
    """

    name: str
    total: int
    budget: int
    deadline: float
    stop: threading.Event | None = None
    seen: Forms = field(default_factory=Forms)
    pages: list[str] = field(default_factory=list)
    was_at: str | None = None
    reports: list[dict] = field(default_factory=list)
    last: dict = field(default_factory=dict)
    redacted_url: bool = False
    status: str = "ok"

    def stops(self, outcome: StepOutcome, error: str) -> bool:
        """End the run at ``outcome``'s step, before it acted."""
        outcome.ok = False
        outcome.error = error
        self.reports.append(outcome.entry())
        self.status = "failed"
        return False

    def acted(
        self, outcome: StepOutcome, raw, guarded: dict[str, Id], keep, forms: Forms
    ) -> None:
        """Record a step that succeeded: its result, its page, its file, each
        scrubbed with ``forms`` (`Hooks.in_scope` and what the run typed)."""
        result = clean(raw, guarded, forms)
        outcome.ok = True
        outcome.summary = scrub(outcome.summary, forms)
        # Asked of the raw URL: a secret whose value is the marker would pass a
        # search for the marker. Set per page, since it describes the last page
        # the run reports, not its history.
        self.redacted_url = isinstance(raw, dict) and taints(raw.get("url"), forms)
        # Only when the step went somewhere, and never a URL carrying a typed
        # secret, as `landed` withholds it.
        went_to = raw.get("url") if isinstance(raw, dict) else None
        if went_to and went_to != self.was_at:
            if not self.redacted_url and "url" not in guarded:
                outcome.url = went_to
                self.pages.append(went_to)
            self.was_at = went_to
        # A kept file is named even when the report is not verbose.
        made = raw.get("file") if isinstance(raw, dict) else None
        if made and not taints(str(made), forms):
            outcome.file = made
        self.last = result
        if keep:
            outcome.result = result

    def landed(
        self, outcome: StepOutcome, page, guarded: dict[str, Id], forms: Forms
    ) -> None:
        """Record where a failed step left the browser, scrubbed with ``forms``:
        a search box lands on `?q=<what was typed>`."""
        landed = scrub_values(page, forms)
        if landed.get("url") and "url" not in guarded:
            outcome.url = landed["url"]
            self.was_at = landed["url"]
            # A step can fail after the value reached the page. Set where
            # `last` changes, so the flag describes the page the report carries.
            self.redacted_url = taints(page.get("url"), forms)
            self.last = {**self.last, **landed}

    def report(self, skill_available: bool) -> dict:
        report = {
            "flow": self.name,
            "status": self.status,
            "steps_run": len(self.reports),
            "steps_total": self.total,
            "steps": self.reports,
        }
        # From the last step that produced it, so a failed run reports the
        # page it failed on.
        for key in ("url", "title"):
            if self.last.get(key):
                report[key] = self.last[key]
        with_hint(report, skill_available)
        if self.redacted_url:
            # The reported page is not an address to go back to.
            report["url_redacted"] = True
        # No top-level `result`: the steps marked `return` carry the answer.
        return report


def resolve_step(
    step: dict,
    params: dict,
    catalogue=None,
    page: str | tuple[str, ...] | Callable[[], str | tuple[str, ...]] = "",
) -> tuple[dict, dict[str, Id]]:
    """A step's keyword arguments, and which of them must not be echoed.

    Parameters are substituted as text and guard nothing. A secret
    (`args.secret`, only `write` has one) goes straight into `text`, and the
    guard maps that argument to the secret's id for the result redaction.
    """
    args = dict(step.get(ARGS) or {})
    # The secret is lifted out before substitution, so a parameter can never
    # reach its name or key.
    reference = args.pop(SECRET_ARG, None)
    kwargs = substitute(args, params)
    if reference is None:
        return kwargs, {}

    tool = step.get("tool", "")
    label = step.get("id") or tool
    # The leash is checked against `page`, the page about to receive the
    # keystroke.
    try:
        return binding.bind_into(
            {**kwargs, SECRET_ARG: reference}, catalogue, page, tool, binding.AT_RUN
        )
    except secrets.Refused as exc:
        raise FlowError(f"step {label}: {exc}") from exc


def page_state(actions, session_id: str) -> dict:
    """Where the browser is, or ``{}`` when it cannot be read.

    A step with a `url` navigates before it waits, so a failed step can leave
    the browser somewhere newer than the last recorded URL. Read through
    `actions.page`, as the binding check reads it, and never raising: it runs
    while a failure is being reported.
    """
    try:
        return actions.page(session_id)
    except Exception:  # noqa: BLE001 - a best-effort read, on an error path
        return {}


def _preflight(document: dict, name: str, skill_available: bool) -> dict | None:
    """A report refusing ``document`` before step one, or None to go ahead."""
    shape = Shape(document)
    malformed = shape.problems()
    if malformed:
        return refused(
            document,
            name,
            "this flow cannot be run as written: " + "; ".join(malformed) + ".",
            skill_available,
        )

    marked = sorted(
        (
            str(param)
            for param, schema in shape.properties.items()
            if isinstance(schema, dict) and schema.get("writeOnly")
        ),
    )
    if marked:
        return refused(
            document,
            name,
            f"this flow marks {listed(marked)} writeOnly, which no longer "
            "hides anything — a parameter is text and may appear in the "
            "report. A value nobody may see is a secret: give write an "
            "args.secret instead.",
            skill_available,
        )

    stale = [
        number
        for number, step in enumerate(shape.steps, start=1)
        if isinstance(step, dict) and ("valueFrom" in step or "params" in step)
    ]
    if stale:
        return with_hint(
            {
                "flow": name,
                "status": "failed",
                "steps_run": 0,
                "steps_total": shape.step_count,
                "steps": [
                    {
                        "n": number,
                        "ok": False,
                        "error": (
                            "this flow was saved in an older format: a step's "
                            "arguments are 'args' now, not 'params', and a "
                            "secret is 'args.secret' rather than 'value_from'. "
                            "A flow parameter is written ${name} in any "
                            "argument. Save it again in the new shape."
                        ),
                    }
                    for number in stale
                ],
            },
            skill_available,
        )
    return None


@dataclass(frozen=True)
class _Call:
    """What every step of one run is given, unchanged from first to last."""

    actions: object
    session_id: str
    params: dict
    verbose: bool
    catalogue: object
    library: str
    toolbox: Toolbox
    hooks: Hooks
    clock: Callable[[], float]


def execute(
    actions,
    document: dict,
    session_id: str,
    *,
    toolbox: Toolbox,
    clock: Callable[[], float],
    default_timeout: int,
    hooks: Hooks | None = None,
    params: dict | None = None,
    verbose: bool = False,
    timeout: int | None = None,
    catalogue=None,
    skill_available: bool = True,
    library: str = "",
    stop: threading.Event | None = None,
) -> dict:
    """Run every step of ``document`` against the browser ``session_id``.

    Once ``stop`` (a `threading.Event`) is set, or the browser ends, no further
    step starts and a waiting step gives up at its next poll (`core.cancel`).

    Each step has the browser to itself, from reading the page a secret is
    checked against to the action's last page read; the run as a whole does
    not, so another call on the session runs between two steps. Ending the
    browser raises the run's own `Cancelled`, never `Ended`: the run-level
    flags are outermost, and the first set one decides.

    ``timeout`` overrides the budget the document declares, which overrides
    ``default_timeout``. ``clock`` is read once for the deadline and once
    before each step.
    """
    # Held for the whole run, so an `end_browser` between two steps is seen.
    turn = locks.hold(session_id)
    with cancel.watching(stop), cancel.watching(turn.ending):
        # Checked against what the caller passed, then filled, so a `default`
        # never satisfies `required`.
        shape = Shape(document)
        check_params(document, params or {})
        params = with_defaults(document, params)
        name = document.get("name", "flow")

        # `is None`, not `or`: an explicit 0 means no budget.
        if timeout is None:
            try:
                timeout = declared_timeout(document)
            except ValueError as exc:
                return refused(document, name, str(exc), skill_available)
        budget = default_timeout if timeout is None else max(int(timeout), 0)
        run = Run(
            name=name,
            total=shape.step_count,
            budget=budget,
            deadline=clock() + budget,
            stop=stop,
        )

        refusal = _preflight(document, name, skill_available)
        if refusal is not None:
            return refusal

        call = _Call(
            actions=actions,
            session_id=session_id,
            params=params,
            verbose=verbose,
            catalogue=catalogue,
            library=library,
            toolbox=toolbox,
            hooks=hooks or Hooks(),
            clock=clock,
        )
        for number, step in enumerate(shape.steps, start=1):
            if not _take(run, call, number, step):
                break
        return run.report(skill_available)


def _take(run: Run, call: _Call, number: int, step: dict) -> bool:
    """One step, start to finish. True to go on to the next, False to stop."""
    tool = step.get("tool")
    outcome = StepOutcome(
        n=number, tool=tool, id=step.get("id") or None, note=step.get("note") or None
    )

    if call.clock() >= run.deadline:
        return run.stops(outcome, f"the run passed its {run.budget}s {OUT_OF_TIME}")

    if cancel.cancelled():
        return run.stops(outcome, "the run was cancelled before this step")

    toolbox = call.toolbox
    method = (
        getattr(call.actions, toolbox.method_for(tool), None)
        if tool in toolbox.runnable
        else None
    )
    if method is None:
        return run.stops(outcome, f"there is no action called {tool!r}")

    # Cancelled while waiting its turn, the step never started and stops the
    # run with its line. Only the entering is caught.
    entered = False
    try:
        with locks.driving(call.session_id):
            entered = True
            return _drive(run, call, step, outcome, method)
    except cancel.Cancelled:
        if entered:
            raise
        return run.stops(outcome, "the run was cancelled before this step")


def _drive(run: Run, call: _Call, step: dict, outcome: StepOutcome, method) -> bool:
    """A step's page read, action and report, on the browser alone (`_take`)."""
    tool = step.get("tool")
    toolbox = call.toolbox
    try:
        # The page is read only for a step that binds a secret: it costs a
        # WebDriver round trip.
        kwargs, guarded = resolve_step(
            step,
            call.params,
            call.catalogue,
            lambda: binding.receiving(page_state(call.actions, call.session_id)),
        )
    except FlowError as exc:
        return run.stops(outcome, str(exc))

    # Set over any value the step carries: the argument is not in the
    # saved-flow schema, and a step that could name a library could read
    # another workspace's kept files.
    holder = toolbox.library_arg.get(tool)
    if holder and call.library:
        kwargs[holder] = call.library

    typed = binding.forms_of(kwargs, guarded)
    run.seen |= typed
    # Scrubbed now of what the run typed, before `before_step` sees it, and
    # of the step's scope once that is known (`acted`, `_failed`).
    outcome.summary = scrub(summarise(tool, kwargs, guarded), run.seen)
    # `step_done` exactly once for a begun step: a call never ended keeps its
    # capture held. A save whose pages could not be written was still begun.
    acting = done = False
    raw = failure = None
    try:
        if typed:
            call.hooks.bound(typed)
        call.hooks.before_step(outcome.entry(), run.total)
        acting = True
        if tool == SAVE_SITE_DATA:
            call.hooks.before_save(list(run.pages))
        raw = method(call.session_id, **kwargs)
        # Before the result is copied: the hook strips the capture from `raw`.
        call.hooks.after_step(tool, raw)
        done = True
        call.hooks.step_done(tool, None)
    except Exception as exc:  # noqa: BLE001 - a failing step is an outcome
        # First, so what `workspace.calls` adds to the error is on the line.
        if acting and not done:
            call.hooks.step_done(tool, exc)
        failure = exc
    try:
        scope = call.hooks.in_scope(None if failure is not None else raw)
    except Exception as exc:  # noqa: BLE001 - fails closed, whatever it was
        return _unchecked(run, outcome, exc)
    forms = run.seen | scope
    if failure is None:
        try:
            run.acted(outcome, raw, guarded, call.verbose or step.get("return"), forms)
        except Exception as exc:  # noqa: BLE001 - a failing step is an outcome
            failure = exc
        else:
            run.reports.append(outcome.entry())
            return True
    return _failed(run, call, step, outcome, guarded, forms, failure)


def _failed(
    run: Run,
    call: _Call,
    step: dict,
    outcome: StepOutcome,
    guarded: dict[str, Id],
    forms: Forms,
    exc: Exception,
) -> bool:
    """Record a step that raised ``exc``, scrubbed with ``forms``. True to go on."""
    tool = step.get("tool")
    outcome.ok = False
    outcome.summary = scrub(outcome.summary, forms)
    # An action puts its arguments in its error text. One line, as a single
    # call answers.
    outcome.error = scrub(faults.message(exc), forms)
    run.landed(outcome, page_state(call.actions, call.session_id), guarded, forms)
    log.info(
        "flow %s step %s (%s) failed: %s",
        run.name,
        outcome.n,
        outcome.label,
        outcome.error,
    )
    run.reports.append(outcome.entry())
    # Never continued past: an assertion, a cancelled run, or a step refused
    # because a person holds the browser (every later step would be too).
    if (
        step.get("onError") == "continue"
        and tool != ASSERTION
        and not isinstance(exc, (cancel.Cancelled, faults.Controlled))
    ):
        return True
    run.status = "failed"
    return False


def _unchecked(run: Run, outcome: StepOutcome, exc: Exception) -> bool:
    """End the run at a step whose answer could not be checked for secrets.

    The step says only the fixed sentence, none of its result, error, page or
    summary, and the run stops whatever its `onError` says. The log names the
    failure by its type alone.
    """
    log.warning(
        "flow %s step %s (%s) could not be checked for secrets: %s",
        run.name, outcome.n, outcome.label, type(exc).__name__,
    )
    outcome.ok = False
    outcome.summary = None
    outcome.error = faults.SCRUB_FAILED
    run.reports.append(outcome.entry())
    run.status = "failed"
    return False
