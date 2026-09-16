"""What a failed MCP tool call says — to the caller, and to the log.

The pilot's six wasted calls were pydantic's report of a `selector` mistake: a
type code, the input echoed and a link to pydantic's documentation (§F2.15).
And the MCP surface never asked `errors.py` anything, so a caller read Selenium's
stack dump and the pod logged a traceback for every typo. These go through the
real MCP client, because what matters is the text a caller and an operator read.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kubed.selenium_flow.server import SeleniumMCP

pytestmark = pytest.mark.unit


@pytest.fixture
def server(tmp_path):
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=None, flow_data_dir=str(tmp_path)
    )
    server.sessions.name = lambda: "refusals"
    server.sessions.library = lambda: "refusals"
    return server


async def refused(server, tool, arguments) -> str:
    async with Client(server.mcp) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool(tool, arguments)
    return str(caught.value)


async def test_the_old_flat_selector_is_told_where_it_went(server):
    """The shape a cached schema keeps sending after §F2.14."""
    said = await refused(server, "interact", {"action": "click", "css": "button.go"})
    assert 'selector={"css": "button.go"}' in said
    # Named once. "needs an element" as well would be the same fix twice.
    assert "needs an element" not in said


async def test_a_selector_given_as_text_is_shown_the_object_it_should_be(server):
    said = await refused(server, "interact", {"action": "click", "selector": "button.go"})
    assert '{"css": "button.go"}' in said and "xpath" in said


async def test_a_word_outside_a_closed_set_is_given_the_set(server):
    said = await refused(server, "interact", {"action": "tap", "selector": {"css": "a"}})
    assert "click, double_click, right_click, hover, scroll_to" in said


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("interact", {"action": "click", "css": "button.go"}),
        ("interact", {"action": "click", "selector": {"id": "go"}}),
        ("navigate", {"url": 5}),
        # Past what the checker models - a selector's own field of the wrong
        # type - so this is the fallback to pydantic's messages.
        ("interact", {"action": "click", "selector": {"css": 5}}),
    ],
)
async def test_no_refusal_reads_like_pydantic(server, tool, arguments):
    said = await refused(server, tool, arguments)
    assert said.startswith(f"{tool} refused its arguments")
    assert "errors.pydantic.dev" not in said
    assert "type=" not in said and "input_value" not in said


async def test_a_step_and_a_call_are_refused_in_the_same_words(server):
    """A step's arguments ARE a tool call's arguments, so one mistake gets one
    sentence whichever door it came through."""
    call = await refused(server, "interact", {"action": "click", "css": "button.go"})
    tool = await server.mcp.get_tool("save_flow")
    with pytest.raises(ValueError) as saved:
        await tool.fn(
            name="x",
            steps=[{"tool": "interact", "args": {"action": "click", "css": "button.go"}}],
        )
    assert 'it goes inside selector: selector={"css": "button.go"}' in call
    assert 'it goes inside selector: selector={"css": "button.go"}' in str(saved.value)


async def test_no_published_schema_carries_a_note_for_developers(server):
    """A model's docstring IS its schema description, in every tool that takes
    it. `Selector`'s cited the saga to every agent that listed the tools."""
    for tool in await server.mcp.list_tools():
        text = json.dumps(tool.parameters, ensure_ascii=False)
        assert "§" not in text, tool.name
        assert "Copilot" not in text, tool.name


async def test_the_flow_schema_has_no_reference_it_cannot_resolve(server):
    """Step schemas are copied out of tool schemas whose `$defs` stayed behind,
    so every selector in the published flow schema pointed at nothing."""
    from kubed.selenium_flow.flows import api

    document = await api._document_schema(api.Schemas(server.mcp))
    assert "$ref" not in json.dumps(document)
    selector = document["x-step-params"]["interact"]["properties"]["selector"]
    assert any("css" in (b.get("properties") or {}) for b in selector["anyOf"])


# ---- failures that are not about argument shape --------------------------------


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, self.format(record)))


@pytest.fixture
def log():
    from kubed.selenium_flow.mcp.failures import FASTMCP_LOGGER

    handler = Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(FASTMCP_LOGGER)
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)


def failing(server, exc):
    def act(*_args, **_kwargs):
        raise exc

    server.sessions.act = act


async def test_a_caller_reads_the_failure_not_seleniums_stack_dump(server):
    from selenium.common.exceptions import WebDriverException

    failing(server, WebDriverException("no such window", stacktrace=["#0 0x55 <unknown>"]))
    said = await refused(server, "extract", {})
    assert said == "Error calling tool 'extract': no such window"


async def test_a_credential_in_a_failure_reaches_neither_caller_nor_log(server, log):
    """#36 scrubbed this for HTTP callers only; the MCP surface quoted `str(exc)`
    and logged the raw traceback."""
    failing(server, ConnectionError("could not reach http://user:hunter2@grid:4444/wd"))
    said = await refused(server, "extract", {})
    assert "hunter2" not in said
    assert log and not any("hunter2" in text for _, text in log)
    # Ours, so the operator still gets the frames — scrubbed, not dropped.
    level, text = log[-1]
    assert level == logging.ERROR and "Traceback" in text


async def test_a_callers_mistake_is_one_warning_line_not_a_traceback(server, log):
    """A refused save is the caller's to fix; the pod log is for our faults."""
    await refused(
        server,
        "save_flow",
        {"name": "x", "steps": [{"tool": "navigate", "args": {"url": "u"}}], "timeout": 0},
    )
    level, text = log[-1]
    assert level == logging.WARNING
    assert "timeout must be a whole number" in text
    assert "Traceback" not in text


async def test_a_refused_argument_shape_is_one_warning_line_too(server, log):
    """FastMCP logs an argument refusal itself, as a warning with no traceback,
    before this module's filter could see it - so it must stay that way rather
    than be read as a fault (Copilot, #38)."""
    await refused(server, "interact", {"action": "click", "css": "button.go"})
    assert log, "nothing was logged for the refusal"
    assert all(level == logging.WARNING for level, _ in log)
    assert not any("Traceback" in text for _, text in log)
