"""A workspace's own config: the file ``workspace.yaml`` in the root of its folder.

Only the admin writes it. It is read while the server runs, through a cache
keyed by the file's revision that holds the parsed result, valid or not, so a
warm read stats the file and parses nothing, and an edit on disk is seen at the
next read. A file that does not parse or validate is held with its reason: the
server refuses every call that names the workspace until an admin fixes it.

Every key the file may hold is in the model, and any other is refused naming it,
so the file can never hold a secret's value.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

import yaml
from cachetools import LRUCache
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..faults import Conflict
from ..flows.library import load_typed, yaml_complaint
from ..flows.store import WorkspaceLayout, open_regular
from ..flows.tags import Filter
from ..names import CONFIG_FILE, GLOBAL_WORKSPACE, InvalidName

log = logging.getLogger(__name__)

__all__ = ["CONFIG_FILE", "Configs", "Loaded", "WorkspaceConfig"]

OFF = "this server keeps no workspace folders: data.dir is unset"
REFUSED = (
    "this workspace's config is invalid: {reason}. An admin fixes it in the admin UI"
)
# Far beyond any real config; a bigger file is refused before it is read.
CAP = 2**20
# Workspaces whose parsed config is kept.
CACHED = 1024


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class FlowsConfig(_Strict):
    global_: list[str] | None = Field(None, alias="global")

    @field_validator("global_")
    @classmethod
    def _rows(cls, rows: list[str] | None) -> list[str] | None:
        if rows is not None:
            Filter.parse(rows)
        return rows


class WorkspaceConfig(_Strict):
    flows: FlowsConfig | None = None


# Every setting the file may hold, by path. Each defaults to unset (None).
SETTINGS = ("flows.global",)


class Loaded(NamedTuple):
    """What a read found: the config, or the reason the file is refused, and
    the file's revision. All None when there is no file."""

    config: WorkspaceConfig | None
    error: str | None
    revision: tuple[int, int, int] | None


NONE = Loaded(None, None, None)


def _problems(exc: ValidationError) -> str:
    """Each thing wrong with the file, by key, never quoting a value."""
    said = []
    for error in exc.errors(include_url=False):
        where = ".".join(str(part) for part in error["loc"])
        if error["type"] == "extra_forbidden":
            said.append(
                f"`{where}` is not a setting {CONFIG_FILE} holds: "
                f"it holds {', '.join(SETTINGS)}"
            )
        else:
            said.append(f"`{where}`: {error['msg'].removeprefix('Value error, ')}")
    return "; ".join(said)


def parse(text: str) -> WorkspaceConfig:
    """The config ``text`` holds, or a ValueError saying why it is refused."""
    try:
        data = load_typed(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{CONFIG_FILE} is not YAML: {yaml_complaint(exc)}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{CONFIG_FILE} must be a mapping of settings, such as `flows:`"
        )
    try:
        return WorkspaceConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_problems(exc)) from None


def _value(config: WorkspaceConfig | None, path: str):
    """One setting's value as the file sets it, or None."""
    value = config.model_dump(by_alias=True) if config is not None else {}
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


class Configs:
    """Every workspace's config, read just in time from its folder.

    ``layout`` is the flow store's (`WorkspaceLayout`), the one place a path is
    built, or None with no ``data.dir``: then there is no config anywhere.
    ``admin`` is where the admin UI is reached, for the link to a workspace's
    Config tab, or "" when the server does not know its public URL.
    """

    def __init__(self, layout: WorkspaceLayout | None, admin: str = ""):
        self._layout = layout
        self._admin = admin
        self._cache: LRUCache = LRUCache(maxsize=CACHED)
        self._lock = threading.Lock()
        # Told a workspace's name once its file is saved or removed, so its
        # record's expiry follows at once (`WorkspaceStore.retain`).
        self.retained: Callable[[str], None] = lambda _name: None

    @property
    def enabled(self) -> bool:
        return self._layout is not None

    def _path(self, name: str) -> Path:
        if self._layout is None:
            raise Conflict(OFF)
        return self._layout.config_path(name)

    def url(self, name: str) -> str | None:
        """The admin UI's view of this workspace's config, or None."""
        if not self._admin or self._layout is None:
            return None
        return f"{self._admin}/#/workspaces/{quote(name, safe='')}/config"

    # -- reads -----------------------------------------------------------------

    def load(self, name: str) -> Loaded:
        """This workspace's config as the file is now. One stat when warm."""
        if self._layout is None:
            return NONE
        path = self._path(name)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return NONE
        revision = (info.st_ino, info.st_size, info.st_mtime_ns)
        with self._lock:
            cached = self._cache.get(name)
        if cached is not None and cached.revision == revision:
            return cached
        loaded = self._read(path, revision)
        if loaded.error is not None:
            log.warning("workspace %s: its config is refused: %s", name, loaded.error)
        with self._lock:
            self._cache[name] = loaded
        return loaded

    def _read(self, path: Path, revision: tuple[int, int, int]) -> Loaded:
        """The file parsed, or why it is refused, as of ``revision``."""
        try:
            text = self._text(path)
        except OSError as exc:
            said = f"{CONFIG_FILE} could not be read: {_why(exc)}"
            return Loaded(None, said, revision)
        except ValueError as exc:
            return Loaded(None, str(exc), revision)
        try:
            return Loaded(parse(text), None, revision)
        except ValueError as exc:
            return Loaded(None, str(exc), revision)

    @staticmethod
    def _text(path: Path) -> str:
        """The file's text, never through a link, and never one over the cap."""
        fd = open_regular(path)
        with os.fdopen(fd, "rb") as file:
            if os.fstat(file.fileno()).st_size > CAP:
                raise ValueError(f"{CONFIG_FILE} is larger than {CAP // 2**20} MiB")
            data = file.read(CAP + 1)
        if len(data) > CAP:
            raise ValueError(f"{CONFIG_FILE} is larger than {CAP // 2**20} MiB")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{CONFIG_FILE} is not UTF-8 text") from None

    def read_text(self, name: str) -> str | None:
        """The file as written, or None when there is none or it cannot be read."""
        if self._layout is None:
            return None
        try:
            return self._text(self._path(name))
        except (OSError, ValueError):
            return None

    def rows(self, loaded: Loaded) -> dict:
        """Every setting, ``{path: {value, source}}``: ``workspace`` when the
        file sets it, ``default`` otherwise."""
        rows = {}
        for path in SETTINGS:
            value = _value(loaded.config, path)
            rows[path] = {
                "value": value,
                "source": "default" if value is None else "workspace",
            }
        return rows

    def global_filter(self, name: str) -> Filter | None:
        """Which `global` flows this workspace is offered: None for every one.
        A refused file offers none; its calls are refused before they ask."""
        loaded = self.load(name)
        if loaded.error is not None:
            return Filter(())
        rows = _value(loaded.config, "flows.global")
        return None if rows is None else Filter.parse(rows)

    def refusal(self, name: str) -> Conflict | None:
        """Why every call naming this workspace is refused, or None."""
        loaded = self.load(name)
        if loaded.error is None:
            return None
        text = REFUSED.format(reason=loaded.error)
        url = self.url(name)
        return Conflict(f"{text}: {url}" if url else text)

    def check(self, name: str) -> None:
        """Raise the refusal of a workspace whose config is invalid."""
        refusal = self.refusal(name)
        if refusal is not None:
            raise refusal

    def kept(self, name: str) -> bool:
        """Whether this workspace's folder holds a config, valid or not."""
        if self._layout is None:
            return False
        try:
            os.lstat(self._path(name))
        except (OSError, InvalidName):
            return False
        return True

    def revision(self, name: str) -> str:
        """A token that changes whenever this workspace's file does, written or
        removed; "" when there is none. One stat, nothing parsed."""
        if self._layout is None:
            return ""
        try:
            info = os.lstat(self._path(name))
        except (OSError, InvalidName):
            return ""
        return f"{info.st_ino}-{info.st_size}-{info.st_mtime_ns}"

    def kept_names(self) -> list[str]:
        """Every workspace kept by a config, by name. Never the shared library:
        no caller is named `global`."""
        if self._layout is None:
            return []
        return [
            name
            for name in self._layout.workspaces()
            if name != GLOBAL_WORKSPACE and self.kept(name)
        ]

    # -- writes ----------------------------------------------------------------

    @staticmethod
    def validate(text: str) -> WorkspaceConfig:
        """``text`` as a config, or a ValueError naming why it is refused."""
        if len(text.encode()) > CAP:
            raise ValueError(f"{CONFIG_FILE} is larger than {CAP // 2**20} MiB")
        return parse(text)

    def save(self, name: str, text: str) -> None:
        """Validate ``text`` and write it whole, renamed over the old file."""
        path = self._path(name)
        self.validate(text)
        self._layout.write_config(name, text)
        log.info("workspace %s: its config was saved (%s)", name, path.name)
        self.retained(name)

    def remove(self, name: str) -> bool:
        """Remove the file. False when there was none."""
        path = self._path(name)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        log.info("workspace %s: its config was removed", name)
        self.retained(name)
        return True


def _why(exc: OSError) -> str:
    """An OSError in words that name no path."""
    kind = type(exc).__name__
    return f"{exc.strerror} ({kind})" if exc.strerror else kind
