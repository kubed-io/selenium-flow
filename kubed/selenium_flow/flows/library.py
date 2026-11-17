"""A flow document as text: parsed once, complained about safely, summarised.

Where the text is kept is ``flows.store``.
"""

from __future__ import annotations

import copy
import re
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


def load_typed(text: str):
    """YAML a person typed, parsed by the pure-Python loader.

    libyaml crashes the process on nesting deep enough, well under any size
    cap; the pure-Python loader runs out of recursion first, which is refused
    here as a YAMLError. Whatever it accepts is shallow enough for libyaml.
    """
    try:
        return yaml.load(text, Loader=yaml.SafeLoader)
    except RecursionError:
        raise yaml.MarkedYAMLError(problem="it nests too deeply") from None
    except ValueError as exc:
        # A scalar its resolver cannot construct, such as a 13th month.
        raise yaml.MarkedYAMLError(problem=f"a value cannot be read: {exc}") from None


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


def _top_key(line: str) -> str | None:
    """The top-level key ``line`` opens, or None."""
    found = re.match(r"([A-Za-z_][\w-]*)[ \t]*:(?:[ \t]|\r?$)", line)
    return found.group(1) if found else None


def _block(lines: list[str], start: int) -> int:
    """Where the top-level value opened at ``start`` ends: past its indented
    and ``-`` lines, never taking the blank or comment lines after it."""
    end = start + 1
    taken = end
    while end < len(lines):
        line = lines[end]
        if line[:1] in (" ", "\t", "-"):
            taken = end = end + 1
        elif not line.strip() or line.startswith("#"):
            end += 1
        else:
            break
    return taken


def _flow_style(text: str) -> bool:
    """Whether the document's top-level mapping is written ``{...}``."""
    for event in yaml.parse(text, Loader=_LOADER):
        if isinstance(event, yaml.MappingStartEvent):
            return bool(event.flow_style)
    return False


def with_tags(text: str, tags: list[str]) -> str:
    """``text`` with its top-level ``tags`` set to ``tags``, every other line
    kept byte for byte: the block is replaced, or added after ``description``
    (else after ``name``, else first), and no tags removes it.

    Raises ValueError when the result would not read back as that change.
    """
    document = view(text)
    if not isinstance(document, dict):
        raise ValueError("a flow document must be a YAML mapping")
    if _flow_style(text):
        raise ValueError(
            "this flow is stored as a flow-style mapping, such as JSON, which has "
            "no lines of its own to set tags on; edit its YAML instead"
        )
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += newline
    keys = {_top_key(line): at for at, line in enumerate(lines)}
    block = [
        line.replace("\n", newline)
        for line in (dump({"tags": tags}).splitlines(keepends=True) if tags else [])
    ]
    if "tags" in keys:
        at = keys["tags"]
        lines[at : _block(lines, at)] = block
    else:
        after = next((keys[k] for k in ("description", "name") if k in keys), None)
        if after is not None:
            at = _block(lines, after)
        else:
            at = min((at for k, at in keys.items() if k), default=len(lines))
        lines[at:at] = block
    rewritten = "".join(lines)
    expected = {key: value for key, value in document.items() if key != "tags"}
    if tags:
        expected["tags"] = tags
    try:
        reread = yaml.load(rewritten, Loader=_LOADER)
    except yaml.YAMLError:
        reread = None
    if reread != expected:
        raise ValueError(
            "the tags could not be changed without touching the rest of this "
            "flow; edit its YAML instead"
        )
    return rewritten
