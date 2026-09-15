"""The pointer: where it is, how it gets somewhere, and what that changes.

Three faults from the second sortie meet here (saga §F2.3, §F2.4, §F2.10):

- a hover onto a target the pointer was already inside fired no `mouseover` and
  still reported `ok` — twice, to the same pilot, on the same menu;
- a click left the pointer where it was, so "the pointer is on what you clicked"
  was not true;
- and a drag had to be written as `execute_script`, which is worse at it.

The WebDriver calls themselves are covered by the `integration` tests at the
bottom, which need a Grid. Everything above them is the arithmetic and the
orchestration, which a double can prove and which is where the mistakes were.
"""

import os
import shutil
import subprocess
from urllib.parse import quote

import pytest
from selenium.webdriver.remote.webelement import WebElement

from kubed.selenium_flow import pointer
from kubed.selenium_flow.pointer import MemoryPointers, RedisPointers

pytestmark = pytest.mark.unit


# ---- the JavaScript it sends --------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to parse JS")
@pytest.mark.parametrize(
    "script", [pointer.NUDGE_JS, pointer.CENTRE_JS], ids=["nudge", "centre"]
)
def test_the_scripts_parse(tmp_path, script):
    """Same guard `test_probe_js.py` has, for the same reason: a script that
    does not parse fails in the browser as a WebDriverException whose message is
    not about the mistake. Wrapped in a function because that is how WebDriver
    runs it — the bare text has a top-level `return`."""
    path = tmp_path / "pointer.js"
    path.write_text("(function () {\n" + script + "\n});", encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


# ---- the path -----------------------------------------------------------


def test_a_glide_ends_exactly_on_its_destination():
    """Rounding that stopped a pixel short would leave the pointer beside the
    element rather than on it, which is the whole property being bought."""
    assert pointer.path((0.0, 0.0), (630.0, 430.0))[-1] == (630, 430)


def test_a_long_move_gets_more_steps_than_a_short_one_and_both_are_bounded():
    short = pointer.path((0.0, 0.0), (10.0, 0.0))
    long = pointer.path((0.0, 0.0), (4000.0, 0.0))
    assert len(short) == pointer.MIN_STEPS
    assert len(long) == pointer.MAX_STEPS
    assert len(pointer.path((0.0, 0.0), (400.0, 0.0))) > pointer.MIN_STEPS


def test_the_path_is_monotonic_along_each_axis():
    """Eased, not overshooting: a step that went backwards would cross elements
    on the way that a hand never would."""
    points = pointer.path((0.0, 0.0), (300.0, 200.0))
    assert points == sorted(points)


def test_a_destination_outside_the_window_stops_at_its_edge():
    """A pointer move to a coordinate outside the viewport is an error, not a
    scroll, so an offset drag that would leave the window has to stop."""
    assert pointer.clamped((5000.0, -20.0), (800, 600)) == (799.0, 0.0)
    assert pointer.clamped((100.0, 100.0), (800, 600)) == (100.0, 100.0)


# ---- remembering it ------------------------------------------------------


def test_a_position_is_kept_per_browser_and_forgotten_on_demand():
    store = MemoryPointers()
    store.set("a", 10, 20)
    store.set("b", 30, 40)
    assert store.get("a") == (10, 20)
    assert store.get("b") == (30, 40)
    store.forget("a")
    assert store.get("a") is None
    assert store.get("b") == (30, 40)


def test_an_unknown_browser_has_no_position_rather_than_a_guessed_one():
    """Never a guessed start: a path plotted from the wrong origin crosses the
    wrong elements, which is worse than no path (§F2.3)."""
    assert MemoryPointers().get("never-seen") is None


def test_a_position_expires_with_the_session_ttl():
    """A stale origin is worse than none: the browser it named is long gone."""
    now = [0.0]
    store = MemoryPointers(ttl=10, clock=lambda: now[0])
    store.set("a", 1, 2)
    assert store.get("a") == (1, 2)
    now[0] = 100.0
    assert store.get("a") is None


class _DeadRedis:
    """A client that is configured and unreachable."""

    def get(self, *_):
        raise ConnectionError("no route to host")

    def set(self, *_, **__):
        raise ConnectionError("no route to host")

    def delete(self, *_):
        raise ConnectionError("no route to host")


def test_an_unreachable_redis_costs_the_position_and_nothing_else():
    """Not knowing where the pointer is is a state this module handles. Raising
    here would turn every click into an outage."""
    store = RedisPointers(_DeadRedis())
    store.set("a", 1, 2)
    store.forget("a")
    assert store.get("a") is None


class _Redis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


def test_redis_round_trips_a_position_under_its_own_prefix():
    client = _Redis()
    store = RedisPointers(client, prefix="p:")
    store.set("abc", 12.5, 7.0)
    assert store.get("abc") == (12.5, 7.0)
    assert list(client.data) == ["p:abc"]


def test_the_pointer_store_follows_the_session_store_backend():
    """One switch, not two. A deployment that shares session records between
    replicas and not pointers would have one replica plotting a glide from
    another's stale origin."""
    assert pointer.from_env({"SESSION_STORE": "memory"}).kind == "memory"
    # Unreachable redis falls back the same way the session store does.
    assert (
        pointer.from_env(
            {"SESSION_STORE": "redis", "REDIS_HOST": "redis.invalid"}
        ).kind
        == "memory"
    )


# ---- what an action does with it -----------------------------------------


class _Element(WebElement):
    """A real WebElement with a stub parent.

    Really one, because `ActionChains.double_click` type-checks its argument —
    so a duck-typed double would pass a test the browser would fail.
    """

    def __init__(self):
        super().__init__(None, "element-1")
        self.clicks = 0

    def click(self):
        self.clicks += 1


@pytest.fixture
def moving(actions, monkeypatch):
    """Record what `interact` asks the pointer to do, without a browser."""
    sent = []

    class _Driver:
        current_url = "https://example.test/"
        title = "t"

        def execute_script(self, *_a, **_k):
            return None

        def execute(self, *_a, **_k):
            # What ActionChains.perform() reaches for. The gestures this
            # fixture does not fake still run for real up to the wire.
            return {"value": None}

    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Driver())
    monkeypatch.setattr(
        "kubed.selenium_flow.browser.wait_for_element", lambda *a, **k: _Element()
    )
    monkeypatch.setattr(
        "kubed.selenium_flow.browser.wait_for_clickable", lambda *a, **k: _Element()
    )

    def fake_move(driver, element, start=None, glide=False):
        sent.append({"start": start, "glide": glide})
        return {
            "at": (120.0, 80.0),
            "glided": bool(glide) and start is not None,
            "nudged": False,
            "unknown_start": bool(glide) and start is None,
        }

    monkeypatch.setattr(pointer, "move", fake_move)
    return sent


def test_a_click_leaves_the_pointer_on_what_it_clicked(actions, moving, monkeypatch):
    """Dr K's rule, and the thing WebDriver's element click does not do: it
    leaves the pointer where it was, measured on the live Grid (§F2.3)."""
    clicked = []
    monkeypatch.setattr(
        "kubed.selenium_flow.browser.wait_for_clickable",
        lambda *a, **k: type("E", (), {"click": lambda self: clicked.append(1)})(),
    )
    actions.interact("abc", "click", css="#go")
    assert clicked == [1], "the element click still happens, and still first-class"
    assert len(moving) == 1, "and the pointer moved onto it before the click"
    assert actions.pointers.get("abc") == (120.0, 80.0)


@pytest.mark.parametrize("action", ["click", "double_click", "right_click", "hover"])
def test_every_pointer_gesture_moves_first(actions, moving, action):
    actions.interact("abc", action, css="#go")
    assert len(moving) == 1, action


def test_scroll_to_does_not_move_the_pointer(actions, moving):
    """It moves the PAGE. A pointer that followed a scroll_to would hover things
    on the way that nobody asked to hover."""
    result = actions.interact("abc", "scroll_to", css="#go")
    assert moving == []
    assert "glided" not in result


def test_a_glide_is_off_unless_asked_for(actions, moving):
    actions.interact("abc", "hover", css="#go")
    assert moving[0]["glide"] is False
    assert actions.interact("abc", "hover", css="#go", glide=True)["glided"] is True


def test_the_first_glide_in_a_browser_is_a_jump_and_says_so(actions, moving):
    """Never a guessed start (§F2.3). The result has to admit the degrade, or a
    caller debugging a slider believes it sent movement it did not send."""
    result = actions.interact("abc", "hover", css="#go", glide=True)
    assert result["glided"] is False
    assert "not known" in result["glide_note"]
    # And the next one has a start to plot from.
    assert actions.interact("abc", "hover", css="#go", glide=True)["glided"] is True


def test_a_move_that_cannot_be_sent_costs_the_position_and_not_the_gesture(
    actions, monkeypatch
):
    """New work in front of gestures that already worked. An element WebDriver
    will not move to - one taller than the window is the usual case - must not
    take a click that would have succeeded."""
    clicked = []

    class _Driver:
        current_url = "https://example.test/"
        title = "t"

        def execute_script(self, *_a, **_k):
            return None

    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Driver())
    monkeypatch.setattr(
        "kubed.selenium_flow.browser.wait_for_clickable",
        lambda *a, **k: type("E", (), {"click": lambda self: clicked.append(1)})(),
    )
    actions.pointers.set("abc", 5, 5)

    def explodes(*_a, **_k):
        raise RuntimeError("move target out of bounds")

    monkeypatch.setattr(pointer, "move", explodes)
    result = actions.interact("abc", "click", css="#go")
    assert clicked == [1], "the click still happened"
    assert "glided" not in result
    assert actions.pointers.get("abc") is None, "a stale origin is worse than none"


def test_a_hover_falls_back_to_the_old_gesture_when_the_move_fails(
    actions, monkeypatch
):
    """For a hover the move IS the action, so a failed move must not mean a
    hover that never happened."""
    performed = []

    class _Chain:
        def __init__(self, _driver):
            pass

        def move_to_element(self, _element):
            performed.append("move_to_element")
            return self

        def perform(self):
            performed.append("perform")

    class _Driver:
        current_url = "https://example.test/"
        title = "t"

        def execute_script(self, *_a, **_k):
            return None

        def execute(self, *_a, **_k):
            # What ActionChains.perform() reaches for. The gestures this
            # fixture does not fake still run for real up to the wire.
            return {"value": None}

    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Driver())
    monkeypatch.setattr(
        "kubed.selenium_flow.browser.wait_for_element", lambda *a, **k: _Element()
    )
    monkeypatch.setattr("kubed.selenium_flow.actions.ActionChains", _Chain)
    monkeypatch.setattr(
        pointer, "move", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))
    )
    actions.interact("abc", "hover", css="#go")
    assert performed == ["move_to_element", "perform"]


def test_opening_a_browser_forgets_where_the_pointer_was(actions, monkeypatch):
    """A new browser's pointer is at (0,0) and nothing we sent put it there."""

    class _Driver:
        session_id = "new"

        def get_window_size(self):
            return {"width": 800, "height": 600}

        def set_window_size(self, *_):
            pass

    monkeypatch.setattr(actions.grid, "open", lambda name=None: _Driver())
    actions.pointers.set("new", 400, 300)
    actions.open_session()
    assert actions.pointers.get("new") is None


# ---- drag: the arguments it refuses --------------------------------------


def test_a_drag_with_no_destination_is_refused_before_the_browser_is_touched(actions):
    with pytest.raises(ValueError, match="destination is required"):
        actions.drag("abc", css="#card")


def test_a_drag_with_two_kinds_of_destination_is_refused(actions):
    with pytest.raises(ValueError, match="never both"):
        actions.drag("abc", css="#card", to_css="#done", by_x=10)


def test_a_drag_to_where_it_already_is_is_refused(actions):
    with pytest.raises(ValueError, match="already is"):
        actions.drag("abc", css="#card", by_x=0, by_y=0)


def test_a_source_the_pointer_cannot_reach_is_the_callers_to_fix(actions, monkeypatch):
    """400, not 500: it describes a geometry the caller can fix - resize,
    scroll, grab a smaller handle - and a 500 tells a workflow with
    Retry-On-Fail to send the identical drag again (Copilot, #31)."""
    from kubed.selenium_flow.errors import status_for

    class _Driver:
        current_url = "https://example.test/"
        title = "t"

        def execute_script(self, *_a, **_k):
            return None

    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Driver())
    monkeypatch.setattr(
        "kubed.selenium_flow.browser.wait_for_clickable", lambda *a, **k: _Element()
    )
    monkeypatch.setattr(actions, "_move_onto", lambda *a, **k: None)

    with pytest.raises(ValueError) as refused:
        actions.drag("abc", css="#card", to_css="#done")
    assert status_for(refused.value) == 400
    assert "resize" in str(refused.value)


# ---- against a real browser ----------------------------------------------
#
# Everything above fakes the WebDriver call. These are the tests that prove the
# call itself does what §F2.3 and §F2.4 say, so they need a Grid: marked
# `integration` and skipped without `GRID_URL`.

GRID_URL = os.environ.get("GRID_URL", "")

LIVE_PAGE = (
    "<style>body{margin:0}"
    "#a{position:absolute;left:20px;top:20px;width:60px;height:60px;background:#ccc}"
    "#b{position:absolute;left:600px;top:400px;width:60px;height:60px;background:#8cf}"
    "#cover{position:fixed;left:0;top:0;width:100%;height:200px;background:#eee}"
    "</style>"
    "<div id=a>A</div><div id=b>B</div><div id=cover>cookies</div>"
    "<script>"
    "window.moves=[];window.overs=0;window.clicked=0;"
    "document.addEventListener('pointermove',e=>window.moves.push([e.clientX,e.clientY]));"
    "document.getElementById('b').addEventListener('mouseover',()=>window.overs++);"
    "document.getElementById('b').addEventListener('click',()=>window.clicked++);"
    "</script>"
)

DRAG_PAGE = (
    "<style>body{margin:0}"
    "#src{position:absolute;left:20px;top:20px;width:80px;height:80px;background:#fc8}"
    "#dst{position:absolute;left:400px;top:300px;width:160px;height:160px;background:#cfc}"
    "</style>"
    "<div id=src>drag me</div><div id=dst>drop here</div>"
    "<input id=slider type=range min=0 max=100 value=0 "
    "style='position:absolute;left:20px;top:500px;width:300px'>"
    "<script>"
    "window.log=[];window.down=0;window.up=0;window.moves=0;"
    "document.addEventListener('pointermove',()=>window.moves++);"
    "src.addEventListener('pointerdown',()=>window.down++);"
    "document.addEventListener('pointerup',()=>window.up++);"
    "for (const n of ['dragstart','dragend']) src.addEventListener(n,e=>window.log.push(n));"
    "for (const n of ['dragover','drop']) dst.addEventListener(n,e=>{e.preventDefault();"
    "window.log.push(n)});"
    "</script>"
)


@pytest.fixture
def live(request):
    """A real browser on a page that logs every pointer event it receives."""
    from kubed.selenium_flow.actions import Actions
    from kubed.selenium_flow.browser import Grid

    page = getattr(request, "param", LIVE_PAGE)
    actions = Actions(Grid(GRID_URL))
    session = actions.open_session(
        url="data:text/html," + quote(page), width=900, height=700
    )["session_id"]
    try:
        yield actions, session
    finally:
        actions.end_browser(session)


needs_grid = pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")


@pytest.mark.integration
@needs_grid
def test_a_jump_delivers_one_move_and_a_glide_delivers_many(live):
    """Chrome does not interpolate: a pointerMove with a duration waits out the
    duration and delivers a single event, so anything watching movement sees a
    teleport. Many small moves in one sequence is the whole mechanism."""
    actions, session = live
    driver = actions.grid.reconnect(session)

    actions.interact(session, "hover", css="#a")
    driver.execute_script("window.moves = [];")
    actions.interact(session, "hover", css="#b")
    jumped = driver.execute_script("return window.moves.length")

    actions.interact(session, "hover", css="#a")
    driver.execute_script("window.moves = [];")
    result = actions.interact(session, "hover", css="#b", glide=True)
    glided = driver.execute_script("return window.moves.length")

    assert result["glided"] is True
    assert glided > jumped
    assert glided >= pointer.MIN_STEPS


@pytest.mark.integration
@needs_grid
def test_a_glide_ends_on_the_element_it_was_aimed_at(live):
    actions, session = live
    driver = actions.grid.reconnect(session)
    actions.interact(session, "hover", css="#a")
    actions.interact(session, "hover", css="#b", glide=True)
    assert driver.execute_script("return window.moves.slice(-1)[0]") == [630, 430]


@pytest.mark.integration
@needs_grid
def test_hovering_what_the_pointer_is_already_on_still_fires_mouseover(live):
    """The §F2.10 fault, and the reason for the nudge: no movement means no
    `mouseover`, and the step reported `ok` either way. Measured twice, on both
    browsers, before this existed."""
    actions, session = live
    driver = actions.grid.reconnect(session)

    actions.interact(session, "hover", css="#b")
    driver.execute_script("window.overs = 0;")
    result = actions.interact(session, "hover", css="#b")

    assert result["nudged"] is True
    assert driver.execute_script("return window.overs") == 1


@pytest.mark.integration
@needs_grid
def test_after_a_click_the_pointer_is_on_what_was_clicked(live):
    """Dr K's rule. WebDriver's element click leaves the pointer where it was,
    so this is the move in front of it doing the work — and the element click
    is still the one that happens, which the next test is about."""
    actions, session = live
    driver = actions.grid.reconnect(session)

    actions.interact(session, "hover", css="#a")
    actions.interact(session, "click", css="#b")
    assert driver.execute_script("return window.clicked") == 1
    # `elementFromPoint` at the pointer's last known position is the only way
    # to ask the page where it is: WebDriver has no such command.
    at = actions.pointers.get(session)
    under = driver.execute_script(
        "return document.elementFromPoint(arguments[0], arguments[1]).id;", *at
    )
    assert under == "b"


@pytest.mark.integration
@needs_grid
def test_a_covered_click_is_still_refused_loudly(live):
    """The reason the click stays WebDriver's element click. A pointer-action
    click would press the overlay and report success."""
    from selenium.common.exceptions import ElementClickInterceptedException

    actions, session = live
    driver = actions.grid.reconnect(session)
    driver.execute_script(
        "document.getElementById('b').style.top = '40px';"
        "document.getElementById('b').style.left = '40px';"
    )
    with pytest.raises(ElementClickInterceptedException) as refused:
        actions.interact(session, "click", css="#b")
    assert "div#cover is on top of it" in str(refused.value)
    assert driver.execute_script("return window.clicked") == 0


@pytest.mark.integration
@needs_grid
def test_gliding_to_something_below_the_fold_still_glides(live):
    """Every point on a path is a coordinate, and a coordinate outside the
    window is an error rather than a scroll — so a path plotted to an element
    below the fold was a sequence WebDriver rejected, taking the whole move
    with it (Copilot, #31). The target is scrolled into view before the path is
    plotted."""
    actions, session = live
    driver = actions.grid.reconnect(session)
    driver.execute_script(
        "const far = document.createElement('div');"
        "far.id = 'far'; far.textContent = 'far';"
        "far.style.cssText = 'position:absolute;left:100px;top:3000px;"
        "width:80px;height:40px;background:#fca';"
        "document.body.appendChild(far);"
        "document.body.style.height = '4000px';"
    )
    actions.interact(session, "hover", css="#a")
    driver.execute_script("window.moves = [];")

    result = actions.interact(session, "hover", css="#far", glide=True)

    assert result["glided"] is True, result.get("glide_note")
    assert driver.execute_script("return window.moves.length") >= pointer.MIN_STEPS
    # And it ended on the element, not at some clamped edge.
    at = actions.pointers.get(session)
    under = driver.execute_script(
        "return document.elementFromPoint(arguments[0], arguments[1]).id;", *at
    )
    assert under == "far"


@pytest.mark.integration
@needs_grid
def test_a_click_that_cannot_glide_still_clicks(live):
    """The degrade has to be the glide, never the gesture."""
    actions, session = live
    driver = actions.grid.reconnect(session)
    driver.execute_script(
        "const tall = document.createElement('div');"
        "tall.id = 'tall';"
        "tall.style.cssText = 'position:absolute;left:0;top:0;width:100%;"
        "height:5000px;background:rgba(0,0,0,0.02)';"
        "tall.addEventListener('click', () => window.clicked++);"
        "document.body.appendChild(tall);"
    )
    actions.interact(session, "hover", css="#a")
    result = actions.interact(session, "click", css="#tall", glide=True)
    assert driver.execute_script("return window.clicked") == 1
    if not result["glided"]:
        assert "jump" in result["glide_note"]


@pytest.mark.integration
@needs_grid
@pytest.mark.parametrize("live", [DRAG_PAGE], indirect=True, ids=["drag-page"])
def test_a_drag_presses_travels_and_releases(live):
    actions, session = live
    driver = actions.grid.reconnect(session)
    driver.execute_script("window.moves = 0;")

    result = actions.drag(session, css="#src", to_css="#dst")

    assert driver.execute_script("return window.down") == 1
    assert driver.execute_script("return window.up") == 1
    assert driver.execute_script("return window.moves") > 1, "the travel was stepped"
    assert result["to"] == {"x": 480, "y": 380}


@pytest.mark.integration
@needs_grid
@pytest.mark.parametrize("live", [DRAG_PAGE], indirect=True, ids=["drag-page"])
def test_a_drag_by_an_offset_moves_a_range_slider(live):
    """The case `execute_script` cannot do: a slider only responds to real
    pointer input, which is why the pilot asked for incremental movement."""
    actions, session = live
    driver = actions.grid.reconnect(session)
    assert driver.execute_script("return slider.value") == "0"

    actions.drag(session, css="#slider", by_x=100)

    assert int(driver.execute_script("return slider.value")) > 50


@pytest.mark.integration
@needs_grid
@pytest.mark.parametrize("live", [DRAG_PAGE], indirect=True, ids=["drag-page"])
def test_a_destination_outside_the_window_stops_at_the_edge_rather_than_failing(live):
    actions, session = live
    driver = actions.grid.reconnect(session)
    # The window is 900 wide and the viewport is not: a scrollbar is the
    # difference, which is why the edge is asked for rather than assumed.
    edge = driver.execute_script("return window.innerWidth;") - 1
    result = actions.drag(session, css="#src", by_x=5000)
    assert "clamped" in result
    assert result["to"]["x"] == edge


def test_an_injected_session_store_can_bring_a_matching_pointer_store():
    """The two are one decision. A caller that hands in a shared session store
    while the environment says memory would otherwise share session mappings
    across replicas and keep pointers local — and a cross-replica glide would
    silently degrade to a jump (Copilot, #31)."""
    from kubed.selenium_flow.server import SeleniumMCP
    from kubed.selenium_flow.store import MemoryStore

    mine = MemoryPointers()
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", store=MemoryStore(), pointers=mine
    )
    assert server.actions.pointers is mine
