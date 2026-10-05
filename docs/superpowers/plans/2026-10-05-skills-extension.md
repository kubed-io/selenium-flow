# The Skills Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Skills-aware MCP client discovers selenium-flow's embedded skill with `skills/list`/`skills/get` and verifies every file it reads against the entry's digests.

**Architecture:** A new module, `kubed/selenium_flow/mcp/skill_extension.py`, builds the skill's one SEP-2640 entry when the server starts, from FastMCP's `SkillProvider`. It registers a FastMCP `ServerExtension` binding the two JSON-RPC methods, plus a middleware that adds the declaration to a legacy `initialize` reply. `skill.register()` calls it, so `--mcp-skill false` turns it off with the skill.

**Tech Stack:** Python ≥3.10, FastMCP 4.0.x (`fastmcp.server.extensions`), `mcp_types`, PyYAML, pytest (asyncio auto mode).

**Spec:** `docs/superpowers/specs/2026-10-05-skills-extension-design.md`

## Global Constraints

- Extension identifier: `io.modelcontextprotocol/skills`, exactly.
- Result fields on both methods: `resultType: "complete"`, `ttlMs: 0`, `cacheScope: "private"`.
- Errors: unknown skill → `INVALID_PARAMS` "Skill not found"; any cursor → `INVALID_PARAMS` "Invalid skills cursor".
- Digests: `sha256:<hex>` of the bytes `resources/read` returns, never the disk bytes.
- No change to `resources/list`, `_manifest`, the mirror tools or the instructions.
- ruff: target py310, 88 columns outside `tests/`. CI runs `ruff check`.
- Comments earn their lines: keep the non-obvious *why*, nothing else.
- The user is "Dr K" in anything committed; no real names.
- One PR, on branch `skills-extension`. The wiki is a submodule: commit inside `wiki/`. The controller pushes wiki `master` before the branch.

## Running tests in this pod

There is no venv. Use the scratch libs and `-S` (the `kubed-krm` package shadows the `kubed` namespace otherwise):

```bash
PY=/tmp/claude-1000/-projects-cluster/c609cf8e-e357-4d7e-8988-ef046fbf64f9/scratchpad/pylibs
PYTHONPATH=$PWD:$PY python3 -S -m pytest tests/test_skills_extension.py -q
$PY/bin/ruff check . && $PY/bin/ruff format --check kubed tests
```

Six full-suite failures are known pod-environment failures and are not yours: `test_boundaries` ×4, the `test_shutdown` SIGTERM test, `test_wiki::test_the_generated_pages_are_current`.

---

### Task 1: The extension, wired in, with its tests

**Files:**
- Create: `kubed/selenium_flow/mcp/skill_extension.py`
- Modify: `kubed/selenium_flow/mcp/skill.py` (import, and `register()` at the end of the file)
- Test: `tests/test_skills_extension.py` (create)

**Interfaces:**
- Consumes: `skill.load() -> SkillProvider | None` and `skill.register(mcp, provider)` (existing); the `server` fixture in `tests/conftest.py` (an authenticated `SeleniumMCP`).
- Produces: `skill_extension.EXTENSION_ID: str`, `entry_for(provider: SkillProvider) -> dict | None`, `SkillsExtension(entry: dict)` with `async list_skills(ctx, params: PaginatedRequestParams) -> dict` and `async get_skill(ctx, params: GetSkillParams) -> dict`, `GetSkillParams`, `AdvertiseSkills(Middleware)`, `register(mcp, provider: SkillProvider) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills_extension.py`:

```python
"""The MCP Skills extension (SEP-2640), through real MCP requests in both eras.

A Skills-aware host trusts nothing it cannot verify: it reads each file the entry
names and checks it against the digest. So the strongest tests here do exactly
that over the wire, rather than comparing the entry with the disk.
"""

import hashlib
import logging

import pytest
import yaml
from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills.skill_provider import SkillProvider
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PaginatedRequestParams,
    Request,
    RequestParams,
)
from pydantic import ConfigDict, TypeAdapter

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import skill_extension
from kubed.selenium_flow.mcp.skill_extension import EXTENSION_ID, SkillsExtension
from kubed.selenium_flow.server import SeleniumMCP

pytestmark = pytest.mark.unit

SKILL_URI = "skill://selenium-flow/SKILL.md"
MANIFEST_URI = "skill://selenium-flow/_manifest"


class ExtensionParams(RequestParams):
    model_config = ConfigDict(extra="allow")


async def request(client, method, **params):
    return await client.session.send_request(
        Request(method=method, params=ExtensionParams(**params)), TypeAdapter(dict)
    )


def header(text: str) -> object:
    return yaml.safe_load(text.split("---", 2)[1])


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_extension_is_declared_beside_resources(server, mode):
    async with Client(server.mcp, mode=mode) as client:
        caps = client.session.server_capabilities
        assert caps.extensions[EXTENSION_ID] == {}
        assert caps.resources is not None


async def test_the_listing_is_the_one_skill(server):
    async with Client(server.mcp) as client:
        listing = await request(client, "skills/list")
    assert listing["resultType"] == "complete"
    assert listing["ttlMs"] == 0
    assert listing["cacheScope"] == "private"
    assert [entry["uri"] for entry in listing["skills"]] == [SKILL_URI]


async def test_get_returns_the_listed_entry(server):
    async with Client(server.mcp) as client:
        listed = (await request(client, "skills/list"))["skills"][0]
        got = await request(client, "skills/get", uri=SKILL_URI)
    assert got["skill"] == listed
    assert got["resultType"] == "complete"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_every_file_verifies_against_its_digest(server, mode):
    async with Client(server.mcp, mode=mode) as client:
        entry = (await request(client, "skills/list"))["skills"][0]
        for resource in entry["resources"]:
            data = (await client.read_resource(resource["uri"]))[0].text.encode()
            assert resource["size"] == len(data), resource["uri"]
            digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
            assert resource["digest"] == digest, resource["uri"]


async def test_the_entry_names_every_file_the_server_serves(server):
    """A reference added later is in the entry without anyone touching it."""
    async with Client(server.mcp) as client:
        entry = (await request(client, "skills/list"))["skills"][0]
        served = {
            str(r.uri)
            for r in await client.list_resources()
            if str(r.uri).startswith("skill://selenium-flow/")
        }
    named = [resource["uri"] for resource in entry["resources"]]
    assert len(named) == len(set(named))
    assert set(named) == served - {MANIFEST_URI}
    assert SKILL_URI in named


async def test_the_frontmatter_is_the_served_header(server):
    async with Client(server.mcp) as client:
        entry = (await request(client, "skills/list"))["skills"][0]
        text = (await client.read_resource(SKILL_URI))[0].text
    assert entry["frontmatter"] == header(text)
    assert entry["frontmatter"]["name"] == "selenium-flow"


@pytest.mark.parametrize(
    ("method", "params", "message"),
    [
        ("skills/get", {"uri": "skill://nope/SKILL.md"}, "Skill not found"),
        ("skills/get", {"uri": MANIFEST_URI}, "Skill not found"),
        ("skills/list", {"cursor": "2"}, "Invalid skills cursor"),
    ],
)
async def test_refusals_are_invalid_params(server, method, params, message):
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as caught:
            await request(client, method, **params)
    assert caught.value.error.code == INVALID_PARAMS
    assert message in str(caught.value)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_switching_the_skill_off_removes_the_extension(mode):
    off = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": "t"},
                 mcp={"skill": False})
    )
    async with Client(off.mcp, mode=mode) as client:
        extensions = client.session.server_capabilities.extensions or {}
        assert EXTENSION_ID not in extensions
        with pytest.raises(MCPError) as caught:
            await request(client, "skills/list")
    assert caught.value.error.code == METHOD_NOT_FOUND


@pytest.mark.parametrize(
    "front",
    [
        "name: some-other-name\ndescription: Does a thing.",
        "name: odd-skill\ndescription: ''",
        "name: odd-skill\ndescription: " + "x" * 1025,
        "- a list\n- not a mapping",
        "name: odd-skill\ndescription: Dated.\nwhen: 2026-10-05",
    ],
    ids=["name", "empty-description", "long-description", "not-a-mapping", "date"],
)
async def test_a_nonconforming_skill_is_served_but_not_offered(tmp_path, caplog, front):
    folder = tmp_path / "odd-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(f"---\n{front}\n---\n\n# Body\n", encoding="utf-8")
    provider = SkillProvider(folder, supporting_files="resources")
    mcp = FastMCP("t")
    mcp.add_provider(provider)

    with caplog.at_level(logging.WARNING):
        assert skill_extension.register(mcp, provider) is False
    assert "not offered through the Skills extension" in caplog.text

    async with Client(mcp) as client:
        assert EXTENSION_ID not in (client.session.server_capabilities.extensions or {})
        assert (await client.read_resource("skill://odd-skill/SKILL.md"))[0].text


async def test_a_caller_cannot_change_what_the_next_caller_is_served(server):
    entry = skill_extension.entry_for(server.skill)
    extension = SkillsExtension(entry)
    first = await extension.list_skills(None, PaginatedRequestParams())
    first["skills"][0]["resources"].clear()
    first["skills"][0]["frontmatter"]["name"] = "changed"
    second = await extension.list_skills(None, PaginatedRequestParams())
    assert second["skills"][0]["resources"]
    assert second["skills"][0]["frontmatter"]["name"] == "selenium-flow"
```

Note on the `date` case: `yaml.safe_load` turns `2026-10-05` into a `datetime.date`, which `json.dumps` rejects. That is the "not representable as JSON" refusal.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD:$PY python3 -S -m pytest tests/test_skills_extension.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'kubed.selenium_flow.mcp.skill_extension'`.

- [ ] **Step 3: Create the module**

Create `kubed/selenium_flow/mcp/skill_extension.py` with exactly this content. It was prototyped against the real server in this pod and passes `ruff check` and `ruff format --check`.

```python
"""The MCP Skills extension (SEP-2640) for the embedded skill.

``skill.py`` serves the skill as resources, which is FastMCP's job. A client that
implements the Skills extension looks for more: the extension declared in the
server's capabilities, then ``skills/list`` and ``skills/get``. They return an
entry naming every file of the skill with the digest and size of what
``resources/read`` hands back, so the client can verify each file it loads. They
are JSON-RPC methods beside ``resources/list``, not tools.

FastMCP has no built-in support yet (its #5016), so this part is ours, and kept
apart from ``skill.py`` so it is easy to delete once FastMCP ships its own.

The entry is built once, with the server: package data cannot change under a
running process.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import mimetypes
from collections.abc import Sequence
from pathlib import Path

import yaml
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers.skills.skill_provider import SkillProvider
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, RequestParams
from pydantic import Field

log = logging.getLogger(__name__)

EXTENSION_ID = "io.modelcontextprotocol/skills"
MAX_DESCRIPTION = 1024


class GetSkillParams(RequestParams):
    uri: str = Field(min_length=1)


def _served_bytes(path: Path) -> bytes:
    """What ``resources/read`` returns for this file, as bytes.

    SkillProvider reads a ``text/*`` file with ``read_text``, which normalises
    newlines, and anything else raw. A host verifies the bytes it receives, so
    the digest follows the read, not the disk (which is what ``_manifest``
    hashes). Importing SkillProvider registers ``.md`` as ``text/markdown``.
    """
    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type and mime_type.startswith("text/"):
        return path.read_text(encoding="utf-8").encode("utf-8")
    return path.read_bytes()


def _frontmatter(text: str) -> object:
    """The YAML header of a SKILL.md, as parsed, or None without one."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        return yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None


def _problem(metadata: object, name: str) -> str | None:
    """Why this frontmatter cannot be offered as a conforming entry, if it cannot."""
    if not isinstance(metadata, dict):
        return "SKILL.md has no YAML frontmatter mapping"
    try:
        # Frontmatter travels as JSON; a YAML-only value would not survive it.
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError):
        return "its frontmatter is not representable as JSON"
    if metadata.get("name") != name:
        return f"its frontmatter name is not the folder name {name!r}"
    description = metadata.get("description")
    if not isinstance(description, str) or not (
        1 <= len(description) <= MAX_DESCRIPTION
    ):
        return f"its description is not a string of 1-{MAX_DESCRIPTION} characters"
    return None


def entry_for(provider: SkillProvider) -> dict | None:
    """The skill's SEP-2640 entry, or None (logged) if it does not conform."""
    info = provider.skill_info
    metadata = _frontmatter((info.path / info.main_file).read_text(encoding="utf-8"))
    problem = _problem(metadata, info.name)
    if problem:
        log.warning(
            "skill %s is not offered through the Skills extension: %s",
            info.name,
            problem,
        )
        return None
    resources = []
    for file in info.files:
        data = _served_bytes(info.path / file.path)
        resources.append(
            {
                "uri": f"skill://{info.name}/{file.path}",
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
                "size": len(data),
            }
        )
    resources.sort(key=lambda resource: resource["uri"])
    return {
        "uri": f"skill://{info.name}/{info.main_file}",
        "frontmatter": metadata,
        "resources": resources,
    }


def _result(**fields: object) -> dict:
    # The same freshness fields as mcp-kb: a hint, not an integrity property.
    return {"resultType": "complete", "ttlMs": 0, "cacheScope": "private", **fields}


class SkillsExtension(ServerExtension):
    """``skills/list`` and ``skills/get`` over the one embedded skill."""

    identifier = EXTENSION_ID

    def __init__(self, entry: dict):
        self._entry = entry

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding("skills/list", PaginatedRequestParams, self.list_skills),
            MethodBinding("skills/get", GetSkillParams, self.get_skill),
        )

    async def list_skills(self, ctx, params: PaginatedRequestParams) -> dict:
        if params.cursor is not None:
            # One skill, so one page; any cursor is one this server never issued.
            raise MCPError(code=INVALID_PARAMS, message="Invalid skills cursor")
        return _result(skills=[copy.deepcopy(self._entry)])

    async def get_skill(self, ctx, params: GetSkillParams) -> dict:
        if params.uri != self._entry["uri"]:
            raise MCPError(code=INVALID_PARAMS, message="Skill not found")
        return _result(skill=copy.deepcopy(self._entry))


class AdvertiseSkills(Middleware):
    """Declare the extension in a legacy ``initialize`` reply too.

    FastMCP declares registered extensions in ``server/discover`` but drops
    ``capabilities.extensions`` from ``initialize``, and still answers the
    methods; without this, a handshake-era client never learns they exist.
    """

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is not None:
            result.capabilities.extensions = {
                **(result.capabilities.extensions or {}),
                EXTENSION_ID: {},
            }
        return result


def register(mcp, provider: SkillProvider) -> bool:
    """Offer the skill through the extension.

    False, with nothing added, when the skill does not conform: its resources
    are still served, so a bad SKILL.md costs the extension, not the boot.
    """
    entry = entry_for(provider)
    if entry is None:
        return False
    mcp.add_extension(SkillsExtension(entry))
    mcp.add_middleware(AdvertiseSkills())
    return True
```

- [ ] **Step 4: Wire it into `skill.register`**

In `kubed/selenium_flow/mcp/skill.py`, add the import after the `SkillProvider` import:

```python
from fastmcp.server.providers.skills.skill_provider import SkillProvider

from . import skill_extension
```

Replace `register` at the end of the file:

```python
def register(mcp, provider: SkillProvider) -> None:
    """Serve the skill's resources, and offer it through the Skills extension."""
    mcp.add_provider(provider)
    skill_extension.register(mcp, provider)
```

In the module docstring, after the paragraph ending "…invisible to every one of them.", add:

```text
A client that implements the MCP Skills extension also finds the skill through
``skills/list`` and ``skills/get``; that part is ours, in ``skill_extension``.
```

- [ ] **Step 5: Run the new tests**

Run: `PYTHONPATH=$PWD:$PY python3 -S -m pytest tests/test_skills_extension.py -q`
Expected: 19 passed. (The plan's tests were run against this exact module in the pod: 19 passed, plus the 123 neighbouring tests in Step 6.)

- [ ] **Step 6: Run the skill and neighbouring tests, then lint**

Run: `PYTHONPATH=$PWD:$PY python3 -S -m pytest tests/test_skill.py tests/test_resources.py tests/test_discoverability.py tests/test_show.py -q`
Expected: all pass.

Run: `$PY/bin/ruff check . && $PY/bin/ruff format --check kubed/selenium_flow/mcp/skill_extension.py kubed/selenium_flow/mcp/skill.py`
Expected: `All checks passed!` and both files already formatted.

- [ ] **Step 7: Commit**

```bash
git add kubed/selenium_flow/mcp/skill_extension.py kubed/selenium_flow/mcp/skill.py tests/test_skills_extension.py
git commit -m "The embedded skill on the MCP Skills extension: skills/list and skills/get"
```

---

### Task 2: Documentation

**Files:**
- Modify: `AGENTS.md` (section `## The embedded skill`, after the paragraph about `test_every_skill_file_is_covered_by_package_data`)
- Modify: `README.md` (section `### 📖 It teaches you how to use it`, after the paragraph ending "`list_skills` and `download_skill` work here with no special casing.")
- Modify: `kubed/selenium_flow/config.py:251`
- Modify: `wiki/Configuration.md` (the `mcp.skill` row), in the `wiki` submodule
- Modify: `CHANGELOG.md` (`## [Unreleased]`, as its first entry)

**Interfaces:**
- Consumes: Task 1's module name `skill_extension` and the behaviour it ships.
- Produces: nothing code depends on.

- [ ] **Step 1: AGENTS.md**

After the package-data paragraph in *The embedded skill* (it ends "…that no pattern matches."), insert:

```markdown
The **Skills extension** (SEP-2640, `io.modelcontextprotocol/skills`) is a second
discovery surface over the same files, in `mcp/skill_extension.py`: `skills/list`
and `skills/get` return one entry naming every file with the sha256 and size of
the bytes `resources/read` serves. Those are not `_manifest`'s hashes, which are of
the disk, and `SkillProvider` reads text with newline normalisation. FastMCP
declares the extension in `server/discover` only, so `AdvertiseSkills` puts it back
into a legacy `initialize`. A SKILL.md that fails the spec (name ≠ folder,
description outside 1–1024 characters, frontmatter that is not JSON) is still
served as resources but not offered. agentgateway 1.6 refuses both methods
(`unsupported method`, agentgateway#3579), so through the gateway a client sees the
declaration and gets an error; in-cluster they work. Delete the module when
FastMCP ships its own (#5016).
```

- [ ] **Step 2: README.md**

After "Those URIs are FastMCP's convention, served by its own `SkillProvider`, so `list_skills` and `download_skill` work here with no special casing.", add a new paragraph:

```markdown
A client that implements the MCP Skills extension also finds it through
`skills/list` and `skills/get`, and can verify every file it reads.
```

- [ ] **Step 3: The flag's description, in both places**

`kubed/selenium_flow/config.py:251` becomes (the longer wording would pass 88 columns):

```python
    skill: bool = Field(
        True, description="Serve the agent skill: resources and the Skills extension."
    )
```

In `wiki/Configuration.md`, the `mcp.skill` row's last cell becomes the same text:

```markdown
| `mcp.skill` | `MCP_SKILL` | `--mcp-skill` | `true` | Serve the agent skill: resources and the Skills extension. |
```

Then try `PYTHONPATH=$PWD:$PY python3 -S scripts/generate_wiki.py --check`. If it reports `Configuration.md` stale, run it without `--check` and keep its output. Its known pod failure (see *Running tests*) is not a blocker; the hand edit is.

Also check the description is the same everywhere it is copied:

Run: `grep -rn "Serve the agent skill" kubed wiki README.md AGENTS.md`
Expected: exactly the two lines above, with identical text.

- [ ] **Step 4: CHANGELOG.md**

As the first entry under `## [Unreleased]`:

```markdown
- Skills-aware clients discover the embedded skill through the MCP Skills extension (`skills/list`, `skills/get`).
```

- [ ] **Step 5: Verify**

Run: `PYTHONPATH=$PWD:$PY python3 -S -m pytest tests/test_readme.py tests/test_wiki.py tests/test_config_schema.py tests/test_config_load.py -q`
Expected: all pass except the known `test_the_generated_pages_are_current`.

Run: `$PY/bin/ruff check .`
Expected: `All checks passed!`

- [ ] **Step 6: Commit (wiki first, then the repo)**

```bash
git -C wiki add Configuration.md
git -C wiki commit -m "mcp.skill: the Skills extension too"
git add AGENTS.md README.md CHANGELOG.md kubed/selenium_flow/config.py wiki
git commit -m "Docs: the Skills extension"
```

---

## Finish (controller)

- Full suite: `PYTHONPATH=$PWD:$PY python3 -S -m pytest -q`. Expected: only the six known pod failures.
- Push wiki `master`, then the branch, then open the PR (one PR). Dr K merges.
- After merge and the `:main` image: `kubectl rollout restart -n flow deploy/selenium-flow`. Then the spec's *Live check after deploy*:
  - in-cluster, legacy `initialize` shows the declaration;
  - `skills/list` returns the entry;
  - every digest verifies;
  - through `mcp.`/`mcp-pub.`, the declaration arrives and the call is refused.
