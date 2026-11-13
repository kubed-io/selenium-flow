"""Agent files load into cards and details (spec 2026-11-12-plugin-agents)."""

import logging

import pytest
import yaml

from kubed.selenium_flow.mcp import agents
from kubed.selenium_flow.mcp.frontmatter import split

pytestmark = pytest.mark.unit

FRONT = (
    "name: explorer\ndescription: |\n  Explores\n  a site.\nmodel: sonnet\n"
    "tools: [navigate, extract]\ncapabilities: [browse]\nskills: [selenium-flow, nope]"
)
BODY = "# Explore\n\nRead the skill first.\n"


def agent_file(folder, file, front, body=BODY):
    path = folder / file
    path.write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")
    return path


def good(folder):
    return agent_file(folder, "explorer.md", "name: explorer\ndescription: Explores.")


def names(folder, **kw):
    return [a.name for a in agents.load(folder, **kw)]


def test_a_file_becomes_a_card_and_a_detail(tmp_path):
    agent_file(tmp_path, "explorer.md", FRONT)
    [agent] = agents.load(tmp_path, skills={"selenium-flow"})
    assert agent.name == "explorer"
    assert agent.card == {"name": "explorer", "description": "Explores a site.", "capabilities": ["browse"]}
    assert agent.detail == {
        "agent": "explorer",
        "instructions": "# Explore\n\nRead the skill first.",
        "modelRoutingHint": {"role": "medium", "vision": False},
        "tools": [],
        "_meta": {
            "io.kubed/agent": {
                "frontmatter": yaml.safe_load(FRONT),
                "skills": {"selenium-flow": "skill://selenium-flow/SKILL.md"},
            }
        },
    }


@pytest.mark.parametrize(
    ("value", "role"),
    [
        ("opus", "large"),
        ("claude-opus-4-1", "large"),
        ("Sonnet", "medium"),
        ("claude-sonnet-4-5", "medium"),
        ("haiku", "small"),
        ("inherit", None),
        ("gpt-5", None),
        (None, None),
        (4, None),
    ],
)
def test_the_routing_hint(value, role):
    assert agents.routing_hint(value) == ({"role": role, "vision": False} if role else None)


def test_no_model_has_no_hint(tmp_path):
    good(tmp_path)
    [agent] = agents.load(tmp_path)
    assert "modelRoutingHint" not in agent.detail


@pytest.mark.parametrize("extra", ["", "\ncapabilities: [1, x]"])
def test_capabilities_default_to_none(tmp_path, extra):
    agent_file(tmp_path, "explorer.md", "name: explorer\ndescription: x" + extra)
    [agent] = agents.load(tmp_path)
    assert agent.card["capabilities"] == []


def test_a_blank_body_has_no_instructions(tmp_path):
    agent_file(tmp_path, "explorer.md", "name: explorer\ndescription: x", body="\n  \n")
    [agent] = agents.load(tmp_path)
    assert "instructions" not in agent.detail


@pytest.mark.parametrize(
    "skills",
    ["skills: selenium-flow", "skills: [1, selenium-flow]", "skills: {selenium-flow: x}", ""],
)
def test_skills_resolve_only_to_served_skills(tmp_path, skills):
    agent_file(tmp_path, "explorer.md", f"name: explorer\ndescription: x\n{skills}")
    [agent] = agents.load(tmp_path, skills={"selenium-flow"})
    assert agent.detail["_meta"][agents.META_KEY]["skills"] == {}


def test_skills_resolve_to_nothing_by_default(tmp_path):
    agent_file(tmp_path, "explorer.md", "name: explorer\ndescription: x\nskills: [selenium-flow]")
    [agent] = agents.load(tmp_path)
    assert agent.detail["_meta"][agents.META_KEY]["skills"] == {}


def test_the_cards_are_in_name_order(tmp_path):
    agent_file(tmp_path, "a.md", "name: zeta\ndescription: x")
    agent_file(tmp_path, "b.md", "name: alpha\ndescription: x")
    assert names(tmp_path) == ["alpha", "zeta"]


@pytest.mark.parametrize(
    "text",
    [
        "# no header\n",
        "---\n- a\n- list\n---\n",
        "---\n{}\n---\n",
        "---\nname: [unclosed\n---\n",
        "---\nname: ex plorer\ndescription: x\n---\n",
        "---\nname: a/b\ndescription: x\n---\n",
        "---\nname: a@b\ndescription: x\n---\n",
        "---\nname: ex​plorer\ndescription: x\n---\n",
        "---\nname: 123\ndescription: x\n---\n",
        "---\nname: bad\ndescription: ''\n---\n",
        "---\nname: bad\ndescription: '   '\n---\n",
        "---\nname: bad\n---\n",
        "---\nname: bad\ndescription: x\nwhen: 2026-11-12\n---\n",
        "---\nname: bad\ndescription: x\nmetadata: {1: one, '1': also}\n---\n",
    ],
)
def test_a_nonconforming_file_is_skipped_and_logged(tmp_path, caplog, text):
    good(tmp_path)
    (tmp_path / "bad.md").write_text(text, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert names(tmp_path) == ["explorer"]
    assert "is skipped" in caplog.text
    assert "bad.md" in caplog.text


def test_a_file_that_is_not_utf8_is_skipped(tmp_path, caplog):
    good(tmp_path)
    (tmp_path / "bad.md").write_bytes(b"---\nname: caf\xe9\ndescription: x\n---\n")
    with caplog.at_level(logging.WARNING):
        assert names(tmp_path) == ["explorer"]
    assert "is skipped" in caplog.text
    assert "bad.md" in caplog.text


def test_crlf_line_endings_load(tmp_path):
    with (tmp_path / "explorer.md").open("w", encoding="utf-8", newline="\r\n") as f:
        f.write(f"---\nname: explorer\ndescription: x\n---\n{BODY}")
    [agent] = agents.load(tmp_path)
    assert agent.detail["instructions"] == "# Explore\n\nRead the skill first."


def test_the_first_of_two_files_with_one_name_wins(tmp_path, caplog):
    agent_file(tmp_path, "a.md", "name: explorer\ndescription: First.")
    agent_file(tmp_path, "b.md", "name: explorer\ndescription: Second.")
    with caplog.at_level(logging.WARNING):
        [agent] = agents.load(tmp_path)
    assert agent.card["description"] == "First."
    assert "b.md" in caplog.text
    assert "a.md" in caplog.text


def test_only_markdown_at_the_top_level_is_read(tmp_path):
    (tmp_path / "drafts").mkdir()
    agent_file(tmp_path / "drafts", "x.md", "name: x\ndescription: x")
    (tmp_path / "notes.txt").write_text("hi")
    assert agents.load(tmp_path) == []


def test_a_missing_folder_is_no_agents(tmp_path):
    assert agents.load(tmp_path / "missing") == []


def test_split_returns_the_header_and_the_body():
    assert split("---\na: 1\n---\nbody\n") == ({"a": 1}, "body")
    assert split("no header") is None
    assert split("---\na: 1\n") is None
    assert split("---\na: [\n---\n") is None
