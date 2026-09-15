"""The JavaScript, as files rather than as strings inside Python.

It used to live in triple-quoted literals, and that cost more than it looked
like it did: no highlighting, nothing that could lint it, and every regular
expression double-escaped to survive the Python parser — `/\\\\s+/` in the source
to reach the browser as `/\\s+/`. A missing backslash was a SyntaxWarning at
import and a broken script at run time.

So this file guards the two things that can now go wrong instead: a script that
does not parse, and a script that is in the wheel and used by nothing (or used
and not in the wheel).
"""

import shutil
import subprocess

import pytest

from kubed.selenium_flow import js, pointer, probe

pytestmark = pytest.mark.unit

# Every file in js/, so a new one is covered the moment it is added rather than
# when somebody remembers to list it here.
SCRIPTS = sorted(path.name for path in js.js_path().glob("*.js"))


def test_there_are_scripts_to_check():
    """Without this the parametrised tests below pass by having no cases."""
    assert SCRIPTS, f"no .js files found in {js.js_path()}"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to parse JS")
@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_parses(tmp_path, name):
    """Wrapped in a function because that is how WebDriver runs them: each has a
    top-level `return`, which is only legal inside one. A script that does not
    parse fails in the browser as a WebDriverException whose message is not
    about the mistake."""
    path = tmp_path / name
    path.write_text("(function () {\n" + js.read(name) + "\n});", encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


# What WebDriver is actually handed. Each file parsing on its own does not make
# the concatenation parse: `helpers.js` goes into both of these, so a `const`
# it already declares, redeclared by the file joined to it, is a SyntaxError
# that only exists in the composition. The per-file loop above cannot see it,
# and the browser reports it as a WebDriverException about nothing useful.
COMPOSED = {
    "USABLE_JS": probe.USABLE_JS,
    "OUTLINE_JS": probe.OUTLINE_JS,
    "NUDGE_JS": pointer.NUDGE_JS,
    "CENTER_JS": pointer.CENTER_JS,
}


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to parse JS")
@pytest.mark.parametrize("name", sorted(COMPOSED))
def test_every_composed_script_parses(tmp_path, name):
    """Copilot, #32: the per-file check replaced the only tests that parsed
    these, so a composition-only error would have passed here and failed in the
    browser."""
    path = tmp_path / f"{name}.js"
    path.write_text("(function () {\n" + COMPOSED[name] + "\n});", encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_is_used(name):
    """A file nobody loads still ships in the wheel and still looks maintained.
    The composed constants are the whole set of callers."""
    composed = "\n".join(
        [probe.USABLE_JS, probe.OUTLINE_JS, pointer.NUDGE_JS, pointer.CENTER_JS]
    )
    assert js.read(name) in composed, f"{name} is packaged and loaded by nothing"


def test_a_script_that_is_not_there_says_it_is_a_packaging_problem():
    """The failure mode this replaced was an empty string the browser evaluated
    to undefined. A missing file means the wheel was built without its package
    data, which no caller can do anything about, so it has to be loud."""
    with pytest.raises(FileNotFoundError) as missing:
        js.read("nothing-here.js")
    assert "package-dir" in str(missing.value)


def test_the_scripts_are_not_escaped_for_python_any_more():
    """The point of the move. A regular expression in a Python string needed
    `\\\\s` to reach the browser as `\\s`; in a file it is written once. A stray
    double backslash here means someone pasted escaped source back in."""
    for name in SCRIPTS:
        assert "\\\\s" not in js.read(name), f"{name} still carries Python escaping"


def test_helpers_are_shared_rather_than_copied():
    """One copy of the reasoning, concatenated into both scripts — the property
    saga §F2.8 rests on, now that the files could drift apart instead."""
    helpers = js.read(probe._HELPERS_FILE)
    assert helpers in probe.USABLE_JS
    assert helpers in probe.OUTLINE_JS
