"""The secrets an agent may bind, and the one thing it may never do with them.

A secret here is **a directory of files**: the directory is the secret's name,
each file is a key, and the file's content is the value. That is exactly how
Kubernetes projects a Secret into a pod, and it is also what somebody makes by
hand on a laptop — so one reader serves both and there is no adapter between
them (saga §F1.18).

```
<a directory on SECRETS_DIRS>/
  nextcloud-admin/
    username
    password
    _description        # optional, and not a key
    _allowed_urls       # optional, one origin per line, and not a key
```

``SECRETS_DIRS`` is PATH-like — ``/run/secrets:/etc/selenium-flow/secrets`` — so
a deployment points at the service account's automounted directory *and* its own
mounts, and a laptop points wherever it likes. First match wins, as with PATH.

**No caller ever sees a value.** This module hands out names, keys, descriptions
and allowed URLs; the value is read at the moment it is bound and is never
returned, never cached and never logged. `value()` exists for the binding path
(E9) and has no surface of its own.

That guarantee is precise and worth stating in its limits, because a security
feature that overstates itself is worse than none: **the value never passes
through the model on its way in.** Once it has been typed into a page,
`execute_script` can read it back off that page, and nothing here can prevent
that (§F1.24).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .flows import InvalidName, valid_name

log = logging.getLogger(__name__)

# How a list of directories is written. The separator is os.pathsep so this
# reads the way PATH does on whatever it is running on.
SEPARATOR = os.pathsep

# Metadata, not keys. The prefix is deliberate: a Kubernetes Secret key must
# match [-._a-zA-Z0-9]+ and may not begin with an underscore, so nothing
# arriving from a real k8s mount can collide with one of these.
RESERVED_PREFIX = "_"
DESCRIPTION = "_description"
ALLOWED_URLS = "_allowed_urls"

# Long enough that a listing is not a directory walk per call, short enough that
# a secret added by an operator shows up without a restart. Only the catalogue
# is cached; a value is read at the moment it is used, every time, so a rotated
# credential is never served from memory after it stopped being valid (§F1.23).
CACHE_SECONDS = 30


def origin(url: str) -> str:
    """Scheme, host and port — what an allowed-URL check compares.

    Origins, never substrings. ``https://nextcloud.example.com.evil.com``
    contains ``nextcloud.example.com``, and a substring check is exactly how
    that gets through (§F1.27).
    """
    parts = urlsplit((url or "").strip())
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


class SecretSource(Protocol):
    """Somewhere secrets come from. Kubernetes is the second one (E8)."""

    kind: str

    def names(self) -> list[str]: ...

    def entry(self, name: str) -> dict | None: ...

    def value(self, name: str, key: str) -> str | None: ...


class FilesystemSource:
    """One directory of secrets, in the shape Kubernetes mounts them."""

    kind = "filesystem"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _dir(self, name: str) -> Path:
        """One secret's directory, refusing anything that leaves the root.

        Same rule the flow store settled on: the resolved path must *be* the
        path we asked for, so nothing below the root may traverse a link. The
        root itself is resolved first and so may be one — pointing SECRETS_DIRS
        at a mount is the whole point.
        """
        root = self.root.resolve()
        expected = root / valid_name(name, "secret name")
        path = expected.resolve()
        if path != expected:
            raise InvalidName(
                f"{name!r} does not resolve to itself inside {self.root}"
            )
        return path

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        found = []
        for path in self.root.iterdir():
            # A k8s projected volume is full of these: `..data` is a symlink to
            # a timestamped directory, and every key is a symlink through it.
            # The secrets themselves never start with a dot.
            if path.name.startswith("."):
                continue
            if not path.is_dir():
                continue
            try:
                self._dir(path.name)
            except InvalidName:
                log.warning("ignoring %s: not a usable secret name", path.name)
                continue
            found.append(path.name)
        return sorted(found)

    def _files(self, name: str) -> dict[str, Path]:
        """Every readable file in one secret, keyed by filename.

        One level deep, always: a Kubernetes mount is exactly one level, and
        recursing would invent a shape nothing else produces.
        """
        try:
            directory = self._dir(name)
        except InvalidName:
            return {}
        if not directory.is_dir():
            return {}
        return {
            path.name: path
            for path in sorted(directory.iterdir())
            if path.is_file() and not path.name.startswith(".")
        }

    def _read(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("could not read %s: %s", path.name, exc)
            return None

    def entry(self, name: str) -> dict | None:
        """What may be published about one secret. Never a value."""
        files = self._files(name)
        if not files:
            return None
        keys = sorted(k for k in files if not k.startswith(RESERVED_PREFIX))
        described = self._read(files[DESCRIPTION]) if DESCRIPTION in files else ""
        urls = self._read(files[ALLOWED_URLS]) if ALLOWED_URLS in files else ""
        allowed = [origin(line) for line in (urls or "").splitlines() if line.strip()]
        return {
            "name": name,
            "keys": keys,
            "description": described or "",
            "allowed_urls": [u for u in allowed if u],
            "source": self.kind,
            "location": str(self.root),
        }

    def value(self, name: str, key: str) -> str | None:
        """One value, read now and returned to exactly one caller.

        Never cached: a rotated credential must not be served from memory after
        it stopped working. Reserved names are refused rather than read, so a
        bind cannot reach `_allowed_urls` and discover its own leash.
        """
        if key.startswith(RESERVED_PREFIX):
            return None
        path = self._files(name).get(key)
        return None if path is None else self._read(path)


class Catalogue:
    """Every source, as one list of secrets.

    Sources are consulted in order and **the first match wins**, the way PATH
    resolves a command. The entry says which source and which directory it came
    from, because "why am I getting the wrong password" is otherwise
    unanswerable.

    Filesystem secrets are visible to every session: a directory carries no
    labels, and inventing a scoping convention for it would mean two mechanisms
    doing one job (§F1.22). Session scoping arrives with Kubernetes in E8.
    """

    def __init__(self, sources: list[SecretSource], ttl: int = CACHE_SECONDS,
                 clock=time.monotonic):
        self.sources = list(sources)
        self._ttl = ttl
        self._clock = clock
        self._cache: tuple[float, dict] | None = None

    def _entries(self) -> dict:
        now = self._clock()
        if self._cache is not None and now < self._cache[0]:
            return self._cache[1]
        entries: dict[str, dict] = {}
        for source in self.sources:
            for name in source.names():
                if name in entries:
                    continue  # first match wins
                entry = source.entry(name)
                if entry is not None:
                    entries[name] = entry
        self._cache = (now + self._ttl, entries)
        return entries

    def listing(self, session: str = "") -> dict:
        """The catalogue, as a caller may see it. Names and keys, never values."""
        entries = self._entries()
        return {
            "count": len(entries),
            "session": session,
            "secrets": [entries[name] for name in sorted(entries)],
        }

    def entry(self, name: str) -> dict | None:
        return self._entries().get(name)

    def source_of(self, name: str) -> SecretSource | None:
        """Which source owns a name, for reading its value."""
        for source in self.sources:
            if name in source.names():
                return source
        return None

    def value(self, name: str, key: str) -> str | None:
        """One value, for the binding path. No surface reaches this."""
        source = self.source_of(name)
        return None if source is None else source.value(name, key)

    def allows(self, name: str, url: str) -> bool:
        """Whether this secret may be used on the page the browser is on.

        No declaration means no restriction, which is the pragmatic default for
        a homelab — and the listing shows which secrets are unrestricted, so the
        gap is visible rather than assumed.
        """
        entry = self.entry(name)
        if entry is None:
            return False
        allowed = entry.get("allowed_urls") or []
        return not allowed or origin(url) in allowed


def directories(env: dict | None = None) -> list[str]:
    """The directories ``SECRETS_DIRS`` names, in order."""
    env = os.environ if env is None else env
    raw = str(env.get("SECRETS_DIRS", "")).strip()
    return [part.strip() for part in raw.split(SEPARATOR) if part.strip()]


def from_env(env: dict | None = None) -> Catalogue | None:
    """The catalogue the environment asks for, or None if there are no secrets.

    None is a real answer and the default one, exactly as it is for flows: the
    tools say so rather than this inventing somewhere to look.
    """
    paths = directories(env)
    if not paths:
        log.info("secrets: off (set SECRETS_DIRS to enable them)")
        return None
    log.info("secrets: %s director%s", len(paths), "y" if len(paths) == 1 else "ies")
    return Catalogue([FilesystemSource(path) for path in paths])


# ---------------------------------------------------------------------------
# The surface. One read, and nothing else — see §F1.31.
#
# There is deliberately no get-one, no create, no update and no delete. A
# catalogue is the only question an agent has about secrets ("what may I
# bind?"), and the answer to every other one is that this server does not do
# that: an agent that could write a secret could write one whose _allowed_urls
# it chose.

LIST_URI = "secret://secrets"
LIST_TOOL = "list_secrets"

LIST_DESCRIPTION = (
    "The secrets you can bind to a field, by name.\n\n"
    "You never see a value — not here, not anywhere. Each entry gives the "
    "secret's name, the keys inside it, what it is for, and the sites it may "
    "be used on.\n\n"
    "To use one, do NOT ask for it: name it where the value would go. In a "
    "flow step that is valueFrom: {text: {secret: {name: ..., key: ...}}}. The "
    "server reads it and types it; it never passes through you, which is the "
    "point.\n\n"
    "A secret listing allowed_urls may only be used on those sites. One with "
    "an empty list is unrestricted."
)


def register(mcp, catalogue, sessions, token: str | None, prefix: str = "") -> set[str]:
    """Serve the catalogue as a resource, a mirroring tool and one endpoint."""
    from starlette.responses import JSONResponse

    from . import auth, errors
    from .hints import reads

    def listing() -> dict:
        if catalogue is None:
            raise ValueError(
                "secrets are not enabled on this server: it was started with no "
                "SECRETS_DIRS, so there is nowhere to read them from"
            )
        from .flows import session_for

        return catalogue.listing(session_for(sessions.key()))

    @mcp.resource(LIST_URI, description=LIST_DESCRIPTION, mime_type="application/json")
    def secrets_resource() -> dict:
        return listing()

    @mcp.tool(
        name=LIST_TOOL,
        description=LIST_DESCRIPTION,
        # Reads a directory this server can already see. It never reaches the
        # browser, the Grid or the network.
        annotations=reads("Secrets you can bind", open_world=False),
    )
    def list_secrets() -> dict:
        return listing()

    @mcp.custom_route(f"{prefix}/secrets", methods=["GET"], name="secrets")
    async def secrets_route(request):
        if not auth.authorized(request, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            return JSONResponse(listing())
        except Exception as exc:  # noqa: BLE001 - errors.py decides what it means
            return JSONResponse(
                {"error": errors.message(exc)}, status_code=errors.status_for(exc)
            )

    return {LIST_TOOL}
