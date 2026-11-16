"""What a flow document is, and whether one is valid.

Steps are checked against the live tool schemas, passed in, never a copy of
them. Validation runs when a flow is saved, so every problem, every undeclared
``${name}`` included, is found before a browser moves.

A parameter is text the author may read in the report, written ``${name}``
anywhere in any argument. A secret is never part of a string: only `write` has
a `secret` argument, and the tool schemas enforce that. So a parameter may
never be `writeOnly`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection

from .. import binding
from ..binding import SECRET_ARG
from . import places
from .shape import NAMES_NO_TOOL, NOT_A_STEP, Shape
from .template import PARAM_REFERENCE, listed, references

log = logging.getLogger(__name__)

# The flow declares `parameters`, a run supplies `params`, and a step passes
# `args` to a tool.
ARGS = "args"

# No `timeout`: a Selenium call blocks, so nothing could honour one. A step
# bounds itself with its tool's `wait_timeout`.
STEP_KEYS = {
    "tool",
    ARGS,  # the tool's arguments, literally and completely
    "id",  # unique in the flow
    "onError",  # abort (default) | continue
    "return",  # include this step's full result in the run report
    "note",
}

ON_ERROR = ("abort", "continue")

# A whole run's budget in seconds, declared by the flow's author, who knows
# whether it exists to wait. It bounds starting a step.
TIMEOUT = "timeout"

# What a caller may say in a flow document, beside its name. The HTTP save and
# the published schema read these; a test holds the MCP save tool to them.
DOCUMENT_KEYS = ("description", "sites", "paths", "parameters", TIMEOUT, "steps")

# Shown in save_flow's schema and description, flow://schema and the HTTP
# save's body.
PARAMETERS_EXAMPLE = {
    "type": "object",
    "properties": {"email": {"type": "string"}},
    "required": ["email"],
}
PARAMETERS_DESCRIPTION = (
    "A JSON Schema object: its properties are the parameters, each written "
    "${name} in a step's args, and required lists the ones every run must "
    "supply."
)


def declared_timeout(document: dict) -> int | None:
    """The run budget ``document`` declares, or None when it declares none.

    Raises ValueError with the sentence to show an author. A bool is refused
    though Python counts it an int.
    """
    if not isinstance(document, dict) or document.get(TIMEOUT) is None:
        return None
    value = document[TIMEOUT]
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"timeout must be a whole number of seconds, at least 1; got {value!r}"
        )
    return value

# The one step `onError: continue` may never be paired with, read by saving and
# running.
ASSERTION = "assert"

# Tools a step may not name, each with the reason the refusal gives.
NOT_STEPS = {
    "await_control": (
        "A flow cannot wait for a person: a refused step stops the run, and "
        "whoever started it calls await_control"
    ),
    "open_session": (
        "A flow runs in the browser you already have, which is what lets one "
        "flow run on Chrome and then on Firefox without being edited"
    ),
    "end_browser": (
        "A flow runs in the browser you already have, which is what lets one "
        "flow run on Chrome and then on Firefox without being edited"
    ),
    "outline": (
        "It is for working out what a flow should do - the selectors, and "
        "whether an element can be used - not for doing it. A saved flow "
        "already knows its selectors; call outline while writing or repairing "
        "one"
    ),
    "network_body": (
        "A request id belongs to one page load, which a saved flow cannot know "
        "when it is written; read workspace://network and fetch a body while "
        "working one out"
    ),
}

# The runner supplies this; a step naming it could address another browser.
RESERVED_PARAMS = {"session_id"}

# Needed but not `required` in the schema, because a secret may supply it
# instead (through `bound`).
NEEDED_SOMEHOW = {"write": ("text",)}

# Tools that act on an element, and the ones that may name none: press_key goes
# wherever focus is and screenshot takes the viewport. frame is decided per call
# (`_needs_an_element`).
ADDRESSES_AN_ELEMENT = {
    "interact", "write", "extract", "upload_file", "press_key", "screenshot",
    "frame", "drag",
}
OPTIONAL_ELEMENT = {"press_key", "screenshot"}

# A drag takes exactly one of a destination selector or a by_x/by_y offset,
# which a JSON schema cannot say.
DESTINATION = "to"
OFFSET = ("by_x", "by_y")


def _needs_an_element(tool: str, params: dict) -> bool:
    """Whether this call has to name an element.

    `frame`'s `switch` needs a selector or an index; `parent` and `default`
    need nothing.
    """
    if tool == "frame":
        action = str(params.get("action", "switch")).strip().lower()
        return action == "switch" and params.get("index") is None
    return tool not in OPTIONAL_ELEMENT


REFERENCE_FIELDS = ("name", "key")


def reference_problems(reference) -> list[str]:
    """What is wrong with a secret reference: a `name` and a `key`, nothing else.

    Two fields, never one dotted string: a Kubernetes key is often `tls.crt`.
    Shared with `secrets.bind`, which gets the HTTP body as raw JSON, so an
    unknown field is refused there too.
    """
    if not isinstance(reference, dict):
        return ["secret must be an object with name and key"]
    problems = [
        f"secret needs a {field}"
        for field in REFERENCE_FIELDS
        if not reference.get(field) or not isinstance(reference[field], str)
    ]
    extra = set(reference) - set(REFERENCE_FIELDS)
    if extra:
        problems.append(f"secret does not take {listed(extra)}")
    return problems


class InvalidFlow(ValueError):
    """A flow document that cannot be saved, with every reason it cannot.

    A ValueError, so `errors.py` answers 400.
    """

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(
            "this flow cannot be saved:\n- " + "\n- ".join(problems)
        )


def _types(schema: dict) -> set[str]:
    """The JSON types a property accepts, flattening the optional `anyOf`.

    FastMCP writes an optional parameter as ``anyOf: [{type: string}, {type:
    null}]``. A model branch arrives as a ``$ref`` with no ``type``, and counts
    as an object.
    """
    if "type" in schema:
        return {schema["type"]}
    found = set()
    for branch in schema.get("anyOf", []):
        if "type" in branch:
            found.add(branch["type"])
        elif "$ref" in branch:
            found.add("object")
    return found


def _allowed(schema: dict) -> list | None:
    """The closed set a property accepts, whether or not it may also be null.

    An optional `Literal` arrives as an `enum` branch beside `null` in `anyOf`.
    """
    if "enum" in schema:
        return schema["enum"]
    for branch in schema.get("anyOf", []):
        if "enum" in branch:
            return branch["enum"]
    return None


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

    Shallow: anything deeper is the action's to refuse. `bool` comes first
    because Python counts `True` an `int`.
    """
    if not accepted:
        return True
    if isinstance(value, bool):
        return "boolean" in accepted
    for name in accepted:
        expected = _JSON_TYPES.get(name)
        if expected is None:
            return True  # a type not modelled here is not a failure
        if name == "integer" and isinstance(value, bool):
            continue
        if isinstance(value, expected):
            return True
    return False


def _check_references(
    where: str, args: dict, declared: set[str], misplaced: Collection[str] = frozenset()
) -> list[str]:
    """Every parameter an argument names must be one the flow declares.

    Both messages name the `$${` escape: the usual cause is a JavaScript
    template literal in a `script`, ``return `${Math.round(n)} KB` ``.
    """
    problems = []
    for name in dict.fromkeys(references(args)):
        if name in misplaced:
            continue  # the shape problem already says where it goes
        if not name:
            problems.append(
                f"{where}: ${{}} names no parameter — write ${{name}}, or "
                "$${} for a literal"
            )
        elif name not in declared:
            known = listed(declared) or "this flow declares none"
            problems.append(
                f"{where}: ${{{name}}} is not a parameter of this flow. "
                f"Declared: {known}. If it was never meant to be one — a "
                f"JavaScript template literal, say — write $${{{name}}}"
            )
    return problems


# Every JSON Schema 2020-12 keyword: core, applicator, unevaluated, validation,
# meta-data, format and content. Any `$` or `x-` key counts as one too. Only a
# key outside these, holding a bare type name, is taken for a misplaced
# parameter: `"title": "string"` is a valid schema.
SCHEMA_KEYWORDS = frozenset({
    "prefixItems", "items", "contains", "additionalProperties", "properties",
    "patternProperties", "dependentSchemas", "propertyNames", "if", "then",
    "else", "allOf", "anyOf", "oneOf", "not",
    "unevaluatedItems", "unevaluatedProperties",
    "type", "const", "enum", "multipleOf", "maximum", "exclusiveMaximum",
    "minimum", "exclusiveMinimum", "maxLength", "minLength", "pattern",
    "maxItems", "minItems", "uniqueItems", "maxContains", "minContains",
    "maxProperties", "minProperties", "required", "dependentRequired",
    "title", "description", "default", "deprecated", "readOnly", "writeOnly",
    "examples", "format", "contentEncoding", "contentMediaType", "contentSchema",
})
# The bare type names a misplaced parameter is written with. Not `"null"`: no
# parameter is only ever null.
TYPE_NAMES = frozenset({"string", "number", "integer", "boolean", "object", "array"})


def _is_keyword(key: str) -> bool:
    return key in SCHEMA_KEYWORDS or key.startswith(("$", "x-"))


def _misplaced(parameters, steps) -> dict[str, dict]:
    """Top-level `parameters` keys that are parameters, each with its schema.

    Never guessed from the key alone: with no `properties`, a key the steps
    name as ``${key}``; with or without, a non-keyword key whose value is a
    bare type name. Only at save: a stored flow still runs as it did.
    """
    if not isinstance(parameters, dict):
        return {}
    named = set()
    if "properties" not in parameters:
        # Each step's args only: a `note` that mentions `${x-ui}` names nothing.
        named = {
            name
            for step in (steps if isinstance(steps, list) else [])
            if isinstance(step, dict)
            for name in references(step.get(ARGS))
        }
    found = {}
    # `str`: YAML makes `1:` an integer, and `${1}` names it as text.
    for key, value in sorted(parameters.items(), key=lambda item: str(item[0])):
        key = str(key)
        if isinstance(value, str) and value in TYPE_NAMES and not _is_keyword(key):
            found[key] = {"type": value}
        elif key in named:
            found[key] = value if isinstance(value, dict) else {"type": "string"}
    return found


def _undeclared_shape(parameters, misplaced: dict[str, dict]) -> list[str]:
    """`parameters` written as ``{"email": ...}`` instead of a schema.

    The example declares every misplaced key, beside the properties already
    declared.
    """
    if not misplaced:
        return []
    properties = parameters.get("properties")
    properties = dict(properties) if isinstance(properties, dict) else {}
    # A name already declared keeps the schema it was declared with.
    for name, schema in misplaced.items():
        properties.setdefault(name, schema)
    example = {"type": "object", "properties": properties}
    if isinstance(parameters.get("required"), list):
        example["required"] = parameters["required"]
    sit = "sits" if len(misplaced) == 1 else "sit"
    # `default=str`: YAML makes `default: 2024-01-01` a date, and a refusal
    # that cannot be written is a 500.
    return [
        f"parameters: {listed(misplaced)} {sit} where only JSON Schema keywords "
        "go. Declare each parameter under properties: "
        f"{json.dumps(example, default=str)}"
    ]


def _check_secret(where: str, reference) -> list[str]:
    """``args.secret``: which secret, and which key inside it."""
    return [f"{where}: {problem}" for problem in reference_problems(reference)]


def _is_whole_reference(value) -> bool:
    """Whether ``value`` is one reference and nothing else.

    `"$${secs}"` also matches the expression, so the group is checked.
    """
    if not isinstance(value, str):
        return False
    whole = PARAM_REFERENCE.fullmatch(value)
    return whole is not None and whole.group(1) is not None


def _branches(schema: dict) -> list[dict]:
    """A property's schema and each alternative inside an optional `anyOf`."""
    if not isinstance(schema, dict):
        return []
    return [schema, *(b for b in schema.get("anyOf") or [] if isinstance(b, dict))]


def _described(schema: dict) -> str:
    """What a property's own schema says it wants, if it says anything."""
    for branch in _branches(schema):
        if branch.get("description"):
            return str(branch["description"]).strip()
    return ""


def _home_of(name: str, properties: dict) -> str | None:
    """The argument ``name`` belongs inside, when it is a key of one of them.

    A client with an old schema sends `interact(css="button.go")`, and the
    refusal says `css` goes inside `selector`.
    """
    for argument, schema in sorted(properties.items()):
        for branch in _branches(schema):
            if name in (branch.get("properties") or {}):
                return argument
    return None


def argument_problems(tool: str, arguments: dict, schema: dict) -> list[str]:
    """What is wrong with a direct tool call, in the words `save_flow` uses.

    A step's arguments are a tool call's arguments, so both get one sentence
    for one mistake.
    """
    # A call carrying a secret has its text, as a step does.
    bound = {binding.SUPPLIES} if binding.binds(arguments) else set()
    return [
        problem.removeprefix(": ")
        for problem in _check_params(
            "", tool, arguments, bound, inlined(schema), in_flow=False
        )
    ]


def _check_params(
    where: str,
    tool: str,
    params: dict,
    bound: set[str],
    schema: dict,
    in_flow: bool = True,
):
    """A step's literal arguments, against the tool's own published schema.

    ``in_flow`` is False for a direct call, where nothing is substituted and
    there is no `args` to point at.
    """
    problems = []
    properties = schema.get("properties") or {}
    known = set(properties)
    # Arguments already told which argument they belong inside, so "needs an
    # element" is not said as well.
    routed = set()

    for name, value in sorted(params.items(), key=lambda item: str(item[0])):
        if name in RESERVED_PARAMS:
            problems.append(
                f"{where}: {name} is supplied by the run, not by the flow — "
                "a flow uses the browser the caller already has"
            )
            continue
        if name not in known:
            home = _home_of(name, properties)
            if home:
                routed.add(home)
                shape = json.dumps({name: value}, default=str)
                problems.append(
                    f"{where}: {tool} has no parameter {name!r}; it goes inside "
                    f"{home}: {home}={shape}"
                )
                continue
            close = ", ".join(sorted(known)) or "it takes none"
            problems.append(
                f"{where}: {tool} has no parameter {name!r}. Takes: {close}"
            )
            continue
        # Exactly one reference takes the parameter's value with its type, so
        # `wait_timeout: "${secs}"` is not a string at run time.
        if in_flow and _is_whole_reference(value):
            continue
        if not _type_fits(value, _types(properties[name])):
            accepted = " or ".join(sorted(_types(properties[name])))
            wanted = _described(properties[name])
            problems.append(
                f"{where}: {tool}.{name} should be {accepted}, got "
                f"{type(value).__name__}" + (f". {wanted}" if wanted else "")
            )
        # Compared without case, because the actions lowercase. A value carrying
        # a reference is left to the run.
        allowed = _allowed(properties[name])
        if (
            allowed
            and isinstance(value, str)
            and not (in_flow and references(value))
            and value.strip().lower() not in {str(a).lower() for a in allowed}
        ):
            problems.append(
                f"{where}: {tool}.{name} is {value!r}; use one of "
                f"{', '.join(map(str, allowed))}"
            )

    # `bound` is what a secret supplies: `write.text`, and nothing else has one.
    if bound and binding.gives_twice(params, as_written=True):
        problems.append(f"{where}: {binding.AT_SAVE.twice}")

    for argument in NEEDED_SOMEHOW.get(tool, ()):
        # `is None` as well as absent: the schema permits null, and `Actions.write`
        # would type `text: null` as the string "None".
        if params.get(argument) is None and argument not in bound:
            where_to = f"give it in {ARGS}" if in_flow else "give it"
            problems.append(
                f"{where}: {tool} needs {argument!r} — {where_to}, "
                "or give a secret for it to type"
            )

    if tool in ADDRESSES_AN_ELEMENT:
        named = params.get("selector") or "selector" in bound or "selector" in routed
        if not named and _needs_an_element(tool, params):
            problems.append(
                f"{where}: {tool} needs an element — give selector with xpath or css"
            )
        elif isinstance(params.get("selector"), dict):
            problems += _check_selector(where, tool, params["selector"])

    if tool == "drag":
        problems += _check_destination(where, params)

    for name in schema.get("required") or []:
        if name not in params and name not in bound and name not in RESERVED_PARAMS:
            problems.append(f"{where}: {tool} requires {name!r}")

    return problems


def _check_selector(where: str, tool: str, selector: dict) -> list[str]:
    """One selector names one element, as `browser.locator` requires.

    The model checks this too, but a flow may be YAML written by hand.
    """
    named = [k for k in ("xpath", "css") if selector.get(k)]
    unknown = sorted(set(selector) - {"xpath", "css"})
    if unknown:
        return [f"{where}: {tool} selector takes xpath or css, not {listed(unknown)}"]
    if len(named) > 1:
        return [f"{where}: {tool} selector takes xpath or css, not both"]
    if not named:
        return [f"{where}: {tool} selector needs xpath or css"]
    return []


def _check_destination(where: str, params: dict) -> list[str]:
    """Where a drag ends: a destination element, or an offset. Never both."""
    to = params.get(DESTINATION)
    by = [k for k in OFFSET if params.get(k) is not None]
    if to and by:
        return [
            f"{where}: drag takes a destination element (to) or an "
            f"offset ({listed(by)}), not both"
        ]
    if not to and not by:
        return [
            f"{where}: drag needs a destination — `to` with a selector for an "
            "element, or by_x/by_y for an offset in pixels"
        ]
    if isinstance(to, dict):
        return _check_selector(where, "drag", to)
    if by and all(params.get(k) in (0, None) for k in OFFSET):
        return [
            f"{where}: drag by_x and by_y are both zero, which is a drag to "
            "where it already is"
        ]
    return []


def _check_step(
    index: int,
    step,
    declared: set[str],
    schemas: dict,
    misplaced: Collection[str] = frozenset(),
) -> list[str]:
    where = f"step {index}"
    if not isinstance(step, dict):
        return [f"{where}: {NOT_A_STEP}"]

    if step.get("id"):
        where = f"step {index} ({step['id']})"

    problems = []
    unknown = set(step) - STEP_KEYS
    if unknown:
        problems.append(
            f"{where}: unknown step key {listed(unknown)}; "
            f"a step takes {', '.join(sorted(STEP_KEYS))}"
        )

    tool = step.get("tool")
    if not tool or not isinstance(tool, str):
        return [*problems, f"{where}: {NAMES_NO_TOOL}"]
    if tool in NOT_STEPS:
        return [*problems, f"{where}: {tool} is not a step. {NOT_STEPS[tool]}"]
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
    elif tool == ASSERTION and on_error == "continue":
        problems.append(
            f"{where}: an assert cannot be continued past - a run that carries "
            "on after a false assertion reports success it did not earn. Drop "
            "onError, or drop the assert"
        )

    for key, kind in (("id", str), ("note", str)):
        if key in step and not isinstance(step[key], kind):
            problems.append(f"{where}: {key} must be a {kind.__name__}")
    if "return" in step and not isinstance(step["return"], bool):
        problems.append(f"{where}: return must be true or false")

    args = step.get(ARGS, {})
    if not isinstance(args, dict):
        return [*problems, f"{where}: {ARGS} must be an object"]

    problems += _check_references(where, args, declared, misplaced)

    secret = args.get(SECRET_ARG)
    bound = set()
    if binding.binds(args):
        bound = {binding.SUPPLIES}
        problems += _check_secret(where, secret)
        # `binding`'s rule, in two halves so the problems keep their order: the
        # url here, the text with the other arguments (`_check_params`).
        if binding.navigates(args):
            problems.append(f"{where}: {binding.AT_SAVE.navigates}")
        # A parameter naming the secret would let the caller choose which
        # credential is typed.
        if references(secret):
            problems.append(
                f"{where}: a secret cannot be named by a parameter — "
                "write the name and key literally"
            )

    checked = _check_params(where, tool, args, bound, schemas[tool])
    return [*problems, *checked]


def validate(document, schemas: dict) -> dict:
    """``document`` if it is a flow that can be run, else raise `InvalidFlow`
    with every problem at once."""
    if not isinstance(document, dict):
        raise InvalidFlow(["a flow must be an object"])

    problems = []
    if "description" in document and not isinstance(document["description"], str):
        problems.append("description must be a string")

    try:
        declared_timeout(document)
    except ValueError as exc:
        problems.append(str(exc))
    problems += places.problems(document)

    shape = Shape(document)
    problems += shape.parameter_problems()
    misplaced = _misplaced(document.get("parameters"), document.get("steps"))
    problems += _undeclared_shape(document.get("parameters"), misplaced)
    properties = shape.properties
    declared = shape.declared
    # `writeOnly` would read as hidden, and nothing hides a parameter.
    for name in sorted(properties):
        schema = properties[name]
        if isinstance(schema, dict) and schema.get("writeOnly"):
            problems.append(
                f"parameters: {name!r} is writeOnly, which no longer hides "
                "anything — a parameter is text and may appear in the report. "
                "A value nobody may see is a secret: give write an args.secret "
                "instead"
            )
    for name in shape.required:
        if name not in declared:
            problems.append(
                f"parameters: {name!r} is required but not declared in properties"
            )

    problem = shape.steps_problem()
    if problem:
        problems.append(problem)
        raise InvalidFlow(problems)

    seen = set()
    for index, step in enumerate(shape.steps, start=1):
        problems += _check_step(index, step, declared, schemas, misplaced.keys())
        # Only a string id: `_check_step` refuses any other, and a list cannot
        # go in a set.
        if isinstance(step, dict) and step.get("id") and isinstance(step["id"], str):
            if step["id"] in seen:
                problems.append(
                    f"step {index}: id {step['id']!r} is already used; "
                    "ids name a step and must be unique"
                )
            seen.add(step["id"])

    if problems:
        raise InvalidFlow(problems)
    return document


def concerns(document) -> list[str]:
    """What is probably wrong with a valid flow, as warnings for its save.

    A first step that acts on whatever page the browser is on is right when
    the caller arranged that page, and a mistake otherwise, so it is warned of
    and kept.
    """
    if not isinstance(document, dict):
        return []
    steps = document.get("steps") or []
    first = steps[0] if steps and isinstance(steps[0], dict) else None
    if first is None:
        return []
    tool = str(first.get("tool") or "")
    args = first.get(ARGS)
    args = args if isinstance(args, dict) else {}
    if tool in ("navigate", ASSERTION) or args.get("url"):
        return []
    return [
        f"this flow starts with {tool or 'a step'} on whatever page the browser "
        "is already on. If it is meant for a particular page, say so: make the "
        "first step navigate, give it a url, or put an assert in front of it "
        "that says where the run must already be."
    ]


def inlined(schema: dict) -> dict:
    """``schema`` with every local ``$ref`` replaced by the definition it names.

    FastMCP publishes a model argument (`Selector`, `SecretRef`) as a `$ref`
    into the schema's own `$defs`, which dangles once a property is copied out.
    """
    defs = schema.get("$defs") or {}

    def walk(node, seen=()):
        if isinstance(node, list):
            return [walk(item, seen) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            if name in defs and name not in seen:
                return walk(defs[name], (*seen, name))
        return {k: walk(v, seen) for k, v in node.items() if k != "$defs"}

    return walk(schema)


def step_schemas(tools: dict) -> dict:
    """The tool schemas a step's ``args`` are checked against.

    ``tools`` are the registered tools' schemas. ``session_id`` is stripped,
    and the tools a step may not name are dropped.
    """
    schemas = {}
    for name, schema in tools.items():
        if name in NOT_STEPS:
            continue
        schema = inlined(schema)
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
