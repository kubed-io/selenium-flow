"""The step loop: every step of a flow, in order, once each, on one clearance.

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

So the browser is resolved **once**, by the caller, and this calls the ordinary
action methods in a loop. It deliberately does *not* thread one driver through
them: that would be a micro-optimisation on the one cost that is already near
zero, paid for by making every action take a driver it does not otherwise need.

**What it needs from outside is handed in.** Which tools a step may name and the
action method behind each (`Toolbox`), what to tell a watcher as a run goes
(`Hooks`), and the clock. Nothing here imports the route table, the MCP layer
or Selenium: the engine drives any object with the action methods on it.

**A run is not a program.** Steps execute in order, once each. No branching, no
loops, no step reading another's output — that last one is Chapter 2. A flow is
a wizard, not a language.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .. import binding, secrets
from ..binding import SECRET_ARG
from ..core import cancel
from ..session import locks
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

# The runner's old logger name, kept: operators filter Loki by it.
log = logging.getLogger("kubed.selenium_flow.flows.run")

# The step that reads every site in the session's history (`before_save`).
SAVE_SITE_DATA = "save_site_data"


@dataclass(frozen=True)
class Toolbox:
    """The tools a step may name, and where each one is served.

    ``runnable`` is the only set a step may dispatch to. `getattr(actions,
    tool)` alone accepts any callable on the object — `_at` would navigate the
    browser and `__init__` would re-point it at another Grid — and saving
    validates the name but a file edited on disk never passed through saving.

    ``library_arg`` names, per tool, the argument that carries the caller's own
    library: a step reading a kept file is answered from the library the run
    belongs to.
    """

    runnable: frozenset[str]
    method_for: Callable[[str], str]
    library_arg: Mapping[str, str]


class Hooks(Protocol):
    """What a run tells the code around it, step by step.

    ``before_step(entry, total)`` as each step is about to act, where ``entry``
    carries its number, tool, id and safe summary. It is how a caller watches a
    long run; it must not change anything.

    ``after_step(tool, result)`` after each step that succeeds. It exists
    because a step can change something the *session record* stores rather than
    just the page: `resize` is the one, and a flow that resized without telling
    the session would come back the old size the next time the Grid reaped the
    browser — exactly the silent shape change `sessions.reshape` was written to
    prevent.

    ``before_save(pages)`` with the pages this run has reached so far, in order
    — exactly the step URLs its report shows — just before a ``save_site_data``
    step acts. A save reads every site in the session's history, and the history
    is otherwise written once, after the run.
    """

    def before_step(self, entry: dict, total: int) -> None: ...

    def after_step(self, tool: str, result) -> None: ...

    def before_save(self, pages: list[str]) -> None: ...


class Callbacks:
    """`Hooks` from up to three plain functions, any of which may be absent."""

    def __init__(self, before_step=None, after_step=None, before_save=None):
        self._before_step = before_step
        self._after_step = after_step
        self._before_save = before_save

    def before_step(self, entry: dict, total: int) -> None:
        if self._before_step is not None:
            self._before_step(entry, total)

    def after_step(self, tool: str, result) -> None:
        if self._after_step is not None:
            self._after_step(tool, result)

    def before_save(self, pages: list[str]) -> None:
        if self._before_save is not None:
            self._before_save(pages)


_UNSET = object()


@dataclass
class StepOutcome:
    """One step as its report line will read, filled in as the step goes.

    A field is in the line only once it is set: silence is information here — a
    step with no `url` did not move the page, and one with no `file` kept none.
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

    ``seen`` is every spelling of every value typed from a secret so far. It is
    accumulated across the run, not scoped to a step: a submitting bound write
    in step two leaves the value in the browser's URL, and step five's page
    state would carry it back out if `seen` were recomputed per step. Once a
    value has been typed, nothing later in this run may echo it.

    ``pages`` are the pages the report shows, in order, for `before_save`;
    ``was_at`` is the page the previous step ended on, so a step can say where it
    went when it went somewhere. It starts unset, which makes step one report
    where the flow began — a fact about the run nobody else states (§F2.7).
    """

    name: str
    total: int
    budget: int
    deadline: float
    stop: threading.Event | None = None
    seen: set = field(default_factory=set)
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

    def acted(self, outcome: StepOutcome, raw, guarded: set, keep) -> None:
        """Record a step that succeeded: its result, its page, its file."""
        result = clean(raw, guarded, self.seen)
        outcome.ok = True
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
        self.redacted_url = isinstance(raw, dict) and taints(raw.get("url"), self.seen)
        # Where this step went, and only when it went somewhere. Silence
        # means the page did not change, which is what makes a navigation
        # that did not happen visible in a report nobody asked to be
        # verbose - the cheapest defence left when an author forgets an
        # assert (§F2.7). Withheld on exactly the terms the failure branch
        # withholds it: a URL carrying a typed secret is not reported.
        went_to = raw.get("url") if isinstance(raw, dict) else None
        if went_to and went_to != self.was_at:
            if not self.redacted_url and "url" not in guarded:
                outcome.url = went_to
                self.pages.append(went_to)
            self.was_at = went_to
        # A step that produced a file says so, even when the report is
        # not verbose. A screenshot whose link appears nowhere cannot show
        # anybody what it saw, which is most of why a flow took it — and a
        # person reading the report is exactly who it was for (pilot, §F2.15).
        made = raw.get("file") if isinstance(raw, dict) else None
        if made and not taints(str(made), self.seen):
            outcome.file = made
        self.last = result
        if keep:
            outcome.result = result

    def landed(self, outcome: StepOutcome, page, guarded: set) -> None:
        """Record where a failed step actually left the browser.

        Where it actually failed, not where the last step succeeded. Swept,
        because the page can carry the value back: typing into a search box
        lands you on `?q=<what you typed>`, and the URL of the page a bound
        write failed on is exactly the kind of place a credential turns up
        without anyone putting it there.
        """
        landed = scrub_values(page, self.seen)
        if landed.get("url") and "url" not in guarded:
            outcome.url = landed["url"]
            self.was_at = landed["url"]
            # The same fact on the branch that had not recorded it: a step
            # can fail *after* the value reached the page, so the URL it
            # failed on is exactly as unsafe to store as one a step
            # succeeded on. Set where `last` changes, so the flag always
            # describes the page the report ends up carrying.
            self.redacted_url = taints(page.get("url"), self.seen)
            self.last = {**self.last, **landed}

    def report(self, skill_available: bool) -> dict:
        report = {
            "flow": self.name,
            "status": self.status,
            "steps_run": len(self.reports),
            "steps_total": self.total,
            "steps": self.reports,
        }
        # Where the browser ended up. Taken from the last step that produced it
        # rather than asked for again: a run that failed should report the page
        # it failed on, and asking now would report whatever it drifted to
        # since.
        for key in ("url", "title"):
            if self.last.get(key):
                report[key] = self.last[key]
        # Chosen from what actually failed, and only when something did.
        with_hint(report, skill_available)
        if self.redacted_url:
            # Says the reported page is not the page: a caller must not store
            # it as somewhere to navigate back to.
            report["url_redacted"] = True
        # No top-level `result`. It used to carry the last step's, which meant
        # a flow ending in `extract` with `return: true` — the shape every
        # example taught — reported the same object twice, once under its step
        # and once here. Worse, it was a second way to say what a run answers
        # with: `return` is the explicit one, and two mechanisms for one job is
        # how they drift.
        #
        # A step now says whether its result is part of the flow's answer, any
        # number of steps may say so, and a caller running somebody else's flow
        # that marks none can still pass `verbose`. `url` and `title` stay,
        # because where the browser ended up is a fact about the *run* rather
        # than a step's output — and it is the thing a caller needs to carry on
        # from.
        return report


def resolve_step(
    step: dict,
    params: dict,
    catalogue=None,
    page: str | tuple[str, ...] | Callable[[], str | tuple[str, ...]] = "",
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
    args = dict(step.get(ARGS) or {})
    # Substituted first, and the secret lifted out before it — so a parameter
    # can never reach the secret's name or key. Saving refuses that too; this
    # is the same rule applied to a document that may never have been saved.
    reference = args.pop(SECRET_ARG, None)
    kwargs = substitute(args, params)
    if reference is None:
        return kwargs, set()

    tool = step.get("tool", "")
    label = step.get("id") or tool
    # Saving refuses a literal or a url beside the secret, and saving is not
    # the only way a document gets here: the store reads YAML somebody may have
    # written by hand. `page` is where the browser actually is, so the leash is
    # checked against the page about to receive the keystroke.
    try:
        return binding.bind_into(
            {**kwargs, SECRET_ARG: reference}, catalogue, page, tool, binding.AT_RUN
        )
    except secrets.Refused as exc:
        raise FlowError(f"step {label}: {exc}") from exc


def page_state(actions, session_id: str) -> dict:
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


def _preflight(document: dict, name: str, skill_available: bool) -> dict | None:
    """A report refusing ``document`` before step one, or None to go ahead."""
    # A document the validator would refuse, reaching here anyway because the
    # store reads YAML that may never have been saved through it. `writeOnly`
    # is the one that matters: it used to hide a parameter from the report and
    # now hides nothing, so an author who trusted it — and a hand-edited file is
    # exactly where that marker survives a migration — would have the value
    # echoed back by any `return: true` step. Refusing the run is the same
    # answer saving gives, in the only other place a document can arrive
    # (§F1.38).
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
        # Preflighted, not caught mid-loop: a stale key on step nine would
        # otherwise have run the first eight and then reported `steps_run: 0`,
        # which both half-runs a flow the message says was refused and misstates
        # what happened.
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

    ``stop`` is a `threading.Event`. Once it is set no further step starts, and
    a step that is waiting gives up at its next poll (see `core.cancel`).
    Ending the browser does the same (`session.locks`).

    Each step has the browser to itself, from reading the page a secret is
    checked against to the action's last page read; the run as a whole does
    not. The lock is first come, first served, so a call on the session that
    asks during a step runs before the next one, not after the whole run.
    Ending the browser raises the run's own `Cancelled`, never `Ended`: the
    run-level flags are outermost, and the first set one decides.

    ``timeout`` overrides the budget the document declares, which overrides
    ``default_timeout``. ``clock`` is read once for the deadline and once
    before each step.
    """
    # Kept for the whole run so an `end_browser` between two steps is still
    # seen when the next one is about to start: unheld, it would be gone.
    turn = locks.hold(session_id)
    with cancel.watching(stop), cancel.watching(turn.ending):
        # Checked against what the CALLER passed, then filled. The other order
        # lets a `default` satisfy `required`, which would make `required` mean
        # nothing — and "needs term: pass them in params" is advice the caller
        # can act on, where a silently-defaulted required parameter is not.
        shape = Shape(document)
        check_params(document, params or {})
        params = with_defaults(document, params)
        name = document.get("name", "flow")

        # `is None`, not `or`: an explicit 0 means "no budget" and must not be
        # read as "unset" and silently given the full default budget.
        if timeout is None:
            # Saving refuses a bad one; a document edited on disk never went
            # through saving, and is refused here on the same terms.
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
            hooks=hooks or Callbacks(),
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
        # Saving validates the name, so reaching this means the document was
        # written before a tool was renamed — or edited on disk, which never
        # passed through saving at all. Hence the allowlist rather than a
        # callable check: `__init__` and `_at` are both callable.
        return run.stops(outcome, f"there is no action called {tool!r}")

    with locks.driving(call.session_id):
        return _drive(run, call, number, step, outcome, method)


def _drive(
    run: Run, call: _Call, number: int, step: dict, outcome: StepOutcome, method
) -> bool:
    """A step's page read, action and report, on the browser alone (`_take`)."""
    tool = step.get("tool")
    label = step.get("id") or tool
    toolbox = call.toolbox
    try:
        # The page is read only when a step actually binds a secret: it costs a
        # WebDriver round trip, and every other step has no leash to check.
        kwargs, guarded = resolve_step(
            step,
            call.params,
            call.catalogue,
            lambda: binding.receiving(page_state(call.actions, call.session_id)),
        )
    except FlowError as exc:
        return run.stops(outcome, str(exc))

    # Filled in before the summary, so a step reading a kept file is
    # answered by the library this run belongs to rather than by whatever
    # the ambient caller key happens to resolve to.
    #
    # It OVERWRITES rather than filling a gap, and that is the point. The
    # argument is not part of the saved-flow schema - `step_schemas` is
    # built from the MCP tool, which deliberately omits it - so a document
    # carrying one was hand-edited on disk and never passed validation. A
    # step that could name a library would be a step that reads another
    # session's kept files, which is not a feature (Copilot, #32).
    holder = toolbox.library_arg.get(tool)
    if holder and call.library:
        kwargs[holder] = call.library

    outcome.summary = summarise(tool, kwargs, guarded)
    run.seen |= binding.forms_of(kwargs, guarded)
    try:
        call.hooks.before_step(outcome.entry(), run.total)
        if tool == SAVE_SITE_DATA:
            call.hooks.before_save(list(run.pages))
        raw = method(call.session_id, **kwargs)
        # Before the result is copied for the report: the hook strips the
        # private capture from `raw`.
        call.hooks.after_step(tool, raw)
        run.acted(outcome, raw, guarded, call.verbose or step.get("return"))
    except Exception as exc:  # noqa: BLE001 - a failing step is an outcome
        outcome.ok = False
        # An action puts its arguments in its error text, so the message is
        # scrubbed before it reaches either the report or the log.
        outcome.error = scrub(str(exc), run.seen)
        run.landed(outcome, page_state(call.actions, call.session_id), guarded)
        log.info(
            "flow %s step %s (%s) failed: %s", run.name, number, label, outcome.error
        )
        run.reports.append(outcome.entry())
        # An assertion is never continued past, whatever the document
        # says. Saving refuses the pairing; this is the second half, for a
        # flow edited on disk - without it a false assertion could still
        # end `ok`, which is the whole failure this action exists to stop.
        if (
            step.get("onError") == "continue"
            and tool != ASSERTION
            and not isinstance(exc, cancel.Cancelled)
        ):
            return True
        run.status = "failed"
        return False
    run.reports.append(outcome.entry())
    return True
