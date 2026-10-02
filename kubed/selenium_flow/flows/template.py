"""A flow's parameters: what a run must be given, and where each value lands.

Pure functions over JSON. Nothing here runs a step, reads a secret or knows a
tool: a parameter is text, and this is the whole of what text needs.

**A parameter is text; a secret is structural** (§F1.38). `${name}` is
substituted into any argument, anywhere, and `args.secret` is not — a secret
goes straight into the keyword arguments and never through a string.

Three rules make the text half safe, and `substitute` below is where they live:

- **single pass**, so a value a caller supplied is never rescanned and passing
  ``${admin_token}`` as a value yields that text;
- **only names the caller supplied** are replaced, so a script holding a
  JavaScript template literal — ``return `${window.scrollY}px` `` — survives
  rather than being silently blanked;
- **`$${` is a literal `${`**, matched by the same expression as a reference so
  an escape can never be read as one.
"""

from __future__ import annotations

import re

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


def listed(keys) -> str:
    """Keys from a document, as a sorted, comma-separated line for a message.

    Every key is made a string first. A flow is YAML that anyone may have
    written by hand, and YAML happily makes `1:` an integer key — so sorting a
    mix of `1` and `"name"` raised TypeError, and so did joining even a lone
    `1`. The message whose job was to refuse a malformed document became a 500
    about our own code instead.
    """
    return ", ".join(sorted(str(key) for key in keys))


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


class FlowError(ValueError):
    """A run that could not start, or a step that could not be resolved.

    Distinct from a step *failing*, which is an ordinary outcome and is reported
    rather than raised.

    A **ValueError**, deliberately: every one of these is something the caller
    got wrong and can fix — a missing parameter, a name the flow does not take,
    a source this server cannot resolve. As a RuntimeError it reached
    `errors.status_for` as a 500, telling an n8n node with Retry-On-Fail to
    replay a request that was never going to work.
    """


def properties(document: dict) -> dict:
    """A flow's declared parameters, or nothing if the document is malformed.

    Defensive because this reads a stored file: `parameters: []` and a scalar
    `properties` are both things a hand edit produces, and neither may become a
    crash in a preflight whose job is to refuse documents cleanly.
    """
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    found = parameters.get("properties")
    return found if isinstance(found, dict) else {}


def required_params(document: dict) -> list[str]:
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        return []
    required = parameters.get("required")
    return list(required) if isinstance(required, list) else []


def with_defaults(document: dict, params: dict) -> dict:
    """``params``, plus the declared ``default`` of anything the caller omitted.

    A parameter is declared with JSON Schema's own vocabulary, and `default` is
    the word that vocabulary uses. It was accepted at save time, rendered in the
    admin panel as "Default", and then ignored — so a flow declaring
    `lang: {default: en}` and run without `lang` sent the browser to
    `https://${lang}.wikipedia.org` *literally* and failed on a DNS error that
    names nothing to do with the cause.

    `substitute` cannot fix this on its own, and deliberately: it leaves an
    unsupplied `${name}` exactly as written, because a `script` argument holding
    a JavaScript template literal is an ordinary payload and blanking it would
    corrupt the step. That rule is right. The gap was that nothing ever put the
    default into `params` for it to find.

    Only declared names, and only when absent — an explicit `None` is a value
    the caller chose, and overriding it here would make `lang: null` mean
    something different from every other value.

    Applied AFTER `check_params`, so a parameter that is both `required` and
    defaulted still has to be passed. A default that satisfied `required` would
    empty the word of meaning, and the two together are a contradiction in the
    document rather than a case worth honouring.
    """
    filled = dict(params or {})
    for name, spec in properties(document).items():
        if not isinstance(spec, dict) or name in filled:
            continue
        if "default" in spec:
            filled[name] = spec["default"]
    return filled


def check_params(document: dict, params: dict) -> None:
    """Refuse before step one rather than at step two with half a form filled."""
    missing = [name for name in required_params(document) if name not in (params or {})]
    if missing:
        raise FlowError(
            f"{document.get('name', 'this flow')} needs "
            f"{listed(missing)}: pass them in params"
        )
    declared = set(properties(document))
    unknown = set(params or {}) - declared
    if unknown:
        known = listed(declared) or "it takes none"
        raise FlowError(
            f"{document.get('name', 'this flow')} does not take "
            f"{listed(unknown)}. Takes: {known}"
        )


def substitute(value, params: dict):
    """``value`` with every ``${name}`` replaced by the parameter it names.

    **Single pass.** `re.sub` never revisits what it wrote, and an escape is
    matched by the same expression as a reference, so a parameter whose *value*
    contains ``${admin_token}`` yields that text and resolves nothing. This is
    the rule that keeps "a payload can never collide with a reference" true now
    that strings are scanned at all (§F1.38).

    A string that is **exactly** one reference takes the parameter's value with
    its type intact, so an integer parameter stays an integer and reaches a
    tool argument that wants one. Anywhere else the value is interpolated as
    text, because the result is a string by construction.

    Walks lists and mappings, because an argument is not always a flat string —
    a selector inside a list is as good a place for a parameter as any.
    """
    if isinstance(value, str):
        whole = PARAM_REFERENCE.fullmatch(value)
        if whole is not None and whole.group(1) in params:
            return params[whole.group(1)]

        def one(match):
            if match.group(1) is None:
                return "${"
            # **Only what the caller supplied.** Anything else is left exactly
            # as written, because it is not ours to interpret: a script holding
            # a JavaScript template literal — `return `${window.scrollY}px`` —
            # is an ordinary payload, and blanking it would corrupt the step
            # silently. Saving refuses an undeclared name, so a flow that went
            # through validation cannot reach here with one; a hand-edited one
            # keeps its text rather than losing it.
            if match.group(1) not in params:
                return match.group(0)
            return str(params[match.group(1)])

        return PARAM_REFERENCE.sub(one, value)
    if isinstance(value, dict):
        return {key: substitute(item, params) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, params) for item in value]
    return value
