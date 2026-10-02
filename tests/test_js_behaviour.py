"""What the page scripts in `js/` answer, run for real under jsdom.

`test_js.py` proves they parse; nothing but the integration suite (which needs
a Grid) proved they were right. `tests/js/run.mjs` evaluates a script against a
canned page, binding `arguments` as `execute_script` does, so a change to
`helpers.js` is provable in the unit suite.

jsdom does no layout: every rectangle is zero and `elementFromPoint` does not
exist. Where a behaviour depends on geometry the test stubs it in the window
(the `setup` argument) and says so; what is pinned is then the script's logic
over those numbers, not the browser's idea of them.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kubed.selenium_flow.core import probe
from kubed.selenium_flow.site_data import transfer

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parent.parent
RUNNER = Path(__file__).parent / "js" / "run.mjs"

if shutil.which("node") is None or not (ROOT / "ui/node_modules/jsdom").is_dir():
    # In CI a missing toolchain is a broken workflow, not a reason to pass: the
    # tests would silently stop running there.
    if os.environ.get("CI"):
        pytest.fail("CI must install node and ui/node_modules", pytrace=False)
    pytest.skip("needs node and ui/node_modules/jsdom", allow_module_level=True)


def run(scripts, html, args=(), setup="", source=None):
    """The script's return, decoded; elements arrive as `tag#id.class` strings."""
    env = {**os.environ, **({"SCRIPT_SOURCE": source} if source else {})}
    done = subprocess.run(
        ["node", str(RUNNER), scripts, html, json.dumps(list(args)), setup],
        capture_output=True, text=True, check=False, env=env,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def usable(html, css, setup=""):
    return run("helpers.js+usable.js", html, [{"$css": css}], setup)


def outline(html, text="", limit=50, interactive=True, selector=probe.INTERACTIVE):
    return run(
        "helpers.js+outline.js", html, [None, text, limit, interactive, selector]
    )


def test_a_hidden_menu_item_names_the_control_that_opens_it():
    """The page's own aria-expanded declaration decides the gesture: a click,
    not the hover a guess would offer, and the opener is found as an ancestor's
    descendant, not by position."""
    answer = usable(
        '<li class="dropdown"><a id="toggle" aria-expanded="false">Menu</a>'
        '<ul style="display:none"><li><a id="item" href="/x">Item</a></li></ul></li>',
        "#item",
    )
    assert answer["reason"] == "hidden"
    assert answer["detail"] == "ul"
    assert answer["trigger"] == "#toggle"
    assert answer["gesture"] == "click"


def test_a_control_naming_a_different_menu_is_not_offered_as_the_opener():
    answer = usable(
        '<nav><a id="other" aria-expanded="false" aria-controls="b">B</a>'
        '<ul id="a" style="display:none"><li><a id="item" href="/x">x</a></li></ul>'
        '<ul id="b" style="display:none"></ul></nav>',
        "#item",
    )
    assert answer["reason"] == "hidden"
    assert answer["trigger"] != "#other"


def test_an_ancestor_that_controls_something_else_is_not_the_opener():
    answer = usable(
        '<div id="wrap" aria-expanded="false" aria-controls="elsewhere">'
        '<ul style="display:none"><li><a id="item" href="/x">x</a></li></ul></div>'
        '<div id="elsewhere"></div>',
        "#item",
    )
    assert answer["reason"] == "hidden"
    # Offered only as the visible ancestor to hover, never as a stated opener.
    assert answer["gesture"] == "hover"


def test_the_centre_is_that_of_the_part_of_the_element_in_view():
    """Stubbed: a 100x100 box hanging off the left of a 1024x768 viewport. The
    centre is of the visible half, [25, 50], not of the box, [0, 50]."""
    setup = (
        "document.getElementById('box').getBoundingClientRect = () => "
        "({left: -50, right: 50, top: 0, bottom: 100, width: 100, height: 100});"
    )
    answer = run(
        "center.js", '<div id="box"></div>', [{"$css": "#box"}, False], setup
    )
    # jsdom's window is 1024x768, and the size comes back with the centre so a
    # pointer move asks the page once, not twice.
    assert answer == {
        "at": [25, 50], "scrolled": False, "outside": False, "viewport": [1024, 768],
    }


def test_an_element_wholly_outside_the_viewport_reports_outside_and_its_own_centre():
    setup = (
        "document.getElementById('box').getBoundingClientRect = () => "
        "({left: 2000, right: 2100, top: 0, bottom: 100, width: 100, height: 100});"
    )
    answer = run(
        "center.js", '<div id="box"></div>', [{"$css": "#box"}, False], setup
    )
    assert answer == {
        "at": [2050, 50], "scrolled": False, "outside": True, "viewport": [1024, 768],
    }


COVERED = (
    "const r = {left: 0, right: 100, top: 0, bottom: 40, width: 100, height: 40};"
    "document.querySelectorAll('*').forEach(e => e.getBoundingClientRect = () => r);"
    "document.elementFromPoint = () => document.getElementById('modal');"
)


def test_explain_for_a_covered_element_names_what_covers_it():
    answer = usable(
        '<button id="go" class="primary">Go</button>'
        '<div id="modal" class="overlay"></div>',
        "#go",
        COVERED,
    )
    assert answer == {"reason": "covered", "detail": "div#modal"}


def test_explain_for_a_disabled_control_says_so_before_anything_else():
    answer = usable('<button id="go" disabled>Go</button>', "#go")
    assert answer == {"reason": "disabled", "detail": "button#go"}


PAGE = """
<header><a href="/home">Home</a></header>
<nav><a href="/docs">Docs</a></nav>
<main>
  <h1>Title</h1>
  <button id="save" class="primary">Save</button>
  <input type="checkbox" id="agree" aria-label="Agree">
  <input type="hidden" name="token" value="x">
  <a href="/more">More</a>
</main>
<footer><a href="/legal">Legal</a></footer>
"""


def test_the_outline_lists_content_before_chrome_with_roles_and_regions():
    answer = outline(PAGE)
    names = [(e["role"], e["name"], e.get("region")) for e in answer["elements"]]
    assert names == [
        ("button", "Save", "main"),
        ("checkbox", "Agree", "main"),
        ("link", "More", "main"),
        ("link", "Home", "banner"),
        ("link", "Docs", "navigation"),
        ("link", "Legal", "contentinfo"),
    ]
    assert answer["total"] == 6


def test_the_outline_leaves_out_hidden_inputs_and_headings_when_interactive():
    answer = outline(PAGE)
    assert all(e["role"] != "heading" for e in answer["elements"])
    assert all("token" not in json.dumps(e) for e in answer["elements"])


def test_the_outline_counts_everything_but_returns_only_the_limit():
    answer = outline(PAGE, limit=2)
    assert len(answer["elements"]) == 2
    assert answer["total"] == 6


def test_the_outline_lists_headings_when_not_restricted_to_interactive():
    answer = outline(PAGE, interactive=False)
    headings = [e for e in answer["elements"] if e["role"] == "heading"]
    assert [h["name"] for h in headings] == ["Title"]


def test_every_outline_selector_matches_exactly_the_element_it_describes():
    page = (
        '<main><button id="a">Same</button><button>Same</button>'
        '<a href="/x?q=1">One</a></main>'
    )
    answer = outline(page)
    assert len(answer["elements"]) == 3
    for entry in answer["elements"]:
        assert ("css" in entry) != ("xpath" in entry)
    # Resolve each selector in the same document the way a later call would,
    # and name what it finds: the element must be the one the entry describes.
    resolve = """
        const named = (el) => el.tagName.toLowerCase() + '|' + el.id + '|'
          + el.textContent;
        return arguments[0].map((s) => {
          if (s.css !== undefined) {
            return Array.from(document.querySelectorAll(s.css)).map(named);
          }
          const hits = document.evaluate(s.xpath, document, null, 5, null);
          const found = [];
          for (let n = hits.iterateNext(); n; n = hits.iterateNext()) {
            found.push(named(n));
          }
          return found;
        });
    """
    selectors = [
        {k: e[k] for k in ("css", "xpath") if k in e} for e in answer["elements"]
    ]
    found = run("-", page, [selectors], source=resolve)
    assert [len(f) for f in found] == [1, 1, 1], (selectors, found)
    assert [f[0] for f in found] == ["button|a|Same", "button||Same", "a||One"]


def test_the_site_data_fill_writes_the_given_keys_and_nothing_else():
    """`fill` is composed in Python, so the runner takes its source from the
    environment. The store already holds `keep`; it must survive, and the
    count answered is the number written."""
    items = {"a": "1", "b": '{"nested": "x"}'}
    source = (
        "localStorage.setItem('keep', 'mine'); "
        "const wrote = " + transfer.fill("localStorage", items) + "; "
        "const all = {}; for (let i = 0; i < localStorage.length; i++) "
        "{ const k = localStorage.key(i); all[k] = localStorage.getItem(k); } "
        "return {wrote, all, session: sessionStorage.length};"
    )
    answer = run("-", "<p></p>", source=source)
    assert answer["wrote"] == 2
    assert answer["all"] == {"keep": "mine", **items}
    assert answer["session"] == 0
