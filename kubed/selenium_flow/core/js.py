"""The JavaScript this server runs in the page, read from the `js/` files.

`js/` sits at the repo root and `pyproject.toml` maps it into the package, as it
maps `plugin/`. `script()` concatenates files, since `execute_script` hands the
browser one string and has no module loader.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

JS_DIR = "js"


def js_path() -> Path:
    """The scripts directory, installed or in a source checkout."""
    packaged = Path(__file__).parent / JS_DIR
    if packaged.is_dir():
        return packaged
    # parents[3] because this module lives in core/: package, kubed, repo root.
    return Path(__file__).parents[3] / JS_DIR


@cache
def read(name: str) -> str:
    """One script, by file name. Cached: these never change while running."""
    path = js_path() / name
    if not path.is_file():
        # A missing file means the wheel was built without its package data.
        raise FileNotFoundError(
            f"no script {name!r} in {js_path()}. The js/ directory has to be "
            "mapped into the package by pyproject.toml's package-dir and "
            "package-data, or an installed copy has no scripts at all"
        )
    return path.read_text(encoding="utf-8")


def script(*names: str) -> str:
    """Several scripts as one, in the order given, joined with a newline."""
    return "\n".join(read(name) for name in names)
