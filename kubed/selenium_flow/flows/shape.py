"""One tolerant view of a flow document.

A flow is YAML that a person may have written by hand, and the store hands back
whatever mapping the file held. Five readers used to each decide for themselves
what to do when `steps` was a string, `required` a bare word or a step a list —
the validator, the run's preflight, the parameter helpers, the admin's `_uses`
and the library's step count — and each guarded a different subset, so the
shapes that slipped through became a TypeError in a message or a 500 on a run.

`Shape` is the only place that question is answered. Every property is total:
whatever the document holds, it returns something of the type its name says, and
`problems` says in a sentence what was not as expected. A reader that wants the
well-formed case reads the property; one that must refuse a bad document asks
for the problems and uses the same sentences, so saving and running agree.
"""

from __future__ import annotations

# Said by the validator for a step and by the run for the same step, so the two
# surfaces refuse it in the same words.
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

        The items are not vetted: a step that is not an object is still here,
        so each reader can keep its numbering and say which one it was.
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

        A falsy value is "no parameters", as it always was, so `parameters: []`
        and `properties: {}` are accepted and `parameters: [email]` is not.
        """
        parameters = self._parameters
        if not parameters:
            return []
        if not isinstance(parameters, dict):
            return [NOT_PARAMETERS]
        problems = []
        if parameters.get("properties") and not isinstance(
            parameters["properties"], dict
        ):
            problems.append(NOT_PROPERTIES)
        required = parameters.get("required")
        if required and (
            not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
        ):
            problems.append(NOT_REQUIRED)
        return problems

    def steps_problem(self, empty_ok: bool = False) -> str | None:
        """The sentence for `steps` not being a list, or None.

        A save wants at least one step; a run of a stored document has always
        let an absent or empty one through, so it passes ``empty_ok``.
        """
        steps = self.document.get("steps")
        if isinstance(steps, list):
            return None if steps or empty_ok else NOT_STEPS
        return None if empty_ok and not steps else NOT_STEPS

    def step_problems(self) -> list[str]:
        """Steps that are not objects, or name no tool, numbered from 1.

        A `tool` that is not a name is refused here because the run looks it up
        in a set before any step's error handling: `tool: [navigate]` was a
        TypeError there, and a number a lookup that could only miss.
        """
        problems = []
        for number, step in enumerate(self.steps, start=1):
            if not isinstance(step, dict):
                problems.append(f"step {number}: {NOT_A_STEP}")
                continue
            tool = step.get("tool")
            if not tool or not isinstance(tool, str):
                # Named as the validator names it, so both say the same thing.
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
