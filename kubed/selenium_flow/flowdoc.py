"""What a flow document is, and whether one is valid.

A flow is a sequence of tool calls saved under a name (saga Chapter 1, §F1.6).
This module owns its *shape*: what a step may contain, which tools may be steps,
and how a step reaches a value it was not given literally. It does not store
flows — that is ``flows.py`` — and it does not run them, which is E3.

**Steps are validated against the tool schemas rather than a second definition
of them.** ``tools.py``'s signatures are the tool schema, FastMCP derives the
JSON Schema from them, and ``openapi.py`` already publishes those same schemas
as its request bodies. A hand-written copy here would be a third place to update
when ``write`` gains a parameter, and the first one anybody forgot. So the
schemas are passed in, and everything below is a check against them.

**Validation happens when a flow is SAVED, not when it runs.** Learning at step
nine that step ten names a parameter that does not exist is the worst version of
this feature: the browser is half way through a form and the flow was never
going to finish. A save is the moment an author is present and can fix it.

**There is no templating.** A step that needs a value it was not given names a
*source* — a flow parameter, a secret, a config entry — in its ``valueFrom``
map, and the runner resolves it. Nothing scans a string for placeholders, so a
payload can never collide with a reference; see §F1.7, which says what that
costs as well as what it buys.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# What a step may carry. `params` is the tool's own arguments and is checked
# against its schema; everything else is ours and is checked here.
STEP_KEYS = {
    "tool",  # which action — the discriminator
    "params",  # its arguments, literally
    "valueFrom",  # arguments that come from somewhere else (§F1.7)
    "id",  # a name for this step, unique in the flow
    "onError",  # abort (default) | continue
    "return",  # include this step's full result in the run report
    "note",  # a human comment
    "timeout",  # per-step bound, falling back to the action's own default
}

ON_ERROR = ("abort", "continue")

# Lifecycle, not action. A flow runs against the browser the caller already has,
# which is what lets one flow be run on Chrome and then on Firefox without being
# edited — see §F1.9. A flow that opened its own browser would also end the
# caller's, since open_session replaces the one you are holding.
NOT_STEPS = {"open_session", "end_browser"}

# The runner supplies this. A step naming it would be addressing someone else's
# browser, which is the one thing a caller must never be able to do.
RESERVED_PARAMS = {"session_id"}

# Where a value may come from. Exactly one per reference.
SOURCES = ("param", "secret", "config")
# The two that name a thing and a key inside it. `param` is just a name.
KEYED_SOURCES = ("secret", "config")


class InvalidFlow(ValueError):
    """A flow document that cannot be saved, with every reason it cannot.

    A ValueError, so `errors.py` classifies it as the caller's problem and
    returns 400 rather than 500.
    """

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(
            "this flow cannot be saved:\n- " + "\n- ".join(problems)
        )


def _types(schema: dict) -> set[str]:
    """The JSON types a property accepts, flattening the optional `anyOf`.

    FastMCP writes an optional parameter as ``anyOf: [{type: string}, {type:
    null}]``, so reading ``type`` alone would find nothing there and check
    nothing at all.
    """
    if "type" in schema:
        return {schema["type"]}
    return {b["type"] for b in schema.get("anyOf", []) if "type" in b}


_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def _type_fits(value, accepted: set[str]) -> bool:
    """Whether ``value`` is one of the JSON types ``accepted`` allows.

    Shallow on purpose: this catches `wait_timeout: "soon"` and leaves anything
    deeper to the action, which has to cope with a hand-written call anyway.
    `bool` is checked before `integer` because in Python `True` is an `int`, and
    accepting it as one would let `clear: true` satisfy a timeout.
    """
    if not accepted:
        return True
    if isinstance(value, bool):
        return "boolean" in accepted
    for name in accepted:
        expected = _JSON_TYPES.get(name)
        if expected is None:
            return True  # a type we do not model — do not invent a failure
        if name == "integer" and isinstance(value, bool):
            continue
        if isinstance(value, expected):
            return True
    return False


def _check_value_from(where: str, name: str, source, declared: set[str]) -> list[str]:
    """One entry of a step's ``valueFrom`` map."""
    if not isinstance(source, dict):
        return [f"{where}: valueFrom.{name} must be an object naming one source"]

    named = [key for key in SOURCES if key in source]
    unknown = sorted(set(source) - set(SOURCES))
    problems = []
    if unknown:
        problems.append(
            f"{where}: valueFrom.{name} has no source called {', '.join(unknown)}; "
            f"use one of {', '.join(SOURCES)}"
        )
    if not named:
        return [
        *problems,
            f"{where}: valueFrom.{name} names no source; "
            f"give exactly one of {', '.join(SOURCES)}"
        ]
    if len(named) > 1:
        return [
        *problems,
            f"{where}: valueFrom.{name} names {' and '.join(named)}; "
            "give exactly one source"
        ]

    kind = named[0]
    reference = source[kind]

    if kind == "param":
        if not isinstance(reference, str) or not reference:
            problems.append(f"{where}: valueFrom.{name}.param must be a parameter name")
        elif reference not in declared:
            known = ", ".join(sorted(declared)) or "none are declared"
            problems.append(
                f"{where}: valueFrom.{name}.param is {reference!r}, which this "
                f"flow does not declare. Declared parameters: {known}"
            )
        return problems

    # secret and config both name a thing and a key inside it. Two fields, never
    # one dotted string: a Kubernetes key is routinely `tls.crt`, so there is no
    # split point to find (§F1.26).
    if not isinstance(reference, dict):
        return [
        *problems,
            f"{where}: valueFrom.{name}.{kind} must be an object with name and key"
        ]
    for field in ("name", "key"):
        if not reference.get(field) or not isinstance(reference[field], str):
            problems.append(
                f"{where}: valueFrom.{name}.{kind} needs a {field}"
            )
    extra = sorted(set(reference) - {"name", "key"})
    if extra:
        problems.append(
            f"{where}: valueFrom.{name}.{kind} does not take {', '.join(extra)}"
        )
    return problems


def _check_params(where: str, tool: str, params: dict, bound: set[str], schema: dict):
    """A step's literal arguments, against the tool's own published schema."""
    problems = []
    properties = schema.get("properties") or {}
    known = set(properties)

    for name, value in sorted(params.items()):
        if name in RESERVED_PARAMS:
            problems.append(
                f"{where}: {name} is supplied by the run, not by the flow — "
                "a flow uses the browser the caller already has"
            )
            continue
        if name not in known:
            close = ", ".join(sorted(known)) or "it takes none"
            problems.append(
                f"{where}: {tool} has no parameter {name!r}. Takes: {close}"
            )
            continue
        if not _type_fits(value, _types(properties[name])):
            accepted = " or ".join(sorted(_types(properties[name])))
            problems.append(
                f"{where}: {tool}.{name} should be {accepted}, got "
                f"{type(value).__name__}"
            )

    for name in bound:
        if name in RESERVED_PARAMS:
            problems.append(f"{where}: valueFrom cannot supply {name}")
        elif name not in known:
            problems.append(
                f"{where}: valueFrom names {name!r}, which {tool} does not take"
            )
        elif name in params:
            problems.append(
                f"{where}: {name} is given in both params and valueFrom — "
                "one value, one place"
            )

    for name in schema.get("required") or []:
        if name not in params and name not in bound and name not in RESERVED_PARAMS:
            problems.append(f"{where}: {tool} requires {name!r}")

    return problems


def _check_step(index: int, step, declared: set[str], schemas: dict) -> list[str]:
    where = f"step {index}"
    if not isinstance(step, dict):
        return [f"{where}: must be an object with a tool and its params"]

    if step.get("id"):
        where = f"step {index} ({step['id']})"

    problems = []
    unknown = sorted(set(step) - STEP_KEYS)
    if unknown:
        problems.append(
            f"{where}: unknown step key {', '.join(unknown)}; "
            f"a step takes {', '.join(sorted(STEP_KEYS))}"
        )

    tool = step.get("tool")
    if not tool or not isinstance(tool, str):
        return [*problems, f"{where}: names no tool"]
    if tool in NOT_STEPS:
        return [
        *problems,
            f"{where}: {tool} is not a step. A flow runs in the browser you "
            "already have, which is what lets one flow run on Chrome and then "
            "on Firefox without being edited"
        ]
    if tool not in schemas:
        return [
        *problems,
            f"{where}: there is no tool called {tool!r}. "
            f"Available: {', '.join(sorted(schemas))}"
        ]

    on_error = step.get("onError", "abort")
    if on_error not in ON_ERROR:
        problems.append(
            f"{where}: onError is {on_error!r}; use {' or '.join(ON_ERROR)}"
        )

    for key, kind in (("id", str), ("note", str), ("timeout", int)):
        if key in step and not isinstance(step[key], kind):
            problems.append(f"{where}: {key} must be a {kind.__name__}")
    if "return" in step and not isinstance(step["return"], bool):
        problems.append(f"{where}: return must be true or false")

    params = step.get("params", {})
    if not isinstance(params, dict):
        return [*problems, f"{where}: params must be an object"]

    value_from = step.get("valueFrom", {})
    if not isinstance(value_from, dict):
        return [*problems, f"{where}: valueFrom must be an object"]
    for name, source in sorted(value_from.items()):
        problems += _check_value_from(where, name, source, declared)

    checked = _check_params(where, tool, params, set(value_from), schemas[tool])
    return [*problems, *checked]


def validate(document, schemas: dict) -> dict:
    """``document`` if it is a flow that can be run, else raise `InvalidFlow`.

    Returns **every** problem at once rather than the first. An agent fixing a
    twelve-step flow one error per round trip is the reason this collects.
    """
    if not isinstance(document, dict):
        raise InvalidFlow(["a flow must be an object"])

    problems = []
    if "description" in document and not isinstance(document["description"], str):
        problems.append("description must be a string")

    parameters = document.get("parameters") or {}
    if not isinstance(parameters, dict):
        problems.append("parameters must be a JSON Schema object")
        parameters = {}
    declared = set(parameters.get("properties") or {})
    for name in parameters.get("required") or []:
        if name not in declared:
            problems.append(
                f"parameters: {name!r} is required but not declared in properties"
            )

    steps = document.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("steps must be a non-empty list")
        raise InvalidFlow(problems)

    seen = set()
    for index, step in enumerate(steps, start=1):
        problems += _check_step(index, step, declared, schemas)
        if isinstance(step, dict) and step.get("id"):
            if step["id"] in seen:
                problems.append(
                    f"step {index}: id {step['id']!r} is already used; "
                    "ids name a step and must be unique"
                )
            seen.add(step["id"])

    if problems:
        raise InvalidFlow(problems)
    return document


def step_schemas(tools: dict) -> dict:
    """The tool schemas a step's ``params`` are checked against.

    ``session_id`` is stripped: the run supplies it, and a step naming it would
    be addressing a browser that is not the caller's. Lifecycle tools are
    dropped entirely (§F1.9).

    Taken from the registered tools rather than from a listing, because a
    listing is rewritten per request by ``resources.ShapeSessionId`` — so what
    it contains depends on which session mode the caller happened to be in, and
    a flow's shape must not.
    """
    schemas = {}
    for name, schema in tools.items():
        if name in NOT_STEPS:
            continue
        properties = {
            key: value
            for key, value in (schema.get("properties") or {}).items()
            if key not in RESERVED_PARAMS
        }
        required = [
            key for key in (schema.get("required") or []) if key not in RESERVED_PARAMS
        ]
        schemas[name] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    return schemas
