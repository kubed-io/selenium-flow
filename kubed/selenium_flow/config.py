"""Every setting this server reads: one schema, three places to set it.

A setting's path is its name, and the other two spellings are derived from it:
`redis.host` is `REDIS_HOST` in the environment and `--redis-host` on the
command line. Precedence is default < config < env < args.

The models here read nothing. `Settings()` is the defaults, which is what a
test wants. `load()`, below, is what reads the file, the environment and the
command line.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import EnvSettingsSource, NoDecode

from .core.browser import DEFAULT_GRID_URL, normalize_browser
from .errors import without_userinfo

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
        "memory",
        description="memory; redis.host or redis.url switches it to redis.",
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
            value = value.split(os.pathsep)
        if not isinstance(value, list):
            # Not a string and not a list: leave it for pydantic's own
            # list[str] check to reject (as a clean ConfigError), rather than
            # crashing here on something we can't iterate.
            return value
        # A blank/whitespace *string* entry is dropped, never an error:
        # `dirs: [""]` means no dirs, same as an unset SECRETS_DIRS — one code
        # path keeps the YAML list form consistent with the env/CLI string
        # form instead of letting it bypass this and hand FilesystemSource("")
        # the cwd. A non-string element is passed through untouched so
        # pydantic's list[str] validation is what reports it, not us.
        result = []
        for part in value:
            if isinstance(part, str):
                part = part.strip()
                if not part:
                    continue
            result.append(part)
        return result

    @field_validator("entries")
    @classmethod
    def _names(cls, entries):
        from .flows.library import valid_name

        for name in entries:
            valid_name(name, "secret name")
        return entries


class McpSettings(Section):
    skill: bool = Field(True, description="Serve the agent skill as resources.")
    apps: bool = Field(True, description="Offer MCP Apps views.")


class Settings(Section):
    config_file: str | None = Field(None, description="The YAML file read at start.")
    transport: Literal["http", "stdio"] = Field(
        "http", description="http, or stdio for one local client."
    )
    host: str = Field("0.0.0.0", description="Address to listen on.")
    port: int = Field(8000, description="Port to listen on.")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO", description="DEBUG, INFO, WARNING or ERROR."
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value):
        # Case-folded before the Literal check, so `LOG_LEVEL=debug` (the
        # logging module itself is case-sensitive) still works — and a real
        # typo is a ConfigError at boot, not a traceback from `logging` once
        # something tries to log.
        return value.upper() if isinstance(value, str) else value

    # Moves the WHOLE server, so the default is "no prefix" rather than a name
    # for one tree (§F1.11). `/` means the same thing and is what an operator
    # types when they mean it.
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
    mcp: McpSettings = Field(
        default_factory=McpSettings,
        description="What MCP clients are offered beyond tools.",
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


SOURCES = ("default", "config", "env", "args")


class ConfigError(ValueError):
    """A configuration this server refuses to start on.

    The message says what and where.
    """


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
        # A generated flag is a name someone will type, and argparse's default
        # prefix matching would let `--redis-h` silently mean `--redis-host` —
        # until a second flag starts the same way and it silently means
        # something else instead.
        allow_abbrev=False,
    )
    for leaf in leaves():
        shown = "unset" if leaf.default in (None, "", []) else leaf.default
        help_text = (
            f"{leaf.description} (env: {leaf.env} · config: {leaf.path}"
            f" · default: {shown})"
        )
        built.add_argument(
            leaf.flag,
            dest=leaf.path,
            metavar=leaf.path.rsplit(".", 1)[-1].upper(),
            help=help_text.replace("%", "%%"),
        )
    return built


def _is_blank(value: Any) -> bool:
    """The value every layer agrees means "not set": `${VAR:-}`'s empty
    string from env, a bare `--flag ""` on the command line, and, in the
    config file, any string that is empty once whitespace is stripped — a
    YAML author can quote a blank the same way. One predicate, so all three
    layers drop it the same way instead of each rolling its own check.
    """
    return isinstance(value, str) and not value.strip()


class _Environment(EnvSettingsSource):
    """pydantic-settings' env reader, over a mapping we hand it.

    `_load_env_vars` is its one hook for where variables come from. Overriding
    it keeps a test's environment a plain dict, and keeps `os.environ` out of
    every module but this one and secrets.py.
    """

    def __init__(self, environ: Mapping[str, str]):
        self._environ = environ
        super().__init__(
            Settings,
            case_sensitive=False,
            env_nested_delimiter="_",
            env_nested_max_split=1,
            # `REDIS_URL=""` is what `${REDIS_URL:-}` sends when compose leaves
            # it unset, not absence — and a blank AUTH_TOKEN must read as "no
            # auth", not as an empty credential. Empty is "not set" everywhere.
            env_ignore_empty=True,
        )

    def _load_env_vars(self):
        # A bare section name (SESSION, REDIS, ...) would otherwise be handed
        # to pydantic-settings as a value for that whole section, which it
        # tries to JSON-decode and raises SettingsError on before `_known()`
        # ever gets a chance to filter it out as unknown.
        #
        # `env_ignore_empty` on the base class has no effect here: it is only
        # applied inside the default `_load_env_vars`'s call to
        # `parse_env_vars`, which this override replaces. So empty values are
        # dropped here instead — `REDIS_URL=""` is what `${REDIS_URL:-}` sends
        # when compose leaves it unset, not a value of its own.
        sections = set(SECTION_ORDER) - {"server"}
        return {
            key.lower(): value
            for key, value in self._environ.items()
            if key.lower() not in sections and not _is_blank(value)
        }


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
        # A directory exists, so "does not exist" would be a lie about what is
        # actually wrong with it.
        reason = "is not a file" if file.exists() else "does not exist"
        raise ConfigError(f"config file {path} {reason}")
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        # Never the parser's message: it quotes the offending line, and that
        # line may be an inline secret value.
        raise ConfigError(f"config file {path} is not valid YAML{where}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"config file {path} cannot be read: {type(exc).__name__}"
        ) from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"config file {path} must be a mapping of sections, "
            f"not a {type(data).__name__}"
        )
    if "config_file" in data:
        raise ConfigError(
            f"config_file cannot be set inside the config file it names ({path})"
        )
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


def _drop_blank_leaves(data: dict) -> dict:
    """A blank leaf value in the config file reads as unset, like env and args.

    Env and args already drop these before `_has`/`_merge` ever see them; the
    file layer did not, so `redis: {host: ""}` used to make `redis.host`'s
    source "config" and drive the store to redis on an empty host. Scoped to
    known leaves only — `secrets.entries` is FILE_ONLY and excluded from
    `leaves()` already (its own models validate it), and `secrets.dirs` is a
    list, never a str, so a blank entry inside it stays the `dirs`
    validator's job, not this one's.
    """
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in data.items()}
    for leaf in leaves():
        head, _, tail = leaf.path.partition(".")
        if tail:
            section = out.get(head)
            if isinstance(section, dict) and _is_blank(section.get(tail)):
                del section[tail]
        elif _is_blank(out.get(head)):
            del out[head]
    return out


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
    by_path = {leaf.path: leaf for leaf in leaves()}
    for error in exc.errors(include_input=False, include_url=False):
        loc = [str(part) for part in error["loc"]]
        if loc and loc[0] in SECTION_ORDER:
            leaf = ".".join(loc[:2])
        else:
            leaf = loc[0] if loc else ""
        source = sources.get(leaf, "config")
        where = {
            "config": f"in {path}",
            "env": f"from env {by_path[leaf].env}" if leaf in by_path else "from env",
            "args": (
                f"from {by_path[leaf].flag}"
                if leaf in by_path
                else "on the command line"
            ),
            "default": "as a default",
        }[source]
        lines.append(f"{'.'.join(loc)}: {error['msg']} ({where})")
    return "; ".join(lines)


def load(
    argv: list[str] | None = None, environ: Mapping[str, str] | None = None
) -> Loaded:
    """The settings this process should run with, and where each one came from."""
    environ = os.environ if environ is None else environ
    raw_args = vars(parser().parse_args(argv))
    # An empty string on the command line (`--auth-token ""`) is "not set", the
    # same as an empty env var — never a value of its own, sensitive or not.
    args = _nest({k: v for k, v in raw_args.items() if not _is_blank(v)})
    env = _known(_Environment(environ)())
    named = args.get("config_file") or env.get("config_file")
    path = str(named).strip() if named else None
    # Blank is unset in the file layer too, same as env and args — a quoted
    # `""` (or whitespace) does not mean "use this empty value".
    file = _drop_blank_leaves(_read_file(path)) if path else {}

    layers = {"args": args, "env": env, "config": file}
    # Highest precedence first: SOURCES is default < config < env < args, so its
    # reverse (minus "default", which is the fallback below) is the search order.
    precedence = tuple(reversed(SOURCES[1:]))
    sources = {
        leaf.path: next(
            (name for name in precedence if _has(layers[name], leaf.path)),
            "default",
        )
        for leaf in leaves()
    }
    merged = _merge(file, env, args)
    if sources["session.store"] == "default":
        # Today's rule, kept: asking for Redis by naming it is asking for the
        # Redis store. Explicitly `memory` still wins.
        redis_sources = (sources["redis.url"], sources["redis.host"])
        wanted = any(name != "default" for name in redis_sources)
        merged.setdefault("session", {})["store"] = "redis" if wanted else "memory"
        if wanted:
            # Provenance follows whichever of redis.url/redis.host was set
            # with the highest precedence; memory needs no such update, since
            # memory IS the default.
            sources["session.store"] = next(
                name for name in precedence if name in redis_sources
            )
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
        leaf.path: (
            "default" if value_of(settings, leaf.path) == leaf.default else "args"
        )
        for leaf in leaves()
    }


# Non-sensitive settings that are still URLs, and so may carry userinfo
# (`GRID_URL=http://user:pass@hub:4444`). `auth.token`/`redis.url`/
# `redis.password` are handled separately below, as `sensitive`: this set is
# for the ones that are shown in full, and so must have their credentials
# stripped first.
#
# `without_userinfo`, not `browser.public_url`: the latter also rewrites the
# path (`rstrip("/")`), which turns `grid.console_url`'s default `/` into `""`
# on the Settings tab even though the live server still serves it at `/`.
URL_LEAVES = {"grid.url", "grid.console_url", "public_base_url"}


def describe(settings: Settings, sources: Mapping[str, str]) -> dict:
    """The Settings tab's payload. Never a sensitive value, and no secret entries.

    A row from the config file carries the `config` source, not the file's
    path — that path is already shown once, as the value of the `config_file`
    setting itself.
    """
    rows: dict[str, list[dict]] = {name: [] for name in SECTION_ORDER}
    for leaf in leaves():
        value = value_of(settings, leaf.path)
        if leaf.path in URL_LEAVES and value:
            value = without_userinfo(str(value))
        # `name` is what the row shows: the card title is already the section.
        row: dict[str, Any] = {
            "key": leaf.path,
            "name": leaf.path.rsplit(".", 1)[-1],
            "description": leaf.description,
        }
        if leaf.sensitive:
            row.update(
                value=None,
                source=sources.get(leaf.path, "default"),
                sensitive=True,
                # Not `value is not None`: a blank inline value or an empty env
                # read (before this loader dropped it) is not a credential
                # either, and the pill would otherwise read "set" on nothing.
                set=value is not None and bool(value.get_secret_value()),
            )
        else:
            row.update(value=value, source=sources.get(leaf.path, "default"))
        rows[leaf.section].append(row)
    return {
        "sections": [
            {
                "name": name,
                "description": SECTION_DESCRIPTIONS[name],
                "settings": rows[name],
            }
            for name in SECTION_ORDER
        ],
    }
