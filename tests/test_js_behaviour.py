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

from kubed.selenium_flow.core import browser, probe
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


def outline(html, text="", limit=50, interactive=True, selector=probe.INTERACTIVE, setup=""):
    return run(
        "helpers.js+outline.js", html, [None, text, limit, interactive, selector], setup
    )


def receiver(html, css, setup=""):
    return run("helpers.js+receiver.js", html, [{"$css": css}, probe.INTERACTIVE], setup)


# jsdom does no layout: every element is one 100x40 box at the origin, and the
# stack at the click point is whatever the test names, top first.
GEOMETRY = (
    "const r = {left: 0, right: 100, top: 0, bottom: 40, width: 100, height: 40};"
    "document.querySelectorAll('*').forEach(e => { e.getBoundingClientRect = () => r;"
    " e.getClientRects = () => [r]; });"
)


def stack(*ids):
    listed = ", ".join(f"'{i}'" for i in ids)
    return (f"document.elementsFromPoint = () => [{listed}]"
            ".map(id => document.getElementById(id));")


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
    # Nothing opens it on hover: no :hover rule, and the stated opener is another's.
    assert answer["trigger"] is None


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


def test_explain_for_a_covered_element_names_what_covers_it():
    answer = usable(
        '<button id="go" class="primary">Go</button>'
        '<div id="modal" class="overlay" style="background:#000"></div>',
        "#go",
        GEOMETRY + stack("modal", "go"),
    )
    assert answer == {"reason": "covered", "detail": "div#modal", "overlay": False}


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


def test_a_frame_that_forges_self_is_still_a_frame_and_its_origin_is_still_read():
    """`window.self` is writable: a frame that runs `self = top` made the old
    `window.self !== window.top` say "not in a frame", and the leash then checked
    only the top page. The frame check and the origin read are the production
    scripts, run inside that frame. jsdom gives an about:blank frame an opaque
    origin, "null", which the leash matches to nothing (test_secrets)."""
    as_in_frame = (
        "const frame = document.createElement('iframe'); "
        "document.body.appendChild(frame); "
        "const w = frame.contentWindow; "
        "w.eval('self = top'); "
        "const run = (body) => w.eval('(function () {\\n' + body + '\\n})')(); "
        "return {forged: run('return window.self !== window.top'), "
        f"in_frame: run({json.dumps(browser.IN_FRAME)}), "
        f"origin: run({json.dumps(browser.ORIGIN_HERE)})}};"
    )
    answer = run("-", "<p></p>", source=as_in_frame)
    assert answer["forged"] is False, "the old check is fooled"
    assert answer["in_frame"] is True
    assert answer["origin"] == "null"


def spare(expression, origin, html=None):
    """The real `_on_spare` script, run under jsdom (which stands on
    https://example.test), the way BiDi's evaluate runs it."""
    from kubed.selenium_flow.site_data.spare import SPARE_PAGE, _on_spare

    return json.loads(run(
        "-", html or SPARE_PAGE, source="return " + _on_spare(expression, origin)
    ))


def test_the_spare_script_runs_nothing_on_a_page_from_another_origin():
    wrong = spare(
        "(localStorage.setItem('k', 'v'), 1)", "https://other.test"
    )
    assert wrong == {"spare": True, "origin": "https://example.test"}
    right = spare("(localStorage.setItem('k', 'v'), localStorage.k)", "https://example.test")
    assert right == {"spare": True, "origin": "https://example.test", "value": "v"}


def test_the_spare_script_leaves_a_page_without_the_marker_alone():
    page = spare("1", "https://example.test", html="<p>a site</p>")
    assert page == {"spare": False, "origin": "https://example.test"}


def test_the_focused_script_follows_an_open_shadow_root_to_the_element_in_it():
    html = '<div id="host"></div>'
    setup = (
        "const root = document.getElementById('host').attachShadow({mode: 'open'});"
        "root.innerHTML = '<input id=\"inner\">';"
        "root.getElementById('inner').focus();"
    )
    assert run("focused.js", html, setup=setup) == "input#inner"


def test_the_focused_script_answers_the_element_itself_in_the_light_dom():
    html = '<textarea id="t"></textarea>'
    assert run("focused.js", html, setup="document.getElementById('t').focus();") == "textarea#t"


def test_the_focused_script_answers_null_when_focus_is_on_the_body():
    assert run("focused.js", "<p>nothing here</p>") is None


def test_a_target_that_paints_nothing_at_its_centre_is_unpainted():   # ruling 10, brief 2
    html = ('<svg><g id="board"><rect id="bg" style="fill:#fff"></rect>'
            '<g id="target"><rect id="own" style="fill:none"></rect><text id="dots">…</text></g></g></svg>')
    assert usable(html, "#target", GEOMETRY + stack("bg")) == {"reason": "unpainted", "detail": "rect#bg"}

def test_an_ancestor_on_top_is_unpainted_not_usable():
    html = '<div id="card"><span id="target"></span></div>'
    assert usable(html, "#target", GEOMETRY + stack("card")) == {"reason": "unpainted", "detail": "div#card"}

def test_a_transparent_shape_on_top_is_an_invisible_cover():          # brief 2, the hotspot
    html = ('<svg><g id="target"><text id="t">Go</text></g>'
            '<rect id="hot" style="fill-opacity:0"></rect></svg>')
    assert usable(html, "#target", GEOMETRY + stack("hot", "t")) == {
        "reason": "invisible_cover", "detail": "rect#hot", "selector": {"css": "#hot"}}

def test_a_fixed_painted_cover_is_an_overlay():
    html = ('<button id="go">Go</button>'
            '<div id="banner" style="position:fixed;background:#000">cookies</div>')
    assert usable(html, "#go", GEOMETRY + stack("banner", "go")) == {
        "reason": "covered", "detail": "div#banner", "overlay": True}

def test_the_click_point_is_the_first_client_rect():
    setup = ("const go = document.getElementById('go');"
             "go.getBoundingClientRect = () => ({left: 0, right: 200, top: 0, bottom: 100, width: 200, height: 100});"
             "go.getClientRects = () => [{left: 0, right: 50, top: 0, bottom: 20, width: 50, height: 20}];"
             "document.elementsFromPoint = (x, y) => (x === 25 && y === 10) ? [go] : [document.body];")
    assert receiver('<a id="go" href="/x">Go</a>', "#go", setup)["inside"] is True

def test_a_hidden_element_with_no_stated_or_css_opener_gets_no_trigger():  # ruling 12, brief 1
    html = '<div id="panel"><section style="display:none"><a id="item" href="/x">x</a></section></div>'
    assert usable(html, "#item") == {"reason": "hidden", "detail": "section", "trigger": None, "gesture": ""}

def test_a_real_hover_rule_still_names_its_trigger():                  # Review Focus 4
    html = ('<style>.menu{display:none} .dd:hover .menu{display:block}</style>'
            '<ul><li class="dd" id="dd"><ul class="menu"><li><a id="item" href="/x">x</a></li></ul></li></ul>')
    answer = usable(html, "#item")
    assert (answer["detail"], answer["trigger"], answer["gesture"]) == ("ul.menu", "#dd", "hover")

UNREADABLE = ("Object.defineProperty(document, 'styleSheets', {value: [{get cssRules() "
              "{ throw new DOMException('cross-origin', 'SecurityError'); }}]});")

def test_an_unreadable_stylesheet_is_skipped_and_said():               # Review Focus 4, ruling 12
    html = '<div><section style="display:none"><a id="item" href="/x">x</a></section></div>'
    assert usable(html, "#item", UNREADABLE) == {
        "reason": "hidden", "detail": "section", "trigger": None, "gesture": "", "unread": True}

def test_a_found_trigger_needs_no_admission():
    html = ('<style>.menu{display:none} .dd:hover .menu{display:block}</style>'
            '<ul><li class="dd" id="dd"><ul class="menu"><li><a id="item" href="/x">x</a></li></ul></li></ul>')
    # A readable <style> and an unreadable sheet together: the rule found decides.
    setup = ("const sheets = document.styleSheets;"
             "const real = Array.from({length: sheets.length}, (_, i) => sheets[i]);"
             "Object.defineProperty(document, 'styleSheets', {value: [...real, {get cssRules() "
             "{ throw new DOMException('cross-origin', 'SecurityError'); }}]});")
    answer = usable(html, "#item", setup)
    assert answer["trigger"] == "#dd" and "unread" not in answer

def test_the_outline_offers_no_opener_for_an_admitted_guess():
    html = '<main><section style="display:none"><a id="item" href="/x">x</a></section></main>'
    entry = outline(html, setup=UNREADABLE)["elements"][0]
    assert entry["reason"] == "hidden" and "revealed_by" not in entry and "open_with" not in entry

def test_usables_answers_each_match_in_order():
    html = '<section style="display:none"><a id="a" href="/x">x</a></section><a id="b" href="/x">x</a>'
    answers = run("helpers.js+usables.js", html, [{"$all": "a"}], GEOMETRY + stack("b"))
    assert [a["reason"] for a in answers] == ["hidden", None]

def test_a_label_inside_a_button_is_a_shallow_painted_hit():             # Review Focus 5
    html = '<button id="go" style="background:#09f"><span id="label">Save</span></button>'
    assert receiver(html, "#go", GEOMETRY + stack("label", "go")) == {
        "element": "span#label", "inside": True, "under": True, "depth": 1, "painted": True,
        "interactive": True, "overlay": False, "reason": None, "selector": None}

def test_a_receiver_outside_carries_its_class_and_selector():           # ruling 14
    html = ('<svg><g id="target"><text id="t">Go</text></g>'
            '<rect id="hot" style="fill-opacity:0"></rect></svg>')
    hit = receiver(html, "#target", GEOMETRY + stack("hot", "t"))
    assert (hit["inside"], hit["depth"], hit["reason"], hit["selector"]) == (
        False, None, "invisible_cover", {"css": "#hot"})

def test_a_deep_unpainted_hit_says_so():                                 # brief 3
    html = '<div id="screen"><div><div><div id="card"><div id="bg"></div></div></div></div></div>'
    hit = receiver(html, "#screen", GEOMETRY + stack("bg", "card", "screen"))
    assert (hit["depth"], hit["painted"], hit["interactive"]) == (4, False, False)

def test_a_control_between_the_hit_and_the_target_is_noticed():
    html = '<div id="card"><a id="link" href="/x"><i id="icon"></i></a></div>'
    assert receiver(html, "#card", GEOMETRY + stack("icon", "link", "card"))["interactive"] is True


# Dr K's QA, 11-13 14:06: each of these reached its handler and was warned.
@pytest.mark.parametrize("html, ids", [
    ('<a id="card" href="/x"><div id="d1"><div id="d2"><h3 id="title">Go</h3></div></div></a>',
     ("title", "d2", "d1", "card")),
    ('<button id="card"><i id="icon" class="ico"></i></button>', ("icon", "card")),
    ('<button id="card"><span id="s"><svg id="svg"><path id="p"></path></svg></span></button>',
     ("p", "svg", "s", "card")),
])
def test_a_selected_control_is_noticed(html, ids):
    assert receiver(html, "#card", GEOMETRY + stack(*ids))["interactive"] is True

def test_a_selected_pointer_group_is_not_a_control():                     # #116's board
    html = '<svg><g id="board" style="cursor:pointer"><g id="g"><rect id="bg"></rect></g></g></svg>'
    assert receiver(html, "#board", GEOMETRY + stack("bg", "g", "board"))["interactive"] is False

def test_the_outline_names_what_an_unpainted_element_lets_through():
    html = '<main><div id="behind" style="background:#fff"></div><button id="go"></button></main>'
    entry = outline(html, setup=GEOMETRY + stack("behind"))["elements"][0]
    assert (entry["reason"], entry["blocked_by"]) == ("unpainted", "div#behind")
