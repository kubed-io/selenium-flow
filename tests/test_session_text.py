"""The agent-facing text says what the code does about sessions (#248, part C)."""

import pytest
from fastmcp import Client

from kubed.selenium_flow.mcp import skill
from kubed.selenium_flow.mcp.instructions import instructions

pytestmark = pytest.mark.unit

RETIRED = (
    "sub-session",
    "is the workspace's own",
    "workspace's default",
    "default session",
    "ends that one first",
)


async def tools(server):
    async with Client(server.mcp.server) as client:
        return {t.name: t for t in await client.list_tools()}


async def test_no_tool_or_resource_text_names_a_fixed_default(server):
    texts = []
    async with Client(server.mcp.server) as client:
        for tool in await client.list_tools():
            texts.append(tool.description or "")
            for prop in (tool.inputSchema.get("properties") or {}).values():
                texts.append(prop.get("description") or "")
        for res in await client.list_resources():
            texts.append(res.description or "")
        for tpl in await client.list_resource_templates():
            texts.append(tpl.description or "")
    for reads in (True, False):
        for via in ("mcp", "extension", "local"):
            texts.append(instructions(reads, via))
    for text in texts:
        for phrase in RETIRED:
            assert phrase not in text.lower(), (phrase, text[:80])


async def test_open_session_says_a_join_joins_and_ends_nothing(server):
    text = " ".join((await tools(server))["open_session"].description.split())
    for phrase in (
        "or joins it when its browser is live; that ends nothing",
        "joined: true",
        "fresh=true",
        "main_moved",
    ):
        assert phrase in text


async def test_open_session_says_a_join_keeps_capture(server):
    text = " ".join((await tools(server))["open_session"].description.split())
    assert "a join keeps it" in text


async def test_the_session_argument_says_your_own_is_named_after_you(server):
    nav = (await tools(server))["navigate"]
    assert nav.inputSchema["properties"]["session"]["description"] == (
        "Which session to act in, by name. Leave it out to act in your own, named "
        "after you. Only open_session opens one. A named session's state is "
        "session://<name>/…; workspace:// is your own."
    )


# Part D's (#249): its plan removes these two from the skip.
PART_D = {"ADMIN_UI.md", "LIVE_VIEW.md"}


def test_no_skill_agent_or_note_names_a_fixed_default():
    root = skill.plugin_path().parent
    files = [*(root / "plugin").rglob("*.md"), *(root / "docs/notes").rglob("*.md")]
    assert files
    for path in files:
        if path.name in PART_D and "using-the-admin-ui" in path.parts:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for phrase in RETIRED:
            assert phrase not in text, (phrase, str(path))
