"""The flow document: what a step may say, and what is refused at save time.

The whole point of validating here rather than at run time is that a flow which
fails at step nine has already half-filled a form. Every case below is one an
author can fix while they are still looking at the thing they wrote.
"""

import pytest

from kubed.selenium_flow.flows.document import InvalidFlow, validate

pytestmark = pytest.mark.unit


def flow(**overrides):
    document = {
        "description": "Log in",
        "steps": [
            {"tool": "navigate", "args": {"url": "https://example.test/login"}},
            {"tool": "write", "args": {"selector": {"css": "#email"}, "text": "a@b.c"}},
            {"tool": "interact", "args": {"action": "click", "selector": {"css": "button"}}},
        ],
    }
    document.update(overrides)
    return document


# ---- the happy path ---------------------------------------------------------


async def test_an_ordinary_flow_validates(step_schema_map):
    assert validate(flow(), step_schema_map)


async def test_the_real_tool_schemas_are_what_it_checks(step_schema_map):
    """If `write` gains a parameter, this map gains it with no edit here."""
    assert "selector" in step_schema_map["write"]["properties"]
    assert "text" in step_schema_map["write"]["properties"]


async def test_a_flow_tool_is_not_a_step(step_schema_map):
    """A step calls a browser action. `save_flow` is not one, and a map built
    from a tool listing would have offered it."""
    for name in ("save_flow", "delete_flow", "run_flow", "list_secrets"):
        assert name not in step_schema_map


async def test_session_id_is_not_a_step_parameter(step_schema_map):
    """The run supplies it. A step naming it would address someone else's
    browser, which is the one thing a caller must never do."""
    for name, schema in step_schema_map.items():
        assert "session_id" not in schema["properties"], name


async def test_lifecycle_tools_are_not_steps(step_schema_map):
    assert "open_session" not in step_schema_map
    assert "end_browser" not in step_schema_map


# ---- what a step may name ---------------------------------------------------


@pytest.mark.parametrize(
    ("refusal", "steps"),
    [
        # It would end the caller's browser, and it would bake the browser choice
        # into a document whose whole point is running on either.
        pytest.param(
            "not a step", [{"tool": "open_session", "args": {}}], id="a-flow-that-opens-its-own-browser-is-refused"
        ),
        # `text` is not `required` in write's schema — it can arrive as a binding
        # instead — so the validator has to know what the schema cannot say.
        pytest.param(
            "write needs 'text'",
            [{"tool": "write", "args": {"selector": {"css": "#a"}}}],
            id="a-missing-required-parameter-is-refused",
        ),
        # A schema cannot say "exactly one of xpath or css" without a oneOf, so
        # this was saving cleanly and failing at run time.
        pytest.param("needs an element", [{"tool": "extract", "args": {}}], id="a-step-with-no-element-is-refused-at-save"),
        # One selector names one element. A flow is YAML somebody may have written
        # by hand, so this is checked at save as well as by the model at run time —
        # a document that saves cleanly and fails later is the split save-time
        # validation exists to close.
        pytest.param(
            "not both",
            [{"tool": "extract", "args": {"selector": {"css": "a", "xpath": "//a"}}}],
            id="a-step-naming-both-selectors-is-refused-at-save",
        ),
        # A script is arbitrary code, and a bound secret inside one is an
        # exfiltration API with extra steps.
        pytest.param(
            "execute_script has no parameter 'secret'",
            [
                {
                    "tool": "execute_script",
                    "args": {"script": "x", "secret": {"name": "n", "key": "k"}},
                }
            ],
            id="a-secret-may-only-be-bound-into-write",
        ),
        # The leash is checked against the page the browser is on. A step that
        # navigates first would be checked against the page it is leaving.
        pytest.param(
            "may not also navigate",
            [
                {
                    "tool": "write",
                    "args": {
                        "selector": {"css": "#p"},
                        "url": "https://x.test/login",
                        "secret": {"name": "n", "key": "k"},
                    },
                }
            ],
            id="a-step-that-binds-a-secret-may-not-also-navigate",
        ),
        pytest.param(
            "supplied by the run",
            [{"tool": "navigate", "args": {"url": "x", "session_id": "s"}}],
            id="a-step-naming-session-id-is-refused",
        ),
        pytest.param(
            "wait_timeout should be integer",
            [{"tool": "extract", "args": {"selector": {"css": "h1"}, "wait_timeout": "soon"}}],
            id="a-wrong-type-is-caught-before-the-browser-sees-it",
        ),
        # In Python True is an int, so the obvious check accepts `clear: true`
        # where a timeout belongs.
        pytest.param(
            "wait_timeout should be integer",
            [{"tool": "extract", "args": {"selector": {"css": "h1"}, "wait_timeout": True}}],
            id="a-boolean-does-not-pass-as-an-integer",
        ),
    ],
)
async def test_a_step_that_cannot_run_is_refused_at_save(step_schema_map, refusal, steps):
    with pytest.raises(InvalidFlow, match=refusal):
        validate(flow(steps=steps), step_schema_map)


async def test_an_unknown_tool_names_the_ones_that_exist(step_schema_map):
    with pytest.raises(InvalidFlow, match="no tool called 'clcik'") as caught:
        validate(flow(steps=[{"tool": "clcik", "args": {}}]), step_schema_map)
    assert "navigate" in str(caught.value)


async def test_an_unknown_parameter_names_the_ones_the_tool_takes(step_schema_map):
    with pytest.raises(InvalidFlow, match="has no parameter 'xpaht'") as caught:
        validate(
            flow(steps=[{"tool": "extract", "args": {"xpaht": "//h1"}}]),
            step_schema_map,
        )
    assert "xpath" in str(caught.value)


async def test_an_action_that_needs_no_element_is_left_alone(step_schema_map):
    """press_key goes wherever focus is; screenshot captures the viewport."""
    assert validate(
        flow(steps=[{"tool": "press_key", "args": {"key": "enter"}}]),
        step_schema_map,
    )
    assert validate(flow(steps=[{"tool": "screenshot", "args": {}}]), step_schema_map)


# ---- structural references, because there is no templating ------------------


@pytest.mark.parametrize(
    ("parameters", "steps"),
    [
        pytest.param(
            {"type": "object", "properties": {"email": {"type": "string"}}},
            [{"tool": "write", "args": {"selector": {"css": "#email"}, "text": "${email}"}}],
            id="a-step-may-take-its-value-from-a-declared-parameter",
        ),
        # A step and a direct tool call are the same thing written twice: there is
        # no step-level key for a value, so nothing has to be kept in step with the
        # tool schemas. A parameter goes in the argument itself, and a secret is an
        # argument `write` declares.
        pytest.param(
            {"type": "object", "properties": {"email": {}}},
            [{"tool": "write", "args": {"selector": {"css": "#e"}, "text": "${email}"}}],
            id="a-steps-args-are-the-calls-arguments-with-no-exception",
        ),
        # A parameter is text, so a flow can vary where it goes as well as what it
        # types.
        pytest.param(
            {"type": "object", "properties": {"site": {}, "who": {}}},
            [
                {"tool": "navigate", "args": {"url": "${site}/login"}},
                {"tool": "write", "args": {"selector": {"css": "#u"}, "text": "${who}"}},
                {"tool": "extract", "args": {"selector": {"css": "[data-user=${who}]"}}},
            ],
            id="a-parameter-reaches-any-argument-of-any-action",
        ),
    ],
)
async def test_a_declared_parameter_may_be_referenced(step_schema_map, parameters, steps):
    assert validate(flow(parameters=parameters, steps=steps), step_schema_map)


@pytest.mark.parametrize(
    "steps",
    [
        # `$${` is a literal `${`. Without the escape there would be no way to
        # type one, and a page that genuinely wants `${x}` in a field is not exotic —
        # it is every templating tutorial on the internet.
        pytest.param(
            [{"tool": "write", "args": {"selector": {"css": "#e"}, "text": "$${notaparam}"}}],
            id="an-escaped-sigil-is-not-a-reference",
        ),
        # `write` requires text; supplying it from a secret is supplying it.
        pytest.param(
            [
                {
                    "tool": "write",
                    "args": {"selector": {"css": "#p"}, "secret": {"name": "n", "key": "password"}},
                }
            ],
            id="a-reference-satisfies-a-required-parameter",
        ),
    ],
)
async def test_a_value_from_a_secret_or_a_literal_saves(step_schema_map, steps):
    assert validate(flow(steps=steps), step_schema_map)


async def test_a_reference_to_an_undeclared_parameter_is_caught_at_save(
    step_schema_map,
):
    """The whole reason references are structural: this is checkable now, and a
    templated `{{emial}}` would not have been until step nine."""
    with pytest.raises(InvalidFlow, match="is not a parameter of this flow") as caught:
        validate(
            flow(
                parameters={"type": "object", "properties": {"email": {}}},
                steps=[
                    {
                        "tool": "write",
                        "args": {"selector": {"css": "#e"}, "text": "${emial}"},
                    }
                ],
            ),
            step_schema_map,
        )
    assert "email" in str(caught.value)


async def test_a_reference_is_found_wherever_it_sits(step_schema_map):
    """Not only a whole argument, and not only a top-level string. A selector
    built from a parameter, or one inside a list, is exactly where an author
    will put one — and a reference that validated nowhere would fail at step
    nine, which is the split save-time checking exists to close."""
    with pytest.raises(InvalidFlow, match=r"\$\{ident\} is not a parameter"):
        validate(
            flow(
                parameters={"type": "object", "properties": {"email": {}}},
                steps=[{"tool": "extract", "args": {"selector": {"css": "#row-${ident} .total"}}}],
            ),
            step_schema_map,
        )


@pytest.mark.parametrize(
    ("refusal", "steps"),
    [
        pytest.param(
            "names no parameter",
            [{"tool": "write", "args": {"selector": {"css": "#e"}, "text": "${}"}}],
            id="an-empty-reference-says-what-to-write",
        ),
        # Two fields, never one dotted string — a Kubernetes key is routinely
        # `tls.crt`, so there is no split point to find.
        pytest.param(
            "needs a key",
            [
                {
                    "tool": "write",
                    "args": {"selector": {"css": "#p"}, "secret": {"name": "nextcloud"}},
                }
            ],
            id="a-secret-reference-needs-a-name-and-a-key",
        ),
        pytest.param(
            "unknown step key",
            [{"tool": "navigate", "args": {"url": "x"}, "retries": 3}],
            id="an-unknown-step-key-is-refused",
        ),
        pytest.param(
            "already used",
            [
                {"tool": "navigate", "args": {"url": "a"}, "id": "go"},
                {"tool": "navigate", "args": {"url": "b"}, "id": "go"},
            ],
            id="step-ids-must-be-unique-because-they-name-a-step",
        ),
        pytest.param(
            r"step 1 \(sign-in\)",
            [{"tool": "write", "args": {"selector": {"css": "#a"}}, "id": "sign-in"}],
            id="an-id-puts-itself-in-the-error-so-you-can-find-the-step",
        ),
    ],
)
async def test_a_reference_or_step_key_that_cannot_work_is_refused(step_schema_map, refusal, steps):
    with pytest.raises(InvalidFlow, match=refusal):
        validate(flow(steps=steps), step_schema_map)


async def test_a_secret_may_not_be_named_by_a_parameter(step_schema_map):
    """Substituting here would leak nothing — the reference names a parameter,
    not a credential. It is refused because a caller choosing *which* secret
    gets typed is a decision a flow's parameters may not make."""
    with pytest.raises(InvalidFlow, match="cannot be named by a parameter"):
        validate(
            flow(
                parameters={"type": "object", "properties": {"which": {}}},
                steps=[
                    {
                        "tool": "write",
                        "args": {
                            "selector": {"css": "#p"},
                            "secret": {"name": "${which}", "key": "password"},
                        },
                    }
                ],
            ),
            step_schema_map,
        )


async def test_a_value_given_twice_is_refused_rather_than_resolved(step_schema_map):
    """`text` beside a `secret` says two things that cannot both be true, and
    picking one would be a guess that is wrong silently."""
    with pytest.raises(InvalidFlow, match="one value, one place"):
        validate(
            flow(
                parameters={"type": "object", "properties": {"email": {}}},
                steps=[
                    {
                        "tool": "write",
                        "args": {
                            "selector": {"css": "#e"},
                            "text": "literal",
                            "secret": {"name": "n", "key": "password"},
                        },
                    }
                ],
            ),
            step_schema_map,
        )


async def test_on_error_takes_two_values(step_schema_map):
    assert validate(
        flow(steps=[{"tool": "navigate", "args": {"url": "x"}, "onError": "continue"}]),
        step_schema_map,
    )
    with pytest.raises(InvalidFlow, match="onError is 'retry'"):
        validate(
            flow(steps=[{"tool": "navigate", "args": {"url": "x"}, "onError": "retry"}]),
            step_schema_map,
        )


# ---- the document ------------------------------------------------------------


@pytest.mark.parametrize(
    ("refusal", "steps"),
    [
        pytest.param("non-empty", [], id="a-flow-with-no-steps-is-refused"),
        # It was specified as a step key and nothing could honour it: a Selenium
        # call blocks, and the actions that can be bounded already take wait_timeout
        # in their own params. A key that parses and then does nothing is worse than
        # one that is refused.
        pytest.param(
            "unknown step key timeout",
            [{"tool": "navigate", "args": {"url": "x"}, "timeout": 5}],
            id="a-step-timeout-is-refused-rather-than-ignored",
        ),
        # `frame` is not simply selector-optional: switch needs xpath, css or
        # index, and `actions.frame` refuses that call at run time — so treating the
        # whole action as optional put validation and execution back out of step.
        pytest.param(
            "needs an element",
            [{"tool": "frame", "args": {"action": "switch"}}],
            id="switching-to-a-frame-still-needs-a-target",
        ),
        # The schema permits null so a secret can supply the value instead, so
        # "present" is not the question — `Actions.write` would type the string
        # "None" into the field.
        pytest.param(
            "write needs 'text'",
            [{"tool": "write", "args": {"selector": {"css": "#p"}, "text": None}}],
            id="a-null-text-is-not-a-supplied-value",
        ),
    ],
)
async def test_a_document_that_cannot_run_is_refused(step_schema_map, refusal, steps):
    with pytest.raises(InvalidFlow, match=refusal):
        validate(flow(steps=steps), step_schema_map)


async def test_a_required_parameter_must_be_declared(step_schema_map):
    with pytest.raises(InvalidFlow, match="required but not declared"):
        validate(
            flow(parameters={"type": "object", "properties": {}, "required": ["email"]}),
            step_schema_map,
        )


async def test_every_problem_is_reported_at_once(step_schema_map):
    """An agent fixing a twelve-step flow one error per round trip is exactly
    what collecting avoids."""
    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                steps=[
                    {"tool": "nope", "args": {}},
                    {"tool": "write", "args": {"selector": {"css": "#a"}}},
                    {"tool": "extract", "args": {"bogus": 1}},
                ]
            ),
            step_schema_map,
        )
    assert len(caught.value.problems) >= 3


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param(
            [{"tool": "extract", "args": {"selector": {"css": "h1"}, "wait_timeout": 5}}],
            id="wait-timeout-is-the-per-step-bound-and-is-accepted",
        ),
        pytest.param(
            [{"tool": "frame", "args": {"action": "switch", "index": 0}}], id="switching-by-index-needs-no-selector"
        ),
    ],
)
async def test_a_step_the_tool_can_run_saves(step_schema_map, steps):
    assert validate(flow(steps=steps), step_schema_map)


@pytest.mark.parametrize("action", ["parent", "default"])
async def test_leaving_a_frame_needs_nothing(step_schema_map, action):
    assert validate(
        flow(steps=[{"tool": "frame", "args": {"action": action}}]), step_schema_map
    )


# ---- the skill's own examples ----------------------------------------------


def _documented():
    """Every flow document and step written in the skill, with where it is.

    A ```json block holding `steps` is a document (unless it is a run report,
    which has a `status`); one holding `tool` is a single step. Everything else
    — a secret listing, say — is not a flow and is left alone.
    """
    import json
    import re

    from kubed.selenium_flow.mcp import skill as skill_module

    root = skill_module.skill_path()
    found = []
    for path in sorted(root.rglob("*.md")):
        for block in re.findall(r"```json\n(.*?)```", path.read_text(), re.DOTALL):
            data = json.loads(block)
            where = path.relative_to(root).as_posix()
            if "steps" in data and "status" not in data:
                found.append((where, data))
            elif "tool" in data:
                found.append((where, {"steps": [data]}))
    return found


def test_every_json_example_in_the_skill_parses():
    """Otherwise a broken example is skipped by the check below rather than
    failing it — the silent version of the bug this exists to catch."""
    import json
    import re

    from kubed.selenium_flow.mcp import skill as skill_module

    for path in skill_module.skill_path().rglob("*.md"):
        for block in re.findall(r"```json\n(.*?)```", path.read_text(), re.DOTALL):
            json.loads(block)


def test_the_skill_documents_flows_in_both_references():
    """Guards the regex as much as the docs: finding nothing would pass the
    validation test vacuously."""
    where = {path for path, _ in _documented()}
    assert {"references/FLOWS.md", "references/SECRETS.md"} <= where


@pytest.mark.parametrize(
    "where,document", _documented(), ids=lambda v: v if isinstance(v, str) else ""
)
async def test_every_flow_the_skill_teaches_would_save(step_schema_map, where, document):
    """Checked against the live tool schemas, the same way `save_flow` checks.

    Every recurring defect in the flows and secrets work was documentation that
    taught a shape the code refused — a withdrawn `valueFrom`, a `{text: ...}`
    level that was never there, prompts describing a step key that did not
    exist. A skill is documentation an agent *acts on*, so its examples are
    tested rather than trusted.
    """
    from kubed.selenium_flow.names import valid_name

    document = dict(document)
    name = document.pop("name", None)
    if name is not None:
        valid_name(name, "flow name")
    assert validate(document, step_schema_map), where


async def test_a_document_with_integer_keys_is_refused_with_every_problem(step_schema_map):
    """The save path of the same YAML hazard: every key a refusal lists is made a
    string first, so a malformed document is described rather than crashing."""
    from kubed.selenium_flow.flows.document import InvalidFlow

    document = {
        "steps": [
            {
                "tool": "write",
                7: "stray",
                "args": {
                    "selector": {"css": "#p"},
                    "secret": {"name": "n", "key": "k", 1: "x"},
                },
            }
        ]
    }
    with pytest.raises(InvalidFlow) as caught:
        validate(document, step_schema_map)
    text = str(caught.value)
    assert "unknown step key 7" in text
    assert "does not take 1" in text


async def test_a_write_only_parameter_is_refused_rather_than_ignored(
    step_schema_map,
):
    """It used to mean "supplied but not returned" and nothing implements that
    now. Accepting a familiar JSON-Schema marker that silently does nothing is
    worse than not having it: an author marks a password `writeOnly`, believes
    it is hidden, and reads it back out of the report."""
    with pytest.raises(InvalidFlow, match="writeOnly") as caught:
        validate(
            flow(
                parameters={
                    "type": "object",
                    "properties": {"password": {"type": "string", "writeOnly": True}},
                },
                steps=[{"tool": "write", "args": {"selector": {"css": "#p"}, "text": "${password}"}}],
            ),
            step_schema_map,
        )
    assert "secret" in str(caught.value), "it should say what to use instead"


async def test_a_whole_reference_is_not_type_checked_against_the_schema(
    step_schema_map,
):
    """`wait_timeout` wants an integer and `"${secs}"` is a string, but at run
    time it is whatever the caller passed — `substitute` preserves the type of a
    value whose argument is exactly one reference. Checking the placeholder
    refused a flow that would have run perfectly."""
    assert validate(
        flow(
            parameters={
                "type": "object",
                "properties": {"secs": {"type": "integer"}},
            },
            steps=[{"tool": "extract", "args": {"selector": {"css": "#a"}, "wait_timeout": "${secs}"}}],
        ),
        step_schema_map,
    )


async def test_a_reference_inside_a_longer_string_is_still_type_checked(
    step_schema_map,
):
    """Interpolation always produces a string, so the exemption is only for an
    argument that is *exactly* one reference."""
    with pytest.raises(InvalidFlow, match="wait_timeout should be integer"):
        validate(
            flow(
                parameters={"type": "object", "properties": {"secs": {}}},
                steps=[
                    {"tool": "extract", "args": {"selector": {"css": "#a"}, "wait_timeout": "${secs}s"}}
                ],
            ),
            step_schema_map,
        )


async def test_a_template_literal_in_a_script_says_how_to_write_it(step_schema_map):
    """The commonest way to reach this message is to have meant no reference at
    all. A `script` carrying a JavaScript template literal is refused here, and
    told only "is not a parameter of this flow" the author goes hunting for a
    parameter they never wanted — `substitute` already treats such a payload as
    ordinary text, so this is the one place that can say how to write it."""
    document = flow(steps=[
        {"tool": "execute_script", "id": "measure",
         "args": {"script": "return `${Math.round(n)} KB`"}},
    ])
    with pytest.raises(InvalidFlow) as raised:
        validate(document, step_schema_map)
    said = str(raised.value)
    assert "is not a parameter of this flow" in said
    assert "$${Math.round(n)}" in said, "it has to show the escape"


async def test_the_escape_it_suggests_actually_saves(step_schema_map):
    """The advice has to work, or it is worse than none."""
    document = flow(steps=[
        {"tool": "execute_script", "id": "measure",
         "args": {"script": "return `$${Math.round(n)} KB`"}},
    ])
    assert validate(document, step_schema_map)


# ---- a closed set is checked at save time ------------------------------------


async def test_an_action_outside_its_set_is_refused_at_save_time(step_schema_map):
    """`mouseover` used to save cleanly and fail at run time — the one kind of
    mistake this server otherwise catches before the browser sees it."""
    with pytest.raises(InvalidFlow, match=r"interact\.action is 'mouseover'") as refused:
        validate(
            flow(steps=[{"tool": "interact", "args": {"action": "mouseover", "selector": {"css": "a"}}}]),
            step_schema_map,
        )
    assert "hover" in str(refused.value), "the refusal should list what is valid"


async def test_a_value_in_the_set_is_accepted_whatever_its_case(step_schema_map):
    """The action layer lowercases, so a run accepts `Hover`. Refusing it at save
    time would make validation stricter than execution, and the two must agree."""
    assert validate(
        flow(steps=[{"tool": "interact", "args": {"action": "Hover", "selector": {"css": "a"}}}]),
        step_schema_map,
    )


async def test_a_whole_reference_is_not_checked_against_the_set(step_schema_map):
    document = flow(
        parameters={
            "type": "object",
            "properties": {"gesture": {"type": "string"}},
        },
        steps=[{"tool": "interact", "args": {"action": "${gesture}", "selector": {"css": "a"}}}],
    )
    assert validate(document, step_schema_map)


async def test_a_flow_that_saves_site_data_confirms_the_sign_in_first(step_schema_map):
    """The skill's rule, held by the one flow that saves: an `assert` between
    the sign-in click and `save_site_data`, never a save on faith."""
    from pathlib import Path

    import yaml

    path = Path(__file__).parent / "integration" / "flows" / "save-site-data.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert validate(document, step_schema_map)
    tools = [step["tool"] for step in document["steps"]]
    save = tools.index("save_site_data")
    click = max(i for i, t in enumerate(tools[:save]) if t == "interact")
    assert "assert" in tools[click + 1:save]


# ---- a document somebody wrote by hand (D8, D19, D20) -----------------------
#
# A flow is YAML anyone may have typed, so the shapes below are what the editor
# is for. A refusal is a 400 with a sentence the author can act on; anything
# else is a fault in our code reported as theirs, or worse a 500.


async def test_a_step_that_is_not_an_object_is_refused_by_number(step_schema_map):
    with pytest.raises(InvalidFlow) as caught:
        validate(flow(steps=[GOOD_STEP, "oops"]), step_schema_map)
    assert caught.value.problems == [
        "step 2: must be an object with a tool and its params"
    ]


@pytest.mark.parametrize("steps", ["navigate", {"a": "b"}, [], None])
async def test_steps_that_are_not_a_list_are_refused(step_schema_map, steps):
    with pytest.raises(InvalidFlow) as caught:
        validate(flow(steps=steps), step_schema_map)
    assert caught.value.problems == ["steps must be a non-empty list"]


async def test_parameters_that_are_a_list_are_refused(step_schema_map):
    with pytest.raises(InvalidFlow) as caught:
        validate(flow(parameters=["email"]), step_schema_map)
    assert caught.value.problems == ["parameters must be a JSON Schema object"]


@pytest.mark.parametrize("properties", [[], None, "email"])
async def test_properties_that_are_not_an_object_are_refused(step_schema_map, properties):
    with pytest.raises(InvalidFlow) as caught:
        validate(flow(parameters={"properties": properties}), step_schema_map)
    assert caught.value.problems == ["parameters.properties must be an object"]


async def test_an_integer_key_in_args_is_a_problem_not_a_crash(step_schema_map):
    """`args: {1: x, url: ...}` — YAML reads the key as an int (D19)."""
    document = flow(steps=[{"tool": "navigate", "args": {1: "x", "url": "https://a.test/"}}])
    with pytest.raises(InvalidFlow, match="navigate has no parameter 1"):
        validate(document, step_schema_map)


GOOD_STEP = {"tool": "navigate", "args": {"url": "https://example.test/"}}


async def test_an_id_that_is_a_list_is_refused_with_a_sentence(step_schema_map):
    document = flow(steps=[{**GOOD_STEP, "id": ["a"]}])
    with pytest.raises(InvalidFlow) as caught:
        validate(document, step_schema_map)
    (problem,) = caught.value.problems
    assert problem.startswith("step 1") and problem.endswith("id must be a str")


async def test_a_required_that_is_a_string_is_refused_with_a_sentence(step_schema_map):
    document = flow(
        parameters={"properties": {"email": {"type": "string"}}, "required": "email"}
    )
    with pytest.raises(InvalidFlow) as caught:
        validate(document, step_schema_map)
    assert caught.value.problems == ["parameters.required must be a list of names"]


async def test_parameters_written_without_properties_say_where_they_go(
    step_schema_map,
):
    """Found live: `parameters: {"pie": {...}}` passed as "no parameters", and
    `${pie}` was refused with "this flow declares none" - true, and no help."""
    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                parameters={"pie": {"type": "string"}},
                steps=[{"tool": "navigate", "args": {"url": "https://x.test/${pie}"}}],
            ),
            step_schema_map,
        )
    problems = caught.value.problems
    assert any(
        "pie sits where only JSON Schema keywords go" in p
        and '"properties": {"pie": {"type": "string"}}' in p
        for p in problems
    ), problems


async def test_a_schema_with_no_properties_is_still_no_parameters(step_schema_map):
    validate(
        flow(
            parameters={"type": "object", "description": "none needed"},
            steps=[{"tool": "navigate", "args": {"url": "https://x.test/"}}],
        ),
        step_schema_map,
    )


async def test_a_valid_schema_using_any_keyword_is_not_misread(step_schema_map):
    """The hint keys on names the steps use, not on a list of keywords that
    would refuse a valid schema using one it did not know (Copilot, #53)."""
    validate(
        flow(
            parameters={"$defs": {"name": {"type": "string"}}, "allOf": [], "x-ui": {}},
            steps=[{"tool": "navigate", "args": {"url": "https://x.test/"}}],
        ),
        step_schema_map,
    )


async def test_a_note_naming_a_schema_key_is_not_a_reference(step_schema_map):
    """Only `args` are substituted, so only `args` are evidence (Copilot, #53)."""
    validate(
        flow(
            parameters={"x-ui": {"order": []}},
            steps=[
                {
                    "tool": "navigate",
                    "note": "documents $${x-ui} and ${x-ui}",
                    "args": {"url": "https://x.test/"},
                }
            ],
        ),
        step_schema_map,
    )


async def test_an_integer_yaml_key_gets_the_hint_too(step_schema_map):
    """YAML makes `1:` an integer, which `${1}` names as text (Copilot, #53)."""
    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                parameters={1: {"type": "string"}},
                steps=[{"tool": "navigate", "args": {"url": "https://x.test/${1}"}}],
            ),
            step_schema_map,
        )
    assert any("where only JSON Schema keywords go" in p for p in caught.value.problems)


def _shape_problem(problems):
    """The one `parameters: ... sits where ...` problem, as JSON-free text."""
    (shape,) = [p for p in problems if "where only JSON Schema keywords go" in p]
    return shape


async def test_a_parameter_given_a_bare_type_name_is_refused(step_schema_map):
    """#118: `{"name": "string"}` with no step using it yet passed as "no
    parameters". A bare type name under a key no keyword has cannot mean
    anything else (Dr K, ruling 1)."""
    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                parameters={"name": "string"},
                steps=[{"tool": "navigate", "args": {"url": "https://x.test/"}}],
            ),
            step_schema_map,
        )
    assert '"properties": {"name": {"type": "string"}}' in _shape_problem(
        caught.value.problems
    )


async def test_every_bare_type_name_is_named_with_its_own_type(step_schema_map):
    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                parameters={"a": "string", "b": "number"},
                steps=[{"tool": "navigate", "args": {"url": "https://x.test/"}}],
            ),
            step_schema_map,
        )
    shape = _shape_problem(caught.value.problems)
    assert shape.startswith("parameters: a, b sit where")
    assert '"a": {"type": "string"}, "b": {"type": "number"}' in shape


async def test_strays_the_steps_name_are_all_shown_and_not_said_twice(
    step_schema_map,
):
    """Seen live on #118: the shape named only the first key, and each step
    said "this flow declares none" for the same names, pointing the other way."""
    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                parameters={
                    "expect": {"type": "string", "format": "email"},
                    "id": "integer",
                },
                steps=[
                    {"tool": "navigate", "args": {"url": "https://x.test/${id}"}},
                    {"tool": "navigate", "args": {"url": "https://x.test/${expect}"}},
                ],
            ),
            step_schema_map,
        )
    problems = caught.value.problems
    shape = _shape_problem(problems)
    assert (
        '"properties": {"expect": {"type": "string", "format": "email"}, '
        '"id": {"type": "integer"}}' in shape
    )
    assert not any("is not a parameter" in p for p in problems), problems


async def test_a_keyword_whose_value_is_a_type_name_is_not_misread(step_schema_map):
    validate(
        flow(
            parameters={
                "type": "object",
                "title": "string",
                "properties": {"x": {"type": "string"}},
            },
            steps=[{"tool": "navigate", "args": {"url": "https://x.test/${x}"}}],
        ),
        step_schema_map,
    )


async def test_a_bare_type_name_beside_properties_is_merged_into_them(
    step_schema_map,
):
    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                parameters={
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "name": "string",
                },
                steps=[{"tool": "navigate", "args": {"url": "https://x.test/${x}"}}],
            ),
            step_schema_map,
        )
    shape = _shape_problem(caught.value.problems)
    assert shape.startswith("parameters: name sits")
    assert '"properties": {"x": {"type": "string"}, "name": {"type": "string"}}' in shape


async def test_a_yaml_date_in_the_shape_is_still_a_refusal(step_schema_map):
    """YAML makes `default: 2024-01-01` a date, which the example quotes as
    written: it has to be said, not raised as a 500."""
    import datetime

    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                parameters={
                    "properties": {
                        "when": {"type": "string", "default": datetime.date(2024, 1, 1)}
                    },
                    "required": ["when"],
                    "name": "string",
                },
                steps=[{"tool": "navigate", "args": {"url": "https://x.test/${when}"}}],
            ),
            step_schema_map,
        )
    shape = _shape_problem(caught.value.problems)
    assert '"default": "2024-01-01"' in shape
    assert '"required": ["when"]' in shape


async def test_a_declared_parameter_keeps_its_schema_in_the_shape(step_schema_map):
    with pytest.raises(InvalidFlow) as caught:
        validate(
            flow(
                parameters={
                    "properties": {"name": {"type": "string", "format": "email"}},
                    "name": "string",
                },
                steps=[{"tool": "navigate", "args": {"url": "https://x.test/${name}"}}],
            ),
            step_schema_map,
        )
    assert '"name": {"type": "string", "format": "email"}' in _shape_problem(
        caught.value.problems
    )


# ---- where a flow applies ---------------------------------------------------


async def test_places_are_checked_with_every_other_problem(step_schema_map):
    document = flow(sites=[])
    document["steps"].append({"tool": "no_such_tool", "args": {}})
    with pytest.raises(InvalidFlow) as caught:
        validate(document, step_schema_map)
    text = "\n".join(caught.value.problems)
    assert "sites is empty: leave it out to match any site" in text
    assert "no_such_tool" in text


async def test_a_flow_with_places_is_valid(step_schema_map):
    document = flow(sites=["localhost:8080", "*.example.com"], paths=["/login", "/apps/*/settings"])
    assert validate(document, step_schema_map)


async def test_a_keep_file_step_is_refused_at_save(step_schema_map):
    """`keep_file` is a files tool, not a browser action: a flow cannot name it."""
    document = flow(steps=[{"tool": "keep_file", "args": {"uri": "session://downloads/a.txt"}}])
    with pytest.raises(InvalidFlow, match="keep_file") as caught:
        validate(document, step_schema_map)
    assert "step 1" in caught.value.problems[0]
