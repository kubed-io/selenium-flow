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
    """The real tool schemas, so these tests cannot drift from the tools."""
    tools = {}
    for tool in await server.mcp.list_tools():
        full = await server.mcp.get_tool(tool.name)
        tools[tool.name] = full.parameters or {}
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
    assert "text" in step_schema_map["write"]["required"]


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
    with pytest.raises(InvalidFlow, match="requires 'text'"):
        validate(
            flow(steps=[{"tool": "write", "params": {"css": "#a"}}]), step_schema_map
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
                    "params": {"css": "#email"},
                    "valueFrom": {"text": {"param": "email"}},
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
                    "params": {"css": "#password"},
                    "valueFrom": {
                        "text": {"secret": {"name": "nextcloud", "key": "password"}}
                    },
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
                        "params": {"css": "#e"},
                        "valueFrom": {"text": {"param": "emial"}},
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
                        "params": {"css": "#e"},
                        "valueFrom": {
                            "text": {
                                "param": "email",
                                "secret": {"name": "n", "key": "k"},
                            }
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
                    {"tool": "write", "params": {"css": "#e"}, "valueFrom": {"text": {}}}
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
                        "params": {"css": "#p"},
                        "valueFrom": {"text": {"secret": {"name": "nextcloud"}}},
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
                        "params": {"css": "#e", "text": "literal"},
                        "valueFrom": {"text": {"param": "email"}},
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
                    "params": {"css": "#p"},
                    "valueFrom": {
                        "text": {"secret": {"name": "n", "key": "password"}}
                    },
                }
            ]
        ),
        step_schema_map,
    )


async def test_a_reference_to_a_parameter_the_tool_does_not_take(step_schema_map):
    with pytest.raises(InvalidFlow, match="which write does not take"):
        validate(
            flow(
                parameters={"type": "object", "properties": {"email": {}}},
                steps=[
                    {
                        "tool": "write",
                        "params": {"css": "#e", "text": "x"},
                        "valueFrom": {"nonsense": {"param": "email"}},
                    }
                ],
            ),
            step_schema_map,
        )


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
    assert len(caught.value.problems) == 3
