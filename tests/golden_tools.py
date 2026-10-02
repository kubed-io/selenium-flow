"""Goldens: a published shape, committed as plain JSON, compared byte for byte.

A golden is the proof that a refactor did not change what an agent, a client or a
generated SDK can see. It is never updated in a commit that moves code: a
changed golden is a changed contract, and belongs in a commit that says so.

``GOLDEN_UPDATE=1`` rewrites the file. Without it a difference fails with the
first path that differs and a unified diff, so the failure names what moved.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden"
SIGNED = re.compile(r"exp=\d+&sig=[0-9a-f]+")


def render(data) -> str:
    """The one on-disk form: sorted keys, two-space indent, a final newline."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def normalised(data, *, replace: dict[str, str] | None = None):
    """``data`` with every volatile value replaced by a stable stand-in.

    Signed URLs lose their expiry and signature (``<signed>``); ``replace`` maps
    an exact string to its stand-in, for a version or a generated id.
    """
    replace = replace or {}
    if isinstance(data, dict):
        return {k: normalised(v, replace=replace) for k, v in data.items()}
    if isinstance(data, list):
        return [normalised(v, replace=replace) for v in data]
    if isinstance(data, str):
        return replace.get(data) or SIGNED.sub("<signed>", data)
    return data


def first_difference(want, got, path="$") -> str | None:
    """The JSON path of the first place two documents differ, or None."""
    if type(want) is not type(got):
        return path
    if isinstance(want, dict):
        for key in sorted(set(want) | set(got)):
            if key not in want or key not in got:
                return f"{path}.{key}"
            found = first_difference(want[key], got[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(want, list):
        for index, (a, b) in enumerate(zip(want, got, strict=False)):
            found = first_difference(a, b, f"{path}[{index}]")
            if found:
                return found
        return path if len(want) != len(got) else None
    return None if want == got else path


def compare(name: str, data) -> None:
    """Assert ``data`` is the committed golden ``name`` (or write it).

    The tool goldens are keyed by tool name, so the path names the tool.
    """
    path = GOLDEN / name
    got = render(data)
    if os.environ.get("GOLDEN_UPDATE") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got)
        return
    assert path.exists(), f"{name} is missing; run with GOLDEN_UPDATE=1 to write it"
    want = path.read_text()
    if want == got:
        return
    where = first_difference(json.loads(want), json.loads(got)) or "(formatting)"
    diff = "".join(
        difflib.unified_diff(
            want.splitlines(True),
            got.splitlines(True),
            f"golden/{name}",
            "actual",
            n=3,
        )
    )
    raise AssertionError(
        f"{name} differs from its golden, first at {where}\n{diff[:6000]}"
    )
