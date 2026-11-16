"""A flow's parameters: what a run must be given, and where each value lands.

Pure functions over JSON. `${name}` is substituted into any argument, anywhere;
a secret never goes through a string. `substitute` keeps three rules:

- one pass, so a value a caller supplied is never rescanned and passing
  ``${admin_token}`` as a value yields that text;
- only names the caller supplied are replaced, so a script holding a
  JavaScript template literal, ``return `${window.scrollY}px` ``, survives;
- `$${` is a literal `${`, matched by the same expression as a reference so
  an escape can never be read as one.
"""

from __future__ import annotations

import re

from .shape import Shape

# One token: an escaped sigil, or a reference with its name in group 1. No
# expressions, defaults or dotted paths.
PARAM_REFERENCE = re.compile(r"\$\$\{|\$\{([^{}]*)\}")
# A doubled sigil, because YAML already eats backslashes.
ESCAPED = "$${"


def listed(keys) -> str:
    """Keys from a document, as a sorted, comma-separated line for a message.

    Every key is made a string first: YAML makes `1:` an integer key.
    """
    return ", ".join(sorted(str(key) for key in keys))


def references(value) -> list[str]:
    """Every ``${name}`` in ``value``, however deeply nested. ``$${name}`` is
    no reference."""
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

    A step that fails is reported, not raised. A ValueError, so
    `errors.status_for` answers 400: the caller can fix every one of these.
    """


def with_defaults(document: dict, params: dict) -> dict:
    """``params``, plus the declared ``default`` of anything the caller omitted.

    Only when absent: an explicit `None` is a value the caller chose. Applied
    after `check_params`, so a default never satisfies `required`.
    """
    filled = dict(params or {})
    for name, spec in Shape(document).properties.items():
        if not isinstance(spec, dict) or name in filled:
            continue
        if "default" in spec:
            filled[name] = spec["default"]
    return filled


def check_params(document: dict, params: dict) -> None:
    """Refuse before step one rather than at step two with half a form filled."""
    shape = Shape(document)
    missing = [name for name in shape.required if name not in (params or {})]
    if missing:
        raise FlowError(
            f"{document.get('name', 'this flow')} needs "
            f"{listed(missing)}: pass them in params"
        )
    declared = shape.declared
    unknown = set(params or {}) - declared
    if unknown:
        known = listed(declared) or "it takes none"
        raise FlowError(
            f"{document.get('name', 'this flow')} does not take "
            f"{listed(unknown)}. Takes: {known}"
        )


def substitute(value, params: dict):
    """``value`` with every ``${name}`` replaced by the parameter it names.

    One pass: `re.sub` never revisits what it wrote. A string that is exactly
    one reference takes the parameter's value with its type; anywhere else the
    value is written in as text. Walks lists and mappings.
    """
    if isinstance(value, str):
        whole = PARAM_REFERENCE.fullmatch(value)
        if whole is not None and whole.group(1) in params:
            return params[whole.group(1)]

        def one(match):
            if match.group(1) is None:
                return "${"
            if match.group(1) not in params:
                return match.group(0)
            return str(params[match.group(1)])

        return PARAM_REFERENCE.sub(one, value)
    if isinstance(value, dict):
        return {key: substitute(item, params) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, params) for item in value]
    return value
