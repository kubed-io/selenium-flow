"""One bind rule, three places that apply it.

Saving a flow, running one and a single bound write each refuse a secret
beside a literal `text` or a `url`. They raise their own exception types, in
the words each has always said, and `binding` holds those words: the same bad
arguments are refused by all three, each with its own sentence from there.
"""

import pytest

from kubed.selenium_flow import binding, errors, secrets
from kubed.selenium_flow.flows.document import InvalidFlow, validate
from kubed.selenium_flow.flows.engine import resolve_step
from kubed.selenium_flow.flows.template import FlowError

from .test_flowdoc import step_schema_map  # noqa: F401 - a fixture

pytestmark = pytest.mark.unit

REFERENCE = {"name": "nextcloud", "key": "password"}

BAD = {
    "twice": {"selector": {"css": "#p"}, "text": "literal", "secret": REFERENCE},
    "navigates": {
        "selector": {"css": "#p"},
        "url": "https://x.test/login",
        "secret": REFERENCE,
    },
}


class Nobody:
    """A catalogue, actions and workspaces that fail the test if a bind gets far
    enough to touch any of them."""

    def __getattr__(self, name):
        raise AssertionError(f"a refused bind reached {name}")


def at_save(args, schemas):
    with pytest.raises(InvalidFlow) as caught:
        validate({"steps": [{"tool": "write", "args": args}]}, schemas)
    return caught.value


def at_run(args, schemas):
    def page():
        raise AssertionError("a refused bind read the page")

    with pytest.raises(FlowError) as caught:
        resolve_step({"tool": "write", "args": args}, {}, Nobody(), page)
    return caught.value


def direct(args, schemas):
    class FakeWorkspaces:
        def resolve(self, name):
            return "browser-1"

    with pytest.raises(secrets.Refused) as caught:
        secrets.perform_write(Nobody(), Nobody(), FakeWorkspaces(), "s", args)
    return caught.value


@pytest.mark.parametrize("rule", sorted(BAD))
@pytest.mark.parametrize(
    "entry, wording",
    [(at_save, binding.AT_SAVE), (at_run, binding.AT_RUN), (direct, binding.DIRECT)],
    ids=["save", "run", "direct"],
)
async def test_every_entry_point_refuses_the_same_arguments_with_the_rules_words(
    rule, entry, wording, step_schema_map  # noqa: F811 - the fixture
):
    refused = entry(dict(BAD[rule]), step_schema_map)
    assert getattr(wording, rule) in str(refused)
    assert errors.status_for(refused) == 400


def test_a_direct_writes_result_sweeps_keys_with_the_runs_scrub():
    """`after` is what a single bound write shows, and it sweeps a key the way
    a run's report does: one scrub, so the two cannot disagree."""
    hidden = binding.forms_of({"text": "hunter2"}, {"text"})
    shown = binding.after(
        {"value": None, "url": "https://x.test/", "seen": {"hunter2": "hunter2"}},
        {"text"},
        hidden,
    )
    assert shown == {
        "value": None, "url": "https://x.test/", "seen": {"<hidden>": "<hidden>"},
    }
