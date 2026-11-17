"""One tolerant view of a flow document, which may be YAML written by hand.

Every property is total: whatever the document holds, it returns something of
the type its name says. `problems` says in a sentence what was not as expected,
and saving and running refuse with the same sentences.
"""

from __future__ import annotations

NOT_A_STEP = "must be an object with a tool and its params"
NAMES_NO_TOOL = "names no tool"
NOT_STEPS = "steps must be a non-empty list"
NOT_PARAMETERS = "parameters must be a JSON Schema object"
NOT_PROPERTIES = "parameters.properties must be an object"
NOT_REQUIRED = "parameters.required must be a list of names"


class Shape:
    """What a document says, read without trusting its types."""

    def __init__(self, document):
        self.document = document if isinstance(document, dict) else {}

    @property
    def _parameters(self):
        return self.document.get("parameters")

    @property
    def steps(self) -> list:
        """The steps as written, or none when `steps` is not a list.

        The items are not vetted, so each reader keeps its numbering.
        """
        steps = self.document.get("steps")
        return steps if isinstance(steps, list) else []

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def properties(self) -> dict[str, dict]:
        """Declared parameters by name, names made strings, schemas as written."""
        parameters = self._parameters
        found = parameters.get("properties") if isinstance(parameters, dict) else None
        if not isinstance(found, dict):
            return {}
        return {str(name): schema for name, schema in found.items()}

    @property
    def declared(self) -> set[str]:
        return set(self.properties)

    @property
    def required(self) -> list[str]:
        """The names `required` lists; none when it is not a list of strings."""
        parameters = self._parameters
        found = parameters.get("required") if isinstance(parameters, dict) else None
        if not isinstance(found, list):
            return []
        return [str(name) for name in found if isinstance(name, str)]

    def parameter_problems(self) -> list[str]:
        """What is wrong with the `parameters` block, one sentence per fault.

        A falsy value is no parameters: `parameters: []` and `properties: {}` are
        accepted, and `parameters: [email]` and `properties: []` are not.
        """
        parameters = self._parameters
        if not parameters:
            return []
        if not isinstance(parameters, dict):
            return [NOT_PARAMETERS]
        problems = []
        if "properties" in parameters and not isinstance(
            parameters["properties"], dict
        ):
            problems.append(NOT_PROPERTIES)
        required = parameters.get("required")
        if "required" in parameters and (
            not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
        ):
            problems.append(NOT_REQUIRED)
        return problems

    def steps_problem(self, empty_ok: bool = False) -> str | None:
        """The sentence for `steps` not being a list, or None.

        A save wants at least one step; a run lets an absent or empty one
        through, with ``empty_ok``.
        """
        steps = self.document.get("steps")
        if isinstance(steps, list):
            return None if steps or empty_ok else NOT_STEPS
        return None if empty_ok and not steps else NOT_STEPS

    def step_problems(self) -> list[str]:
        """Steps that are not objects, or name no tool, numbered from 1.

        The run looks `tool` up in a set before any step's error handling, where
        a list would raise TypeError.
        """
        problems = []
        for number, step in enumerate(self.steps, start=1):
            if not isinstance(step, dict):
                problems.append(f"step {number}: {NOT_A_STEP}")
                continue
            tool = step.get("tool")
            if not tool or not isinstance(tool, str):
                where = f"step {number}"
                if step.get("id"):
                    where = f"step {number} ({step['id']})"
                problems.append(f"{where}: {NAMES_NO_TOOL}")
        return problems

    def problems(self) -> list[str]:
        """Everything about the document's *form* a run cannot read past."""
        found = self.parameter_problems()
        problem = self.steps_problem(empty_ok=True)
        if problem:
            return [*found, problem]
        return [*found, *self.step_problems()]
