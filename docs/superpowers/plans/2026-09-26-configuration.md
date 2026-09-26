# Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every setting can come from a YAML config file, the environment or
the command line, under one naming rule. The config file can declare secrets.
The admin UI gets a read-only Settings tab.

**Architecture:** A new `kubed/selenium_flow/config.py` holds the whole schema
as plain pydantic models, with no environment reads. A loader builds it from
three dicts: the YAML file, the environment and the command line. The
environment is read through pydantic-settings' `EnvSettingsSource`, filtered to
known fields. The flags come from our own argparse, generated from the schema.
The loader merges the dicts, validates the result, and records which layer set
each leaf. Every module that read `os.environ` takes its config section
instead. `secrets.py` overlays `secrets.entries` on the collected directories.
A token-gated `GET /admin/settings` feeds a new Svelte pane.

**Tech Stack:** Python 3.10+, pydantic 2, pydantic-settings ≥ 2.8 (already in
the image through fastmcp), PyYAML, FastMCP 4, Starlette, pytest; Svelte 5,
Vite and vitest in `ui/`.

**Spec:** `docs/superpowers/specs/2026-09-26-configuration-design.md`. The
drawing is the Penpot file *Admin UI*, version *Configuration design, round 5 —
keys without their section prefix*, on page **Admin**, boards `settings`, `settings-no-config`
and `secrets`.

## Global Constraints

- **Naming rule:** a path `section.key` is env `SECTION_KEY` and flag
  `--section-key`. A top-level `key` is `KEY` / `--key`. No exceptions, and a
  test holds every leaf to it.
- **Precedence:** default < config < env < args. The four source names, exactly:
  `default`, `config`, `env`, `args` (Dr K renamed `file` → `config`, 2026-09-26).
- **Section names:** no `_`, and never `browser`, `mcp` or `selenium`.
- **Renames, with no aliases:** `MCP_AUTH_TOKEN` → `AUTH_TOKEN`;
  `DEFAULT_BROWSER` → `SESSION_BROWSER`; `WINDOW_WIDTH` → `SESSION_WIDTH`;
  `WINDOW_HEIGHT` → `SESSION_HEIGHT`; `PAGE_LOAD_TIMEOUT` →
  `SESSION_PAGE_LOAD_TIMEOUT`; `SCRIPT_TIMEOUT` → `SESSION_SCRIPT_TIMEOUT`;
  `--no-skill` → `--skill-enabled false`; `--no-apps` → `--apps-enabled false`.
- **Config file:** `--config-file`, else `CONFIG_FILE`, else none. There is no
  default location.
- **Strict file, lenient env:** an unknown YAML key stops the boot, and so does
  a named file that is missing or is not a mapping. An unknown env name is
  ignored.
- **`os.environ` is read in exactly two modules** after Task 3: `config.py`
  and `secrets.py`.
- **Never a value:** a secret value, an inline `value:` or a sensitive setting
  never reaches a log line, an error message, a listing, the settings payload
  or a `repr`.
- **Dependency:** `pydantic-settings>=2.8,<3`. 2.8.0 is the first release with
  both `env_nested_max_split` and `NoDecode`; checked against the wheels on
  2026-09-26.
- **UI copy** (Dr K, 2026-09-26): a sensitive value is `●●●●` when set and
  blank when not. The legend is the four pills and no words. There is no
  helper message line, rows do not expand, and the description is the ⓘ
  tooltip and nothing longer.
- **Changelog:** one terse line per user-visible change, in `[Unreleased]` only.
- **Tests:** unit tests only. The integration suite gets no new flow (AGENTS.md
  *less is more*).
- **Pod rule:** in the code-server pod, run only `ruff check` and `pytest` for
  Python, and `npm --prefix ui run check|lint|test|build` for the UI. CI runs
  the rest.

## The settings, and the text the app shows for them

This table is the source for every `Field(description=…)` in Task 1. The same
string is the ⓘ tooltip, the CLI help and the wiki's table cell, so it stays
one short sentence. Defaults are what `Settings()` gives. A row shows the key
**without** its section (`port` under the `redis` card, not `redis.port`); the
full dotted path is the row's identity in the payload and the name in the file,
env and flag.

| Section (card) | Card description |
|---|---|
| `server` (top-level keys) | Where it listens, and where it lives. |
| `auth` | The bearer token every request needs. |
| `grid` | The Selenium Grid it drives. |
| `session` | How sessions are kept, and how new browsers open. |
| `redis` | The session store's connection. |
| `flow` | Saved flows and kept files. |
| `secrets` | Where secrets are read from. |
| `skill` | The embedded agent skill. |
| `apps` | MCP Apps views, for hosts that draw them. |

| Key | Type | Default | Sensitive | Tooltip |
|---|---|---|---|---|
| `config_file` | str \| None | None | | The YAML file read at start. |
| `transport` | `http` \| `stdio` | `http` | | http, or stdio for one local client. |
| `host` | str | `0.0.0.0` | | Address to listen on. |
| `port` | int | `8000` | | Port to listen on. |
| `log_level` | str | `INFO` | | DEBUG, INFO, WARNING or ERROR. |
| `route_prefix` | str | `/` | | Path the whole server is mounted under. |
| `public_base_url` | str \| None | None | | Where browsers reach this server, for links. |
| `auth.token` | SecretStr \| None | None | yes | Bearer token for every request. Unset is open. |
| `grid.url` | str | `DEFAULT_GRID_URL` | | The Selenium Grid hub. |
| `grid.console_url` | str | `/` | | Where the Grid console tab is framed from. |
| `session.store` | `memory` \| `redis` | `memory` (the loader derives it) | | memory, or redis to survive restarts. |
| `session.ttl` | int | `86400` | | Seconds a session is kept after its last use. |
| `session.browser` | `chrome` \| `firefox` \| None | None | | chrome or firefox, for new sessions. |
| `session.width` | int \| None | None | | Window width for new sessions. |
| `session.height` | int \| None | None | | Window height for new sessions. |
| `session.page_load_timeout` | int \| None | None | | Seconds a page may take to load. |
| `session.script_timeout` | int \| None | None | | Seconds a script may run. |
| `redis.url` | SecretStr \| None | None | yes | A full Redis URL, instead of host and port. |
| `redis.host` | str | `localhost` | | Redis host. |
| `redis.port` | int | `6379` | | Redis port. |
| `redis.db` | int | `0` | | Redis database number. |
| `redis.username` | str \| None | None | | Redis username. |
| `redis.password` | SecretStr \| None | None | yes | Redis password. |
| `redis.ssl` | bool | `false` | | Connect to Redis over TLS. |
| `redis.prefix` | str | `selenium-flow:session:` | | Prefix on every key, so a shared database is safe. |
| `flow.data_dir` | str \| None | None | | Where flows and kept files live. Unset turns flows off. |
| `secrets.dirs` | list[str] | `[]` | | Directories of secrets. First match wins. |
| `secrets.entries` | dict[str, SecretEntry] | `{}` | | *(file only; not a row: the Secrets tab shows it)* |
| `skill.enabled` | bool | `true` | | Serve the agent skill as resources. |
| `apps.enabled` | bool | `true` | | Offer MCP Apps views. |

How the Settings tab renders a row: **ⓘ** (hover shows the tooltip), then the
key's `name` in mono (the path without its section), then the value, the file
path, and a pill.

- **Value:** `—` when it is `null`, empty or an empty list. A list is joined
  with `, `. A sensitive value is `●●●●` when `set`, and empty otherwise.
- **File path:** the config file's path, but only when the source is `file`.
- **Pill:** one of `default`, `config`, `env`, `args`.

---

## File Structure

| File | Responsibility |
|---|---|
| **Create** `kubed/selenium_flow/config.py` | The schema, the naming rule, the loader, provenance, and the settings payload. |
| Modify `kubed/selenium_flow/main.py` | `main()` calls `config.load()`; `build_parser` goes. |
| Modify `kubed/selenium_flow/server.py` | `SeleniumMCP(settings, *, sources=None, store=None, pointers=None)`. |
| Modify `kubed/selenium_flow/session/store.py` | `from_settings(session, conn)` and `redis_client(conn)` replace the env readers. |
| Modify `kubed/selenium_flow/core/pointer.py` | Delete `from_env`; `matching(store)` is the only constructor path in use. |
| Modify `kubed/selenium_flow/flows/library.py` | `from_settings(flow)` replaces `from_env`. |
| Modify `kubed/selenium_flow/session/settings.py` | `from_settings(session)` gives the operator's defaults; `resolve(explicit, defaults, previous)`. |
| Modify `kubed/selenium_flow/session/sessions.py` | `SessionManager(..., defaults=None)` feeds `resolve`. |
| Modify `kubed/selenium_flow/mcp/apps.py`, `mcp/skill.py` | Delete the unused `enabled()`, and `public_base()`; `apps.register` takes `base`. |
| Modify `kubed/selenium_flow/http/admin.py` | No env fallback for the console URL; new `GET /admin/settings`. |
| Modify `kubed/selenium_flow/secrets.py` | `from_settings`, `ConfigEntries`, a per-key owner, `origins`, `key_sources`, `keys_unresolved`, `inline_keys`. |
| Modify `kubed/selenium_flow/spec/builder.py:427` | "The token from AUTH_TOKEN." |
| **Create** `ui/src/admin/SettingsPane.svelte` (+ `.test.ts`) | The Settings tab. |
| Modify `ui/src/lib/types.ts`, `ui/src/admin/router.svelte.ts`, `ui/src/admin/Admin.svelte` | Types, the `#/settings` route, the tab. |
| Modify `ui/src/admin/SecretsPane.svelte` (+ test) | Key sources, origins, two new warn pills. |
| Modify `scripts/generate_wiki.py`, `tests/test_wiki.py` | A generated `Configuration.md`. |
| Modify README, AGENTS.md, CHANGELOG, `wiki/{Secrets,Deployment,_Sidebar}.md`, `wiki/notes/Configuration.notes.md` (new), `skills/selenium-flow/references/CONFIGURATION.md`, `docker-compose.yaml`, `tests/integration/conftest.py`, `tests/test_shutdown.py`, `pyproject.toml` | Docs and the renamed variables. |
| Cluster repo `apps/selenium/components/mcp/*` | The live deployment on a config file (Task 10). |

---

### Task 1: The schema and the naming rule

**Files:**
- Create: `kubed/selenium_flow/config.py`
- Test: `tests/test_config_schema.py`

**Interfaces:**
- Produces: the section models `AuthSettings`, `GridSettings`,
  `SessionSettings`, `RedisSettings`, `FlowSettings`, `SecretsSettings`,
  `SkillSettings` and `AppsSettings`, all subclasses of `Section`; the key
  references `FromFile`, `FromEnv`, `FromValue` and `KeyRef`; `SecretEntry`;
  `Settings`; `Leaf(path, info)` with `.env`, `.flag`, `.section`,
  `.description`, `.sensitive` and `.default`; `leaves() -> list[Leaf]`;
  `value_of(settings, path)`; `SECTION_DESCRIPTIONS: dict[str, str]`;
  `SECTION_ORDER: list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
"""The schema: one setting, three spellings, and nothing read from the environment."""

import pytest
from pydantic import ValidationError

from kubed.selenium_flow import config
from kubed.selenium_flow.config import Settings

pytestmark = pytest.mark.unit

SECTION_NAMES = [name for name in config.SECTION_ORDER if name != "server"]


def test_every_leaf_follows_the_naming_rule():
    for leaf in config.leaves():
        assert leaf.env == leaf.path.upper().replace(".", "_")
        assert leaf.flag == "--" + leaf.path.replace(".", "-").replace("_", "-")


def test_no_section_name_can_collide_with_an_env_prefix_already_in_use():
    for name in SECTION_NAMES:
        assert "_" not in name, name
        assert name not in {"browser", "mcp", "selenium"}, name


def test_every_leaf_has_a_one_sentence_description():
    for leaf in config.leaves():
        assert leaf.description, leaf.path
        assert len(leaf.description) <= 60, (leaf.path, leaf.description)
        assert leaf.description.endswith("."), leaf.path


def test_every_section_has_a_description_and_an_order():
    assert config.SECTION_ORDER[0] == "server"
    assert set(config.SECTION_ORDER) == set(config.SECTION_DESCRIPTIONS)


def test_the_secret_definitions_are_not_a_leaf():
    """`secrets.entries` is structure: no env name, no flag, no row."""
    assert "secrets.entries" not in {leaf.path for leaf in config.leaves()}


def test_the_sensitive_settings_are_exactly_the_three():
    assert {l.path for l in config.leaves() if l.sensitive} == {
        "auth.token", "redis.url", "redis.password",
    }


def test_defaults_need_no_environment(monkeypatch):
    monkeypatch.setenv("PORT", "9999")
    monkeypatch.setenv("SESSION_TTL", "1")
    s = Settings()
    assert s.port == 8000
    assert s.session.ttl == 86400
    assert s.session.store == "memory"


def test_an_unknown_key_is_refused():
    with pytest.raises(ValidationError, match="bogus"):
        Settings.model_validate({"redis": {"bogus": 1}})


def test_a_bad_browser_is_refused_and_a_good_one_normalised():
    assert Settings(session={"browser": "Firefox"}).session.browser == "firefox"
    with pytest.raises(ValidationError):
        Settings(session={"browser": "safari"})


def test_secrets_dirs_split_like_path(tmp_path):
    import os
    s = Settings(secrets={"dirs": f"/a{os.pathsep}/b{os.pathsep} "})
    assert s.secrets.dirs == ["/a", "/b"]


def test_a_blank_flow_dir_is_off():
    assert Settings(flow={"data_dir": "   "}).flow.data_dir is None


def test_a_key_reference_is_exactly_one_of_file_env_value():
    entry = {"keys": {"a": {"file": "/f"}, "b": {"env": "X"}, "c": {"value": "v"}}}
    s = Settings(secrets={"entries": {"demo": entry}})
    keys = s.secrets.entries["demo"].keys
    assert isinstance(keys["a"], config.FromFile)
    assert isinstance(keys["b"], config.FromEnv)
    assert isinstance(keys["c"], config.FromValue)
    for bad in ({"file": "/f", "env": "X"}, {}, {"path": "/f"}):
        with pytest.raises(ValidationError):
            Settings(secrets={"entries": {"demo": {"keys": {"k": bad}}}})


def test_allowed_urls_must_be_bare_origins_and_the_error_never_echoes_credentials():
    ok = Settings(secrets={"entries": {"d": {"allowed_urls": ["https://Example.com/"]}}})
    assert ok.secrets.entries["d"].allowed_urls == ["https://example.com"]
    with pytest.raises(ValidationError) as caught:
        Settings(secrets={"entries": {"d": {"allowed_urls": ["https://u:hunter2@x.com"]}}})
    assert "hunter2" not in str(caught.value)


def test_a_secret_name_follows_the_directory_rule():
    with pytest.raises(ValidationError):
        Settings(secrets={"entries": {"../up": {}}})


def test_a_key_name_cannot_be_reserved():
    with pytest.raises(ValidationError):
        Settings(secrets={"entries": {"d": {"keys": {"_allowed_urls": {"env": "X"}}}}})


def test_an_inline_value_never_reaches_a_repr():
    s = Settings(auth={"token": "t0ken"}, secrets={"entries": {"d": {"keys": {"k": {"value": "s3cret"}}}}})
    assert "t0ken" not in repr(s) and "s3cret" not in repr(s)
    assert "s3cret" not in s.model_dump_json()


def test_value_of_reads_a_dotted_path():
    s = Settings(redis={"db": 2})
    assert config.value_of(s, "redis.db") == 2
    assert config.value_of(s, "port") == 8000
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_config_schema.py -q`
Expected: FAIL. `ModuleNotFoundError: kubed.selenium_flow.config`.

- [ ] **Step 3: Write `config.py`, the schema half**

```python
"""Every setting this server reads: one schema, three places to set it.

A setting's path is its name, and the other two spellings are derived from it:
`redis.host` is `REDIS_HOST` in the environment and `--redis-host` on the
command line. Precedence is default < file < env < args.

The models here read nothing. `Settings()` is the defaults, which is what a
test wants. `load()`, below, is what reads the file, the environment and the
command line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import NoDecode

from .core.browser import DEFAULT_GRID_URL, normalize_browser

# Marks a field that only the config file may set: structure, not a value.
FILE_ONLY = "file_only"


class Section(BaseModel):
    """A group of settings. Unknown keys are refused, so a typo stops the boot."""

    model_config = ConfigDict(extra="forbid")


class FromFile(Section):
    file: str


class FromEnv(Section):
    env: str


class FromValue(Section):
    value: SecretStr


# The key present is the discriminator. Each member forbids extra keys, so
# `{file, env}` and `{}` match none of them and are refused, rather than
# pydantic quietly picking the first member that fits.
KeyRef = Union[FromFile, FromEnv, FromValue]


class SecretEntry(Section):
    description: str | None = None
    allowed_urls: list[str] | None = None
    keys: dict[str, KeyRef] = Field(default_factory=dict)

    @field_validator("allowed_urls")
    @classmethod
    def _origins(cls, urls):
        if urls is None:
            return None
        # Local: secrets.py imports this module for the key references.
        from .secrets import _shown, declared_origin

        origins = []
        for url in urls:
            origin = declared_origin(url)
            if origin is None:
                # `_shown`, never the line: a refused line may carry credentials.
                raise ValueError(
                    f"{_shown(url)} is not a bare origin like https://example.com"
                )
            origins.append(origin)
        return origins

    @field_validator("keys")
    @classmethod
    def _key_names(cls, keys):
        for name in keys:
            if not name or name.startswith("_"):
                raise ValueError(f"key {name!r}: a key cannot be empty or start with _")
        return keys


class AuthSettings(Section):
    token: SecretStr | None = Field(None, description="Bearer token for every request. Unset is open.")


class GridSettings(Section):
    url: str = Field(DEFAULT_GRID_URL, description="The Selenium Grid hub.")
    console_url: str = Field("/", description="Where the Grid console tab is framed from.")


class SessionSettings(Section):
    store: Literal["memory", "redis"] = Field("memory", description="memory, or redis to survive restarts.")
    ttl: int = Field(86400, description="Seconds a session is kept after its last use.")
    browser: Literal["chrome", "firefox"] | None = Field(None, description="chrome or firefox, for new sessions.")
    width: int | None = Field(None, description="Window width for new sessions.")
    height: int | None = Field(None, description="Window height for new sessions.")
    page_load_timeout: int | None = Field(None, description="Seconds a page may take to load.")
    script_timeout: int | None = Field(None, description="Seconds a script may run.")

    @field_validator("browser", mode="before")
    @classmethod
    def _browser(cls, value):
        return normalize_browser(value) if value not in (None, "") else None


class RedisSettings(Section):
    url: SecretStr | None = Field(None, description="A full Redis URL, instead of host and port.")
    host: str = Field("localhost", description="Redis host.")
    port: int = Field(6379, description="Redis port.")
    db: int = Field(0, description="Redis database number.")
    username: str | None = Field(None, description="Redis username.")
    password: SecretStr | None = Field(None, description="Redis password.")
    ssl: bool = Field(False, description="Connect to Redis over TLS.")
    prefix: str = Field("selenium-flow:session:", description="Prefix on every key, so a shared database is safe.")


class FlowSettings(Section):
    data_dir: str | None = Field(None, description="Where flows and kept files live. Unset turns flows off.")

    @field_validator("data_dir")
    @classmethod
    def _blank_is_off(cls, value):
        return (value or "").strip() or None


class SecretsSettings(Section):
    # NoDecode: pydantic-settings would otherwise JSON-decode a list read from
    # env, and `SECRETS_DIRS=/a:/b` is not JSON.
    dirs: Annotated[list[str], NoDecode] = Field(default_factory=list, description="Directories of secrets. First match wins.")
    entries: Annotated[dict[str, SecretEntry], NoDecode] = Field(
        default_factory=dict,
        description="Secrets defined in the config file.",
        json_schema_extra={FILE_ONLY: True},
    )

    @field_validator("dirs", mode="before")
    @classmethod
    def _split(cls, value):
        if isinstance(value, str):
            return [part.strip() for part in value.split(os.pathsep) if part.strip()]
        return value

    @field_validator("entries")
    @classmethod
    def _names(cls, entries):
        from .flows.library import valid_name

        for name in entries:
            valid_name(name, "secret name")
        return entries


class SkillSettings(Section):
    enabled: bool = Field(True, description="Serve the agent skill as resources.")


class AppsSettings(Section):
    enabled: bool = Field(True, description="Offer MCP Apps views.")


class Settings(Section):
    config_file: str | None = Field(None, description="The YAML file read at start.")
    transport: Literal["http", "stdio"] = Field("http", description="http, or stdio for one local client.")
    host: str = Field("0.0.0.0", description="Address to listen on.")
    port: int = Field(8000, description="Port to listen on.")
    log_level: str = Field("INFO", description="DEBUG, INFO, WARNING or ERROR.")
    route_prefix: str = Field("/", description="Path the whole server is mounted under.")
    public_base_url: str | None = Field(None, description="Where browsers reach this server, for links.")
    auth: AuthSettings = Field(default_factory=AuthSettings, description="The bearer token every request needs.")
    grid: GridSettings = Field(default_factory=GridSettings, description="The Selenium Grid it drives.")
    session: SessionSettings = Field(default_factory=SessionSettings, description="How sessions are kept, and how new browsers open.")
    redis: RedisSettings = Field(default_factory=RedisSettings, description="The session store\u2019s connection.")
    flow: FlowSettings = Field(default_factory=FlowSettings, description="Saved flows and kept files.")
    secrets: SecretsSettings = Field(default_factory=SecretsSettings, description="Where secrets are read from.")
    skill: SkillSettings = Field(default_factory=SkillSettings, description="The embedded agent skill.")
    apps: AppsSettings = Field(default_factory=AppsSettings, description="MCP Apps views, for hosts that draw them.")


def _is_section(info: FieldInfo) -> bool:
    return isinstance(info.annotation, type) and issubclass(info.annotation, Section)


SECTION_ORDER = ["server"] + [n for n, i in Settings.model_fields.items() if _is_section(i)]
SECTION_DESCRIPTIONS = {"server": "Where it listens, and where it lives."} | {
    n: i.description for n, i in Settings.model_fields.items() if _is_section(i)
}


@dataclass(frozen=True)
class Leaf:
    """One setting: its path, and everything derived from it."""

    path: str
    info: FieldInfo

    @property
    def env(self) -> str:
        return self.path.replace(".", "_").upper()

    @property
    def flag(self) -> str:
        return "--" + self.path.replace(".", "-").replace("_", "-")

    @property
    def section(self) -> str:
        return self.path.split(".", 1)[0] if "." in self.path else "server"

    @property
    def description(self) -> str:
        return self.info.description or ""

    @property
    def sensitive(self) -> bool:
        return "SecretStr" in repr(self.info.annotation)

    @property
    def default(self) -> Any:
        return self.info.get_default(call_default_factory=True)


def leaves() -> list[Leaf]:
    """Every setting that has an env name and a flag, in schema order."""
    found = []
    for name, info in Settings.model_fields.items():
        if not _is_section(info):
            found.append(Leaf(name, info))
            continue
        for sub, subinfo in info.annotation.model_fields.items():
            if (subinfo.json_schema_extra or {}).get(FILE_ONLY):
                continue
            found.append(Leaf(f"{name}.{sub}", subinfo))
    return found


def value_of(settings: Settings, path: str) -> Any:
    value: Any = settings
    for part in path.split("."):
        value = getattr(value, part)
    return value
```

Before running, check that `InvalidName` in `flows/library.py` subclasses
`ValueError`; pydantic only converts `ValueError` into a `ValidationError`:

Run: `grep -n "class InvalidName" kubed/selenium_flow/flows/library.py`
Expected: `class InvalidName(ValueError)`. If it names another base, wrap the
`valid_name` call in `_names` with `except InvalidName as exc: raise ValueError(str(exc)) from None`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_config_schema.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/config.py tests/test_config_schema.py
git commit -m "Config: one schema, and the naming rule every setting follows"
```

---

### Task 2: The loader: three layers, merged, with where each value came from

**Files:**
- Modify: `kubed/selenium_flow/config.py` (append)
- Modify: `pyproject.toml` (dependency)
- Test: `tests/test_config_load.py`

**Interfaces:**
- Consumes: `Settings`, `leaves()` and `value_of()` from Task 1.
- Produces:
  - `ConfigError(ValueError)`;
  - `Loaded(settings: Settings, sources: dict[str, str])`, frozen;
  - `parser() -> argparse.ArgumentParser`;
  - `load(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> Loaded`;
  - `sources_for(settings) -> dict[str, str]`;
  - `describe(settings, sources) -> dict`, the payload shape from the spec.

- [ ] **Step 1: Declare the dependency**

In `pyproject.toml` `[project] dependencies`, after `"pydantic>=2.12",`:

```toml
  # The environment layer of the config (config.py). It already arrives with
  # fastmcp; declared because config.py imports it. 2.8 is the first release
  # with env_nested_max_split, which maps REDIS_HOST to redis.host.
  "pydantic-settings>=2.8,<3",
```

- [ ] **Step 2: Write the failing tests**

```python
"""The loader: defaults < file < env < args, and where every value came from."""

import pytest

from kubed.selenium_flow import config
from kubed.selenium_flow.config import ConfigError, load

pytestmark = pytest.mark.unit


def _file(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_nothing_set_is_every_default():
    loaded = load([], {})
    assert loaded.settings.port == 8000
    assert set(loaded.sources.values()) == {"default"}


def test_precedence_per_leaf(tmp_path):
    path = _file(tmp_path, "port: 1\nsession:\n  ttl: 1\nredis:\n  port: 1\n")
    loaded = load(["--port", "3", "--config-file", path], {"PORT": "2", "SESSION_TTL": "2", "REDIS_HOST": "h"})
    s, src = loaded.settings, loaded.sources
    assert (s.port, src["port"]) == (3, "args")
    assert (s.session.ttl, src["session.ttl"]) == (2, "env")
    # A partial section survives another layer setting a sibling.
    assert (s.redis.port, src["redis.port"]) == (1, "file")
    assert (s.redis.host, src["redis.host"]) == ("h", "env")
    assert src["config_file"] == "args"


def test_the_config_file_comes_from_env_when_no_flag(tmp_path):
    path = _file(tmp_path, "log_level: DEBUG\n")
    loaded = load([], {"CONFIG_FILE": path})
    assert loaded.settings.log_level == "DEBUG"
    assert loaded.sources["log_level"] == "file"
    assert loaded.settings.config_file == path


def test_session_store_is_derived_from_a_redis_setting():
    assert load([], {}).settings.session.store == "memory"
    assert load([], {"REDIS_HOST": "r"}).settings.session.store == "redis"
    assert load(["--redis-url", "redis://r:6379"], {}).settings.session.store == "redis"
    assert load([], {"REDIS_HOST": "r", "SESSION_STORE": "memory"}).settings.session.store == "memory"


@pytest.mark.parametrize("name", ["FLOW_UI_PORT", "MCP_KB_PORT", "SESSION_FOO_PORT", "BROWSER", "SELENIUM_FLOW_PORT", "SECRETS_ENTRIES"])
def test_unknown_env_names_are_ignored(name):
    assert load([], {name: "tcp://10.0.0.1:80"}).settings == config.Settings()


def test_env_names_are_case_insensitive():
    assert load([], {"session_ttl": "5"}).settings.session.ttl == 5


def test_secrets_dirs_from_env_and_args():
    import os
    assert load([], {"SECRETS_DIRS": f"/a{os.pathsep}/b"}).settings.secrets.dirs == ["/a", "/b"]
    assert load(["--secrets-dirs", "/c"], {}).settings.secrets.dirs == ["/c"]


def test_booleans_take_a_value_on_the_command_line():
    assert load(["--skill-enabled", "false"], {}).settings.skill.enabled is False
    assert load([], {"APPS_ENABLED": "off"}).settings.apps.enabled is False


@pytest.mark.parametrize("text,needle", [
    ("redis:\n  bogus: 1\n", "redis.bogus"),
    ("- a\n- b\n", "mapping"),
    ("session: [\n", "line"),
    ("config_file: /x\n", "config_file"),
    ("secrets:\n  entries:\n    d:\n      allowed_urls: [staging.internal]\n", "bare origin"),
])
def test_a_bad_file_stops_the_boot_and_says_why(tmp_path, text, needle):
    with pytest.raises(ConfigError, match=needle):
        load(["--config-file", _file(tmp_path, text)], {})


def test_a_named_file_that_is_missing_stops_the_boot(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load(["--config-file", str(tmp_path / "nope.yaml")], {})


def test_a_bad_env_value_names_the_variable():
    with pytest.raises(ConfigError, match="SESSION_TTL"):
        load([], {"SESSION_TTL": "soon"})


def test_an_error_never_echoes_a_sensitive_value():
    with pytest.raises(ConfigError) as caught:
        load([], {"REDIS_PORT": "hunter2"})
    assert "hunter2" not in str(caught.value)


def test_the_file_cannot_define_entries_through_env(tmp_path):
    path = _file(tmp_path, "secrets:\n  entries:\n    d:\n      keys:\n        k: {env: X}\n")
    loaded = load(["--config-file", path], {})
    assert "d" in loaded.settings.secrets.entries


def test_help_names_all_three_spellings(capsys):
    with pytest.raises(SystemExit):
        load(["--help"], {})
    out = capsys.readouterr().out
    assert "--session-ttl" in out and "SESSION_TTL" in out and "session.ttl" in out
    assert "--secrets-entries" not in out


def test_sources_for_code_built_settings():
    s = config.Settings(port=9)
    src = config.sources_for(s)
    assert src["port"] == "args" and src["host"] == "default"


def test_describe_is_the_payload_the_tab_renders(tmp_path):
    path = _file(tmp_path, "redis:\n  db: 2\n")
    loaded = load(["--config-file", path], {"AUTH_TOKEN": "t0ken"})
    body = config.describe(loaded.settings, loaded.sources)
    assert body["config_file"] == path
    assert [s["name"] for s in body["sections"]] == config.SECTION_ORDER
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert rows["redis.db"] == {"key": "redis.db", "name": "db", "description": "Redis database number.", "value": 2, "source": "file", "file": path}
    assert rows["auth.token"] == {"key": "auth.token", "name": "token", "description": "Bearer token for every request. Unset is open.", "value": None, "source": "env", "sensitive": True, "set": True}
    assert rows["redis.password"]["set"] is False
    assert "secrets.entries" not in rows
    assert "t0ken" not in str(body)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `pytest tests/test_config_load.py -q`
Expected: FAIL. `ImportError: cannot import name 'ConfigError'`.

- [ ] **Step 4: Append the loader to `config.py`**

Add to the imports at the top:

```python
import argparse
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError
from pydantic_settings import EnvSettingsSource
```

Then append:

```python
SOURCES = ("default", "file", "env", "args")


class ConfigError(ValueError):
    """A configuration this server refuses to start on. The message says what and where."""


@dataclass(frozen=True)
class Loaded:
    settings: Settings
    sources: dict[str, str]


def parser() -> argparse.ArgumentParser:
    """One flag per setting, generated, so the rule cannot be broken by hand.

    `SUPPRESS` as the default: the namespace then holds only what was typed,
    which is what makes "args" a true answer about where a value came from.
    """
    built = argparse.ArgumentParser(
        prog="selenium-flow",
        description="Drive a Selenium Grid browser over MCP and HTTP.",
        argument_default=argparse.SUPPRESS,
    )
    for leaf in leaves():
        shown = "unset" if leaf.default in (None, "", []) else leaf.default
        help_text = f"{leaf.description} (env: {leaf.env} · file: {leaf.path} · default: {shown})"
        built.add_argument(
            leaf.flag,
            dest=leaf.path,
            metavar=leaf.path.rsplit(".", 1)[-1].upper(),
            help=help_text.replace("%", "%%"),
        )
    return built


class _Environment(EnvSettingsSource):
    """pydantic-settings' env reader, over a mapping we hand it.

    `_load_env_vars` is its one hook for where variables come from. Overriding
    it keeps a test's environment a plain dict, and keeps `os.environ` out of
    every module but this one and secrets.py.
    """

    def __init__(self, environ: Mapping[str, str]):
        self._environ = environ
        super().__init__(Settings, case_sensitive=False, env_nested_delimiter="_", env_nested_max_split=1)

    def _load_env_vars(self):
        return {key.lower(): value for key, value in self._environ.items()}


def _known(raw: dict) -> dict:
    """Only the leaves the schema has.

    The environment is shared with Kubernetes and the shell. A Service called
    `flow-ui` injects `FLOW_UI_PORT`, which reads as `flow.ui_port`, and a strict
    model would refuse to boot over it. So env is lenient about names and the
    file is not.
    """
    allowed = {leaf.path for leaf in leaves()}
    kept: dict = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            for sub, inner in value.items():
                if f"{key}.{sub}" in allowed:
                    kept.setdefault(key, {})[sub] = inner
        elif key in allowed:
            kept[key] = value
    return kept


def _read_file(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"config file {path} does not exist")
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        # Never the parser's message: it quotes the offending line, and that
        # line may be an inline secret value.
        raise ConfigError(f"config file {path} is not valid YAML{where}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"config file {path} cannot be read: {type(exc).__name__}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must be a mapping of sections, not a {type(data).__name__}")
    if "config_file" in data:
        raise ConfigError(f"config_file cannot be set inside the config file it names ({path})")
    return data


def _nest(flat: Mapping[str, Any]) -> dict:
    tree: dict = {}
    for path, value in flat.items():
        head, _, tail = path.partition(".")
        if tail:
            tree.setdefault(head, {})[tail] = value
        else:
            tree[head] = value
    return tree


def _has(tree: dict, path: str) -> bool:
    head, _, tail = path.partition(".")
    if not tail:
        return head in tree
    section = tree.get(head)
    return isinstance(section, dict) and tail in section


def _merge(*layers: dict) -> dict:
    """Later layers win, one section deep, which is as deep as the schema goes."""
    merged: dict = {}
    for layer in layers:
        for key, value in layer.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = dict(value) if isinstance(value, dict) else value
    return merged


def _explain(exc: ValidationError, sources: dict[str, str], path: str | None) -> str:
    """Where each problem is, and what it is. Never the input: it may be secret."""
    lines = []
    for error in exc.errors(include_input=False, include_url=False):
        loc = [str(part) for part in error["loc"]]
        leaf = ".".join(loc[:2]) if loc and loc[0] in SECTION_ORDER else (loc[0] if loc else "")
        by_path = {l.path: l for l in leaves()}
        source = sources.get(leaf, "file")
        where = {
            "file": f"in {path}",
            "env": f"from env {by_path[leaf].env}" if leaf in by_path else "from env",
            "args": f"from {by_path[leaf].flag}" if leaf in by_path else "on the command line",
            "default": "as a default",
        }[source]
        lines.append(f"{'.'.join(loc)}: {error['msg']} ({where})")
    return "; ".join(lines)


def load(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> Loaded:
    """The settings this process should run with, and where each one came from."""
    environ = os.environ if environ is None else environ
    args = _nest(vars(parser().parse_args(argv)))
    env = _known(_Environment(environ)())
    named = args.get("config_file") or env.get("config_file")
    path = str(named).strip() if named else None
    file = _read_file(path) if path else {}

    layers = {"args": args, "env": env, "file": file}
    sources = {
        leaf.path: next((name for name in ("args", "env", "file") if _has(layers[name], leaf.path)), "default")
        for leaf in leaves()
    }
    merged = _merge(file, env, args)
    if sources["session.store"] == "default":
        # Today's rule, kept: asking for Redis by naming it is asking for the
        # Redis store. Explicitly `memory` still wins.
        wanted = sources["redis.url"] != "default" or sources["redis.host"] != "default"
        merged.setdefault("session", {})["store"] = "redis" if wanted else "memory"
    try:
        settings = Settings.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(_explain(exc, sources, path)) from None
    return Loaded(settings, sources)


def sources_for(settings: Settings) -> dict[str, str]:
    """Where each value came from, for Settings built in code (tests, embedding).

    A value equal to its default reads as `default`, anything else as `args`:
    code that builds Settings is passing arguments.
    """
    return {
        leaf.path: "default" if value_of(settings, leaf.path) == leaf.default else "args"
        for leaf in leaves()
    }


def describe(settings: Settings, sources: Mapping[str, str]) -> dict:
    """The Settings tab's payload. Never a sensitive value, and no secret entries."""
    rows: dict[str, list[dict]] = {name: [] for name in SECTION_ORDER}
    for leaf in leaves():
        value = value_of(settings, leaf.path)
        # `name` is what the row shows: the card title is already the section.
        row: dict[str, Any] = {"key": leaf.path, "name": leaf.path.rsplit(".", 1)[-1], "description": leaf.description}
        if leaf.sensitive:
            row.update(value=None, source=sources.get(leaf.path, "default"), sensitive=True, set=value is not None)
        else:
            row.update(value=value, source=sources.get(leaf.path, "default"))
        if row["source"] == "file":
            row["file"] = settings.config_file
        rows[leaf.section].append(row)
    return {
        "config_file": settings.config_file,
        "sections": [
            {"name": name, "description": SECTION_DESCRIPTIONS[name], "settings": rows[name]}
            for name in SECTION_ORDER
        ],
    }
```

Two notes for the implementer:

- The `describe` test compares whole dicts. So the key order in `row` must
  come out as `key, description, value, source` and then `file` or
  `sensitive, set`, or the test has to compare with `==` on each field
  instead. Dict equality ignores order, so `==` on the whole dict is already
  fine.
- `_explain` looks up `by_path` inside the loop. Hoist it above the loop if
  ruff flags the repeated build (PERF).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_config_load.py tests/test_config_schema.py -q`
Expected: all pass. If `test_unknown_env_names_are_ignored[SECRETS_ENTRIES]`
fails with a `SettingsError`, the `NoDecode` on `entries` is missing.

- [ ] **Step 6: Commit**

```bash
git add kubed/selenium_flow/config.py tests/test_config_load.py pyproject.toml
git commit -m "Config: load from file, env and args, and record where each value came from"
```

---

### Task 3: Every module takes its section, not the environment

The whole switch lands in one task. Half-switched, the server has two ways to
read one setting, which is the thing this change removes.

**Files:**
- Modify: `kubed/selenium_flow/main.py` (rewrite `main`, delete `build_parser`)
- Modify: `kubed/selenium_flow/server.py:52-120`, `:150`, `:159-190`, `:230-240`
- Modify: `kubed/selenium_flow/session/store.py:321-427`
- Modify: `kubed/selenium_flow/core/pointer.py:133-160` (delete `from_env`)
- Modify: `kubed/selenium_flow/flows/library.py:753-766`
- Modify: `kubed/selenium_flow/session/settings.py`, `kubed/selenium_flow/session/sessions.py:186-197,378-386`
- Modify: `kubed/selenium_flow/mcp/apps.py:46-62,103-110`, `kubed/selenium_flow/mcp/skill.py:63-71`
- Modify: `kubed/selenium_flow/http/admin.py:280`, `kubed/selenium_flow/secrets.py:418-436`
- Modify: `kubed/selenium_flow/spec/builder.py:427`
- Modify: every test that builds a `SeleniumMCP` or calls a `from_env`; `tests/test_shutdown.py:48`; `tests/integration/conftest.py:118`; `docker-compose.yaml:27-40`
- Test: `tests/test_config_wiring.py`

**Interfaces:**
- Consumes: `Settings`, `load`, `sources_for` and `ConfigError` from Tasks 1–2.
- Produces:
  - `SeleniumMCP(settings: Settings | None = None, *, sources: dict[str, str] | None = None, store=None, pointers=None)`, which sets `self.settings` and `self.sources`;
  - `store.from_settings(session: SessionSettings, conn: RedisSettings) -> SessionStore` and `store.redis_client(conn: RedisSettings)`;
  - `library.from_settings(flow: FlowSettings) -> FlowStore | None`;
  - `secrets.from_settings(conf: SecretsSettings, config_file: str | None = None) -> Catalogue | None` (directories only here; Task 4 adds entries);
  - `session.settings.from_settings(session: SessionSettings) -> dict` and `resolve(explicit=None, defaults=None, previous=None) -> dict`;
  - `SessionManager(actions, store=None, skill_available=True, defaults: dict | None = None)`;
  - `apps.register(mcp, actions, token, base: str)`.

- [ ] **Step 1: Write the failing wiring tests**

```python
"""The switch from the environment to the config, held in place."""

import pathlib
import re

import pytest

import kubed.selenium_flow as package
from kubed.selenium_flow import main as main_module
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP

pytestmark = pytest.mark.unit
GRID = {"url": "http://grid.invalid:4444"}


def test_the_environment_is_read_in_two_modules_only():
    root = pathlib.Path(package.__file__).parent
    readers = sorted(
        str(p.relative_to(root)) for p in root.rglob("*.py")
        if re.search(r"os\.environ|os\.getenv|getenv\(", p.read_text(encoding="utf-8"))
    )
    assert readers == ["config.py", "secrets.py"]


def test_a_server_built_from_settings_uses_them(tmp_path):
    s = Settings(grid=GRID, auth={"token": "t"}, route_prefix="/flow",
                 flow={"data_dir": str(tmp_path)}, skill={"enabled": False},
                 session={"browser": "firefox", "ttl": 42})
    server = SeleniumMCP(s)
    assert server.settings is s
    assert server.auth_token == "t"
    assert server.prefix == "/flow"
    assert server.flows is not None
    assert server.skill is None
    assert server.sessions.defaults == {"browser": "firefox"}
    assert server.store._ttl == 42
    assert server.sources["session.ttl"] == "args"


def test_no_settings_is_every_default(monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", "/nowhere")
    server = SeleniumMCP()
    assert server.flows is None and server.secrets is None and server.auth_token is None


def test_main_refuses_a_bad_config_with_the_reason(tmp_path, capsys):
    bad = tmp_path / "c.yaml"
    bad.write_text("redis:\n  bogus: 1\n")
    with pytest.raises(SystemExit) as caught:
        main_module.main(["--config-file", str(bad)])
    assert "redis.bogus" in str(caught.value)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_config_wiring.py -q`
Expected: FAIL. `SeleniumMCP` takes no `settings`, and `readers` lists nine
modules.

- [ ] **Step 3: Store and Redis read their sections**

In `session/store.py`, delete `redis_configured` and `chosen_backend`, then
replace `from_env` and `redis_client` with:

```python
def from_settings(session: SessionSettings, conn: RedisSettings) -> SessionStore:
    """Build the session store the config asks for.

    Redis configured and unreachable, or missing its package, is a startup
    error rather than a silent step down to memory (§F4.12). An unknown
    backend no longer reaches here: the config refuses it at load.
    """
    if session.store == "memory":
        log.info("session store: memory, ttl %ss", session.ttl)
        return MemoryStore(ttl=session.ttl)
    client = redis_client(conn)  # raises StoreUnavailable rather than returning None
    log.info("session store: redis db %s, prefix %s, ttl %ss", conn.db, conn.prefix, session.ttl)
    return RedisStore(client, prefix=conn.prefix, ttl=session.ttl)


def redis_client(conn: RedisSettings):
    """A connected Redis client, or raises ``StoreUnavailable``. See §F4.12."""
    url = conn.url.get_secret_value() if conn.url else None
    where = errors.without_userinfo(url) if url else f"{conn.host}:{conn.port}/{conn.db}"
    try:
        import redis  # imported here: an optional dependency must not be a hard import
    except ImportError:
        raise StoreUnavailable(
            "Redis is configured but the redis package is missing; "
            "pip install kubed-selenium-flow[redis]"
        ) from None
    try:
        if url:
            # Passed explicitly, so redis.db is honoured with no /<index> in the URL.
            client = redis.Redis.from_url(url, db=conn.db)
        else:
            client = redis.Redis(
                host=conn.host,
                port=conn.port,
                db=conn.db,
                password=conn.password.get_secret_value() if conn.password else None,
                username=conn.username or None,
                ssl=conn.ssl,
            )
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure here is Redis's, not this package's
        raise StoreUnavailable(
            f"Redis is configured but unreachable at {where} "
            f"({type(exc).__name__}: {errors.message(exc)}); "
            "refusing to start on in-memory sessions"
        ) from None
    return client
```

Import `from ..config import RedisSettings, SessionSettings` under
`TYPE_CHECKING`, and drop `import os` if nothing else uses it.
`DEFAULT_PREFIX`, `DEFAULT_DB` and `DEFAULT_TTL_SECONDS` stay only if another
module imports them. Check with
`grep -rn "store.DEFAULT_\|store_module.DEFAULT_" kubed tests`.

- [ ] **Step 4: Delete `pointer.from_env`**

Delete `from_env` in `core/pointer.py` (lines 133–160); `server.py` builds
pointers through `matching(store)`. Delete `tests/test_pointer.py`'s two
`from_env` tests (lines ~135–150), because the function they tested is gone.

- [ ] **Step 5: Flows, secrets, apps, skill and admin**

`flows/library.py`, replacing `from_env`:

```python
def from_settings(flow: FlowSettings) -> FlowStore | None:
    """The flow store the config asks for, or None when flows are off (the default)."""
    if not flow.data_dir:
        log.info("flows: off (set flow.data_dir to enable them)")
        return None
    log.info("flows: local, under %s", flow.data_dir)
    return LocalFlowStore(flow.data_dir)
```

`secrets.py`: delete `directories` and `from_env`, and add:

```python
def from_settings(conf: SecretsSettings, config_file: str | None = None) -> Catalogue | None:
    """The catalogue the config asks for, or None when there are no secrets."""
    if not conf.dirs:
        log.info("secrets: off (set secrets.dirs to enable them)")
        return None
    log.info("secrets: %s director%s", len(conf.dirs), "y" if len(conf.dirs) == 1 else "ies")
    return Catalogue([FilesystemSource(path) for path in conf.dirs])
```

Also change `OFF` to `"secrets are not enabled on this server: it was started
with no secrets.dirs and no secrets.entries, so there is nowhere to read them
from"`.

`mcp/apps.py`: delete `enabled` and `public_base`. Change `register` to
`def register(mcp, actions, token: str | None, base: str) -> set[str]:`, and
use `base` where it called `public_base()`. Drop `import os`.

`mcp/skill.py`: delete `enabled`; nothing calls it (check with
`grep -rn "skill.enabled(" kubed tests`). Drop `import os` if unused.

`http/admin.py:280`: `console = console_url or DEFAULT_CONSOLE_URL`, and drop
`import os` if unused.

`spec/builder.py:427`: `"The token from AUTH_TOKEN. The bare token is also "`.

- [ ] **Step 6: The session cascade takes its defaults**

In `session/settings.py`, the module docstring's cascade line becomes
`server default (config: session.*)  <  client default (param/header)  <  this session's last values  <  explicit`.
Delete `_as_browser` and `from_env`, and drop `import os`. The `SETTINGS`
table loses its env column:

```python
# name -> (query parameter, header, coercion)
SETTINGS = {
    "browser": ("browser", "x-browser", None),
    "width": ("width", "x-window-width", _as_int),
    "height": ("height", "x-window-height", _as_int),
    "page_load_timeout": ("page_load_timeout", "x-page-load-timeout", _as_int),
    "script_timeout": ("script_timeout", "x-script-timeout", _as_int),
    # Explicit only (§F3.8): no parameter, no header, no default.
    "insecure": (None, None, _as_flag),
}

# The config's session section, as the operator's floor. `store` and `ttl` are
# about keeping sessions, not about the browser a session opens.
FROM_CONFIG = ("browser", "width", "height", "page_load_timeout", "script_timeout")


def from_settings(session: SessionSettings) -> dict:
    """The operator's defaults: only what is set, so unset stays unset."""
    return {name: getattr(session, name) for name in FROM_CONFIG if getattr(session, name) is not None}
```

A browser from a query parameter or a header goes through a lenient helper,
kept from today's behaviour:

```python
def _as_client_browser(value) -> str | None:
    """Lenient, like _as_int: a typo in a client's URL must not stop a browser opening."""
    if value in (None, ""):
        return None
    from ..core.browser import normalize_browser
    try:
        return normalize_browser(value)
    except ValueError as exc:
        log.warning("ignoring unusable browser default: %s", exc)
        return None
```

Use it as the `browser` coercion in `SETTINGS`, in place of the `None`
placeholder above. Then write `from_client` against the three-column tuples,
and write `resolve` as:

```python
def resolve(explicit: dict | None = None, defaults: dict | None = None, previous: dict | None = None) -> dict:
    """The settings a new session opens with (the cascade at the top of this module)."""
    from .sessions import http_request  # local: avoids a circular import

    http = http_request()
    params, headers = http if http else (None, None)
    merged = dict(defaults or {})
    merged.update(from_client(params, headers))
    merged.update({k: v for k, v in (previous or {}).items() if k in SETTINGS})
    for name, value in (explicit or {}).items():
        if name not in SETTINGS or value is None:
            continue
        if name == "browser":
            from ..core.browser import normalize_browser
            merged[name] = normalize_browser(value)  # strict for an explicit argument
            continue
        coerced = SETTINGS[name][2](value)
        if coerced is not None:
            merged[name] = coerced
    return merged
```

In `session/sessions.py`, `SessionManager.__init__` gains
`defaults: dict | None = None`, stored as `self.defaults = dict(defaults or {})`.
`open_browser` calls
`settings_module.resolve(wanted, defaults=self.defaults, previous=previous.get("settings"))`.

- [ ] **Step 7: Server and main**

`server.py`: the constructor becomes the code below, and every use of a
removed argument reads from `settings`.

```python
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        sources: dict[str, str] | None = None,
        store: SessionStore | None = None,
        pointers=None,
    ):
        settings = settings if settings is not None else Settings()
        self.settings = settings
        # Where each value came from, for the Settings tab. Built from the
        # settings themselves when a caller constructed them in code.
        self.sources = dict(sources) if sources is not None else config.sources_for(settings)
        self.grid = Grid(settings.grid.url)
        self.store = store if store is not None else store_module.from_settings(settings.session, settings.redis)
        # ... (pointer comment unchanged)
        self.actions = Actions(self.grid, pointers=pointers if pointers is not None else pointer.matching(self.store))
        auth_token = settings.auth.token.get_secret_value() if settings.auth.token else None
        self.auth_token = auth_token
        self.prefix = routes.mount(settings.route_prefix)
        self.mcp_path = f"{self.prefix}/mcp"
        self.skill = skill.load() if settings.skill.enabled else None
        self.sessions = SessionManager(
            self.actions, store=self.store, skill_available=self.skill is not None,
            defaults=session_settings.from_settings(settings.session),
        )
        self.flows = flows.from_settings(settings.flow)
        self.secrets = secrets.from_settings(settings.secrets, settings.config_file)
```

The imports become `from . import config, routes, secrets`,
`from .config import Settings`, `from .session import settings as session_settings`
and `from .session import store as store_module`. Remove the `from_env` import.

Further down the constructor:

- `base = (settings.public_base_url or "").strip().rstrip("/")`, where it
  called `apps.public_base()`;
- `apps_enabled = settings.apps.enabled and apps.available()`;
- `apps.register(self.mcp, self.actions, auth_token, base)`;
- `admin.register(..., console_url=settings.grid.console_url, ...)`.

`main.py` becomes:

```python
"""Entry point: the config, loaded from file, env and args, into a running server."""

from __future__ import annotations

import logging

from . import config
from .server import SeleniumMCP

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``selenium-flow`` console script."""
    try:
        loaded = config.load(argv)
    except config.ConfigError as exc:
        # A config this server cannot run on stops the boot with the reason:
        # restarted until fixed beats started on something misread (§F4.12).
        raise SystemExit(f"selenium-flow: {exc}") from None
    settings = loaded.settings
    logging.basicConfig(level=settings.log_level.upper())
    server = SeleniumMCP(settings, sources=loaded.sources)
    log.info(
        "config=%s grid=%s auth=%s sessions=%s skill=%s flows=%s secrets=%s",
        settings.config_file or "none",
        settings.grid.url,
        "on" if server.auth_token else "off",
        server.sessions.kind,
        server.skill.skill_info.name if server.skill else "off",
        server.flows.kind if server.flows else "off",
        len(server.secrets.sources) if server.secrets else "off",
    )
    server.run(transport=settings.transport, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Migrate the tests and the harnesses**

List every call site first:

Run: `grep -rn "SeleniumMCP(\|from_env(\|build_parser\|settings_module.resolve\|settings_module.from_env" tests scripts`

Rewrite each `SeleniumMCP(...)` with this mapping. Import
`from kubed.selenium_flow.config import Settings`.

| Old keyword | New |
|---|---|
| `grid_url=X` | `Settings(grid={"url": X}, ...)` |
| `auth_token=T` (T not None) | `auth={"token": T}` |
| `auth_token=None` | omit |
| `route_prefix=P` | `route_prefix=P` |
| `flow_data_dir=D` | `flow={"data_dir": D}` |
| `secrets_dirs=S` | `secrets={"dirs": S}` |
| `skill_enabled=B` | `skill={"enabled": B}` |
| `apps_enabled=B` | `apps={"enabled": B}` |
| `store=`, `pointers=` | unchanged, keyword arguments after the settings |

Example: `SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN, secrets_dirs=str(d))`
becomes `SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}, secrets={"dirs": str(d)}))`.

The other call sites:

- `store.from_env({...})` tests in `tests/test_sessions.py:570-740` call
  `from_settings(SessionSettings(...), RedisSettings(...))` with the same
  values: `REDIS_URL` → `url`, `REDIS_DB` → `db`, `SESSION_TTL` → `ttl`, and
  `store="redis"` wherever the env named a Redis. The `SESSION_STORE=postgres`
  test becomes `pytest.raises(ValidationError)` on `SessionSettings(store="postgres")`.
- `flows.from_env` tests (`tests/test_flows.py:339-344`) call
  `flows.from_settings(FlowSettings(data_dir=...))`, and `"   "` gives `None`.
- `secrets.from_env` tests (`tests/test_secrets.py:276-286`) call
  `secrets.from_settings(SecretsSettings(dirs=...))`.
- `tests/test_browser_choice.py:140-230`: `settings_module.from_env({"DEFAULT_BROWSER": "firefox"})`
  becomes `settings_module.from_settings(SessionSettings(browser="firefox"))`.
  `resolve(..., env={"DEFAULT_BROWSER": X})` becomes
  `resolve(..., defaults={"browser": X})`, and `env={}` becomes
  `defaults={}`. The `BROWSER=/usr/bin/xdg-open` and `nonsense` default cases
  move to `tests/test_config_load.py`: `load([], {"BROWSER": "/usr/bin/xdg-open"})`
  leaves `session.browser` `None`, and `SESSION_BROWSER=nonsense` raises
  `ConfigError`.
- `tests/test_screenshot_saving.py:225,294`: replace
  `monkeypatch.setenv("PUBLIC_BASE_URL", X)` by building the server with
  `Settings(public_base_url=X, ...)`. At `:443`, `settings.from_env()` becomes
  `settings.from_settings(SessionSettings())`.
- `tests/test_shutdown.py:48` and `tests/integration/conftest.py:118`:
  `"MCP_AUTH_TOKEN"` → `"AUTH_TOKEN"`.
- `tests/test_wiki.py:184`: `"MCP_AUTH_TOKEN"` → `"AUTH_TOKEN"`. Task 8
  replaces this test anyway.
- `tests/conftest.py`: the `server` and `open_server` fixtures use `Settings`.
- `scripts/generate_wiki.py` and `tests/test_wiki.py:_spec` build
  `SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": "x"}))`.
- `docker-compose.yaml`: `MCP_AUTH_TOKEN: ${MCP_AUTH_TOKEN:-}` becomes
  `AUTH_TOKEN: ${AUTH_TOKEN:-}`. Delete the dead `SAVED_SESSIONS` line and its
  comment.

- [ ] **Step 9: Run the whole unit suite**

Run: `ruff check kubed tests scripts && pytest -q -m "not integration"`
Expected: all pass, `test_the_environment_is_read_in_two_modules_only`
included.

- [ ] **Step 10: Commit**

```bash
git add -A kubed tests scripts docker-compose.yaml
git commit -m "Every module reads its config section; os.environ only in config and secrets"
```

---

### Task 4: Secrets defined in the config

**Files:**
- Modify: `kubed/selenium_flow/secrets.py`
- Test: `tests/test_config_secrets.py`; update the `source`/`location`
  assertions in `tests/test_secrets.py` and `tests/test_admin_secrets.py` to
  `origins`.

**Interfaces:**
- Consumes: `SecretsSettings`, `SecretEntry`, `FromFile`, `FromEnv` and
  `FromValue` from Task 1; `from_settings` from Task 3.
- Produces:
  - `ConfigEntries(entries, location, environ=None)`;
  - `Catalogue(sources, ttl=..., clock=..., config: ConfigEntries | None = None)`;
  - `Catalogue.unresolved(name, key) -> str | None`.
  - Each listing entry replaces `source` and `location` with
    `origins: [{"source": "filesystem" | "config", "location": str}]`, and
    adds `key_sources: {key: {"from": "filesystem" | "file" | "env" | "value", "path"?: str, "name"?: str}}`,
    `keys_unresolved: [{"key": str, "reason": str}]` (only when non-empty) and
    `inline_keys: [str]` (only when non-empty).

- [ ] **Step 1: Write the failing tests**

```python
"""secrets.entries: merged over the collected secrets, or defined whole."""

import pytest

from kubed.selenium_flow import secrets
from kubed.selenium_flow.config import SecretsSettings

pytestmark = pytest.mark.unit
CFG = "/etc/selenium-flow/config.yaml"


@pytest.fixture
def dirs(tmp_path):
    grafana = tmp_path / "grafana"
    grafana.mkdir()
    (grafana / "username").write_text("viewer")
    (grafana / "password").write_text("pw")
    (grafana / "_allowed_urls").write_text("staging.internal\n")  # broken on disk
    return tmp_path


def _catalogue(dirs, entries, environ=None):
    conf = SecretsSettings(dirs=[str(dirs)] if dirs else [], entries=entries)
    cat = secrets.from_settings(conf, CFG)
    if cat is not None and cat.config is not None and environ is not None:
        cat.config._environ = environ
    return cat


def test_config_merges_description_and_leash_over_a_directory(dirs):
    cat = _catalogue(dirs, {"grafana": {"description": "Viewer", "allowed_urls": ["https://grafana.example.com"]}})
    e = cat.entry("grafana")
    assert e["description"] == "Viewer"
    assert e["allowed_urls"] == ["https://grafana.example.com"] and e["restricted"]
    assert "allowed_urls_rejected" not in e  # the config replaced the broken file
    assert e["keys"] == ["password", "username"]
    assert e["origins"] == [{"source": "filesystem", "location": str(dirs)}, {"source": "config", "location": CFG}]
    assert cat.allows("grafana", "https://grafana.example.com/login")


def test_a_config_key_replaces_a_file_key_of_the_same_name(dirs):
    cat = _catalogue(dirs, {"grafana": {"keys": {"password": {"env": "GF_PW"}}}}, {"GF_PW": "from-env"})
    assert cat.value("grafana", "password") == "from-env"
    assert cat.value("grafana", "username") == "viewer"
    assert cat.entry("grafana")["key_sources"]["password"] == {"from": "env", "name": "GF_PW"}
    assert cat.entry("grafana")["key_sources"]["username"] == {"from": "filesystem"}


def test_a_secret_defined_whole_in_config(tmp_path):
    token = tmp_path / "token"
    token.write_text("abc\n")
    cat = _catalogue(None, {"admin": {"allowed_urls": ["https://s.example.com"],
                                      "keys": {"token": {"file": str(token)}, "user": {"env": "U"}}}}, {"U": "me"})
    assert cat is not None  # secrets are on with entries alone
    assert cat.value("admin", "token") == "abc" and cat.value("admin", "user") == "me"
    e = cat.entry("admin")
    assert e["origins"] == [{"source": "config", "location": CFG}]
    assert e["key_sources"]["token"] == {"from": "file", "path": str(token)}


def test_an_unresolved_key_is_listed_with_its_reason_and_refused_with_it():
    cat = _catalogue(None, {"github": {"keys": {"token": {"env": "GITHUB_TOKEN"}}}}, {})
    assert cat.entry("github")["keys_unresolved"] == [{"key": "token", "reason": "env GITHUB_TOKEN is not set"}]
    assert cat.unresolved("github", "token") == "env GITHUB_TOKEN is not set"
    with pytest.raises(secrets.Refused, match="GITHUB_TOKEN is not set"):
        secrets.bind(cat, {"name": "github", "key": "token"}, "https://github.com", tool="write")


def test_an_inline_value_is_marked_and_never_listed():
    cat = _catalogue(None, {"demo": {"keys": {"password": {"value": "s3cret"}}}})
    assert cat.entry("demo")["inline_keys"] == ["password"]
    assert "s3cret" not in str(cat.listing())
    assert cat.value("demo", "password") == "s3cret"


def test_an_env_key_is_read_at_bind_time_not_at_listing():
    environ = {"U": "one"}
    cat = _catalogue(None, {"d": {"keys": {"u": {"env": "U"}}}}, environ)
    cat.listing()
    environ["U"] = "two"
    assert secrets.bind(cat, {"name": "d", "key": "u"}, "https://x.example", tool="write") == "two"


def test_no_dirs_and_no_entries_is_off():
    assert secrets.from_settings(SecretsSettings()) is None
```

Before running, look at how `bind` reads the catalogue today
(`grep -n "catalogue.value\|has no readable value" kubed/selenium_flow/secrets.py`).
The unresolved test depends on Step 3's change there.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_config_secrets.py -q`
Expected: FAIL. `from_settings` ignores entries, and there is no `config`.

- [ ] **Step 3: Implement the overlay**

In `secrets.py`, import `from pathlib import Path` (already imported) and
`from .config import FromEnv, FromFile, FromValue, SecretEntry, SecretsSettings`.
Then add:

```python
class ConfigEntries:
    """`secrets.entries` from the config file: policy over collected secrets, and whole new ones.

    A key's value is read at the moment it is bound, like a directory's: an
    env reference reads the environment then, and a file reference reads the
    file then. Only presence is checked when the listing is built.
    """

    kind = "config"

    def __init__(self, entries: dict[str, SecretEntry], location: str | None, environ=None):
        self.entries = dict(entries)
        self.location = location or "config"
        self._environ = environ  # None: os.environ, read at the moment of use

    def _env(self):
        return os.environ if self._environ is None else self._environ

    @staticmethod
    def describe(ref) -> dict:
        if isinstance(ref, FromEnv):
            return {"from": "env", "name": ref.env}
        if isinstance(ref, FromFile):
            return {"from": "file", "path": ref.file}
        return {"from": "value"}

    def unresolved(self, ref) -> str | None:
        if isinstance(ref, FromEnv):
            return None if self._env().get(ref.env) else f"env {ref.env} is not set"
        if isinstance(ref, FromFile):
            return None if Path(ref.file).is_file() else f"file {ref.file} is missing"
        return None

    def value(self, ref) -> str | None:
        if isinstance(ref, FromEnv):
            return self._env().get(ref.env) or None
        if isinstance(ref, FromFile):
            try:
                return Path(ref.file).read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
                log.warning("could not read %s: %s", ref.file, type(exc).__name__)
                return None
        return ref.value.get_secret_value()
```

The other changes in `secrets.py`:

- **`FilesystemSource.entry`** replaces `"source": self.kind, "location": str(self.root)`
  with `"origins": [{"source": self.kind, "location": str(self.root)}]` and
  adds `"key_sources": {k: {"from": "filesystem"} for k in keys}`.
- **`Catalogue.__init__`** gains `config: ConfigEntries | None = None`,
  stored as `self.config`.
- **`Catalogue._snapshot`** records owners per key,
  `owners[name] = {key: source for key in entry["keys"]}`. After the
  filesystem loop, if `self.config` is set, it applies each config entry:

```python
            for name, conf in self.config.entries.items():
                base = entries.get(name)
                entry = dict(base) if base else {
                    "name": name, "keys": [], "description": "", "allowed_urls": [],
                    "restricted": False, "origins": [], "key_sources": {},
                }
                entry["origins"] = [*entry["origins"], {"source": "config", "location": self.config.location}]
                if conf.description is not None:
                    entry["description"] = conf.description
                if conf.allowed_urls is not None:
                    # Replaces the directory's leash entirely, a broken one included:
                    # the config was validated at boot, so this one parses.
                    entry["allowed_urls"] = list(conf.allowed_urls)
                    entry["restricted"] = True
                    entry.pop("allowed_urls_rejected", None)
                key_owners = dict(owners.get(name, {}))
                key_sources = dict(entry["key_sources"])
                for key, ref in conf.keys.items():
                    key_owners[key] = ref
                    key_sources[key] = self.config.describe(ref)
                entry["keys"] = sorted(key_owners)
                entry["key_sources"] = key_sources
                unresolved = []
                for key, ref in sorted(conf.keys.items()):
                    reason = self.config.unresolved(ref)
                    if reason:
                        unresolved.append({"key": key, "reason": reason})
                inline = sorted(k for k, ref in conf.keys.items() if isinstance(ref, FromValue))
                if unresolved:
                    entry["keys_unresolved"] = unresolved
                if inline:
                    entry["inline_keys"] = inline
                entries[name] = entry
                owners[name] = key_owners
```

- **`Catalogue.source_of(name)`** returns the directory source that owns any
  of the entry's keys, or `None` for a config-only secret:
  `next((o for o in self._snapshot()[1].get(name, {}).values() if not isinstance(o, (FromFile, FromEnv, FromValue))), None)`.
- **`Catalogue.value(name, key)`** looks up the owner for that key.
  `owner = self._snapshot()[1].get(name, {}).get(key)`; `None` means `None`.
  A reference goes to `self.config.value(owner)`, and anything else to
  `owner.value(name, key)`.
- **`Catalogue.unresolved(name, key)`** returns the reason from
  `entry.get("keys_unresolved")` for that key, or `None`.
- **`bind`**, where it refuses a missing value, becomes:

```python
    value = catalogue.value(name, key)
    if value is None:
        reason = catalogue.unresolved(name, key)
        raise Refused(
            f"the secret {name!r} cannot read {key!r}: {reason}" if reason
            else f"the secret {name!r} has no readable value for {key!r}"
        )
```

- **`from_settings`** turns on with entries alone:

```python
def from_settings(conf: SecretsSettings, config_file: str | None = None) -> Catalogue | None:
    """The catalogue the config asks for, or None when there are no secrets."""
    if not conf.dirs and not conf.entries:
        log.info("secrets: off (set secrets.dirs or secrets.entries to enable them)")
        return None
    overlay = ConfigEntries(conf.entries, config_file) if conf.entries else None
    log.info("secrets: %s director%s, %s from config", len(conf.dirs),
             "y" if len(conf.dirs) == 1 else "ies", len(conf.entries))
    return Catalogue([FilesystemSource(path) for path in conf.dirs], config=overlay)
```

- [ ] **Step 4: Run the secrets tests**

Run: `pytest tests/test_config_secrets.py tests/test_secrets.py tests/test_admin_secrets.py -q`
Expected: the new tests pass. Where old tests read `entry["source"]` or
`entry["location"]`, change them to `entry["origins"][0]["source"]` or
`["location"]`. Then `test_the_audit_trail_never_contains_a_value` must still
pass: break it on purpose once (log the value in `bind`), watch it fail, then
revert.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/secrets.py tests/test_config_secrets.py tests/test_secrets.py tests/test_admin_secrets.py
git commit -m "Secrets can be defined in the config, merged over the collected ones"
```

---

### Task 5: `GET /admin/settings`

**Files:**
- Modify: `kubed/selenium_flow/http/admin.py` (`register` signature, a new route after `admin_secrets`)
- Modify: `kubed/selenium_flow/server.py` (pass the payload function)
- Test: `tests/test_admin_settings.py`

**Interfaces:**
- Consumes: `config.describe(settings, sources)` from Task 2;
  `SeleniumMCP.settings` and `.sources` from Task 3.
- Produces: `admin.register(..., settings_payload: Callable[[], dict] | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
"""The Settings tab's one endpoint."""

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}, redis={"password": "pw!"}),
        sources={"auth.token": "env", "redis.password": "file", "port": "args"},
    )
    return TestClient(server.mcp.http_app())


def test_it_needs_the_token(client):
    assert client.get("/admin/settings").status_code == 401


def test_every_section_and_nothing_sensitive(client):
    body = client.get("/admin/settings", headers=AUTH).json()
    assert [s["name"] for s in body["sections"]][0] == "server"
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert rows["auth.token"]["value"] is None and rows["auth.token"]["set"] is True
    assert rows["redis.password"]["source"] == "file"
    assert TOKEN not in str(body) and "pw!" not in str(body)
    assert "secrets.entries" not in rows
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_admin_settings.py -q`
Expected: FAIL. The route 404s, so the 401 assertion fails.

- [ ] **Step 3: Add the route and wire it**

In `admin.register`, add the parameter `settings_payload=None`. After
`admin_secrets`, add:

```python
    @mcp.custom_route(f"{prefix}/admin/settings", methods=["GET"], name="admin_settings")
    @guarded
    async def admin_settings(_request: Request) -> JSONResponse:
        """How this server was started: every setting and where it came from.

        Never a sensitive value and never a secret definition; the Secrets tab
        has those. Read-only, like every admin view of configuration.
        """
        if settings_payload is None:
            return JSONResponse({"config_file": None, "sections": []})
        return JSONResponse(settings_payload())
```

In `server.py`'s `admin.register(...)` call, add
`settings_payload=lambda: config.describe(self.settings, self.sources)`.

- [ ] **Step 4: Run them to verify they pass**

Run: `pytest tests/test_admin_settings.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add kubed/selenium_flow/http/admin.py kubed/selenium_flow/server.py tests/test_admin_settings.py
git commit -m "Admin: GET /admin/settings, every setting and where it came from"
```

---

### Task 6: The Settings tab

**Files:**
- Modify: `ui/src/lib/types.ts`, `ui/src/admin/router.svelte.ts`, `ui/src/admin/Admin.svelte`
- Create: `ui/src/admin/SettingsPane.svelte`, `ui/src/admin/SettingsPane.test.ts`
- Modify: `ui/src/admin/router.test.ts`, `ui/src/admin/Admin.test.ts` (the new tab)

**Interfaces:**
- Consumes: the `GET /admin/settings` payload from Task 5.
- Produces: the route `{ view: 'settings' }` and `hashes.settings = '#/settings'`.

- [ ] **Step 1: The types**

Append to `ui/src/lib/types.ts`:

```ts
export type SettingSource = 'default' | 'file' | 'env' | 'args'
export interface SettingRow {
  key: string
  name: string
  description: string
  value: unknown
  source: SettingSource
  file?: string
  sensitive?: boolean
  set?: boolean
}
export interface SettingsSection { name: string; description: string; settings: SettingRow[] }
export interface SettingsPayload { config_file: string | null; sections: SettingsSection[] }
```

- [ ] **Step 2: Write the failing tests**

`ui/src/admin/SettingsPane.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import SettingsPane from './SettingsPane.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
const FILE = '/etc/selenium-flow/config.yaml'

const body = {
  config_file: FILE,
  sections: [
    { name: 'server', description: 'Where it listens, and where it lives.', settings: [
      { key: 'port', name: 'port', description: 'Port to listen on.', value: 8000, source: 'args' },
      { key: 'public_base_url', name: 'public_base_url', description: 'Where browsers reach this server, for links.', value: null, source: 'default' },
    ] },
    { name: 'auth', description: 'The bearer token every request needs.', settings: [
      { key: 'auth.token', name: 'token', description: 'Bearer token for every request. Unset is open.', value: null, source: 'env', sensitive: true, set: true },
    ] },
    { name: 'redis', description: 'The session store\u2019s connection.', settings: [
      { key: 'redis.db', name: 'db', description: 'Redis database number.', value: 2, source: 'file', file: FILE },
      { key: 'redis.password', name: 'password', description: 'Redis password.', value: null, source: 'default', sensitive: true, set: false },
    ] },
    { name: 'secrets', description: 'Where secrets are read from.', settings: [
      { key: 'secrets.dirs', name: 'dirs', description: 'Directories of secrets. First match wins.', value: ['/a', '/b'], source: 'file', file: FILE },
    ] },
  ],
}

const row = (c: HTMLElement, key: string) => c.querySelector(`.setting[data-key="${key}"]`) as HTMLElement

test('a card per section, a row per setting, a pill per source', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  expect(screen.getByText('Loading…')).toBeInTheDocument()
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument())
  expect(container.querySelectorAll('.settings-card')).toHaveLength(4)
  expect(row(container, 'port').querySelector('.value')).toHaveTextContent('8000')
  expect(row(container, 'port').querySelector('.pill.src')).toHaveTextContent('args')
  expect(row(container, 'public_base_url').querySelector('.value')).toHaveTextContent('—')
  expect(row(container, 'secrets.dirs').querySelector('.value')).toHaveTextContent('/a, /b')
})

test('the file path shows only for a value from the file', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(row(container, 'redis.db').querySelector('.file')).toHaveTextContent(FILE)
  expect(row(container, 'port').querySelector('.file')?.textContent).toBe('')
})

test('a sensitive value is dots when set and nothing when not, and never labelled', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(row(container, 'auth.token').querySelector('.value')?.textContent).toBe('●●●●')
  expect(row(container, 'redis.password').querySelector('.value')?.textContent).toBe('')
  expect(container).not.toHaveTextContent(/sensitive|not set/i)
})

test('the ⓘ carries the description, the legend is four bare pills, and the wiki is linked', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(row(container, 'port').querySelector('.info')).toHaveAttribute('data-tip', 'Port to listen on.')
  const legend = container.querySelector('.legend') as HTMLElement
  expect(Array.from(legend.querySelectorAll('.pill')).map((p) => p.textContent)).toEqual(['default', 'file', 'env', 'args'])
  expect(legend.textContent?.replace(/default|file|env|args|\s/g, '')).toBe('')
  expect(screen.getByRole('link', { name: /wiki/ })).toHaveAttribute('href', 'https://github.com/kubed-io/selenium-flow/wiki/Configuration')
})

test('a key shows without its section: the card title is the section', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(row(container, 'redis.db').querySelector('.key')?.textContent).toBe('db')
  expect(row(container, 'port').querySelector('.key')?.textContent).toBe('port')
})

test('nothing on the tab expands', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(container.querySelectorAll('button, details, [aria-expanded]')).toHaveLength(0)
})

test('an error', async () => {
  fakeFetch({ 'GET /admin/settings': { status: 500, body: { error: 'nope' } } })
  render(SettingsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
})
```

In `router.test.ts`, add
`expect(parse('#/settings')).toEqual({ view: 'settings' })` and
`expect(hashes.settings).toBe('#/settings')`. In `Admin.test.ts`, extend the
existing tab-order test so the tabs read
`['Sessions', 'Secrets', 'Settings', 'Grid console']`, and assert that
clicking `#tabSettings` sets `location.hash` to `'#/settings'`. Follow the
file's existing pattern for the Secrets tab.

- [ ] **Step 3: Run them to verify they fail**

Run: `npm --prefix ui run test -- --run SettingsPane router Admin`
Expected: FAIL. `SettingsPane.svelte` does not exist.

- [ ] **Step 4: Implement the router, the tab and the pane**

`router.svelte.ts`:

- add `| { view: 'settings' }` to `Route`;
- in `parse`, `if (view === 'settings') return { view: 'settings' }`;
- in `hashes`, add `settings: '#/settings'`.

`Admin.svelte`:

- import `SettingsPane`;
- `top` becomes
  `route.view === 'secrets' ? 'secrets' : route.view === 'settings' ? 'settings' : route.view === 'console' && !consoleSelf ? 'console' : 'sessions'`;
- after the Secrets tab button, add
  `<button id="tabSettings" role="tab" aria-selected={top === 'settings'} onclick={() => go(hashes.settings)}>Settings</button>`;
- in the panes, add `{:else if top === 'settings'}<SettingsPane {api} />`.

`SettingsPane.svelte`:

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import type { SettingRow, SettingsPayload } from '../lib/types'
  import type { Api } from './api'
  import { Latest } from './latest'

  let { api }: { api: Api } = $props()
  // Raw: an API response, replaced wholesale, never mutated.
  let data = $state.raw<SettingsPayload | null>(null)
  let error = $state<string | null>(null)
  const loads = new Latest()
  const WIKI = 'https://github.com/kubed-io/selenium-flow/wiki/Configuration'
  // Weakest first, which is also precedence: the order is the legend.
  const SOURCES = ['default', 'file', 'env', 'args'] as const

  onMount(() => {
    void loads.run((signal) => api<SettingsPayload>('/admin/settings', 'GET', undefined, signal),
      (d) => { data = d; error = null }, (e) => { error = e.message })
    return () => loads.abort()
  })

  /* Show, never explain: dots say sensitive, a dash says unset. */
  function shown(row: SettingRow): string {
    if (row.sensitive) return row.set ? '●●●●' : ''
    if (Array.isArray(row.value)) return row.value.length ? row.value.join(', ') : '—'
    if (row.value === null || row.value === undefined || row.value === '') return '—'
    return String(row.value)
  }
</script>

<section id="paneSettings">
  {#if error}
    <div class="empty error">{error}</div>
  {:else if !data}
    <div class="empty">Loading…</div>
  {:else}
    <h2>Settings</h2>
    <p class="small muted">How this server was started. Read-only. <a href={WIKI} target="_blank" rel="noopener noreferrer">Every setting is described on the wiki →</a></p>
    <div class="legend">{#each SOURCES as s (s)}<span class={['pill', 'src', s]}>{s}</span>{/each}</div>
    {#each data.sections as section (section.name)}
      <div class="card settings-card">
        <strong>{section.name}</strong>
        <div class="small muted">{section.description}</div>
        {#each section.settings as row (row.key)}
          <div class="setting" data-key={row.key}>
            <span class="info" tabindex="0" role="img" aria-label={row.description} data-tip={row.description}>i</span>
            <code class="key">{row.name}</code>
            <span class="value">{shown(row)}</span>
            <code class="file">{row.source === 'file' ? (row.file ?? '') : ''}</code>
            <span class={['pill', 'src', row.source]}>{row.source}</span>
          </div>
        {/each}
      </div>
    {/each}
  {/if}
</section>

<style>
  .legend { display: flex; justify-content: flex-end; gap: 4px; margin: 8px 0; }
  .settings-card { display: flex; flex-direction: column; gap: 2px; margin-bottom: var(--gap); }
  .setting { display: grid; grid-template-columns: 16px 240px 1fr auto auto; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid var(--line); }
  .key, .file { font-size: 12px; }
  .file { color: var(--muted); }
  .value { overflow-wrap: anywhere; }
  .info { position: relative; display: inline-grid; place-items: center; width: 14px; height: 14px; border: 1px solid var(--muted); border-radius: 50%; color: var(--muted); font-size: 10px; font-weight: 600; cursor: help; }
  .info:hover::after, .info:focus::after {
    content: attr(data-tip); position: absolute; left: 20px; top: -4px; z-index: 5; white-space: nowrap;
    padding: 4px 8px; border-radius: 6px; background: var(--ink); color: var(--panel); font-size: 12px; font-weight: 400;
  }
  .pill.src.default { color: var(--muted); }
  .pill.src.file { color: var(--accent); border-color: currentColor; }
  .pill.src.env { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .pill.src.arg, .pill.src.args { background: var(--ink); border-color: var(--ink); color: var(--panel); }
</style>
```

- [ ] **Step 5: Run the UI checks**

Run: `npm --prefix ui run check && npm --prefix ui run lint && npm --prefix ui run test -- --run && npm --prefix ui run build`
Expected: all pass. The build writes into `kubed/selenium_flow/http/static/`,
which is gitignored.

- [ ] **Step 6: Commit**

```bash
git add ui/src
git commit -m "Admin UI: a Settings tab — every setting, its value, and where it came from"
```

---

### Task 7: The Secrets tab shows where config secrets come from

**Files:**
- Modify: `ui/src/lib/types.ts` (`Secret`), `ui/src/admin/SecretsPane.svelte`, `ui/src/admin/SecretsPane.test.ts`

**Interfaces:**
- Consumes: `origins`, `key_sources`, `keys_unresolved` and `inline_keys` from Task 4.

- [ ] **Step 1: The types**

In `types.ts`, `Secret` loses `source` and `location`, and gains:

```ts
  origins?: { source: 'filesystem' | 'config'; location: string }[]
  key_sources?: Record<string, { from: 'filesystem' | 'file' | 'env' | 'value'; name?: string; path?: string }>
  keys_unresolved?: { key: string; reason: string }[]
  inline_keys?: string[]
```

- [ ] **Step 2: Write the failing test** (append to `SecretsPane.test.ts`, and
  in test S1 swap `source: 'file', location: '/s/admin'` for
  `origins: [{ source: 'filesystem', location: '/s/admin' }]`, expecting
  `fromfilesystem · /s/admin`)

```ts
test('config secrets: key sources, every origin, and the two new warnings', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 'grafana', keys: ['password'], restricted: true, allowed_urls: ['https://g'],
        origins: [{ source: 'filesystem', location: '/secrets' }, { source: 'config', location: '/etc/c.yaml' }],
        key_sources: { password: { from: 'filesystem' } }, uses: [] },
      { name: 'admin', keys: ['token'], restricted: true, allowed_urls: ['https://s'],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { token: { from: 'env', name: 'AUTH_TOKEN' } }, uses: [] },
      { name: 'demo', keys: ['password'], restricted: true, allowed_urls: ['http://l'], inline_keys: ['password'],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { password: { from: 'value' } }, uses: [] },
      { name: 'github', keys: ['token'], restricted: true, allowed_urls: ['https://github.com'],
        keys_unresolved: [{ key: 'token', reason: 'env GITHUB_TOKEN is not set' }],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { token: { from: 'env', name: 'GITHUB_TOKEN' } }, uses: [] },
    ],
    undefined: [],
  } } })
  const { container } = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const [grafana, admin, demo, github] = container.querySelectorAll('.card.secret')
  expect(grafana).toHaveTextContent('fromfilesystem · /secrets + config · /etc/c.yaml')
  expect(grafana).toHaveTextContent('keyspassword')  // a directory key stays bare
  expect(admin).toHaveTextContent('token · env AUTH_TOKEN')
  expect(demo.querySelector('.pill.warn')).toHaveTextContent('inline value')
  expect(demo).toHaveTextContent('password · value')
  expect(github.querySelector('.pill.warn')).toHaveTextContent('key unresolved')
  expect(github).toHaveTextContent('token: env GITHUB_TOKEN is not set')
})
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm --prefix ui run test -- --run SecretsPane`
Expected: FAIL. The key pills show bare names, and there is no warn pill.

- [ ] **Step 4: Implement**

In `SecretsPane.svelte`:

```ts
  const keyLabel = (s: Secret, k: string) => {
    const from = s.key_sources?.[k]
    if (!from || from.from === 'filesystem') return k
    if (from.from === 'env') return `${k} · env ${from.name}`
    return `${k} · ${from.from}`
  }
  const warnOf = (s: Secret) =>
    s.allowed_urls_rejected ? 'unusable until fixed'
      : s.keys_unresolved?.length ? 'key unresolved'
      : s.inline_keys?.length ? 'inline value'
      : s.restricted ? '' : 'any site'
  const reasonOf = (s: Secret) =>
    s.allowed_urls_rejected ? 'allowed_urls: ' + ([] as string[]).concat(s.allowed_urls_rejected).join(', ')
      : s.keys_unresolved?.length ? s.keys_unresolved.map((u) => `${u.key}: ${u.reason}`).join('; ')
      : ''
  const fromOf = (s: Secret) => (s.origins ?? []).map((o) => `${o.source} · ${o.location}`).join(' + ')
```

In the card snippet, the key pills render `{keyLabel(s, k)}`. The `from` fact
becomes `{#if s.origins?.length}…{fromOf(s)}…{/if}`.

- [ ] **Step 5: Run the UI checks**

Run: `npm --prefix ui run check && npm --prefix ui run lint && npm --prefix ui run test -- --run`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ui/src
git commit -m "Secrets tab: where each key comes from, and warnings for inline and unresolved keys"
```

---

### Task 8: Documentation

**Files:**
- Modify: `scripts/generate_wiki.py`, `tests/test_wiki.py`
- Create: `wiki/notes/Configuration.notes.md`; `wiki/Configuration.md` (generated)
- Modify: `wiki/Secrets.md`, `wiki/Deployment.md`, `wiki/_Sidebar.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md`, `skills/selenium-flow/references/CONFIGURATION.md`

- [ ] **Step 1: Write the failing wiki test**

In `tests/test_wiki.py`, replace
`test_the_env_table_documents_the_switches_for_every_feature` with:

```python
@needs_wiki
def test_the_configuration_page_documents_every_setting_in_all_three_spellings():
    from kubed.selenium_flow import config

    page = (WIKI / "Configuration.md").read_text()
    for leaf in config.leaves():
        for spelling in (leaf.path, leaf.env, leaf.flag):
            assert f"`{spelling}`" in page, spelling


def test_deployment_points_at_the_configuration_page():
    if not (WIKI / "Deployment.md").is_file():
        pytest.skip("wiki submodule not checked out")
    assert "(Configuration)" in (WIKI / "Deployment.md").read_text()
```

Run: `pytest tests/test_wiki.py -q`
Expected: FAIL. `Configuration.md` does not exist.

- [ ] **Step 2: Generate the page**

In `scripts/generate_wiki.py`, import `from kubed.selenium_flow import config`
and add:

```python
def configuration() -> str:
    """Every setting, from the schema: the page a person reads to configure the server."""
    sections = []
    by_section: dict[str, list] = {name: [] for name in config.SECTION_ORDER}
    for leaf in config.leaves():
        default = "—" if leaf.default in (None, "", []) else f"`{str(leaf.default).lower() if isinstance(leaf.default, bool) else leaf.default}`"
        by_section[leaf.section].append(
            [f"`{leaf.path}`", f"`{leaf.env}`", f"`{leaf.flag}`", default, leaf.description + (" **Sensitive.**" if leaf.sensitive else "")]
        )
    for name in config.SECTION_ORDER:
        sections.append(f"## {name}\n\n{config.SECTION_DESCRIPTIONS[name]}\n\n"
                        + table(by_section[name], ["File", "Env", "Flag", "Default", "What"]))
    notes = NOTES / f"Configuration{NOTES_SUFFIX}"
    tail = f"\n\n---\n\n{notes.read_text().strip()}\n" if notes.is_file() else "\n"
    return (
        f"{BANNER.format(name='Configuration')}\n\n# Configuration\n\n"
        "Every setting can be set in a YAML config file, in the environment, or on the command "
        "line, and a later one wins: **default < file < env < args**. A setting's file path is "
        "its name: `redis.host` is `REDIS_HOST` and `--redis-host`.\n\n"
        "The file is named by `--config-file` or `CONFIG_FILE`. There is no default location. "
        "A file with an unknown key stops the server with the reason.\n\n"
        + "\n\n".join(sections) + tail
    )
```

and in `pages()`, before `return out`: `out["Configuration.md"] = configuration()`.

- [ ] **Step 3: Write the hand-written notes**

`wiki/notes/Configuration.notes.md`:

```markdown
## One value in each place

```yaml
# /etc/selenium-flow/config.yaml
session:
  ttl: 86400
redis:
  host: redis.data
  db: 2
```

```sh
export CONFIG_FILE=/etc/selenium-flow/config.yaml
export GRID_URL=http://selenium-hub:4444
selenium-flow --log-level DEBUG
```

The Settings tab of the admin UI shows every value and where it came from.

## Worth setting

`session.page_load_timeout`: with no bound, one hung page holds a Grid slot
until the Grid reaps it.

## The environment is lenient, the file is not

An environment variable the server does not know is ignored: Kubernetes
injects `<SERVICE>_PORT` variables into every pod. A key the file does not
know stops the boot, because it is a typo.

## Secrets

The file's `secrets.entries` section defines secrets. See [Secrets](Secrets).
```

- [ ] **Step 4: Update the hand-written pages**

- **`wiki/Secrets.md`**: add a section **Secrets in the config**. It has the
  spec's merge table (description, allowed_urls, keys), the three reference
  kinds with one YAML example each, the three warnings the Secrets tab shows
  (inline value, key unresolved, a broken leash), and this line: *prefer
  `file:` or `env:`; `value:` writes the secret into the config file itself.*
- **`wiki/Deployment.md`**: replace the `## Configuration` table and its
  paragraph with two sentences linking to [Configuration](Configuration). In
  the Kubernetes section, show the config ConfigMap mounted at
  `/etc/selenium-flow/config.yaml`, `CONFIG_FILE` in env, and
  `enableServiceLinks: false`.
- **`wiki/_Sidebar.md`**: under **Start here**, after `[Deployment](Deployment)`,
  add `- [Configuration](Configuration)`.
- **`skills/selenium-flow/references/CONFIGURATION.md`**: every renamed
  variable per Global Constraints, and a short *config file* section with the
  naming rule and the three places.
- **`README.md`** `## ⚙️ Configuration`: replace the table with the naming rule,
  `default < file < env < args`, a ten-line YAML example (with a secret using
  `env:`), a line recommending `file:`/`env:` over `value:`, and a link to the
  wiki Configuration page. `tests/test_readme.py` holds the size limit. Also
  fix every `MCP_AUTH_TOKEN` in the README.
- **`AGENTS.md`**: a `## Configuration` section.
  - The naming rule, and the three constraints on section names.
  - `os.environ` is read only in `config.py` and in `secrets.py` (for `env:`
    keys at bind time), and `test_the_environment_is_read_in_two_modules_only`
    holds that.
  - Env is lenient because Kubernetes injects `*_PORT` variables, and the file
    is strict.
  - `_Environment` overrides a pydantic-settings private hook,
    `_load_env_vars`, so a pydantic-settings upgrade is checked by
    `tests/test_config_load.py`.
  - The Settings tab shows and does not explain (Dr K, 2026-09-26).
- **`CHANGELOG.md` `[Unreleased]`**:

```markdown
- A YAML config file (`--config-file` / `CONFIG_FILE`): every setting can be set there, in env or as a flag, and a later one wins.
- Secrets can be defined in the config file, merged over the ones in `secrets.dirs`, with keys read from a file or an env var.
- The admin UI has a Settings tab showing every setting and where its value came from.
- **Breaking:** `MCP_AUTH_TOKEN` is `AUTH_TOKEN`; `DEFAULT_BROWSER`, `WINDOW_WIDTH`, `WINDOW_HEIGHT`, `PAGE_LOAD_TIMEOUT` and `SCRIPT_TIMEOUT` are `SESSION_*`; `--no-skill`/`--no-apps` are `--skill-enabled false`/`--apps-enabled false`.
```

- [ ] **Step 5: Regenerate the wiki and run the checks**

Run: `python scripts/generate_wiki.py && pytest tests/test_wiki.py tests/test_readme.py -q && ruff check scripts tests`
Expected: pass. `wiki/Configuration.md` is written.

- [ ] **Step 6: Commit (the repo, then the wiki submodule)**

```bash
git -C wiki add -A && git -C wiki commit -m "Configuration: every setting in all three spellings, and config secrets"
git add scripts tests README.md AGENTS.md CHANGELOG.md skills wiki
git commit -m "Docs: the Configuration page, config secrets, and the renamed variables"
```

Memory note: *push the wiki submodule too*. A PR whose wiki SHA is not pushed
fails `Test` in 10 s with no pytest output.

---

### Task 9: The pull request and the review loop

- [ ] **Step 1: Push the branch and the wiki**

```bash
git -C wiki push origin HEAD:master
git push -u origin config
```

(Push over ssh: the https token cannot push workflow edits.)

- [ ] **Step 2: Open the PR.** Dr K's brief for this session lists it as a
  completion condition, so this one PR is approved.

```bash
gh pr create --base main --head config \
  --title "A config file: every setting from file, env or args; config secrets; a Settings tab" \
  --body-file /tmp/claude-1000/-projects-cluster/d80279da-f7a0-4f9c-8b13-1bf1f3e48455/scratchpad/pr-body.md
```

The body covers:

- what a user can now do;
- the breaking renames;
- the spec and plan paths;
- the Penpot file, its **Admin** page, and version *round 4*;
- the cluster change that follows the image (Task 10);
- the attribution line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 3: Watch the checks, then answer Copilot**

Run: `gh pr checks --watch`
Expected: Test, Quality, UI and PR (changelog) all green.

For every Copilot and code-scanning thread:

- read it and verify the claim against the code;
- fix it with a commit, or answer why not;
- resolve it through `gh api graphql` (the `resolveReviewThread` mutation);
- push, and request a new review with
  `gh api repos/kubed-io/selenium-flow/pulls/<n>/requested_reviewers -f "reviewers[]=copilot-pull-request-reviewer[bot]"`.

Repeat until Copilot's review is a plain comment with no suggestions: the 🔵.
After each fix, sweep the class and not just the instance. Grep every other
reader of the same state before you reply.

---

### Task 10: The image, and the live deployment on a config file

**Files (cluster repo, `/projects/cluster/apps/selenium/components/mcp`):**
- Create: `config.yaml`
- Modify: `kustomization.yaml`, `deployment.yaml`, `mcp.env`, `auth.yaml` (comment only)

- [ ] **Step 1: Build the branch image**

Run: `gh workflow run image.yml --repo kubed-io/selenium-flow --ref config`
then `gh run watch --repo kubed-io/selenium-flow $(gh run list --repo kubed-io/selenium-flow --workflow image.yml --limit 1 --json databaseId -q '.[0].databaseId')`
Expected: success. The run pushes `kubed/selenium-flow:config` and moves `:latest`.

- [ ] **Step 2: Write `config.yaml`**

```yaml
# selenium-flow's config file, mounted at /etc/selenium-flow/config.yaml and
# named by CONFIG_FILE in mcp.env. What is about the server lives here; what is
# about where the pod runs stays in mcp.env; two flags ride on the container
# args. The Settings tab shows which place each value came from.
session:
  store: redis
  ttl: 86400
# db 2 is the shared, key-namespaced database the flow-namespace services use
# (see apps/redis).
redis:
  host: redis.data
  db: 2
  prefix: "selenium-flow:session:"
flow:
  data_dir: /data/flows
secrets:
  dirs:
  - /secrets
  entries:
    # This server's own token, typed by a flow that signs in to the admin UI.
    # Read from the pod's env at the moment it is bound; nothing is copied.
    admin:
      description: This server's own admin token, so a flow can sign in to the admin UI
      allowed_urls:
      - https://selenium.kellyferrone.com
      keys:
        token:
          env: AUTH_TOKEN
    # Keys from /secrets/grafana (the LDAP-owned Secret); only the policy is here.
    grafana:
      description: The selenium LDAP service account, a Grafana Viewer, for checking dashboards
      allowed_urls:
      - https://grafana.kellyferrone.com
    # Mounted as env vars from the secretGenerator, to show secrets arriving as env.
    the-internet:
      description: Public demo login for the-internet.herokuapp.com, for testing flows
      allowed_urls:
      - https://the-internet.herokuapp.com
      keys:
        username:
          env: THE_INTERNET_USERNAME
        password:
          env: THE_INTERNET_PASSWORD
```

- [ ] **Step 3: `kustomization.yaml`**

- `images: newTag: config`, until the release that ships this.
- Add a `configMapGenerator` entry `selenium-flow-config` with
  `files: [config.yaml]`.
- Delete the two `*-meta` generators.
- Change the `the-internet` secretGenerator literals to `USERNAME=tomsmith`
  and `PASSWORD=SuperSecretPassword!`, and drop the two `_` literals.
- Trim the comments to what is still true.

- [ ] **Step 4: `mcp.env`**

Keep `CONFIG_FILE=/etc/selenium-flow/config.yaml`, `GRID_URL`,
`PUBLIC_BASE_URL` and `GRID_CONSOLE_URL`, with their comments. Delete `PORT`,
`LOG_LEVEL`, `SESSION_*`, `REDIS_*`, `FLOW_DATA_DIR` and `SECRETS_DIRS`, since
they now live in `config.yaml` or the args. Say so in one header line.

- [ ] **Step 5: `deployment.yaml`**

- `spec.template.spec.enableServiceLinks: false`, commented: service-link
  `*_PORT` variables are noise to an env-configured server.
- Container `args: [--log-level, INFO, --port, "8000"]`, commented: the third
  place a setting can come from.
- `env`:
  `- name: AUTH_TOKEN` with `valueFrom.secretKeyRef: {name: selenium-flow-auth, key: MCP_AUTH_TOKEN}`,
  commented: codeserver reads the key by that name.
- `envFrom`: remove the `selenium-flow-auth` secretRef. Add
  `- secretRef: {name: selenium-flow-secret-the-internet}` with
  `prefix: THE_INTERNET_`.
- Volumes and mounts:
  - delete `secret-the-internet` and `secret-admin`;
  - `secret-grafana` projects the LDAP Secret only, with no configMap source;
  - add a volume `config` from configMap `selenium-flow-config`, mounted
    read-only at `/etc/selenium-flow`.

- [ ] **Step 6: Render and apply**

Run: `kubectl build apps/selenium | grep -A3 "selenium-flow-config\|AUTH_TOKEN\|enableServiceLinks\|THE_INTERNET_"`
Expected: the ConfigMap, the env and the flag are rendered.

Then end any open browser first. Memory note: `kubectl up` resets the KEDA
node replicas and kills live browsers. Use `mcp__selenium-flow__end_browser`.
Then run `kubectl up apps/selenium`. `kubectl plan` is broken, so use
`build`/`diff` to preview.

- [ ] **Step 7: Verify it live, with the server itself**

- `kubectl -n flow logs deploy/selenium-flow | head -5` shows
  `config=/etc/selenium-flow/config.yaml`.
- With selenium-flow's own MCP tools, open a session, go to
  `https://selenium.kellyferrone.com/flow/#/settings`, and sign in by
  binding the `admin` secret: `write` with `secret={"name": "admin", "key": "token"}`.
  That proves the config-defined env key works.
- `extract` the Settings tab. Expected sources:
  - `session.ttl`: `file`;
  - `grid.url`: `env`;
  - `port` and `log_level`: `args`;
  - `transport`: `default`;
  - `auth.token`: `●●●●`.

  Then screenshot it once for Dr K.
- `extract` the Secrets tab: `admin` shows `token · env AUTH_TOKEN`,
  `grafana` shows `filesystem · /secrets + config · /etc/selenium-flow/config.yaml`,
  and `the-internet` shows two env keys.
- Run the saved `the-internet-login` flow to prove the env-sourced secret is
  typed.
- `end_browser`.

- [ ] **Step 8: Commit the cluster change (not pushed without asking)**

```bash
cd /projects/cluster
git add apps/selenium/components/mcp
git commit -m "selenium-flow: run on a config file, with settings in file, env and args"
```

Then report to Dr K: the PR link, the checks, Copilot's final state, the live
screenshots, and that the PR awaits his approval. After the merge, and after
a release, the cluster pin goes from `newTag: config` to the release tag.
