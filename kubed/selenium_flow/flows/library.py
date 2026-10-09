"""A flow document as text: parsed once, complained about safely, summarised.

Where the text is kept is ``flows.store``; this is what the text *is*, and it
is the same for a directory today and for WebDAV tomorrow.
"""

from __future__ import annotations

import copy
import threading

import yaml
from cachetools import LRUCache, cached

from ..faults import TooLarge
from .shape import Shape


def yaml_complaint(exc: Exception) -> str:
    """Say where a flow document broke, without quoting what was there.

    PyYAML's own message embeds the offending source line verbatim. That text
    is a person's document, typed into an editor that accepts anything, and it
    travels further than the person expects: into the HTTP response and into
    the server log, which outlives the request and is read by people who were
    never shown the flow. Position plus the parser's short ``problem`` is
    enough to find the mistake and carries none of the line.

    Every YAML failure in this server goes through here — the store reading a
    hand-edited file and the admin editor saving one are the same disclosure.
    """
    problem = getattr(exc, "problem", None) or "it could not be parsed"
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return str(problem).strip()
    return f"{str(problem).strip()} (line {mark.line + 1}, column {mark.column + 1})"


# libyaml when the wheel has it, which every platform we ship does. The
# pure-Python parser is ten times slower, and a Secrets tab or a flow listing
# parses every stored flow: 26 flows took 1.1s of CPU in the pod (§F4.19).
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


# Measured in YAML source, not entries: 512 entries is no bound at all when one
# entry can be any size. Today's flows average 3KB, so this holds over a
# thousand, and a document bigger than all of it is parsed but never kept.
CACHE_BYTES = 4 * 2**20
# What every entry costs beyond its source, the key and the parsed dict, so a
# directory of empty files cannot fill the cache for free (Copilot, #45).
ENTRY_BYTES = 1024


@cached(
    LRUCache(maxsize=CACHE_BYTES, getsizeof=lambda kept: kept[0]),
    condition=threading.Condition(),
)
def _parsed(text: str) -> tuple[int, object]:
    return len(text.encode()) + ENTRY_BYTES, yaml.load(text, Loader=_LOADER)


def view(text: str):
    """A YAML document, parsed once per distinct text (§F4.19), and SHARED.

    Keyed on the text itself, not a TTL or an mtime: reading a file is cheap
    and parsing it is not, and a key that *is* the content cannot serve a stale
    flow after a save, a hand edit or a clock that ticks coarser than the disk.

    The object is the cache's own, so it is read and never changed. A reader
    that keeps or hands out any part of it takes a copy of that part: `parse`
    copies the whole, `summary` only the small fields it returns.
    """
    return _parsed(text)[1]


FLOW_CAP = 2**20


def check_size(text: str) -> None:
    """Refuse a flow document over 1 MiB: they run to a few KB, and parsing is
    the work an oversized one would make a token-holder's save cost."""
    if len(text.encode()) > FLOW_CAP:
        raise TooLarge(f"a flow is limited to {FLOW_CAP // 2**20} MiB of YAML")


def parse(text: str):
    """A YAML document, parsed once per distinct text: a copy every time,
    because callers own what they get back. See `view`."""
    return copy.deepcopy(view(text))


def dump(document: dict) -> str:
    """``document`` as the YAML a save writes: keys in the order given.

    The pure-Python emitter, not libyaml's, though libyaml is seven times
    faster: it escapes anything outside the Basic Multilingual Plane (`😀`
    becomes `"\\U0001F600"`) and breaks long quoted lines elsewhere, and a
    person reads this text in the admin editor (Dr K).
    """
    return yaml.safe_dump(document, sort_keys=False, width=100, allow_unicode=True)


def summary(name: str, workspace: str, document: dict) -> dict:
    """Name, description and parameters for one flow — never the steps.

    ``document`` may be the cache's own (`view`), so the two fields handed out
    are copied and the steps, the bulk of a document, are only counted.
    """
    return {
        "name": name,
        "workspace": workspace,
        "description": copy.deepcopy(document.get("description", "")),
        "parameters": copy.deepcopy(document.get("parameters", {})),
        # Named for what it is. Calling it `steps` would put an int where the
        # document itself carries a list, and E2 reads both — one consumer
        # doing summary["steps"][0] is the whole cost of the shorter name.
        "step_count": Shape(document).step_count,
    }
