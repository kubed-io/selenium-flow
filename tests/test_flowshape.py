"""`Shape` reads any YAML a person might have left in a flow document."""

import pytest

from kubed.selenium_flow.flows.shape import Shape


@pytest.mark.parametrize(
    "document", [None, [], "flow", {"steps": 1, "parameters": []}, {"steps": {"a": 1}}]
)
def test_every_reader_is_total(document):
    shape = Shape(document)
    assert shape.steps == [] and shape.step_count == 0
    assert shape.properties == {} and shape.declared == set()
    assert shape.required == []


def test_names_are_strings_and_required_keeps_only_names():
    shape = Shape(
        {"parameters": {"properties": {1: {}, "a": {}}, "required": ["a", ["x"]]}}
    )
    assert shape.declared == {"1", "a"}
    assert shape.required == ["a"]
    assert shape.parameter_problems() == [
        "parameters.required must be a list of names"
    ]


def test_a_run_tolerates_no_steps_but_not_the_wrong_kind():
    assert Shape({}).steps_problem(empty_ok=True) is None
    assert Shape({"steps": []}).steps_problem(empty_ok=True) is None
    assert Shape({"steps": []}).steps_problem() == "steps must be a non-empty list"
    assert Shape({"steps": "go"}).steps_problem(empty_ok=True)


def test_a_step_that_is_not_an_object_is_numbered():
    assert Shape({"steps": [{"tool": "x"}, "oops"]}).step_problems() == [
        "step 2: must be an object with a tool and its params"
    ]


@pytest.mark.parametrize("tool", [["navigate"], {"a": 1}, 3, None, ""])
def test_a_step_whose_tool_is_not_a_name_names_no_tool(tool):
    """In the validator's words, the id included when the step has one."""
    steps = [{"tool": "navigate"}, {"tool": tool}, {"id": "go", "tool": tool}]
    assert Shape({"steps": steps}).step_problems() == [
        "step 2: names no tool",
        "step 3 (go): names no tool",
    ]
