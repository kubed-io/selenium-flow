"""`outline`: what is on the page, with a selector and a verdict for each.

The first pilot report spent three hand-written `execute_script` DOM dumps
finding one sidebar link — enumerate the links, walk each parent chain for
`display: none`, find the toggle — and then clicked it and failed anyway. Both
halves of that are this tool: the selector, and whether the element can actually
be used. See saga §F2.8.
"""

import pytest

from kubed.selenium_flow.core import probe
from kubed.selenium_flow.flowdoc import InvalidFlow, step_schemas, validate
from kubed.selenium_flow.routes import ENDPOINTS, method_for

pytestmark = pytest.mark.unit

FOUND = [
    {"role": "button", "name": "HelpDesk", "selector": {"css": "#menu-toggle"}, "visible": True},
    {
        "role": "link",
        "name": "Feature Requests",
        "selector": {"xpath": "//a[normalize-space()='Feature Requests']"},
        "visible": False,
        "reason": "hidden",
        "blocked_by": "ul.menu-content",
    },
]


class _Page:
    current_url = "https://example.test/tickets"
    title = "Tickets"

    def __init__(self, found=FOUND):
        self.found = found
        self.args = None

    def execute_script(self, script, *args):
        self.args = args
        return self.found

    def find_element(self, *_):
        return "<scope>"


# ---- the surface ------------------------------------------------------------


def test_outline_is_on_both_surfaces():
    assert ENDPOINTS["outline"] == "outline"
    assert method_for("outline") == "outline"


async def test_the_tool_publishes_its_arguments(server):
    schema = (await server.mcp.get_tool("outline")).parameters
    assert {"selector", "text", "limit", "interactive"} <= set(schema["properties"])
    assert schema.get("required", []) == []


async def test_it_reads_the_page_and_says_so(server):
    """`readOnlyHint` decides whether a client asks the user first. This one
    runs a script, but it is *our* script and it only looks."""
    tool = await server.mcp.get_tool("outline")
    assert tool.annotations.read_only_hint is True


# ---- what it returns --------------------------------------------------------


def test_it_answers_with_the_elements_and_where_it_looked(actions, monkeypatch):
    page = _Page()
    monkeypatch.setattr(actions, "_at", lambda *a, **k: page)
    result = actions.outline("abc")
    assert result["elements"] == FOUND
    assert result["count"] == 2
    assert result["url"] == page.current_url


def test_the_whole_page_is_the_default_scope(actions, monkeypatch):
    """No scope means no element lookup at all — `null` reaches the script and
    it falls back to the body."""
    page = _Page()
    monkeypatch.setattr(actions, "_at", lambda *a, **k: page)
    actions.outline("abc")
    assert page.args[0] is None


def test_a_scope_is_waited_for_like_any_other_element(actions, monkeypatch):
    """A scope is an element, so it gets the same wait every element gets —
    otherwise outlining a panel that has not rendered yet answers about nothing."""
    from kubed.selenium_flow.core import actions as actions_module

    page = _Page()
    waited = []
    monkeypatch.setattr(actions, "_at", lambda *a, **k: page)
    monkeypatch.setattr(
        actions_module.browser,
        "wait_for_element",
        lambda driver, target, timeout: waited.append(target) or "<scope>",
    )
    actions.outline("abc", selector={"css": "nav"})
    assert waited == [("css selector", "nav")]
    assert page.args[0] == "<scope>"


def test_the_filter_and_the_limit_reach_the_page(actions, monkeypatch):
    page = _Page()
    monkeypatch.setattr(actions, "_at", lambda *a, **k: page)
    actions.outline("abc", text="Feature", limit=5, interactive=False)
    assert page.args[1] == "Feature"
    assert page.args[2] == 5
    assert page.args[3] is False


def test_the_limit_defaults_to_something_a_page_map_can_afford(actions, monkeypatch):
    page = _Page()
    monkeypatch.setattr(actions, "_at", lambda *a, **k: page)
    actions.outline("abc")
    assert page.args[2] == probe.DEFAULT_LIMIT


# ---- not a step -------------------------------------------------------------


async def test_a_flow_cannot_outline_anything(server):
    """It is for working out what a flow should do, not for doing it — a saved
    flow already knows its selectors (saga §F2.8)."""
    tools = {}
    for name in sorted(set(ENDPOINTS.values())):
        tools[name] = (await server.mcp.get_tool(name)).parameters or {}

    document = {
        "description": "Look around",
        "steps": [{"tool": "outline", "args": {"selector": {"css": "nav"}}}],
    }
    with pytest.raises(InvalidFlow, match="outline is not a step") as refused:
        validate(document, step_schemas(tools))
    assert "while writing or repairing" in str(refused.value), "say what it is for"


async def test_the_step_schema_does_not_offer_it(server):
    """`flow://schema` is what an author writes against; offering a tool the
    runner refuses is how a flow gets written that cannot run."""
    tools = {}
    for name in sorted(set(ENDPOINTS.values())):
        tools[name] = (await server.mcp.get_tool(name)).parameters or {}
    assert "outline" not in step_schemas(tools)


def test_the_runner_would_refuse_it_too():
    from kubed.selenium_flow.flowrun import RUNNABLE

    assert "outline" not in RUNNABLE
    assert "extract" in RUNNABLE, "and the ordinary reads are still steps"
