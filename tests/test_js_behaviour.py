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


# An element with no label of its own used to be named by its whole subtree's
# textContent, so a toolbar read "SaveCancel" and a container read as
# everything in it (#138). A control is still named from its content,
# descendants included, as ARIA names a link or a button; anything else is
# named only from text it owns, as for the text selector (saga §F2.10).
def names(page, interactive=True, text=""):
    answer = outline(page, text=text, interactive=interactive)
    return [(e["role"], e["name"]) for e in answer["elements"]]


def test_a_toolbar_is_not_named_by_its_buttons_run_together():
    page = (
        '<main><div role="toolbar"><button>Save</button><button>Cancel</button>'
        "</div></main>"
    )
    assert names(page, interactive=False) == [
        ("main", ""), ("toolbar", ""), ("button", "Save"), ("button", "Cancel")]


def test_a_grid_row_and_a_tree_item_are_containers_not_controls():
    page = (
        '<main><div role="row"><div role="gridcell">John</div>'
        '<div role="gridcell">42</div></div>'
        '<div role="treeitem"><span>Docs</span><div role="group">'
        '<div role="treeitem">A</div><div role="treeitem">B</div></div></div></main>'
    )
    named = names(page, interactive=False)
    assert ("row", "") in named and ("gridcell", "John") in named
    assert ("treeitem", "") in named and ("treeitem", "A") in named


def test_a_container_is_not_named_by_everything_inside_it():
    page = '<main><section><p>One</p><p>Two</p></section></main>'
    assert ("section", "") in names(page, interactive=False)


def test_a_link_and_a_button_are_named_from_the_spans_inside_them():
    # QA on #140: ARIA names a control from its content, descendants included,
    # with the spacing the DOM has between them.
    page = (
        '<main><a href="#x"><span>Open</span> <span>file</span></a>'
        "<button><span>Open</span> <span>file</span></button></main>"
    )
    assert names(page) == [("link", "Open file"), ("button", "Open file")]
    assert names(page, text="open file") == [("link", "Open file"), ("button", "Open file")]


def test_an_explicit_role_is_named_from_its_content_too():
    page = (
        '<main><div role="Button" tabindex="0"><span>Open</span> <span>file</span></div>'
        '<div role="tab button" tabindex="0"><span>Last</span> <span>run</span></div></main>'
    )
    assert names(page) == [("Button", "Open file"), ("tab button", "Last run")]


ICON = '<main><button><svg><path d="M0 0h1"/></svg><span>Save</span></button></main>'


def test_an_icon_beside_a_label_does_not_cost_a_control_its_name():
    assert names(ICON, text="save") == [("button", "Save")]


def test_the_name_and_the_text_selector_agree_on_whose_text_it_is():
    entry = outline(ICON)["elements"][0]
    assert entry["xpath"] == '//button[normalize-space()="Save"]'


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


# jsdom computes no pseudo-element style (it answers `content: normal`), so a
# test that needs one stubs it, from attributes: `data-before` / `data-after` is
# that pseudo-element's computed `content`, and `data-pseudo-<property>` sets
# one property for both. What is pinned is the decision over those values.
PSEUDO = (
    "const real = window.getComputedStyle.bind(window);"
    "window.getComputedStyle = (el, which) => {"
    " if (!which) return real(el);"
    " const content = el.getAttribute(which === '::before' ? 'data-before' : 'data-after');"
    " const own = (name, initial) => el.getAttribute('data-pseudo-' + name) || initial;"
    " return {content: content === null ? 'none' : content,"
    " display: own('display', 'inline'), visibility: own('visibility', 'visible'),"
    " opacity: own('opacity', '1'), backgroundColor: own('bg', 'rgba(0, 0, 0, 0)'),"
    " backgroundImage: own('image', 'none')}; };"
)

# Dr K's QA, 11-13 14:06: each of these reached its handler and was warned.
QA = [
    ('<a id="card" href="/x"><div id="d1"><div id="d2"><h3 id="title">Go</h3></div></div></a>',
     ("title", "d2", "d1", "card")),
    ('<button id="card"><i id="icon" class="ico"></i></button>', ("icon", "card")),
    ('<button id="card"><span id="s"><svg id="svg"><path id="p"></path></svg></span></button>',
     ("p", "svg", "s", "card")),
]


@pytest.mark.parametrize("html, ids", QA)
def test_a_selected_control_is_noticed(html, ids):
    assert receiver(html, "#card", GEOMETRY + stack(*ids))["interactive"] is True


@pytest.mark.parametrize("html, ids", QA)
def test_a_click_inside_a_selected_control_is_not_warned(html, ids):     # ruling 13, amended
    hit = receiver(html, "#card", GEOMETRY + stack(*ids))
    report = probe.receiver_report(hit, "click")
    assert report["received_by"]["depth"] == len(ids) - 1 and "warning" not in report


def test_a_deep_click_inside_a_plain_div_still_warns():                  # ruling 13
    html = '<div id="card"><div id="d1"><div id="d2"><h3 id="title">Go</h3></div></div></div>'
    hit = receiver(html, "#card", GEOMETRY + stack("title", "d2", "d1", "card"))
    assert "3 levels inside" in probe.receiver_report(hit, "click")["warning"]


def test_a_selected_pointer_group_is_not_a_control():                     # #116's board
    html = '<svg><g id="board" style="cursor:pointer"><g id="g"><rect id="bg"></rect></g></g></svg>'
    assert receiver(html, "#board", GEOMETRY + stack("bg", "g", "board"))["interactive"] is False


def test_an_icon_font_glyph_is_paint():                                   # ruling 11, amended
    html = '<div id="card"><i id="icon" class="ico" data-before="&quot;&#xe900;&quot;"></i></div>'
    hit = receiver(html, "#card", GEOMETRY + PSEUDO + stack("icon", "card"))
    assert hit["painted"] is True
    assert "warning" not in probe.receiver_report(hit, "click")


@pytest.mark.parametrize("attrs, painted", [
    ('data-after="&quot;&quot;" data-pseudo-bg="rgb(255, 0, 0)"', True),   # a drawn shape
    ('data-after="&quot;&quot;" data-pseudo-image="url(x.png)"', True),     # a drawn image
    ('data-before="url(x.png)"', True),                                     # an image as content
    ('data-before="&quot;&quot;"', False),                                  # empty content
    ('data-before="&quot; &quot;"', False),                                 # a blank glyph
    ('data-before="none" data-after="normal"', False),                      # no box at all
    ('data-before="&quot;Go&quot;" data-pseudo-display="none"', False),
    ('data-before="&quot;Go&quot;" data-pseudo-visibility="hidden"', False),
    ('data-before="&quot;Go&quot;" data-pseudo-opacity="0"', False),        # a CSS tooltip
])
def test_pseudo_content_is_paint_only_when_it_draws(attrs, painted):
    html = f'<div id="card"><i id="icon" {attrs}></i></div>'
    assert receiver(html, "#card", GEOMETRY + PSEUDO + stack("icon", "card"))["painted"] is painted


def test_an_unpainted_icon_inside_a_plain_div_still_warns():              # ruling 13
    html = '<div id="card"><i id="icon" class="ico"></i></div>'
    hit = receiver(html, "#card", GEOMETRY + PSEUDO + stack("icon", "card"))
    assert "warning" in probe.receiver_report(hit, "click")


def test_a_stretched_link_is_still_an_invisible_cover():                  # ruling 10
    html = ('<div><a id="stretch" href="/x" data-after="&quot;&quot;"></a>'
            '<button id="go">Go</button></div>')
    hit = receiver(html, "#go", GEOMETRY + PSEUDO + stack("stretch", "go"))
    assert (hit["inside"], hit["reason"]) == (False, "invisible_cover")


def test_the_outline_names_what_an_unpainted_element_lets_through():
    html = '<main><div id="behind" style="background:#fff"></div><button id="go"></button></main>'
    entry = outline(html, setup=GEOMETRY + stack("behind"))["elements"][0]
    assert (entry["reason"], entry["blocked_by"]) == ("unpainted", "div#behind")


def test_extract_never_returns_a_password_value():  # ruling 24, the issue's repro
    html = '<form id="f"><input type="password" value="hunter22"><input name="u" value="kept"></form>'
    out = run("extract.js", html, [{"$css": "#f"}])
    assert "hunter22" not in out and 'value="kept"' in out
    assert 'type="password"' in out


def test_extract_of_the_password_input_itself_drops_its_value():
    html = '<input id="p" type="PASSWORD" value="hunter22" name="pw">'
    out = run("extract.js", html, [{"$css": "#p"}])
    assert "hunter22" not in out and 'name="pw"' in out and out.startswith("<input")


def test_extract_leaves_every_other_element_as_innerhtml_has_it():
    html = '<div id="d"><b>x</b> <input value="a"><textarea>t</textarea></div>'
    out = run("extract.js", html, [{"$css": "#d"}])
    assert out == '<b>x</b> <input value="a"><textarea>t</textarea>'


def test_the_outline_names_no_password_by_its_value():
    html = '<input id="p" type="password" value="hunter22">'
    answer = outline(html)
    assert "hunter22" not in json.dumps(answer)


def test_extract_strips_password_values_inside_templates_however_deep():
    html = (
        '<div id="d"><template><input type="password" value="hunter22">'
        '<template><input type="password" value="deeper33"></template></template>'
        '<input name="u" value="kept"></div>'
    )
    out = run("extract.js", html, [{"$css": "#d"}])
    assert "hunter22" not in out and "deeper33" not in out and 'value="kept"' in out


def test_extract_of_an_element_with_a_content_attribute_is_its_innerhtml():
    html = '<head><meta id="m" name="description" content="x"></head>'
    assert run("extract.js", html, [{"$css": "#m"}]) == ""
