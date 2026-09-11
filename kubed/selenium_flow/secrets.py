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
    return _origin(parts)


def _origin(parts) -> str:
    """Scheme, host and port from an already-split URL.

    Built from `hostname` and `port`, never `netloc`: netloc includes
    **userinfo**, so `https://user:pass@example.com/` would have put a password
    into the audit log and into every refusal message — and would have compared
    unequal to the same site without credentials, which is a leash that fails in
    a confusing direction even though it fails closed.
    """
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    try:
        # `.port` PARSES, and raises for anything out of range — so one
        # `https://host:99999` line in a permission file would have taken down
        # catalogue construction instead of being recorded as a rejected line.
        #
        # `is not None`, not truthiness: port 0 is an explicit port, and
        # dropping it would make `https://host:0` compare equal to the same
        # host on its default port. This is the exact-origin boundary, so an
        # edge that collapses two origins into one is the kind that matters.
        declared = parts.port
    except ValueError:
        return ""
    port = f":{declared}" if declared is not None else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def _shown(line: str) -> str:
    """A rejected permission line, safe to publish.

    An operator needs to see *which* line is wrong. They do not need the
    password that made it wrong, and `/secrets` is a place a credential must
    never appear — so userinfo is replaced rather than echoed.
    """
    parts = urlsplit(line)
    if parts.username or parts.password:
        host = parts.hostname or ""
        return f"{parts.scheme}://<credentials removed>@{host}{parts.path}"
    return line


def declared_origin(line: str) -> str | None:
    """One line of ``_allowed_urls`` as an origin, or None if it is not one.

    A path is **refused**, not trimmed. §F1.27 compares origins, so
    ``https://host/admin`` and ``https://host/`` are the same permission —
    quietly widening the first into the second makes the file say less than its
    author wrote, and open question #12 settled that a rule which quietly means
    less than it says is worse than no rule.
    """
    text = (line or "").strip()
    if not text:
        return None
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return None
    # No `params` here: that is urlparse's ParseResult, not urlsplit's
    # SplitResult — a `;` segment lands in `path` for this one.
    if parts.path.strip("/") or parts.query or parts.fragment:
        return None
    if parts.username or parts.password:
        # Credentials in a permission line are always a mistake, and accepting
        # them would mean the same site reads as two different origins.
        return None
    return _origin(parts)


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
        try:
            if not self.root.is_dir():
                return []
            entries = list(self.root.iterdir())
        except OSError as exc:
            # "Unreadable is absent" is a promise this module makes and did not
            # keep: a PermissionError here propagated through Catalogue._entries
            # and took down the whole catalogue rather than skipping one
            # directory.
            log.warning("could not list %s: %s", self.root, exc)
            return []
        found = []
        for path in entries:
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
        try:
            if not directory.is_dir():
                return {}
            return {
                path.name: path
                for path in sorted(directory.iterdir())
                if path.is_file() and not path.name.startswith(".")
            }
        except OSError as exc:
            log.warning("could not read %s: %s", directory, exc)
            return {}

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

        # Whether a leash was DECLARED, kept separately from what it resolved
        # to. A file that exists but parses to nothing must not read as "no
        # restriction" — that is one typo turning a leashed credential into an
        # unleashed one, silently.
        declared = ALLOWED_URLS in files
        raw = self._read(files[ALLOWED_URLS]) if declared else ""
        allowed, rejected = [], []
        for line in (raw or "").splitlines():
            if not line.strip():
                continue
            parsed = declared_origin(line)
            if parsed:
                allowed.append(parsed)
            else:
                # Never the line itself: a rejected line may be rejected
                # *because* it carries credentials, and publishing it in
                # /secrets would hand them back — undoing the check that
                # refused it.
                rejected.append(_shown(line.strip()))

        entry = {
            "name": name,
            "keys": keys,
            "description": described or "",
            "allowed_urls": allowed,
            "restricted": declared,
            "source": self.kind,
            "location": str(self.root),
        }
        if rejected:
            # Published rather than logged and forgotten: the listing is where
            # an operator finds out their leash does not work, and the secret is
            # unusable until they fix it.
            entry["allowed_urls_rejected"] = rejected
        return entry

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

        **No declaration** means no restriction — the pragmatic default for a
        homelab, and the listing marks which secrets those are so the gap is
        visible rather than assumed.

        **A declaration that does not parse means nowhere.** Failing open there
        would turn one typo in a metadata file into an unleashed credential, and
        would do it silently. A broken leash is still a leash.
        """
        entry = self.entry(name)
        if entry is None:
            return False
        if not entry.get("restricted"):
            return True
        if entry.get("allowed_urls_rejected"):
            return False
        allowed = entry.get("allowed_urls") or []
        return bool(allowed) and origin(url) in allowed


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
    "To use one, do NOT ask for it: name it where the value would go. Pass "
    "write a value_from instead of text — value_from={'secret': {'name': ..., "
    "'key': ...}} — or in a saved flow put the same thing in that step's "
    "params. The server reads it and types it; it never passes through you, "
    "which is the point.\n\n"
    "A secret listing allowed_urls may only be used on those sites. One that "
    "is not restricted may be used anywhere. If an entry carries "
    "allowed_urls_rejected, its leash is broken and it cannot be used at all "
    "until an operator fixes the file."
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


# ---------------------------------------------------------------------------
# Binding: the one path that reads a value, and the only one there will be.

# Which actions may have a secret bound into them, and nothing else (§F1.28).
#
# Not `execute_script`: a script is arbitrary code, and a bindable argument
# there is an exfiltration API with extra steps. Not `navigate`: a secret in a
# URL lands in browser history, the referrer header, and this server's own
# session record, which is stored in Redis. Not `press_key`, which has no value
# to carry. `upload_file` says "not yet" rather than "never" — a credentials
# file is a plausible later case.
BINDABLE = {"write"}
NOT_YET = {"upload_file"}


class Refused(ValueError):
    """A binding this server will not perform.

    A ValueError so `errors.py` returns 400: every one of these is something
    the caller or the operator can fix, and none of them is our failure.
    """


def bind(catalogue, source: dict, url: str, tool: str = "write") -> str:
    """The value a `value_from.secret` reference names, or refuse.

    **The only function in this package that returns a secret value**, and it
    returns it to exactly one caller: whichever surface is about to type it into
    a field. It is not cached, not logged, and not put in any result.

    ``url`` is the page the browser is **actually on**, read at the moment of
    the bind. Checking anything else would check a permission against a page
    other than the one receiving the keystroke.
    """
    # Shape-checked here, not only in the typed MCP parameter: the HTTP surface
    # passes raw JSON straight in, so `value_from: "secret"` reached `.get` and
    # raised AttributeError, which `errors.status_for` could only read as a 500
    # — our failure, for a caller's malformed request.
    if not isinstance(source, dict):
        raise Refused("value_from must be an object naming a source")
    reference = source.get("secret")
    if reference is None:
        raise Refused("value_from must name a secret: {'secret': {'name', 'key'}}")
    if not isinstance(reference, dict):
        raise Refused("value_from.secret must be an object with a name and a key")
    name, field = reference.get("name"), reference.get("key")
    key = field  # the identifier, never the credential — see the audit log below

    if tool in NOT_YET:
        raise Refused(
            f"{tool} cannot take a secret yet — only {', '.join(sorted(BINDABLE))} can"
        )
    if tool not in BINDABLE:
        raise Refused(
            f"a secret cannot be bound into {tool}: only "
            f"{', '.join(sorted(BINDABLE))} may receive one, because it is the "
            "only action that types a value into a field and nothing else"
        )
    if catalogue is None:
        raise Refused(
            "secrets are not enabled on this server: it was started with no "
            "SECRETS_DIRS, so there is nowhere to read them from"
        )
    if not name or not key:
        raise Refused("a secret reference needs both a name and a key")

    entry = catalogue.entry(name)
    if entry is None:
        raise Refused(
            f"there is no secret called {name!r}. list_secrets shows what there is."
        )
    if key not in entry["keys"]:
        raise Refused(
            f"the secret {name!r} has no key {key!r}. It has: "
            f"{', '.join(entry['keys']) or 'none'}"
        )

    if not catalogue.allows(name, url):
        # Logged loudest of anything here: something tried to use a credential
        # on a page its owner did not allow, which is the event an operator most
        # wants to know about.
        #
        # `name` and `key` are IDENTIFIERS — "nextcloud" and "password" — not
        # the credential, and an audit line without them says nothing useful.
        # CodeQL flags them because the words look like secrets; the value is
        # not read until after every check below has passed, and
        # `test_the_audit_trail_never_contains_a_value` captures this logger and
        # proves it.
        # codeql[py/clear-text-logging-sensitive-data]
        log.warning(
            "REFUSED binding secret %s/%s on %s: not an allowed site",
            name, key, origin(url) or "an unknown page",
        )
        allowed = ", ".join(entry.get("allowed_urls") or [])
        raise Refused(
            f"the secret {name!r} may not be used on "
            f"{origin(url) or 'this page'}. It allows: "
            + (allowed or "nowhere — its _allowed_urls file does not parse")
        )

    value = catalogue.value(name, key)
    if value is None:
        raise Refused(f"the secret {name!r} has no readable value for {key!r}")

    # The audit trail: what was used, where, by which action. Never the value —
    # `name` and `key` are the identifiers it was looked up by, and `value`
    # below is deliberately not among the arguments.
    # codeql[py/clear-text-logging-sensitive-data]
    log.info("bound secret %s/%s on %s for %s", name, key, origin(url), tool)
    return value


def prepare_write(
    catalogue, actions, session_id: str, kwargs: dict
) -> tuple[dict, set]:
    """Turn a `value_from` on a write into the text it stands for.

    Shared by the MCP tool and the HTTP endpoint, because the alternative is two
    implementations of a security check and one of them being the older.

    Refuses `url` alongside it, for the reason the flow validator refuses the
    same pair: `actions.write` navigates *before* it types, so a leash checked
    beforehand would be checked against the page being left — and a redirect
    would defeat even checking the URL that was asked for. Navigation is its own
    call.
    """
    kwargs = dict(kwargs)
    source = kwargs.pop("value_from", None)
    if source is None:
        return kwargs, set()
    if hasattr(source, "model_dump"):
        source = source.model_dump(exclude_none=True)
    if kwargs.get("text") is not None:
        raise Refused("pass text or value_from, not both")
    if kwargs.get("url"):
        raise Refused(
            "a write that takes its value from a secret may not also navigate: "
            "go to the page first, so the secret's allowed sites are checked "
            "against the page that receives it"
        )
    if "param" in (source or {}):
        raise Refused(
            "value_from.param names one of a flow's own parameters and means "
            "nothing outside a flow; pass text, or name a secret"
        )
    here = actions.page(session_id).get("url", "")
    kwargs["text"] = bind(catalogue, source, here, tool="write")
    return kwargs, {"text"}
