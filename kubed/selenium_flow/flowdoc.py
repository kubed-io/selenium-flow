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

**A parameter is text; a secret is structural.** The two look alike and are
opposites (§F1.38).

A *parameter* is supplied by the caller, belongs to the flow, and is something
the author may read in the report. It is written ``${name}`` and substituted
into any argument, anywhere — including part of a longer string. That is worth
it because a parameter has no reason to be confined: the alternative forced one
bindable argument per action, so a flow could vary what it typed and never
where it went.

A *secret* is the opposite in every respect: the caller never sees it, it may
reach exactly one sink, and it is checked against the page about to receive it.
So it is **never** substituted into a string. `write` takes a `secret`
parameter, structurally, and no other action has one — which is enforced by the
tool schemas rather than by a list here, because an action that has no such
parameter cannot be given one.

**A parameter may therefore never hold something secret.** There is no
``writeOnly`` any more: the mechanism for a value nobody may see is a secret,
and a half-secret riding the text path would need every summary, result, error
and URL string-scrubbed to hide it again.

**Substitution is single-pass.** A value a caller supplied is never rescanned,
so passing ``${admin_token}`` as a parameter's value yields that literal text
rather than resolving anything. This is the rule that keeps "a payload can
never collide with a reference" true now that strings are scanned at all.

**Validation happens when a flow is SAVED, not when it runs.** Learning at step
nine that step ten names a parameter that does not exist is the worst version of
this feature: the browser is half way through a form and the flow was never
going to finish. A save is the moment an author is present and can fix it — so
every ``${name}`` is checked against the declared parameters here.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# A step's arguments are `args`, not `params`, and the distinction is the whole
# point: the FLOW has `parameters`, a run supplies `params`, and a step passes
# `args` to a tool. One word for two of those made `${name}` inside a step's
# `params` read as if it referred to the step's own. It is also the ordinary
# distinction — a parameter is declared, an argument is passed.
ARGS = "args"

STEP_KEYS = {
    "tool",  # which action — the discriminator
    ARGS,  # its arguments, literally and completely
    "id",  # a name for this step, unique in the flow
    "onError",  # abort (default) | continue
    "return",  # include this step's full result in the run report
    "note",  # a human comment
}
# `timeout` was specified as a step key and is deliberately NOT one. Nothing
# could honour it: a Selenium call blocks, so a wall-clock bound cannot
# interrupt one, and the actions that *can* be bounded already take
# `wait_timeout` in their own params — which is the per-step bound, is
# validated against each tool's schema, and is what a step should use. A key
# that parses and then does nothing is worse than a key that is refused.

ON_ERROR = ("abort", "continue")

# Lifecycle, not action. A flow runs against the browser the caller already has,
# which is what lets one flow be run on Chrome and then on Firefox without being
# edited — see §F1.9. A flow that opened its own browser would also end the
# caller's, since open_session replaces the one you are holding.
NOT_STEPS = {"open_session", "end_browser"}

# The runner supplies this. A step naming it would be addressing someone else's
# browser, which is the one thing a caller must never be able to do.
RESERVED_PARAMS = {"session_id"}

# A reference to one of the flow's own parameters, written in any string of any
# argument. `${name}` and nothing cleverer: no expressions, no defaults, no
# dotted paths — those are a language, and a language in a config file is a
# thing nobody can validate at save time.
# One token: either an escaped sigil or a reference. Matched together and in
# one left-to-right pass, so an escape can never be read as a reference and a
# substituted value is never rescanned — `re.sub` does not revisit what it
# wrote. That is what keeps a caller's value from resolving anything.
PARAM_REFERENCE = re.compile(r"\$\$\{|\$\{([^{}]*)\}")
# `$${` is a literal `${`. Written as a doubled sigil rather than a backslash
# because YAML already eats backslashes and an author should not have to know
# how many to write.
ESCAPED = "$${"

# The argument through which a secret reaches the page. Not a table of tools:
# `write` is the only action with a `secret` parameter, so the tool schemas
# refuse it everywhere else without this module holding a second list that
# could disagree with them (§F1.38).
SECRET_ARG = "secret"

# `write` accepts its value as `text` or as a `secret`, so the tool schema
# marks neither required and this says what it actually needs. A secret
# satisfies it through `bound` rather than by being listed here — listing
# both would demand both.
NEEDED_SOMEHOW = {"write": ("text",)}

# Tools that act on an element, and the ones where naming none is legitimate —
# press_key goes wherever focus is, screenshot captures the viewport, frame
# takes an index instead.
ADDRESSES_AN_ELEMENT = {
    "interact", "write", "extract", "upload_file", "press_key", "screenshot", "frame",
}
OPTIONAL_ELEMENT = {"press_key", "screenshot"}


def _needs_an_element(tool: str, params: dict) -> bool:
    """Whether this particular call has to name one.

    `frame` is not simply optional: `switch` needs xpath, css **or** index, and
    only `parent` and `default` need nothing. Treating the whole action as
    optional let `frame(action="switch")` with no target save cleanly and then
    be refused by `actions.frame` at run time, which is precisely the split
    between validation and execution that save-time checking exists to close.
    """
    if tool == "frame":
        action = str(params.get("action", "switch")).strip().lower()
        return action == "switch" and params.get("index") is None
    return tool not in OPTIONAL_ELEMENT


def listed(keys) -> str:
    """Keys from a document, as a sorted, comma-separated line for a message.

    Every key is made a string first. A flow is YAML that anyone may have
    written by hand, and YAML happily makes `1:` an integer key — so sorting a
    mix of `1` and `"name"` raised TypeError, and so did joining even a lone
    `1`. The message whose job was to refuse a malformed document became a 500
    about our own code instead.
    """
    return ", ".join(sorted(str(key) for key in keys))


REFERENCE_FIELDS = ("name", "key")


def reference_problems(reference) -> list[str]:
    """What is wrong with a secret reference.

    It names a secret and a key inside it. Two fields, never one dotted string:
    a Kubernetes key is routinely `tls.crt`, so there is no split point to find
    (§F1.26).

    Shared with `secrets.bind`, because the HTTP body reaches the binder as raw
    JSON: the binder read `name` and `key` and ignored the rest, so
    `{"name": ..., "key": ..., "namespace": ...}` ran as a different binding
    than the request described, while the validator and the MCP model both
    refused it. Unknown fields are refused, never dropped.
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

    A branch that is a **model** arrives as ``$ref`` with no ``type`` of its
    own, and skipping it left `write`'s optional `secret` reading as
    null-only — so the one shape it exists to accept was refused as "should be
    null, got dict". A reference is to an object; what is inside it is
    `reference_problems`' business, not this one's.
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


def references(value) -> list[str]:
    """Every ``${name}`` in ``value``, however deeply nested.

    Walks the whole argument rather than only top-level strings: a selector
    inside a list, or a header inside a mapping, is exactly as much a place an
    author will put a parameter, and one that validated nowhere would fail at
    run time — which is the split save-time checking exists to close.

    Escaped sigils are removed before scanning, so ``$${name}`` contributes no
    reference and cannot be reported as an undeclared one.
    """
    if isinstance(value, str):
        return [
            match.group(1)
            for match in PARAM_REFERENCE.finditer(value)
            if match.group(1) is not None
        ]
    if isinstance(value, dict):
        return [name for item in value.values() for name in references(item)]
    if isinstance(value, list):
        return [name for item in value for name in references(item)]
    return []


def _check_references(where: str, args: dict, declared: set[str]) -> list[str]:
    """Every parameter an argument names must be one the flow declares."""
    problems = []
    for name in dict.fromkeys(references(args)):
        if not name:
            problems.append(
                f"{where}: ${{}} names no parameter — write ${{name}}, or "
                "$${} for a literal"
            )
        elif name not in declared:
            known = listed(declared) or "this flow declares none"
            problems.append(
                f"{where}: ${{{name}}} is not a parameter of this flow. "
                f"Declared: {known}"
            )
    return problems


def _check_secret(where: str, reference) -> list[str]:
    """``args.secret``: which secret, and which key inside it."""
    return [f"{where}: {problem}" for problem in reference_problems(reference)]


def _is_whole_reference(value) -> bool:
    """Whether ``value`` is one reference and nothing else.

    `"${secs}"` is; `"page ${n}"` is not, and neither is `"$${secs}"` — an
    escape matches the same expression, so the group has to be checked rather
    than the match alone.
    """
    if not isinstance(value, str):
        return False
    whole = PARAM_REFERENCE.fullmatch(value)
    return whole is not None and whole.group(1) is not None


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
        # An argument that is *exactly* one reference carries the parameter's
        # value with its type intact — `substitute` is deliberate about that —
        # so checking the placeholder against the tool's schema would refuse
        # `wait_timeout: "${secs}"` for being a string, when at run time it is
        # the integer the caller passed. The reference is checked instead:
        # `_check_references` has already required the name to be declared.
        if _is_whole_reference(value):
            continue
        if not _type_fits(value, _types(properties[name])):
            accepted = " or ".join(sorted(_types(properties[name])))
            problems.append(
                f"{where}: {tool}.{name} should be {accepted}, got "
                f"{type(value).__name__}"
            )

    # `bound` is what a secret supplies: `write.text`, and nothing else has one.
    for name in bound:
        if name in params:
            problems.append(
                f"{where}: {name} is given literally and by a secret — one "
                "value, one place. Give the text or the secret, not both"
            )

    # Arguments a tool needs but its JSON schema cannot demand, because they
    # may arrive by more than one route. `write` takes `text` OR a binding, so
    # neither is `required` in the schema — and without this, a step with no
    # text at all saved cleanly and failed at run time, which is the whole thing
    # validating-on-save exists to prevent.
    for argument in NEEDED_SOMEHOW.get(tool, ()):
        # `is None` as well as absent: the schema permits null so that
        # `is None` as well as absent: the schema permits null so a secret can
        # supply the value instead, and `args: {text: null}` would otherwise
        # save cleanly and have `Actions.write` type the string "None" into
        # the field.
        if params.get(argument) is None and argument not in bound:
            problems.append(
                f"{where}: {tool} needs {argument!r} — give it in {ARGS}, "
                "or give a secret for it to type"
            )

    # Same shape for the element: exactly one of xpath or css, which a schema
    # cannot say without a oneOf and `browser.locator` enforces at the boundary.
    if tool in ADDRESSES_AN_ELEMENT:
        named = [k for k in ("xpath", "css") if params.get(k) or k in bound]
        if len(named) > 1:
            problems.append(f"{where}: {tool} takes xpath or css, not both")
        elif not named and _needs_an_element(tool, params):
            problems.append(f"{where}: {tool} needs an element — give xpath or css")

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
    unknown = set(step) - STEP_KEYS
    if unknown:
        problems.append(
            f"{where}: unknown step key {listed(unknown)}; "
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

    for key, kind in (("id", str), ("note", str)):
        if key in step and not isinstance(step[key], kind):
            problems.append(f"{where}: {key} must be a {kind.__name__}")
    if "return" in step and not isinstance(step["return"], bool):
        problems.append(f"{where}: return must be true or false")

    args = step.get(ARGS, {})
    if not isinstance(args, dict):
        return [*problems, f"{where}: {ARGS} must be an object"]

    # Every `${name}` anywhere in the arguments, against what the flow declares.
    # Checked before the schema check below, because an argument whose value is
    # a reference is still the type the tool wants once it is substituted.
    problems += _check_references(where, args, declared)

    secret = args.get(SECRET_ARG)
    bound = set()
    if secret is not None:
        bound = {"text"}
        problems += _check_secret(where, secret)
        if args.get("url"):
            # The leash is checked against the page the browser is on. A step
            # that navigates first would be checked against the page it is
            # leaving, and a redirect would defeat even that. Navigate as its
            # own step.
            problems.append(
                f"{where}: a step that types a secret may not also navigate — "
                "put the url in its own navigate step, so the secret's allowed "
                "sites are checked against the page that receives it"
            )
        # A secret is the one value that may not be assembled from text, so it
        # is the one argument a reference may not reach. Nothing about `${}`
        # resolution would leak here — the reference names a *parameter* — but
        # a secret whose name came from the caller is a caller choosing which
        # credential gets typed, which is not a decision a flow's parameters
        # are allowed to make.
        if references(secret):
            problems.append(
                f"{where}: a secret cannot be named by a parameter — "
                "write the name and key literally"
            )

    checked = _check_params(where, tool, args, bound, schemas[tool])
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
    properties = parameters.get("properties") or {}
    if not isinstance(properties, dict):
        problems.append("parameters.properties must be an object")
        properties = {}
    declared = set(properties)
    # `writeOnly` is standard JSON Schema for "supplied but not returned", and
    # it used to mean exactly that here. Accepting it now that nothing redacts
    # it is the worst of both: an author marks a password `writeOnly`, believes
    # it is hidden, and reads it back out of the report. A familiar marker that
    # silently does nothing is a leak with a reassuring name on it, so it is
    # refused and the refusal says what to use instead (§F1.38).
    # Sorted by the *string* of each key, and every value type-checked before
    # it is read. A flow is YAML anyone may have written by hand: `properties:
    # []` has no `.items()`, `1:` is an integer key so sorting it beside a
    # string raises TypeError, and a scalar schema has no `.get`. Each of those
    # turned a document this function exists to refuse into a 500 about our own
    # code — the same trap `listed()` was written for.
    for name in sorted(properties, key=str):
        schema = properties[name]
        if isinstance(schema, dict) and schema.get("writeOnly"):
            problems.append(
                f"parameters: {name!r} is writeOnly, which no longer hides "
                "anything — a parameter is text and may appear in the report. "
                "A value nobody may see is a secret: give write an args.secret "
                "instead"
            )
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
