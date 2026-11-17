"""A flow document as text: parsed once, complained about safely, summarised.

Where the text is kept is ``flows.store``.
"""

from __future__ import annotations

import copy
import threading

import yaml
from cachetools import LRUCache, cached

from ..faults import TooLarge
from . import places
from .shape import Shape


def yaml_complaint(exc: Exception) -> str:
    """Say where a flow document broke, without quoting what was there.

    PyYAML's own message quotes the offending line, and this goes into HTTP
    answers and the log. Every YAML failure in this server goes through here.
    """
    problem = getattr(exc, "problem", None) or "it could not be parsed"
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return str(problem).strip()
    return f"{str(problem).strip()} (line {mark.line + 1}, column {mark.column + 1})"


# libyaml when the wheel has it: the pure-Python parser is ten times slower,
# and a listing parses every stored flow.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


# Measured in YAML source, since an entry can be any size. A document bigger
# than all of it is parsed and never kept.
CACHE_BYTES = 4 * 2**20
# What every entry costs beyond its source, so empty files cannot fill the cache
# for free.
ENTRY_BYTES = 1024


@cached(
    LRUCache(maxsize=CACHE_BYTES, getsizeof=lambda kept: kept[0]),
    condition=threading.Condition(),
)
def _parsed(text: str) -> tuple[int, object]:
    return len(text.encode()) + ENTRY_BYTES, yaml.load(text, Loader=_LOADER)


def view(text: str):
    """A YAML document, parsed once per distinct text, and shared.

    Keyed on the text itself, so no save, hand edit or coarse clock can serve
    a stale flow. The object is the cache's own: read it, never change it, and
    copy any part that leaves (`parse` copies the whole).
    """
    return _parsed(text)[1]


FLOW_CAP = 2**20


def check_size(text: str) -> None:
    """Refuse a flow document over 1 MiB, before it is parsed."""
    if len(text.encode()) > FLOW_CAP:
        raise TooLarge(f"a flow is limited to {FLOW_CAP // 2**20} MiB of YAML")


def parse(text: str):
    """A YAML document, parsed once per distinct text: a copy every time,
    because callers own what they get back. See `view`."""
    return copy.deepcopy(view(text))


def dump(document: dict) -> str:
    """``document`` as the YAML a save writes: keys in the order given.

    The pure-Python emitter: libyaml's escapes anything outside the Basic
    Multilingual Plane and breaks long quoted lines elsewhere, and a person
    reads this text in the admin editor.
    """
    return yaml.safe_dump(document, sort_keys=False, width=100, allow_unicode=True)


def summary(name: str, workspace: str, document: dict) -> dict:
    """Name, description, tags and parameters for one flow, never the steps.

    ``document`` may be the cache's own (`view`), so what is handed out is
    copied.
    """
    # Places only when they can be read: a bad hand edit lists without them.
    where = {}
    if not places.problems(document):
        where = {
            key: copy.deepcopy(document[key])
            for key in (places.SITES, places.PATHS)
            if document.get(key) is not None
        }
    shape = Shape(document)
    tagged = {"tags": shape.tags} if shape.tags else {}
    return {
        "name": name,
        "workspace": workspace,
        "description": copy.deepcopy(document.get("description", "")),
        **tagged,
        **where,
        "parameters": copy.deepcopy(document.get("parameters", {})),
        "step_count": shape.step_count,
    }
