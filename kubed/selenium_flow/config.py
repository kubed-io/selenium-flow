"""Every setting this server reads, as sections registered with kubed-mcp.

kubed-mcp reads the file, the environment and the flags, in that order of
precedence, and says where each value came from. This module holds what is
this server's own: each section's names, written out (`redis.host` is
`REDIS_HOST` and `--redis-host`; `serving`'s drop the section, so
`serving.port` is `PORT`), the rules a section keeps, and the Settings tab.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import kubed.mcp
from kubed.mcp import Config, Field, NoDecode, Source, config, config_schema
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    JsonValue,
    SecretStr,
    StrictBool,
    field_validator,
    model_validator,
)

from .core.defaults import DEFAULT_GRID_URL, normalize_browser
from .core.session_caps import check as check_capabilities
from .names import INBOX_DIR, WORKSPACES_DIR, valid_name
from .urls import without_userinfo


def _parts(value, sep: str):
    """A list given as one string, split on ``sep``, each part trimmed and a
    blank one dropped. A non-string element, or a value that is neither a
    string nor a list, is left for pydantic's list[str] check to refuse."""
    if isinstance(value, str):
        value = value.split(sep)
    if not isinstance(value, list):
        return value
    return [
        part.strip() if isinstance(part, str) else part
        for part in value
        if not (isinstance(part, str) and not part.strip())
    ]


# NoDecode: kubed-mcp would otherwise JSON-decode a list read from env, and
# `SECRETS_DIRS=/a:/b` is not JSON.
CommaList = Annotated[list[str], NoDecode, BeforeValidator(lambda v: _parts(v, ","))]
# A blank entry is dropped, so `dirs: [""]` is no dirs and never
# FilesystemSource("") on the cwd.
PathList = Annotated[
    list[str], NoDecode, BeforeValidator(lambda v: _parts(v, os.pathsep))
]
# A path setting, where blank is unset.
OptionalPath = Annotated[
    str | None, AfterValidator(lambda v: (v or "").strip() or None)
]


class Section(BaseModel):
    """A group of settings. Unknown keys are refused, so a typo stops the boot."""

    # A rejected value can be a credential (an allowed URL's userinfo, a
    # secret's inline value), and pydantic's errors quote `input_value`.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class FromFile(Section):
    file: str
    masked: StrictBool = True


class FromEnv(Section):
    env: str
    masked: StrictBool = True


class FromValue(Section):
    value: SecretStr
    masked: StrictBool = True


class KeySettings(Section):
    """Settings for a key a directory provides; its value stays the directory's."""

    masked: StrictBool


# The key present is the discriminator. Each member forbids extra keys, so
# `{file, env}` and `{}` match none of them and are refused. `masked: false`
# leaves the key's value out of the scrub, for a value that is not secret. It is
# strict: a stray "no" or 0 must not unmask a key.
KeyRef = FromFile | FromEnv | FromValue | KeySettings


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


@config("auth")
class AuthSettings(Section):
    token: SecretStr | None = Field(
        None,
        doc="Bearer token for every request. Unset is open.",
        env="AUTH_TOKEN",
        arg="--auth-token",
    )


@config("oidc")
class OidcSettings(Section):
    # Every cross-field rule lives in oidc_problem(), not a model validator: it
    # is checked when the server is built, so its message is its own, where a
    # root validator's would name whichever layer was highest.
    issuer: str | None = Field(
        None,
        doc="The issuer a JWT must name; setting it turns OIDC on.",
        env="OIDC_ISSUER",
        arg="--oidc-issuer",
    )
    audience: str | None = Field(
        None,
        doc="The audience a JWT must include.",
        env="OIDC_AUDIENCE",
        arg="--oidc-audience",
    )
    jwks_uri: str | None = Field(
        None,
        doc="Where the issuer publishes its signing keys.",
        env="OIDC_JWKS_URI",
        arg="--oidc-jwks-uri",
    )
    roles: CommaList = Field(
        default_factory=list,
        doc="A JWT must hold one of these roles; empty checks none.",
        env="OIDC_ROLES",
        arg="--oidc-roles",
    )
    roles_claim: str = Field(
        "roles",
        doc="The claim holding the roles; dots walk into objects.",
        env="OIDC_ROLES_CLAIM",
        arg="--oidc-roles-claim",
    )
    client_id: str | None = Field(
        None,
        doc="The admin UI's public client; setting it offers Sign in with OIDC.",
        env="OIDC_CLIENT_ID",
        arg="--oidc-client-id",
    )
    admin_roles: CommaList = Field(
        default_factory=list,
        doc="A JWT from the admin UI holding one of these is the admin.",
        env="OIDC_ADMIN_ROLES",
        arg="--oidc-admin-roles",
    )

    @field_validator("issuer", "jwks_uri")
    @classmethod
    def _http(cls, value):
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("must be an http(s) URL")
        return value


@config("grid")
class GridSettings(Section):
    url: str = Field(
        DEFAULT_GRID_URL,
        doc="The Selenium Grid hub.",
        env="GRID_URL",
        arg="--grid-url",
    )
    console_url: str = Field(
        "/",
        doc="Where the Grid console tab is framed from.",
        env="GRID_CONSOLE_URL",
        arg="--grid-console-url",
    )


@config("workspace")
class WorkspaceSettings(Section):
    store: Literal["memory", "redis"] = Field(
        "memory",
        doc="memory; redis.host or redis.url switches it to redis.",
        env="WORKSPACE_STORE",
        arg="--workspace-store",
    )
    ttl: int = Field(
        86400,
        doc="Seconds a workspace is kept after its last use.",
        env="WORKSPACE_TTL",
        arg="--workspace-ttl",
    )


def _json_object(value):
    """A mapping setting given as a string: a JSON object."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError("must be a JSON object") from None
    if not isinstance(value, dict):
        raise ValueError("must be a JSON object")
    return value


@config("session")
class SessionSettings(Section):
    browser: Literal["chrome", "firefox"] | None = Field(
        None,
        doc="chrome or firefox, for new sessions.",
        env="SESSION_BROWSER",
        arg="--session-browser",
    )
    width: int | None = Field(
        None,
        doc="Window width for new sessions.",
        env="SESSION_WIDTH",
        arg="--session-width",
    )
    height: int | None = Field(
        None,
        doc="Window height for new sessions.",
        env="SESSION_HEIGHT",
        arg="--session-height",
    )
    page_load_timeout: int | None = Field(
        None,
        doc="Seconds a page may take to load.",
        env="SESSION_PAGE_LOAD_TIMEOUT",
        arg="--session-page-load-timeout",
    )
    script_timeout: int | None = Field(
        None,
        doc="Seconds a script may run.",
        env="SESSION_SCRIPT_TIMEOUT",
        arg="--session-script-timeout",
    )
    # Not an open's default: `FROM_CONFIG` does not list it, so no session
    # remembers it.
    keep_alive: int = Field(
        900,
        ge=0,
        le=3600,
        doc="Seconds a browser is kept alive after its last call, "
        "0 for off; each holds a Grid slot.",
        env="SESSION_KEEP_ALIVE",
        arg="--session-keep-alive",
    )
    sub_ttl: int = Field(
        3600,
        gt=0,
        doc="Seconds a sub-session's record is kept after its last use.",
        env="SESSION_SUB_TTL",
        arg="--session-sub-ttl",
    )
    version: str | None = Field(
        None,
        doc="The browser version new sessions ask for, such as 152.",
        env="SESSION_VERSION",
        arg="--session-version",
    )
    # kubed-mcp decodes the JSON from env or a flag; a string in the file is
    # parsed below, so a bad one is refused naming this setting.
    capabilities: dict[str, JsonValue] = Field(
        default_factory=dict,
        doc="W3C capabilities every new session sends, under the "
        "caller's, as a JSON object.",
        env="SESSION_CAPABILITIES",
        arg="--session-capabilities",
    )

    @field_validator("browser", mode="before")
    @classmethod
    def _browser(cls, value):
        return normalize_browser(value) if value not in (None, "") else None

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities_json(cls, value):
        return _json_object(value)

    @field_validator("capabilities")
    @classmethod
    def _capabilities_owned(cls, value):
        # Refused at boot, naming the option, instead of at every open.
        check_capabilities(value, where="session.capabilities")
        return value


@config("redis")
class RedisSettings(Section):
    url: SecretStr | None = Field(
        None,
        doc="A full Redis URL, instead of host and port.",
        env="REDIS_URL",
        arg="--redis-url",
    )
    host: str = Field(
        "localhost", doc="Redis host.", env="REDIS_HOST", arg="--redis-host"
    )
    port: int = Field(6379, doc="Redis port.", env="REDIS_PORT", arg="--redis-port")
    db: int = Field(0, doc="Redis database number.", env="REDIS_DB", arg="--redis-db")
    username: str | None = Field(
        None, doc="Redis username.", env="REDIS_USERNAME", arg="--redis-username"
    )
    password: SecretStr | None = Field(
        None, doc="Redis password.", env="REDIS_PASSWORD", arg="--redis-password"
    )
    ssl: bool = Field(
        False, doc="Connect to Redis over TLS.", env="REDIS_SSL", arg="--redis-ssl"
    )
    prefix: str = Field(
        "selenium-flow:workspace:",
        doc="Prefix on every key, so a shared database is safe.",
        env="REDIS_PREFIX",
        arg="--redis-prefix",
    )


@config("data")
class DataSettings(Section):
    dir: OptionalPath = Field(
        None,
        doc=(
            "Where workspaces and recordings live. Unset turns flows, kept files "
            "and recordings off."
        ),
        env="DATA_DIR",
        arg="--data-dir",
    )


@config("capture")
class CaptureSettings(Section):
    ttl: int = Field(
        10_800,
        ge=60,
        doc="Seconds captured console and network entries are kept after "
        "the last one arrived.",
        env="CAPTURE_TTL",
        arg="--capture-ttl",
    )


@config("live")
class LiveSettings(Section):
    """The admin UI's live view: selenium-flow proxies a session's VNC stream
    and answers its password itself."""

    enabled: bool = Field(
        True,
        doc="Offer the admin UI's live view of a browser.",
        env="LIVE_ENABLED",
        arg="--live-enabled",
    )
    password_file: OptionalPath = Field(
        None,
        doc="The file holding the Grid's VNC password, read at every connect.",
        env="LIVE_PASSWORD_FILE",
        arg="--live-password-file",
    )
    # At least 15: the page's heartbeat is fixed at 10 s, so a shorter grace
    # would drop a viewer whose tab is in plain sight.
    grace: int = Field(
        30,
        ge=15,
        doc="Seconds a live viewer may send no presence before it is dropped.",
        env="LIVE_GRACE",
        arg="--live-grace",
    )
    # At least 30: two of the progress heartbeat's 15 s (`mcp/progress.py`), and
    # under Claude Code's two-minute move of a slow call to the background.
    await_cap: int = Field(
        90,
        ge=30,
        doc='Seconds a blocking await_control waits before it answers "call again".',
        env="LIVE_AWAIT_CAP",
        arg="--live-await-cap",
    )


@config("recording")
class RecordingSettings(Section):
    enabled: bool = Field(
        False,
        doc="The Grid's recordings reach recording.dir.",
        env="RECORDING_ENABLED",
        arg="--recording-enabled",
    )
    dir: OptionalPath = Field(
        None,
        doc="Where the Grid's recordings arrive. Unset is data.dir/recordings.",
        env="RECORDING_DIR",
        arg="--recording-dir",
    )
    wait: int = Field(
        600,
        ge=30,
        doc="Seconds after a browser ends to wait for its recording.",
        env="RECORDING_WAIT",
        arg="--recording-wait",
    )
    watch: Literal["auto", "events", "poll"] = Field(
        "auto",
        doc="auto, events or poll: how recording.dir is watched.",
        env="RECORDING_WATCH",
        arg="--recording-watch",
    )
    poll: int = Field(
        1000,
        ge=200,
        doc="Milliseconds between looks, when polling.",
        env="RECORDING_POLL",
        arg="--recording-poll",
    )
    settle: int = Field(
        10,
        ge=0,
        doc="Seconds a finished recording sits unchanged before it is "
        "filed, so its transport is done with it.",
        env="RECORDING_SETTLE",
        arg="--recording-settle",
    )


@config("secrets")
class SecretsSettings(Section):
    dirs: PathList = Field(
        default_factory=list,
        doc="Directories of secrets. First match wins.",
        env="SECRETS_DIRS",
        arg="--secrets-dirs",
    )
    # Structure, not a value: only the file sets it, so it has no env name and
    # no flag.
    entries: dict[str, SecretEntry] = Field(
        default_factory=dict, doc="Secrets defined in the config file."
    )

    @field_validator("entries")
    @classmethod
    def _names(cls, entries):
        for name in entries:
            valid_name(name, "secret name")
        return entries


@config("mcp")
class McpSettings(Section):
    apps: bool = Field(
        True, doc="Offer MCP Apps views.", env="MCP_APPS", arg="--mcp-apps"
    )


@config("security")
class SecuritySettings(Section):
    frame_ancestors: CommaList = Field(
        default_factory=list,
        doc=(
            "Origins allowed to frame the admin page, e.g. a Nextcloud or "
            "Grafana host; empty forbids framing."
        ),
        env="SECURITY_FRAME_ANCESTORS",
        arg="--security-frame-ancestors",
    )

    @field_validator("frame_ancestors")
    @classmethod
    def _origins(cls, origins):
        # Each entry is pasted into a Content-Security-Policy header, so one
        # that carries a `;`, a space or a non-ASCII character would add a
        # directive or crash the response, and `*` or `none` would defeat it.
        # A refused entry is quoted without its userinfo: it may be a credential.
        for origin in origins:
            if origin in ("*", "none", "'none'"):
                raise ValueError(
                    f"{origin!r} is not an origin: list the hosts that may "
                    "frame the page; the list is empty by default, which "
                    "forbids framing"
                )
            if not _ORIGIN.fullmatch(origin):
                raise ValueError(
                    f"{without_userinfo(origin)!r} is not an origin: write "
                    "scheme://host[:port], or scheme://*.host for every "
                    "subdomain, in printable ASCII"
                )
        return origins


_ORIGIN = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?"
    r"(:[0-9]{1,5})?"
)


@config("serving")
class ServingSettings(kubed.mcp.ServingSettings):
    """kubed-mcp's serving section with this server's defaults: it listens on
    every address over HTTP, and offers only `http` and `stdio`."""

    transport: Literal["http", "stdio"] = Field(
        "http",
        doc="http, or stdio for one local client.",
        env="TRANSPORT",
        arg="--transport",
    )
    host: str = Field(
        "0.0.0.0", doc="Address to listen on.", env="HOST", arg="--host"
    )
    port: int = Field(
        8000, ge=1, le=65535, doc="Port to listen on.", env="PORT", arg="--port"
    )
    # Case-folded by the built-in's own validator, before the Literal check.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO",
        doc="DEBUG, INFO, WARNING or ERROR.",
        env="LOG_LEVEL",
        arg="--log-level",
    )


def _held(section: Any, name: str) -> Any:
    """What a section given as a mapping holds under ``name``, or what an
    instance's field holds when it was set, else None."""
    if isinstance(section, Mapping):
        return section.get(name)
    if isinstance(section, BaseModel) and name in section.model_fields_set:
        return getattr(section, name)
    return None


def _named(value: Any) -> bool:
    """Whether a Redis address is there: None and blank are not. kubed-mcp drops
    blanks from the file, env and flags, but not from values given in code."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value is not None and not (isinstance(value, str) and not value.strip())


def derive_store(data: Any) -> Any:
    """``data`` asking for the Redis store when it names Redis and sets no store.

    Naming Redis is a `url` or `host` under `redis`; an explicit
    `workspace.store`, `memory` included, wins. A section instance is copied,
    never changed. The result adds no key but `workspace`, and a second call
    returns it as it is. Anything else is returned as it came.
    """
    if not isinstance(data, Mapping):
        return data
    redis = data.get("redis")
    if not any(_named(_held(redis, name)) for name in ("url", "host")):
        return data
    workspace = data.get("workspace", {})
    if isinstance(workspace, Mapping):
        if "store" in workspace:
            return data
        derived: Any = {**workspace, "store": "redis"}
    elif isinstance(workspace, BaseModel):
        if "store" in workspace.model_fields_set:
            return data
        derived = workspace.model_copy(update={"store": "redis"})
    else:
        # Not a section: the validation names it.
        return data
    return {**data, "workspace": derived}


@config()
class ServerSettings(Section):
    """The root: the keys at the top of the config."""

    # Moves the WHOLE server, so the default is "no prefix" rather than a name
    # for one tree. `/` means the same thing and is what an operator types when
    # they mean it.
    route_prefix: str = Field(
        "/",
        doc="Path the whole server is mounted under.",
        env="ROUTE_PREFIX",
        arg="--route-prefix",
    )
    public_base_url: str | None = Field(
        None,
        doc="Where browsers reach this server, for links.",
        env="PUBLIC_BASE_URL",
        arg="--public-base-url",
    )
    link_ttl: int = Field(
        3600,
        ge=60,
        doc="Seconds a file link opens for, at least 60.",
        env="LINK_TTL",
        arg="--link-ttl",
    )

    @model_validator(mode="before")
    @classmethod
    def _store(cls, data: Any) -> Any:
        return derive_store(data)


# Each key and its section, in the Settings tab's order. kubed-mcp composes
# its own telemetry and delivery.
SECTION_CLASSES: dict[str, type[BaseModel]] = {
    "auth": AuthSettings,
    "oidc": OidcSettings,
    "grid": GridSettings,
    "workspace": WorkspaceSettings,
    "session": SessionSettings,
    "redis": RedisSettings,
    "data": DataSettings,
    "recording": RecordingSettings,
    "capture": CaptureSettings,
    "live": LiveSettings,
    "secrets": SecretsSettings,
    "mcp": McpSettings,
    "security": SecuritySettings,
    "serving": ServingSettings,
    "telemetry": kubed.mcp.TelemetrySettings,
    "delivery": kubed.mcp.DeliverySettings,
}

# What `QuickMCP(sections=)` registers: the root, then this server's own.
SECTIONS: tuple[type[BaseModel], ...] = (
    ServerSettings,
    *(
        cls
        for key, cls in SECTION_CLASSES.items()
        if key not in ("telemetry", "delivery")
    ),
)

SECTION_ORDER = ["server", *SECTION_CLASSES]
SECTION_DESCRIPTIONS = {
    "server": "Where it is mounted, and the file it read.",
    "auth": "The bearer token every request needs.",
    "oidc": (
        "Accept a JWT from an OIDC issuer beside the token, "
        "on /mcp and in the admin UI."
    ),
    "grid": "The Selenium Grid it drives.",
    "workspace": "How workspaces are kept.",
    "session": "How a session opens a browser, and how long it is kept alive.",
    "redis": "The workspace store\u2019s connection.",
    "data": "Where workspaces, their flows and files, and recordings live.",
    "recording": "Video of a browser's whole life, from the Grid's recorder.",
    "capture": "Console and network capture, for open_session(capture=true).",
    "live": "The admin UI's live view of a browser.",
    "secrets": "Where secrets are read from.",
    "mcp": "What MCP clients are offered beyond tools.",
    "security": "Who may frame the admin page.",
    "serving": "Where it listens, and what it logs.",
    "telemetry": "Profiles, traces and metrics, run by kubed-mcp; all off unless set.",
    "delivery": "What a request gets when it sends no delivery header.",
}


@dataclass(frozen=True)
class Leaf:
    """One setting: its path, its two names, and what the schema says of it."""

    path: str
    env: str
    flag: str
    description: str
    default: Any
    sensitive: bool

    @property
    def section(self) -> str:
        return self.path.split(".", 1)[0] if "." in self.path else "server"


@functools.cache
def _schema() -> dict:
    return config_schema(*SECTIONS)


def _leaf(path: str, prop: dict) -> Leaf:
    return Leaf(
        path=path,
        env=prop["env"],
        flag=prop["arg"],
        description=prop.get("description") or "",
        default=prop.get("default"),
        sensitive=prop.get("sensitive") is True,
    )


def _section_properties(schema: dict, key: str) -> dict:
    """The properties of the section under ``key``, through its ``$ref``."""
    prop = schema["properties"][key]
    ref = prop.get("$ref") or next(
        part["$ref"] for part in prop.get("allOf", ()) if "$ref" in part
    )
    return schema["$defs"][ref.rsplit("/", 1)[-1]]["properties"]


def leaves() -> list[Leaf]:
    """Every setting that has an env name and a flag, in schema order.

    Read from kubed-mcp's schema of the sections, so the names, the docs, the
    defaults it writes and what is sensitive are kubed-mcp's own reading.
    `config_file` comes first: kubed-mcp reserves its names, so it has no field.
    """
    schema = _schema()
    found = [
        Leaf(
            "config_file",
            "CONFIG_FILE",
            "--config-file",
            "The YAML file read at start.",
            None,
            False,
        )
    ]
    for name, prop in schema["properties"].items():
        if name not in SECTION_CLASSES and "env" in prop:
            found.append(_leaf(name, prop))
    for key in SECTION_CLASSES:
        for name, prop in _section_properties(schema, key).items():
            if "env" in prop:
                found.append(_leaf(f"{key}.{name}", prop))
    return found


def help_text() -> str:
    """What `--help` prints: one line per setting, its three spellings and default.

    The parser only formats. kubed-mcp parses the flags.
    """
    built = argparse.ArgumentParser(
        prog="selenium-flow",
        description="Drive a Selenium Grid browser over MCP and HTTP.",
        allow_abbrev=False,
    )
    for leaf in leaves():
        shown = "unset" if leaf.default in (None, "", [], {}) else leaf.default
        text = (
            f"{leaf.description} (env: {leaf.env} · config: {leaf.path}"
            f" · default: {shown})"
        )
        built.add_argument(
            leaf.flag,
            metavar=leaf.path.rsplit(".", 1)[-1].upper(),
            help=text.replace("%", "%%"),
        )
    return built.format_help()


def value_of(settings: Config, path: str) -> Any:
    value: Any = settings
    for part in path.split("."):
        value = getattr(value, part)
    return value


class ConfigError(ValueError):
    """A configuration this server refuses to start on.

    The message says what and where.
    """


def config_file(sources: Mapping[str, Source]) -> str | None:
    """The file the config was read from: the first value from a file names it.
    A file that set nothing reads as none."""
    return next(
        (source.name for source in sources.values() if source.kind == "file"), None
    )


def oidc_problem(settings: Config) -> str | None:
    """Why this server's OIDC cannot run, or None. Checked after every layer merges."""
    oidc = settings.oidc
    if bool(oidc.client_id) != bool(oidc.admin_roles):
        # An empty list would let nobody in or, read the other way, everybody.
        return "oidc.client_id and oidc.admin_roles are set together or not at all"
    named = [oidc.issuer, oidc.audience, oidc.jwks_uri]
    if not any(named):
        if oidc.client_id:
            return (
                "oidc.client_id needs oidc.issuer, oidc.audience and "
                "oidc.jwks_uri: the admin UI signs in with the issuer /mcp trusts"
            )
        return None
    if not all(named):
        # An issuer without an audience accepts any token that issuer ever minted.
        return (
            "oidc.issuer, oidc.audience and oidc.jwks_uri "
            "are set together or not at all"
        )
    if not (settings.auth.token and settings.auth.token.get_secret_value()):
        # The token is the admin login and the signing key; without it the REST
        # and admin routes are open, and OIDC must not be a way to get there.
        return "oidc needs auth.token: the token stays the admin login"
    return None


def recording_problem(settings: Config) -> str | None:
    """Why recording cannot run, or None. Checked after every layer merges."""
    if settings.recording.enabled and not settings.data.dir:
        return (
            "recording.enabled needs data.dir: a recording is filed into its "
            "workspace"
        )
    if settings.recording.enabled:
        # Resolved as the server opens them: relative to the working directory.
        inbox = Path(recording_dir(settings)).resolve()
        workspaces = (Path(settings.data.dir) / WORKSPACES_DIR).resolve()
        if (
            inbox == workspaces
            or workspaces in inbox.parents
            or inbox in workspaces.parents
        ):
            return (
                f"recording.dir {inbox} must not overlap {workspaces}: inside it, "
                "unfinished videos show as a workspace's files; above it, the "
                "collector sweeps every workspace"
            )
    return None


def recording_dir(settings: Config) -> str | None:
    """The inbox: recording.dir, else data.dir/recordings, else None."""
    if settings.recording.dir:
        return settings.recording.dir
    if settings.data.dir:
        return str(Path(settings.data.dir) / INBOX_DIR)
    return None


# Settings shown in full that are URLs, and so may carry userinfo
# (`GRID_URL=http://user:pass@hub:4444`). Stripped with `without_userinfo`:
# `urls.public_url` also trims the path, and would show `grid.console_url`'s
# default `/` as "".
URL_LEAVES = {
    "grid.url",
    "grid.console_url",
    "public_base_url",
    "oidc.issuer",
    "oidc.jwks_uri",
}


PROXY_KEYS = {"httpProxy", "sslProxy", "socksProxy", "ftpProxy"}


def _without_proxy_userinfo(value):
    """``value`` with ``user:pass@`` dropped from the W3C proxy keys, which are
    ``host:port`` with no scheme for `without_userinfo` to anchor on."""
    if isinstance(value, dict):
        return {
            key: re.sub(r"^[^/@\s]*@", "", item)
            if key in PROXY_KEYS and isinstance(item, str)
            else _without_proxy_userinfo(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_without_proxy_userinfo(item) for item in value]
    return value


# A W3C proxy key and its JSON string value, as a line of compact JSON writes it.
_PROXY_ENTRY = re.compile(
    r'"(?P<key>' + "|".join(sorted(PROXY_KEYS)) + r')":"(?P<value>(?:[^"\\]|\\.)*)"'
)


def _proxy_entry_without_userinfo(match: re.Match) -> str:
    key = match["key"]
    value = json.loads(f'"{match["value"]}"')
    value = _without_proxy_userinfo({key: value})[key]
    return f"{json.dumps(key)}:{json.dumps(value, ensure_ascii=False)}"


def without_config_userinfo(text: str) -> str:
    """``text``, a line that writes settings as JSON, with the userinfo cut out
    as the Settings tab cuts it: every URL's by `without_userinfo`, then a proxy
    key's ``user:pass@`` by `_without_proxy_userinfo`'s rule."""
    return _PROXY_ENTRY.sub(_proxy_entry_without_userinfo, without_userinfo(text))


# The tab's four labels for kubed-mcp's sources: code builds the server with
# arguments, and so does a hook.
SOURCE_LABELS = {
    "default": "default",
    "file": "config",
    "env": "env",
    "arg": "args",
    "code": "args",
    "hook": "args",
}


def describe(settings: Config, sources: Mapping[str, Source]) -> dict:
    """The Settings tab's payload. Never a sensitive value, and no secret entries.

    A row from the config file has the source `config`: the file's path is the
    `config_file` row's value.
    """
    rows: dict[str, list[dict]] = {name: [] for name in SECTION_ORDER}
    for leaf in leaves():
        if leaf.path == "config_file":
            value = config_file(sources)
            source = "config" if value else "default"
        else:
            value = value_of(settings, leaf.path)
            source = (
                SOURCE_LABELS[sources[leaf.path].kind]
                if leaf.path in sources
                else "default"
            )
        if leaf.path in URL_LEAVES and value:
            value = without_userinfo(str(value))
        elif leaf.path == "session.capabilities":
            # A proxy URL or a `--proxy-server=` argument can carry userinfo.
            value = _without_proxy_userinfo(
                json.loads(without_userinfo(json.dumps(value)))
            )
        # `name` is what the row shows: the card's title is the section.
        row: dict[str, Any] = {
            "key": leaf.path,
            "name": leaf.path.rsplit(".", 1)[-1],
            "description": leaf.description,
            "value": None if leaf.sensitive else value,
            "source": source,
        }
        if leaf.sensitive:
            # A blank value is not a credential either.
            set_ = value is not None and bool(value.get_secret_value())
            row.update(sensitive=True, set=set_)
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
