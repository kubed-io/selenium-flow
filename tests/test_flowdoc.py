"""The flow document: what a step may say, and what is refused at save time.

The whole point of validating here rather than at run time is that a flow which
fails at step nine has already half-filled a form. Every case below is one an
author can fix while they are still looking at the thing they wrote.
"""

import pytest

from kubed.selenium_flow.flowdoc import InvalidFlow, step_schemas, validate

pytestmark = pytest.mark.unit


@pytest.fixture
async def step_schema_map(server):
    """The real tool schemas, built the way production builds them.

    From ENDPOINTS rather than from a listing — a listing is shaped per request
    and, since flows gained tools of their own, would have offered `save_flow`
    as a valid step. `flowrun.RUNNABLE` refuses that at run time, so a looser
    map here would have let validation and execution disagree.
    """
    from kubed.selenium_flow.routes import ENDPOINTS

    tools = {}
    for name in sorted(set(ENDPOINTS.values())):
        full = await server.mcp.get_tool(name)
        tools[name] = full.parameters or {}
    return step_schemas(tools)


def flow(**overrides):
    document = {
        "description": "Log in",
        "steps": [
            {"tool": "navigate", "params": {"url": "https://example.test/login"}},
            {"tool": "write", "params": {"css": "#email", "text": "a@b.c"}},
            {"tool": "interact", "params": {"action": "click", "css": "button"}},
        ],
    }
    document.update(overrides)
    return document


# ---- the happy path ---------------------------------------------------------


async def test_an_ordinary_flow_validates(step_schema_map):
    assert validate(flow(), step_schema_map)


async def test_the_real_tool_schemas_are_what_it_checks(step_schema_map):
    """If `write` gains a parameter, this map gains it with no edit here."""
    assert "css" in step_schema_map["write"]["properties"]
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


async def test_a_flow_that_opens_its_own_browser_is_refused(step_schema_map):
    """It would end the caller's browser, and it would bake the browser choice
    into a document whose whole point is running on either."""
    with pytest.raises(InvalidFlow, match="not a step"):
        validate(flow(steps=[{"tool": "open_session", "params": {}}]), step_schema_map)


async def test_an_unknown_tool_names_the_ones_that_exist(step_schema_map):
    with pytest.raises(InvalidFlow, match="no tool called 'clcik'") as caught:
        validate(flow(steps=[{"tool": "clcik", "params": {}}]), step_schema_map)
    assert "navigate" in str(caught.value)


async def test_an_unknown_parameter_names_the_ones_the_tool_takes(step_schema_map):
    with pytest.raises(InvalidFlow, match="has no parameter 'xpaht'") as caught:
        validate(
            flow(steps=[{"tool": "extract", "params": {"xpaht": "//h1"}}]),
            step_schema_map,
        )
    assert "xpath" in str(caught.value)


async def test_a_missing_required_parameter_is_refused(step_schema_map):
    """`text` is not `required` in write's schema — it can arrive as a binding
    instead — so the validator has to know what the schema cannot say."""
    with pytest.raises(InvalidFlow, match="write needs 'text'"):
        validate(
            flow(steps=[{"tool": "write", "params": {"css": "#a"}}]), step_schema_map
        )


async def test_a_binding_satisfies_what_the_schema_cannot_demand(step_schema_map):
    assert validate(
        flow(
            steps=[
                {
                    "tool": "write",
                    "params": {"css": "#p", "value_from": {"secret": {"name": "n", "key": "password"}}},
                }
            ]
        ),
        step_schema_map,
    )


async def test_a_step_with_no_element_is_refused_at_save(step_schema_map):
    """A schema cannot say "exactly one of xpath or css" without a oneOf, so
    this was saving cleanly and failing at run time."""
    with pytest.raises(InvalidFlow, match="needs an element"):
        validate(
            flow(steps=[{"tool": "extract", "params": {}}]), step_schema_map
        )


async def test_a_step_naming_both_selectors_is_refused_at_save(step_schema_map):
    with pytest.raises(InvalidFlow, match="not both"):
        validate(
            flow(steps=[{"tool": "extract", "params": {"css": "a", "xpath": "//a"}}]),
            step_schema_map,
        )


async def test_an_action_that_needs_no_element_is_left_alone(step_schema_map):
    """press_key goes wherever focus is; screenshot captures the viewport."""
    assert validate(
        flow(steps=[{"tool": "press_key", "params": {"key": "enter"}}]),
        step_schema_map,
    )
    assert validate(flow(steps=[{"tool": "screenshot", "params": {}}]), step_schema_map)


async def test_a_secret_may_only_be_bound_into_write(step_schema_map):
    """A script is arbitrary code, and a bound secret inside one is an
    exfiltration API with extra steps (§F1.28)."""
    # Enforced by the parameter simply not existing on other actions, which is
    # stronger than a rule about it: there is nowhere to put one.
    with pytest.raises(InvalidFlow, match="execute_script does not take value_from"):
        validate(
            flow(
                steps=[
                    {
                        "tool": "execute_script",
                        "params": {"script": "x", "value_from": {"secret": {"name": "n", "key": "k"}}},
                    }
                ]
            ),
            step_schema_map,
        )


async def test_a_step_that_binds_a_secret_may_not_also_navigate(step_schema_map):
    """The leash is checked against the page the browser is on. A step that
    navigates first would be checked against the page it is leaving."""
    with pytest.raises(InvalidFlow, match="may not also navigate"):
        validate(
            flow(
                steps=[
                    {
                        "tool": "write",
                        "params": {"css": "#p", "url": "https://x.test/login", "value_from": {"secret": {"name": "n", "key": "k"}}},
                    }
                ]
            ),
            step_schema_map,
        )


async def test_a_step_naming_session_id_is_refused(step_schema_map):
    with pytest.raises(InvalidFlow, match="supplied by the run"):
        validate(
            flow(steps=[{"tool": "navigate", "params": {"url": "x", "session_id": "s"}}]),
            step_schema_map,
        )


async def test_a_wrong_type_is_caught_before_the_browser_sees_it(step_schema_map):
    with pytest.raises(InvalidFlow, match="wait_timeout should be integer"):
        validate(
            flow(
                steps=[
                    {"tool": "extract", "params": {"css": "h1", "wait_timeout": "soon"}}
                ]
            ),
            step_schema_map,
        )


async def test_a_boolean_does_not_pass_as_an_integer(step_schema_map):
    """In Python True is an int, so the obvious check accepts `clear: true`
    where a timeout belongs."""
    with pytest.raises(InvalidFlow, match="wait_timeout should be integer"):
        validate(
            flow(steps=[{"tool": "extract", "params": {"css": "h1", "wait_timeout": True}}]),
            step_schema_map,
        )


# ---- structural references, because there is no templating ------------------


async def test_a_step_may_take_a_value_from_a_declared_parameter(step_schema_map):
    assert validate(
        flow(
            parameters={"type": "object", "properties": {"email": {"type": "string"}}},
            steps=[
                {
                    "tool": "write",
                    "params": {"css": "#email", "value_from": {"param": "email"}},
                }
            ],
        ),
        step_schema_map,
    )


async def test_a_step_may_take_a_value_from_a_secret(step_schema_map):
    assert validate(
        flow(
            steps=[
                {
                    "tool": "write",
                    "params": {"css": "#password", "value_from": {"secret": {"name": "nextcloud", "key": "password"}}},
                }
            ]
        ),
        step_schema_map,
    )


async def test_a_reference_to_an_undeclared_parameter_is_caught_at_save(
    step_schema_map,
):
    """The whole reason references are structural: this is checkable now, and a
    templated `{{emial}}` would not have been until step nine."""
    with pytest.raises(InvalidFlow, match="which this flow does not declare") as caught:
        validate(
            flow(
                parameters={"type": "object", "properties": {"email": {}}},
                steps=[
                    {
                        "tool": "write",
                        "params": {"css": "#e", "value_from": {"param": "emial"}},
                    }
                ],
            ),
            step_schema_map,
        )
    assert "email" in str(caught.value)


async def test_a_reference_naming_two_sources_is_refused(step_schema_map):
    with pytest.raises(InvalidFlow, match="give exactly one source"):
        validate(
            flow(
                parameters={"type": "object", "properties": {"email": {}}},
                steps=[
                    {
                        "tool": "write",
                        "params": {
                            "css": "#e",
                            "value_from": {
                                "param": "email",
                                "secret": {"name": "n", "key": "k"},
                            },
                        },
                    }
                ],
            ),
            step_schema_map,
        )


async def test_a_reference_naming_no_source_is_refused(step_schema_map):
    with pytest.raises(InvalidFlow, match="names no source"):
        validate(
            flow(
                steps=[
                    {
                        "tool": "write",
                        "params": {"css": "#e", "value_from": {}},
                    }
                ]
            ),
            step_schema_map,
        )


async def test_a_secret_reference_needs_a_name_and_a_key(step_schema_map):
    """Two fields, never one dotted string — a Kubernetes key is routinely
    `tls.crt`, so there is no split point to find."""
    with pytest.raises(InvalidFlow, match="needs a key"):
        validate(
            flow(
                steps=[
                    {
                        "tool": "write",
                        "params": {"css": "#p", "value_from": {"secret": {"name": "nextcloud"}}},
                    }
                ]
            ),
            step_schema_map,
        )


async def test_a_value_given_twice_is_refused_rather_than_resolved(step_schema_map):
    with pytest.raises(InvalidFlow, match="one value, one place"):
        validate(
            flow(
                parameters={"type": "object", "properties": {"email": {}}},
                steps=[
                    {
                        "tool": "write",
                        "params": {"css": "#e", "text": "literal", "value_from": {"param": "email"}},
                    }
                ],
            ),
            step_schema_map,
        )


async def test_a_reference_satisfies_a_required_parameter(step_schema_map):
    """`write` requires text; supplying it from a secret is supplying it."""
    assert validate(
        flow(
            steps=[
                {
                    "tool": "write",
                    "params": {"css": "#p", "value_from": {"secret": {"name": "n", "key": "password"}}},
                }
            ]
        ),
        step_schema_map,
    )


async def test_value_from_is_a_parameter_like_any_other(step_schema_map):
    """A step's params ARE the call's arguments, with no exception — so a step
    and a direct tool call are the same thing written twice. Which argument
    value_from fills is the action's own business, the way Kubernetes never
    repeats an env var's name inside its valueFrom."""
    assert validate(
        flow(
            parameters={"type": "object", "properties": {"email": {}}},
            steps=[
                {
                    "tool": "write",
                    "params": {"css": "#e", "value_from": {"param": "email"}},
                }
            ],
        ),
        step_schema_map,
    )


async def test_only_the_actions_that_offer_it_take_value_from(step_schema_map):
    """It is a real parameter, so an action that does not declare one does not
    have it — and the refusal names the ones that do."""
    with pytest.raises(InvalidFlow, match="does not take value_from") as caught:
        validate(
            flow(
                parameters={"type": "object", "properties": {"site": {}}},
                steps=[
                    {"tool": "navigate", "params": {"value_from": {"param": "site"}}}
                ],
            ),
            step_schema_map,
        )
    assert "write" in str(caught.value)


# ---- the step's own keys ----------------------------------------------------


async def test_an_unknown_step_key_is_refused(step_schema_map):
    with pytest.raises(InvalidFlow, match="unknown step key"):
        validate(
            flow(steps=[{"tool": "navigate", "params": {"url": "x"}, "retries": 3}]),
            step_schema_map,
        )


async def test_on_error_takes_two_values(step_schema_map):
    assert validate(
        flow(steps=[{"tool": "navigate", "params": {"url": "x"}, "onError": "continue"}]),
        step_schema_map,
    )
    with pytest.raises(InvalidFlow, match="onError is 'retry'"):
        validate(
            flow(steps=[{"tool": "navigate", "params": {"url": "x"}, "onError": "retry"}]),
            step_schema_map,
        )


async def test_step_ids_must_be_unique_because_they_name_a_step(step_schema_map):
    with pytest.raises(InvalidFlow, match="already used"):
        validate(
            flow(
                steps=[
                    {"tool": "navigate", "params": {"url": "a"}, "id": "go"},
                    {"tool": "navigate", "params": {"url": "b"}, "id": "go"},
                ]
            ),
            step_schema_map,
        )


async def test_an_id_puts_itself_in_the_error_so_you_can_find_the_step(
    step_schema_map,
):
    with pytest.raises(InvalidFlow, match=r"step 1 \(sign-in\)"):
        validate(
            flow(steps=[{"tool": "write", "params": {"css": "#a"}, "id": "sign-in"}]),
            step_schema_map,
        )


# ---- the document ------------------------------------------------------------


async def test_a_flow_with_no_steps_is_refused(step_schema_map):
    with pytest.raises(InvalidFlow, match="non-empty"):
        validate(flow(steps=[]), step_schema_map)


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
                    {"tool": "nope", "params": {}},
                    {"tool": "write", "params": {"css": "#a"}},
                    {"tool": "extract", "params": {"bogus": 1}},
                ]
            ),
            step_schema_map,
        )
    assert len(caught.value.problems) >= 3


async def test_a_step_timeout_is_refused_rather_than_ignored(step_schema_map):
    """It was specified as a step key and nothing could honour it: a Selenium
    call blocks, and the actions that can be bounded already take wait_timeout
    in their own params. A key that parses and then does nothing is worse than
    one that is refused."""
    with pytest.raises(InvalidFlow, match="unknown step key timeout"):
        validate(
            flow(steps=[{"tool": "navigate", "params": {"url": "x"}, "timeout": 5}]),
            step_schema_map,
        )


async def test_wait_timeout_is_the_per_step_bound_and_is_accepted(step_schema_map):
    assert validate(
        flow(steps=[{"tool": "extract", "params": {"css": "h1", "wait_timeout": 5}}]),
        step_schema_map,
    )


async def test_switching_to_a_frame_still_needs_a_target(step_schema_map):
    """`frame` is not simply selector-optional: switch needs xpath, css or
    index, and `actions.frame` refuses that call at run time — so treating the
    whole action as optional put validation and execution back out of step."""
    with pytest.raises(InvalidFlow, match="needs an element"):
        validate(
            flow(steps=[{"tool": "frame", "params": {"action": "switch"}}]),
            step_schema_map,
        )


async def test_switching_by_index_needs_no_selector(step_schema_map):
    assert validate(
        flow(steps=[{"tool": "frame", "params": {"action": "switch", "index": 0}}]),
        step_schema_map,
    )


@pytest.mark.parametrize("action", ["parent", "default"])
async def test_leaving_a_frame_needs_nothing(step_schema_map, action):
    assert validate(
        flow(steps=[{"tool": "frame", "params": {"action": action}}]), step_schema_map
    )


async def test_a_null_text_is_not_a_supplied_value(step_schema_map):
    """The schema permits null so value_from can supply it instead, so
    "present" is not the question — `Actions.write` would type the string
    "None" into the field."""
    with pytest.raises(InvalidFlow, match="write needs 'text'"):
        validate(
            flow(steps=[{"tool": "write", "params": {"css": "#p", "text": None}}]),
            step_schema_map,
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

    from kubed.selenium_flow import skill as skill_module

    root = skill_module.skill_path()
    found = []
    for path in sorted(root.rglob("*.md")):
        for block in re.findall(r"```json\n(.*?)```", path.read_text(), re.S):
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

    from kubed.selenium_flow import skill as skill_module

    for path in skill_module.skill_path().rglob("*.md"):
        for block in re.findall(r"```json\n(.*?)```", path.read_text(), re.S):
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
    from kubed.selenium_flow.flows import valid_name

    document = dict(document)
    name = document.pop("name", None)
    if name is not None:
        valid_name(name, "flow name")
    assert validate(document, step_schema_map), where


async def test_a_document_with_integer_keys_is_refused_with_every_problem(step_schema_map):
    """The save path of the same YAML hazard: every key a refusal lists is made a
    string first, so a malformed document is described rather than crashing."""
    from kubed.selenium_flow.flowdoc import InvalidFlow

    document = {
        "steps": [
            {
                "tool": "write",
                7: "stray",
                "params": {
                    "css": "#p",
                    "value_from": {"secret": {"name": "n", "key": "k", 1: "x"}},
                },
            }
        ]
    }
    with pytest.raises(InvalidFlow) as caught:
        validate(document, step_schema_map)
    text = str(caught.value)
    assert "unknown step key 7" in text
    assert "does not take 1" in text
