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
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import NoDecode

from .core.browser import DEFAULT_GRID_URL, normalize_browser

# Marks a field that only the config file may set: structure, not a value.
FILE_ONLY = "file_only"


class Section(BaseModel):
    """A group of settings. Unknown keys are refused, so a typo stops the boot."""

    # hide_input_in_errors: a rejected value can be a credential (an allowed
    # URL's userinfo, a secret's inline value), and pydantic's default error
    # message otherwise echoes it verbatim via `input_value=...`.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class FromFile(Section):
    file: str


class FromEnv(Section):
    env: str


class FromValue(Section):
    value: SecretStr


# The key present is the discriminator. Each member forbids extra keys, so
# `{file, env}` and `{}` match none of them and are refused, rather than
# pydantic quietly picking the first member that fits.
KeyRef = FromFile | FromEnv | FromValue


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
    token: SecretStr | None = Field(
        None, description="Bearer token for every request. Unset is open."
    )


class GridSettings(Section):
    url: str = Field(DEFAULT_GRID_URL, description="The Selenium Grid hub.")
    console_url: str = Field(
        "/", description="Where the Grid console tab is framed from."
    )


class SessionSettings(Section):
    store: Literal["memory", "redis"] = Field(
        "memory", description="memory, or redis to survive restarts."
    )
    ttl: int = Field(86400, description="Seconds a session is kept after its last use.")
    browser: Literal["chrome", "firefox"] | None = Field(
        None, description="chrome or firefox, for new sessions."
    )
    width: int | None = Field(None, description="Window width for new sessions.")
    height: int | None = Field(None, description="Window height for new sessions.")
    page_load_timeout: int | None = Field(
        None, description="Seconds a page may take to load."
    )
    script_timeout: int | None = Field(None, description="Seconds a script may run.")

    @field_validator("browser", mode="before")
    @classmethod
    def _browser(cls, value):
        return normalize_browser(value) if value not in (None, "") else None


class RedisSettings(Section):
    url: SecretStr | None = Field(
        None, description="A full Redis URL, instead of host and port."
    )
    host: str = Field("localhost", description="Redis host.")
    port: int = Field(6379, description="Redis port.")
    db: int = Field(0, description="Redis database number.")
    username: str | None = Field(None, description="Redis username.")
    password: SecretStr | None = Field(None, description="Redis password.")
    ssl: bool = Field(False, description="Connect to Redis over TLS.")
    prefix: str = Field(
        "selenium-flow:session:",
        description="Prefix on every key, so a shared database is safe.",
    )


class FlowSettings(Section):
    data_dir: str | None = Field(
        None, description="Where flows and kept files live. Unset turns flows off."
    )

    @field_validator("data_dir")
    @classmethod
    def _blank_is_off(cls, value):
        return (value or "").strip() or None


class SecretsSettings(Section):
    # NoDecode: pydantic-settings would otherwise JSON-decode a list read from
    # env, and `SECRETS_DIRS=/a:/b` is not JSON.
    dirs: Annotated[list[str], NoDecode] = Field(
        default_factory=list, description="Directories of secrets. First match wins."
    )
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
    transport: Literal["http", "stdio"] = Field(
        "http", description="http, or stdio for one local client."
    )
    host: str = Field("0.0.0.0", description="Address to listen on.")
    port: int = Field(8000, description="Port to listen on.")
    log_level: str = Field("INFO", description="DEBUG, INFO, WARNING or ERROR.")
    route_prefix: str = Field(
        "/", description="Path the whole server is mounted under."
    )
    public_base_url: str | None = Field(
        None, description="Where browsers reach this server, for links."
    )
    auth: AuthSettings = Field(
        default_factory=AuthSettings,
        description="The bearer token every request needs.",
    )
    grid: GridSettings = Field(
        default_factory=GridSettings, description="The Selenium Grid it drives."
    )
    session: SessionSettings = Field(
        default_factory=SessionSettings,
        description="How sessions are kept, and how new browsers open.",
    )
    redis: RedisSettings = Field(
        default_factory=RedisSettings,
        description="The session store\u2019s connection.",
    )
    flow: FlowSettings = Field(
        default_factory=FlowSettings, description="Saved flows and kept files."
    )
    secrets: SecretsSettings = Field(
        default_factory=SecretsSettings, description="Where secrets are read from."
    )
    skill: SkillSettings = Field(
        default_factory=SkillSettings, description="The embedded agent skill."
    )
    apps: AppsSettings = Field(
        default_factory=AppsSettings,
        description="MCP Apps views, for hosts that draw them.",
    )


def _is_section(info: FieldInfo) -> bool:
    return isinstance(info.annotation, type) and issubclass(info.annotation, Section)


SECTION_ORDER = ["server"] + [
    n for n, i in Settings.model_fields.items() if _is_section(i)
]
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
