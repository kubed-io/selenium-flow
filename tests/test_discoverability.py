"""What an agent can find out from the tool list alone.

The first agent to fly a real app never found `hover`. It was there, and named in
the description, but the schema said `action` was any string — and a model plans
against types, so it reached for `execute_script` instead and built a flaky
workaround. These tests hold the surface to the rule that came out of it: a
closed set of values is an enum in the schema, and an open one says what it
accepts. See saga §F2.1 and §F2.2.
"""

import re
from pathlib import Path

import pytest
from selenium.webdriver.common.keys import Keys

from kubed.selenium_flow.core import actions as actions_module
from kubed.selenium_flow.core.actions import (
    DIALOG_ACTIONS,
    FRAME_ACTIONS,
    KEY_NAMES,
    KEYS,
    MOUSE_ACTIONS,
    PRINT_FORMATS,
    resolve_key,
)
from kubed.selenium_flow.core.browser import BROWSERS

pytestmark = pytest.mark.unit

PACKAGE = Path(actions_module.__file__).parent

# Every argument the action layer checks against a fixed tuple, and the tuple.
# `test_every_closed_set_the_action_layer_checks_is_listed_here` fails when a
# new one appears without being added, which is the part that stops the next
# `action: str`.
CLOSED_SETS = [
    ("interact", "action", "MOUSE_ACTIONS", MOUSE_ACTIONS),
    ("dialog", "action", "DIALOG_ACTIONS", DIALOG_ACTIONS),
    ("frame", "action", "FRAME_ACTIONS", FRAME_ACTIONS),
    ("print", "format", "PRINT_FORMATS", PRINT_FORMATS),
    ("open_session", "browser", "BROWSERS", BROWSERS),
]


def _enum(prop: dict):
    """The enum on a property, whether it is required or may also be null."""
    if "enum" in prop:
        return prop["enum"]
    for branch in prop.get("anyOf", []):
        if "enum" in branch:
            return branch["enum"]
    return None


@pytest.mark.parametrize("tool,argument,_name,values", CLOSED_SETS)
async def test_a_closed_set_is_an_enum_in_the_tool_schema(
    server, tool, argument, _name, values
):
    schema = (await server.mcp.get_tool(tool)).parameters
    prop = schema["properties"][argument]
    assert _enum(prop) == list(values), (
        f"{tool}.{argument} is validated against a fixed list but the schema "
        "does not say so, so a model reading it sees an open string"
    )


def test_every_closed_set_the_action_layer_checks_is_listed_here():
    """Found by reading the action layer, not by trusting this table."""
    checked = set()
    for module in ("actions.py", "browser.py"):
        source = (PACKAGE / module).read_text()
        checked |= set(re.findall(r"\bnot in ([A-Z][A-Z_]+)\b", source))
    assert checked == {name for _, _, name, _ in CLOSED_SETS}


async def test_press_key_is_deliberately_not_an_enum(server):
    """Any character is a key, so the set is open. It stays a string and says
    what it accepts instead."""
    prop = (await server.mcp.get_tool("press_key")).parameters["properties"]["key"]
    assert _enum(prop) is None
    assert prop["type"] == "string"


# ---- press_key: names, characters, combinations ----------------------------


@pytest.mark.parametrize(
    "given,sent",
    [
        # Selenium's own spelling, as before.
        ("enter", Keys.ENTER),
        ("arrow_left", Keys.ARROW_LEFT),
        ("page_down", Keys.PAGE_DOWN),
        # The DOM's KeyboardEvent.key spelling — what a model has seen most.
        ("Enter", Keys.ENTER),
        ("ArrowLeft", Keys.ARROW_LEFT),
        ("PageDown", Keys.PAGE_DOWN),
        ("Escape", Keys.ESCAPE),
        ("Backspace", Keys.BACKSPACE),
        # A single character is a key.
        ("a", "a"),
        ("/", "/"),
        ("+", "+"),
        (" ", " "),
        # Combinations are held, not typed.
        ("Control+a", Keys.CONTROL + "a"),
        ("Shift+Tab", Keys.SHIFT + Keys.TAB),
        ("control+shift+ArrowLeft", Keys.CONTROL + Keys.SHIFT + Keys.ARROW_LEFT),
        ("Control++", Keys.CONTROL + "+"),
    ],
)
def test_a_key_resolves(given, sent):
    assert resolve_key(given) == sent


@pytest.mark.parametrize("given", ["mouseover", "Control+mouseover", "", "Control+"])
def test_an_unknown_key_is_refused_with_the_names(given):
    with pytest.raises(ValueError) as refused:
        resolve_key(given)
    message = str(refused.value)
    assert "arrow_left" in message
    assert "Control+a" in message, "the refusal should show a combination"


def test_the_names_offered_are_one_per_key():
    """Selenium's Keys class spells 60 keys with 73 names — LEFT and ARROW_LEFT,
    four for Meta. Listing all of them offers thirteen duplicates as choices."""
    assert len(KEY_NAMES) == len(set(KEYS.values()))
    assert len({KEYS[name] for name in KEY_NAMES}) == len(KEY_NAMES)
    assert {"arrow_left", "backspace", "meta", "control", "shift"} <= set(KEY_NAMES)
    assert not {"left", "back_space", "command", "left_meta"} & set(KEY_NAMES)


class _Driver:
    current_url = "https://example.test/"
    title = "t"

    def __init__(self):
        self.sent = []

    def find_element(self, *_):
        return self

    def send_keys(self, *keys):
        self.sent.append("".join(keys))

    def execute_script(self, *_):
        return False


def test_press_key_sends_the_resolved_combination(actions, monkeypatch):
    """Through the action, not just the resolver: a combination reaches the
    browser as one send, which WebDriver releases the modifiers after."""
    driver = _Driver()
    monkeypatch.setattr(actions, "_at", lambda *a, **k: driver)
    monkeypatch.setattr(actions_module.browser, "settled", lambda *a, **k: None)

    result = actions.press_key("abc", "Control+a")
    assert driver.sent == [Keys.CONTROL + "a"]
    assert result["key"] == "Control+a"


# ---- descriptions that send a reader to the right tool ----------------------


async def test_press_key_describes_names_characters_and_combinations(server):
    description = (await server.mcp.get_tool("press_key")).description
    assert "ArrowLeft" in description
    assert "Control+a" in description
    assert "back_space" not in description, "aliases are not choices"


async def test_interact_says_a_hover_stays(server):
    """The fact that makes hover-then-click a two-call pattern."""
    description = (await server.mcp.get_tool("interact")).description
    assert "stays open" in description


async def test_execute_script_points_back_before_taking_the_job(server):
    description = (await server.mcp.get_tool("execute_script")).description
    assert "interact" in description
    assert ":hover" in description, "a script cannot open a :hover menu"


# ---- publishing the list must not start refusing what used to work ----------


@pytest.mark.parametrize(
    "tool,given,expected",
    [
        ("interact", {"action": "Hover", "selector": {"css": "a"}}, "hover"),
        ("frame", {"action": "Default"}, "default"),
        ("dialog", {"action": " ACCEPT "}, "accept"),
    ],
)
async def test_a_choice_is_accepted_in_any_case_over_mcp(
    server, monkeypatch, tool, given, expected
):
    """Before the enum these were plain strings the action layer lowercased, so
    `Hover` worked over MCP — and it still works over HTTP and in a flow. The
    schema now shows the lowercase spelling, but a caller that writes it
    capitalised must not start failing. Through the real tool, not a model."""
    from fastmcp import Client

    from .conftest import NAMED

    monkeypatch.setattr(server.sessions, "name", lambda: NAMED)
    monkeypatch.setattr(server.sessions, "resolve", lambda name: "abc")
    monkeypatch.setattr(
        server.actions, tool, lambda s, action, **_: {"action": action, "url": "about:blank"}
    )
    server.sessions.remember(NAMED, "abc", "", {"browser": "chrome"})

    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, given)
    assert result.data["action"] == expected


async def test_a_choice_outside_the_set_is_still_refused_over_mcp(server):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    async with Client(server.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("interact", {"action": "mouseover", "selector": {"css": "a"}})


@pytest.mark.parametrize("given", ["", "   "])
async def test_a_blank_browser_still_means_the_default(server, monkeypatch, given):
    """`normalize_browser` has always read a blank browser as "not given", and a
    client that encodes an omitted optional as "" relied on it. Publishing the
    choices must not turn that into a validation error (Copilot, #25).

    Asserted against what omitting it does, rather than against a name: the
    default is settings' to decide, and this is only about the blank reaching it.
    """
    from fastmcp import Client

    from .conftest import NAMED

    async def opened_with(arguments):
        seen = {}

        def fake_open(**kwargs):
            seen.update(kwargs)
            return {"session_id": "abc", "url": "about:blank"}

        monkeypatch.setattr(server.sessions, "name", lambda: NAMED)
        monkeypatch.setattr(server.actions, "open_session", fake_open)
        async with Client(server.mcp) as client:
            await client.call_tool("open_session", arguments)
        return seen

    # Only the browser: a second call inherits the first's remembered page, and
    # that has nothing to do with what this is about.
    blank = await opened_with({"browser": given})
    omitted = await opened_with({})
    assert blank.get("browser") == omitted.get("browser")
    # And a browser that was actually named still arrives, in any case.
    assert (await opened_with({"browser": "Firefox"})).get("browser") == "firefox"
