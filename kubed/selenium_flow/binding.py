"""How a secret reaches a field, and why it comes back out nowhere.

Three places apply this rule: saving a flow (`flows.document`), running one
(`flows.engine.resolve_step`), and a single bound write
(`secrets.perform_write`, behind both the MCP tool and the HTTP route). Each
used to state it for itself, so a fix in one was a hole in the other two.

The rule, in the order it is applied:

* **The pair.** A secret supplies `text`, so a call that also gives `text` says
  two things, and one that also gives `url` would navigate *before* it types —
  the leash would be checked against the page being left, and a redirect would
  defeat even checking the URL that was asked for. Navigation is its own call.
* **The bind.** The secret is lifted out of the arguments, the page the browser
  is actually on is read, and `secrets.bind` — the only function that returns a
  value — puts it into `text`. The field is then not read back at all, rather
  than read and hidden.
* **After.** What an argument's value comes back under is nulled, and every
  string in the result is swept of every spelling the value can take.

Each surface raises its own exception type — `InvalidFlow`, `FlowError`,
`secrets.Refused`, all `ValueError`s and so all 400 — in the words it has always
used. The words are published, so they are held here as data rather than
rewritten into one sentence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import secrets
from .flows.redact import hidden_forms, redacted_fields, scrub_values, taints

# The argument through which a secret reaches the page. Not a table of tools:
# `write` is the only action with a `secret` parameter, so the tool schemas
# refuse it everywhere else without a second list that could disagree (§F1.38).
SECRET_ARG = "secret"

# The argument a secret supplies.
SUPPLIES = "text"

# Actions that can be told not to read their value back off the page.
READ_BACK_OFF = {"write"}

GIVEN_TWICE = "text is given literally and by a secret — one value, one place"
NOT_BOTH = "pass text or secret, not both"
NAVIGATES = "a step that types a secret may not also navigate"


@dataclass(frozen=True)
class Wording:
    """What one surface says when a call breaks the pair."""

    twice: str
    navigates: str


AT_SAVE = Wording(
    twice=f"{GIVEN_TWICE}. Give the text or the secret, not both",
    navigates=(
        f"{NAVIGATES} — put the url in its own navigate step, so the secret's "
        "allowed sites are checked against the page that receives it"
    ),
)
AT_RUN = Wording(
    twice=GIVEN_TWICE,
    navigates=(
        f"{NAVIGATES} — the secret's allowed sites are checked against the page "
        "the browser is on, and this would type it on a page that was never checked"
    ),
)
DIRECT = Wording(
    twice=NOT_BOTH,
    navigates=(
        "a write that takes its value from a secret may not also navigate: go to "
        "the page first, so the secret's allowed sites are checked against the "
        "page that receives it"
    ),
)


def binds(args: dict) -> bool:
    """Whether ``args`` take a value from a secret."""
    return args.get(SECRET_ARG) is not None


def gives_twice(args: dict, as_written: bool = False) -> bool:
    """Whether ``args`` give the secret's value themselves as well.

    ``as_written`` is for a saved document, held to what it says: a key written
    as null was still written. A call or a run reads null as unset, because the
    MCP tool fills every parameter it was not given with None.
    """
    if as_written:
        return SUPPLIES in args
    return args.get(SUPPLIES) is not None


def navigates(args: dict) -> bool:
    """Whether ``args`` would move the browser before typing."""
    return bool(args.get("url"))


def check_pair(args: dict, say: Wording = AT_RUN) -> list[str]:
    """What is wrong with what ``args`` give beside their secret, in ``say``'s words.

    Empty for a call that binds nothing. The reference itself is shape-checked
    by `secrets.bind`, which every bind goes through.
    """
    if not binds(args):
        return []
    problems = []
    if gives_twice(args):
        problems.append(say.twice)
    if navigates(args):
        problems.append(say.navigates)
    return problems


def bind_into(
    kwargs: dict,
    catalogue,
    page: str | Callable[[], str],
    tool: str,
    say: Wording = AT_RUN,
) -> tuple[dict, set]:
    """``kwargs`` with their secret typed into `text`, and which of them are guarded.

    ``page`` is where the browser is, or how to ask: a callable is read only
    once the pair has been checked, so a refused call costs no WebDriver round
    trip. Raises `secrets.Refused`, which each surface re-raises as its own.
    """
    kwargs = dict(kwargs)
    reference = kwargs.pop(SECRET_ARG, None)
    if reference is None:
        return kwargs, set()
    if hasattr(reference, "model_dump"):
        reference = reference.model_dump(exclude_none=True)
    problems = check_pair({**kwargs, SECRET_ARG: reference}, say)
    if problems:
        raise secrets.Refused(problems[0])
    here = page() if callable(page) else page
    kwargs[SUPPLIES] = secrets.bind(catalogue, reference, here, tool=tool)
    if tool in READ_BACK_OFF:
        # The read must not HAPPEN for a bound value, not merely be redacted
        # afterwards.
        kwargs["read_back"] = False
    return kwargs, {SUPPLIES}


def forms_of(kwargs: dict, guarded: set) -> set:
    """Every spelling of every guarded value in ``kwargs``."""
    return hidden_forms(kwargs.get(name) for name in guarded)


def after(result: dict, guarded: set, hidden: set) -> dict:
    """``result`` as it may be shown.

    The fields a guarded argument's value comes back under are nulled where
    present, and every string is swept of ``hidden`` — however deep it sits and
    whatever it is called, because the named-field map is a list somebody has
    to remember to extend.
    """
    nulled = redacted_fields(guarded)
    shown = {k: (None if k in nulled else v) for k, v in result.items()}
    return scrub_values(shown, hidden) if hidden else shown


def safe_url(result: dict, hidden: set) -> str | None:
    """The page ``result`` landed on, unless the value reached it.

    A submitting write can land on `?q=<what was typed>`, and the scrubbed form
    of that is a URL that does not exist. Asked of the URL rather than by
    comparing it with its scrubbed form: a secret whose value is the marker
    scrubs to itself, so equality would call the credential URL safe.
    """
    url = result.get("url")
    return None if taints(url, hidden) else url
