"""The JavaScript this server runs in the page, as JavaScript files.

It used to live in triple-quoted Python strings, which cost more than it looked
like it did. An editor saw one long string, so there was no highlighting, no
bracket matching and nothing to lint it with. Worse, the text was *escaped*: a
regular expression had to be written `/\\\\s+/` in the source to reach the browser
as `/\\s+/`, and a missing backslash was a SyntaxWarning at import and a broken
script at run time.

So they are files now, in `js/` at the repo root, and `pyproject.toml` maps that
directory into the package the same way it maps `prompts/`, `skills/` and
`static/` — it reads as source in a checkout and installs inside the wheel. Get
the mapping wrong and the scripts are simply absent from an installed copy, so
`tests/test_packaging.py` asserts every mapped directory exists and rebuilds the
image, and `tests/test_js.py` asserts every file parses and is actually used.

`script()` is the only way to read one, and it **concatenates** rather than
letting a file import another: these run through `execute_script`, which has no
module loader, and the browser is handed one string.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

JS_DIR = "js"


def js_path() -> Path:
    """The scripts directory, installed or in a source checkout.

    Same two-step as `prompts.prompts_path`, and for the same reason: the folder
    lives at the repo root where it reads as source, and pyproject maps it into
    the package at build time.
    """
    packaged = Path(__file__).parent / JS_DIR
    if packaged.is_dir():
        return packaged
    # parents[3] because this module lives in core/: package, kubed, repo root.
    return Path(__file__).parents[3] / JS_DIR


@cache
def read(name: str) -> str:
    """One script, by file name. Cached — these never change while running."""
    path = js_path() / name
    if not path.is_file():
        # Loud, and at the first call rather than as an empty script the browser
        # silently evaluates to undefined. A missing file here means the wheel
        # was built without its package data, which is a packaging bug and not
        # something a caller can do anything about.
        raise FileNotFoundError(
            f"no script {name!r} in {js_path()}. The js/ directory has to be "
            "mapped into the package by pyproject.toml's package-dir and "
            "package-data, or an installed copy has no scripts at all"
        )
    return path.read_text(encoding="utf-8")


def script(*names: str) -> str:
    """Several scripts as one, in the order given.

    Joined with a newline so a file that ends in `};` and one that starts with
    `const` do not run together into a syntax error.
    """
    return "\n".join(read(name) for name in names)
