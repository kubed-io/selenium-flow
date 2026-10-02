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

``secrets.entries`` in the config file is the second source: an overlay, not a
replacement. It can add a description or a leash over a directory's secret, swap
in one key read from an env var or a file, or define a whole secret with no
directory behind it at all. A key it names always wins over the same key from a
directory, and an ``allowed_urls`` it names always replaces the directory's
leash entirely — a broken one included, because the config was validated at
boot and this one parses.

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

from .config import FromEnv, FromFile, FromValue, SecretEntry, SecretsSettings
from .names import InvalidName, valid_name
from .urls import host_of

log = logging.getLogger(__name__)

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

    An operator needs to see *which* line is wrong. They do not need anything it
    carries, and `/secrets` is a place a credential must never appear — so this
    **rebuilds** the line from its harmless parts rather than echoing it with
    the bad part taken out.

    That distinction is the whole fix. Removing userinfo still published the
    path and query, and `https://host/login?token=hunter2` is exactly the shape
    a credential arrives in — so the branch that refuses a line for carrying one
    was handing it back. What went missing is *named*, never quoted: the reason
    a line was refused is enough to correct it.
    """
    try:
        parts = urlsplit((line or "").strip())
        origin = _origin(parts)
    except ValueError:
        return "<a line that is not a URL>"
    if not parts.scheme or not origin:
        return "<a line that is not a URL>"
    dropped = []
    if parts.username or parts.password:
        dropped.append("credentials")
    if parts.path.strip("/") or parts.query or parts.fragment:
        dropped.append("a path")
    return origin + (f" (+ {' and '.join(dropped)})" if dropped else "")


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
            # Never `exc` itself: a `UnicodeDecodeError`'s message quotes the
            # offending byte, which is a byte of the secret's value.
            log.warning("could not read %s: %s", path.name, type(exc).__name__)
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
            "origins": [{"source": self.kind, "location": str(self.root)}],
            "key_sources": {k: {"from": "filesystem"} for k in keys},
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


class ConfigEntries:
    """`secrets.entries` from the config file: policy over collected secrets,
    and whole new ones.

    A key's value is read at the moment it is bound, like a directory's: an
    env reference reads the environment then, and a file reference reads the
    file then. Only presence is checked when the listing is built.
    """

    kind = "config"

    def __init__(
        self, entries: dict[str, SecretEntry], location: str | None, environ=None
    ):
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
                 clock=time.monotonic, config: ConfigEntries | None = None):
        self.sources = list(sources)
        self._ttl = ttl
        self._clock = clock
        self.config = config
        self._cache: tuple[float, dict, dict] | None = None

    def _snapshot(self) -> tuple[dict, dict]:
        """Every secret, and which source each of its keys came from, read together.

        **One snapshot, because the two are one fact.** The entry carries the
        policy — which URLs a secret may be used on — and the owners are where
        each key's value will be read from. Resolved separately they could
        disagree: the listing was cached while a value was read live, so a name
        appearing in a higher-priority directory during the TTL meant the old
        secret's leash was checked and the new secret's value was returned.

        A bind is a policy and a value about the same secret, or it is nothing.

        Owners are per key, not per secret: `secrets.entries` can replace one
        key of a directory's secret while leaving its other keys owned by the
        filesystem, so "who owns this name" is not one answer.
        """
        now = self._clock()
        if self._cache is not None and now < self._cache[0]:
            return self._cache[1], self._cache[2]
        entries: dict[str, dict] = {}
        owners: dict[str, dict] = {}
        for source in self.sources:
            for name in source.names():
                if name in entries:
                    continue  # first match wins
                entry = source.entry(name)
                if entry is not None:
                    entries[name] = entry
                    owners[name] = dict.fromkeys(entry["keys"], source)
        if self.config is not None:
            for name, conf in self.config.entries.items():
                base = entries.get(name)
                entry = dict(base) if base else {
                    "name": name, "keys": [], "description": "", "allowed_urls": [],
                    "restricted": False, "origins": [], "key_sources": {},
                }
                entry["origins"] = [
                    *entry["origins"],
                    {"source": "config", "location": self.config.location},
                ]
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
                inline = sorted(
                    k for k, ref in conf.keys.items() if isinstance(ref, FromValue)
                )
                if unresolved:
                    entry["keys_unresolved"] = unresolved
                if inline:
                    entry["inline_keys"] = inline
                entries[name] = entry
                owners[name] = key_owners
        self._cache = (now + self._ttl, entries, owners)
        return entries, owners

    def _entries(self) -> dict:
        return self._snapshot()[0]

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

    def value(self, name: str, key: str) -> str | None:
        """One value, for the binding path. No surface reaches this.

        The value itself is read now rather than cached — a rotated password
        should be the one that gets typed, and keeping credentials in memory to
        make a check atomic would be a poor trade. What the snapshot fixes is
        *which secret and which owner*, not what is inside it.
        """
        owner = self._snapshot()[1].get(name, {}).get(key)
        if owner is None:
            return None
        if isinstance(owner, (FromFile, FromEnv, FromValue)):
            return self.config.value(owner)
        return owner.value(name, key)

    def unresolved(self, name: str, key: str) -> str | None:
        """Why a config-defined key has no value, or None."""
        entry = self.entry(name) or {}
        return next(
            (u["reason"] for u in entry.get("keys_unresolved", []) if u["key"] == key),
            None,
        )

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


def from_settings(
    conf: SecretsSettings, config_file: str | None = None
) -> Catalogue | None:
    """The catalogue the config asks for, or None when there are no secrets."""
    if not conf.dirs and not conf.entries:
        log.info("secrets: off (set secrets.dirs or secrets.entries to enable them)")
        return None
    overlay = ConfigEntries(conf.entries, config_file) if conf.entries else None
    log.info("secrets: %s director%s, %s from config", len(conf.dirs),
              "y" if len(conf.dirs) == 1 else "ies", len(conf.entries))
    return Catalogue([FilesystemSource(path) for path in conf.dirs], config=overlay)


# ---------------------------------------------------------------------------
# The surface. One read, and nothing else — see §F1.31.
#
# There is deliberately no get-one, no create, no update and no delete. A
# catalogue is the only question an agent has about secrets ("what may I
# bind?"), and the answer to every other one is that this server does not do
# that: an agent that could write a secret could write one whose _allowed_urls
# it chose.

LIST_URI = "secret://secrets"

LIST_DESCRIPTION = (
    "The secrets you can bind to a field, by name.\n\n"
    "You never see a value — not here, not anywhere. Each entry gives the "
    "secret's name, the keys inside it, what it is for, and the sites it may "
    "be used on.\n\n"
    "To use one, do NOT ask for it: name it where the value would go. Pass "
    "write a secret instead of text — secret={'name': ..., 'key': ...} — or "
    "in a saved flow put the same thing in that step's args. The server reads "
    "it and types it; it never passes through you, which is the point.\n\n"
    "A secret listing allowed_urls may only be used on those sites. One that "
    "is not restricted may be used anywhere. If an entry carries "
    "allowed_urls_rejected, its leash is broken and it cannot be used at all "
    "until an operator fixes the file."
)


def register(mcp, catalogue, token: str | None, prefix: str = "") -> None:
    """Serve the catalogue as a resource and one endpoint."""
    from starlette.responses import JSONResponse

    from . import errors, faults
    from .http import auth
    from .mcp import clients
    from .session.sessions import Caller, values_of

    def listing(caller) -> dict:
        if catalogue is None:
            raise ValueError(OFF)
        return catalogue.listing(caller.name)

    @mcp.resource(
        LIST_URI,
        name="Secrets",
        description=LIST_DESCRIPTION,
        mime_type="application/json",
    )
    def secrets_resource() -> dict:
        return listing(clients.caller())

    @mcp.custom_route(f"{prefix}/secrets", methods=["GET"], name="secrets")
    async def secrets_route(request):
        if not auth.authorized(request, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            return JSONResponse(listing(Caller.from_request(*values_of(request))))
        except Exception as exc:  # noqa: BLE001 - errors.py decides what it means
            return JSONResponse(
                {"error": faults.message(exc)}, status_code=errors.status_for(exc)
            )


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
# Said by both surfaces that can meet a server with no catalogue — the listing
# and the bind — and raised as two different exception types, which is why it
# was written twice and why the two could drift.
OFF = (
    "secrets are not enabled on this server: it was started with no "
    "secrets.dirs and no secrets.entries, so there is nowhere to read them from"
)

BINDABLE = {"write"}
NOT_YET = {"upload_file"}


class Refused(ValueError):
    """A binding this server will not perform.

    A ValueError so `errors.py` returns 400: every one of these is something
    the caller or the operator can fix, and none of them is our failure.
    """


def bind(catalogue, reference, url: str, tool: str = "write") -> str:
    """The value a `secret` reference names, or refuse.

    **The only function in this package that returns a secret value**, and it
    returns it to exactly one caller: whichever surface is about to type it into
    a field. It is not cached, not logged, and not put in any result.

    ``url`` is the page the browser is **actually on**, read at the moment of
    the bind. Checking anything else would check a permission against a page
    other than the one receiving the keystroke.
    """
    # Shape-checked here, not only in the typed MCP parameter: the HTTP surface
    # passes raw JSON straight in, so a bare string reached `.get` and
    # raised AttributeError, which `errors.status_for` could only read as a 500
    # — our failure, for a caller's malformed request.
    from .flows.document import reference_problems

    # The validator's rule, not a copy of it: a reference with a field this
    # reads nothing from is a request for a different binding than the one that
    # would run.
    problems = reference_problems(reference)
    if problems:
        raise Refused("; ".join(problems))
    name, key = reference["name"], reference["key"]

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
        raise Refused(OFF)
    entry = catalogue.entry(name)
    if entry is None:
        raise Refused(
            f"there is no secret called {name!r}. secret://secrets shows what there is."
        )
    if key not in entry["keys"]:
        raise Refused(
            f"the secret {name!r} has no key {key!r}. It has: "
            f"{', '.join(entry['keys']) or 'none'}"
        )

    # What the audit lines below name the secret by. Taken from the catalogue's
    # own record rather than from the caller's reference, which is both safer
    # and more accurate: it is the secret that was *resolved*, spelled as the
    # source spells it, instead of the string a request asked with.
    #
    # `name` and `key` are IDENTIFIERS — "nextcloud" and "password" — never the
    # credential, and an audit line without them says nothing useful. Reading
    # them off the entry is also what stops a scanner reading every field of a
    # caller-supplied `{"secret": ...}` object as the secret itself: the entry
    # is built from the source's own listing, so nothing here is derived from
    # the request. `test_the_audit_trail_never_contains_a_value` captures this
    # logger and proves the value never joins them.
    known = entry.get("name") or "?"
    known_key = next((k for k in entry["keys"] if k == key), "?")

    if not catalogue.allows(name, url):
        # Logged loudest of anything here: something tried to use a credential
        # on a page its owner did not allow, which is the event an operator most
        # wants to know about.
        log.warning(
            "REFUSED binding secret %s/%s on %s: not an allowed site",
            known, known_key, origin(url) or "an unknown page",
        )
        allowed = ", ".join(entry.get("allowed_urls") or [])
        raise Refused(
            f"the secret {name!r} may not be used on "
            f"{origin(url) or 'this page'}. It allows: "
            # True of both sources of a leash: a directory's `_allowed_urls`
            # that fails to parse, and a config `allowed_urls: []` — declared,
            # and immediately exhausted.
            + (allowed or "nowhere — its allowed_urls list is empty or does not parse")
        )

    value = catalogue.value(name, key)
    if value is None:
        reason = catalogue.unresolved(name, key)
        raise Refused(
            f"the secret {name!r} cannot read {key!r}: {reason}" if reason
            else f"the secret {name!r} has no readable value for {key!r}"
        )

    # The audit trail: what was used, where, by which action. Never the value —
    # these are the identifiers it was looked up by, and `value` above is
    # deliberately not among the arguments.
    log.info("bound secret %s/%s on %s for %s", known, known_key, origin(url), tool)
    return value


def perform_write(catalogue, actions, sessions, name: str, kwargs: dict) -> dict:
    """A whole bound write — resolve, type, redact, remember — on either surface.

    Deliberately NOT routed through ``sessions.act``. That touches the session
    with the URL the action returned, and ``submit=True`` can land the browser
    on ``?q=<what was typed>`` — so the shared wrapper would persist the
    credential into the session record before anything had a chance to redact
    it. Everything else about a write is identical, which is exactly why this
    lives in one place: two copies of a redaction are one copy that is older.
    It settles like any other action, through ``sessions.settle``, with the page
    withheld when the value reached it.
    """
    from . import binding
    from .flows import redact

    resolved = sessions.resolve(name)
    given, guarded = binding.bind_into(
        kwargs,
        catalogue,
        lambda: binding.receiving(actions.page(resolved)),
        "write",
        binding.DIRECT,
    )
    hidden = binding.forms_of(given, guarded)
    rest = {k: v for k, v in given.items() if k not in ("text", "url")}
    try:
        result = actions.write(resolved, given["text"], **rest)
    except Exception as exc:  # noqa: BLE001 - rewrapped, never swallowed
        # An action puts its arguments in its error text.
        raise ValueError(redact.scrub(str(exc), hidden)) from None
    shown = binding.after({**result, "text_from": "secret"}, guarded, hidden)
    # Only remember a page the value never reached; a later reattach would
    # navigate to it. Touched either way. Withholding the page must not also
    # stop the clock: `touch` slides the TTL, and skipping it entirely let a
    # session expire *because* its URL was correctly kept out of the store.
    #
    # The first call after a silent reopen says what came back: `settle` puts
    # that on `shown`.
    safe = binding.safe_url(result, hidden)
    sessions.settle(name, shown, url=safe, browser=resolved)
    return shown


def matching_secrets(secrets: list[dict] | None, host: str) -> list[dict]:
    """The secrets allowed on ``host``, names and keys only. A secret with no
    `allowed_urls` is usable anywhere and is not listed under every site; one
    with `allowed_urls_rejected` is usable nowhere, its valid lines included
    (`Catalogue.allows`), so it is not listed either."""
    return [
        {
            "name": s["name"],
            "description": s.get("description") or "",
            "keys": list(s.get("keys") or []),
        }
        for s in secrets or []
        if not s.get("allowed_urls_rejected")
        and any(host_of(u) == host for u in s.get("allowed_urls") or [])
    ]
