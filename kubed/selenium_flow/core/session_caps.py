"""The capabilities a session sends, and where each one came from.

The one place the precedence rules live (spec 2026-10-26-session-capabilities,
*Building the sent set*). Four layers, applied in order:

1. **flow**: what selenium-flow always sends, because its own tools need it.
2. **option**: what a named ``open_session`` option produces.
3. **config**: the operator's ``session.capabilities``.
4. **raw**: the caller's ``capabilities``, the escape hatch.

A key flow or an option produces is **owned**: config or raw setting it is
refused, naming what controls it, never applied quietly. Raw beats config for
the same key. The browser's own vendor block (``goog:chromeOptions``,
``moz:firefoxOptions``) is the one place the layers meet: args are added, prefs
merge, and where both set the same pref ours wins and the loss is listed.

What comes out is exactly what is sent. Selenium's Options objects add keys of
their own (``pageLoadStrategy``, and on Firefox ``acceptInsecureCerts: true``
and ``moz:debuggerAddress``); `Grid._options` sends this dict instead, so the
admin's Capabilities section is the truth (ruling 23).

Plain on purpose: no Selenium, no protocol library. ``config.py`` checks the
operator's capabilities with :func:`check` at load.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..faults import NodeRefused, NoNodeTook
from .defaults import CHROME, FIREFOX, normalize_browser
from .guidance import pointer

SOURCES = ("flow", "option", "config", "raw")

# The word that clears a remembered version. Never sent: the Grid's own "any
# version" is an empty value or `stable` (ruling 19).
LATEST = "latest"

# Each browser's own options block, which is merged rather than replaced
# (ruling 5). The other browser's block is an ordinary key, sent untouched.
VENDOR = {CHROME: "goog:chromeOptions", FIREFOX: "moz:firefoxOptions"}

# The keys selenium-flow or a named option produces, and the end of the
# sentence that refuses one (ruling 15). `timeouts` is sent by nobody: the two
# timeouts are WebDriver commands after the browser starts, and a capability
# would fight them.
OWNED = {
    "unhandledPromptBehavior": "selenium-flow keeps dialogs open for the dialog tool",
    "se:downloadsEnabled": "the downloads folder needs it",
    "webSocketUrl": "site data, capture and the monitor need it",
    "browserName": "use browser= instead",
    "browserVersion": "use version= instead",
    "acceptInsecureCerts": "use insecure=true or insecure=false instead",
    "se:recordVideo": "use record=true instead",
    "se:videoName": "use record=true instead",
    "timeouts": "use page_load_timeout= and script_timeout= instead",
}

# Headless is an option, not a raw flag: selenium-flow must know a browser is
# headless to refuse record and the live view, and a flag in the vendor block
# hides it. So the option owns every spelling of the flag (rulings 6, 7).
HEADLESS_ARGS = {CHROME: "--headless=new", FIREFOX: "-headless"}
HEADLESS_OWNED = "use headless=true or headless=false instead"


def _headless(arg: str) -> bool:
    """Whether ``arg`` is a spelling of the headless flag, in either browser."""
    return arg.lstrip("-").split("=", 1)[0].lower() == "headless"


REMEMBERED = (
    "; this workspace remembers it, so clear it with open_session(capabilities={})"
)

# What flow sends on every browser.
_FLOW_KEYS = {
    # Never let the browser answer a dialog on the caller's behalf. The
    # default, "dismiss and notify", silently clicks Cancel on a confirm and
    # only reports it as an error on whatever command happened to notice — so
    # a destructive prompt gets answered by accident and the dialog is gone
    # before anyone can decide. "ignore" leaves it open for `dialog`.
    "unhandledPromptBehavior": "ignore",
    # Ask the Grid to manage this session's downloads. The node then keeps a
    # per-session directory and exposes it at /session/<id>/se/files, which is
    # the whole file store — listed, read and reaped by the Grid itself.
    # Without this the browser downloads into a directory nothing can reach.
    "se:downloadsEnabled": True,
    # Every browser speaks BiDi so a saved site can be restored into it, and
    # capture and the monitor can listen; every other tool still uses classic
    # WebDriver.
    "webSocketUrl": True,
}


def _flow_block(browser: str) -> dict:
    """What flow puts in ``browser``'s own vendor block, fresh each call."""
    if browser == FIREFOX:
        return {
            "prefs": {
                # 2 = the directory the Grid node set for this session. Firefox
                # otherwise saves to its own default and se:downloadsEnabled has
                # nothing to collect.
                "browser.download.folderList": 2,
                "browser.download.useDownloadDir": True,
                # Firefox's equivalent of Chrome's automatic-downloads prompt:
                # it asks what to do with a type it does not recognise, and
                # nobody is there to answer. A PDF a site serves would hang
                # without this, since Firefox opens PDFs in its own viewer.
                "browser.helperApps.neverAsk.saveToDisk": (
                    "application/pdf,image/png,application/octet-stream"
                ),
                "pdfjs.disabled": True,
                # See Chrome's prefs: the password-save prompt is a browser
                # popup, and the browser is the only thing that can answer it.
                "signon.rememberSignons": False,
            }
        }
    return {
        "args": [
            "--no-sandbox",
            # Chrome's default /dev/shm is 64MB and it crashes under it.
            "--disable-dev-shm-usage",
        ],
        "prefs": {
            # Chrome asks permission before a page's *second* automatic
            # download and denies it silently when nobody can answer. The first
            # file of a session would arrive and every one after it would
            # vanish with no error. 1 = allow.
            "profile.default_content_setting_values.automatic_downloads": 1,
            # Submitting a password offers to save it, and that offer is a
            # *browser* popup rather than anything in the page. It takes the
            # input focus and never gives it back, so every later click and
            # keystroke is delivered to the bubble instead of the document —
            # silently: the element is found, the command succeeds, and nothing
            # happens. Nobody is here to answer the offer, so do not let it be
            # made.
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            # Same popup, different trigger: Chrome warns about a password it
            # believes was breached.
            "profile.password_manager_leak_detection": False,
        },
    }


# Where a version the caller owns came from, as a refusal says it (ruling 4).
# "config" is the operator's, so it has no phrase: it is never blamed.
VERSION_FROM = {
    "client": "from X-Version or ?version=",
    "call": "from this call's version=",
    "remembered": 'remembered by this workspace (version="latest" forgets it)',
}


@dataclass(frozen=True)
class Built:
    """What one open sends, and what the admin is shown about it."""

    browser: str
    # Exactly what is sent, in the order the admin lists it.
    send: dict[str, Any]
    # [{"key", "value", "sources"}], in `send`'s order: display only.
    sent: list[dict] = field(default_factory=list)
    # `<block>.prefs.<name>` for each config or raw pref ours beat (ruling 28).
    ignored: list[str] = field(default_factory=list)
    # The top-level keys the caller's raw object set: what a node's refusal
    # may be blamed on (ruling 16).
    raw_keys: tuple[str, ...] = ()
    # The browserVersion sent, or None.
    version: str | None = None
    # Whose that version is (a key of `VERSION_FROM`), set only when one is
    # sent: `latest` has no origin. Never shown to a caller or logged.
    version_from: str | None = None


def _refusal(key: str, where: str, remembered: bool) -> str:
    said = f"{key} cannot be sent in {where}: {OWNED[key]}"
    return said + (REMEMBERED if remembered else "")


def check(
    raw: Mapping[str, Any] | None,
    *,
    where: str = "capabilities",
    remembered: bool = False,
) -> None:
    """Refuse an owned key, a headless flag, or a vendor block a merge cannot add to.

    Raised as a ``ValueError`` (a 400) before anything opens or ends: a
    rejected argument must cost nothing. ``where`` names the object in the
    message; ``remembered`` says the workspace holds it, and how to clear it
    (ruling 26).
    """
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where} must be an object")
    owned = [key for key in raw if key in OWNED]
    if owned:
        raise ValueError("; ".join(_refusal(key, where, remembered) for key in owned))
    for block in VENDOR.values():
        if block not in raw:
            continue
        value = raw[block]
        if not isinstance(value, Mapping):
            raise ValueError(f"{block} in {where} must be an object")
        args = value.get("args")
        if "args" in value and not (
            isinstance(args, list) and all(isinstance(a, str) for a in args)
        ):
            raise ValueError(f"{block}.args in {where} must be a list of strings")
        if "prefs" in value and not isinstance(value["prefs"], Mapping):
            raise ValueError(f"{block}.prefs in {where} must be an object")
    flags = []
    for block in VENDOR.values():
        value = raw.get(block)
        if not isinstance(value, Mapping):
            continue
        flags += [
            f"{block}.args {arg} cannot be sent in {where}: {HEADLESS_OWNED}"
            for arg in value.get("args", ())
            if _headless(arg)
        ]
        env = value.get("env")
        moz = block == VENDOR[FIREFOX] and isinstance(env, Mapping)
        if moz and "MOZ_HEADLESS" in env:
            flags.append(
                f"{block}.env.MOZ_HEADLESS cannot be sent in {where}: "
                + HEADLESS_OWNED
            )
    if flags:
        suffix = REMEMBERED if remembered else ""
        raise ValueError("; ".join(flag + suffix for flag in flags))


def _version(version: str | None) -> str | None:
    """The version to send, or None for unset, blank or `latest`."""
    if version is None:
        return None
    text = str(version).strip()
    return None if not text or text.lower() == LATEST else text


def _merged(
    block: str,
    flow: dict,
    option_args: Sequence[str],
    config: Mapping[str, Any] | None,
    raw: Mapping[str, Any] | None,
) -> tuple[dict, list[str], list[str]]:
    """The browser's own vendor block: (merged block, sources, ignored)."""
    sources = ["flow"]
    if option_args:
        sources.append("option")
    layers = []  # (source, block) for config then raw, when given
    for name, given in (("config", config), ("raw", raw)):
        if given and block in given:
            sources.append(name)
            layers.append(copy.deepcopy(dict(given[block])))

    merged: dict[str, Any] = {}
    # Any other key comes from config, then raw (raw wins), unless flow sets
    # it. Flow sets only args and prefs, which are merged below.
    for given in layers:
        for key, value in given.items():
            if key not in ("args", "prefs") and key not in flow:
                merged[key] = value

    theirs_args = [arg for given in layers for arg in given.get("args", [])]
    args = list(dict.fromkeys([*flow.get("args", []), *option_args, *theirs_args]))
    if args:
        merged["args"] = args

    # Raw beats config; option (none produce prefs today) and flow beat both.
    theirs: dict[str, Any] = {}
    for given in layers:
        theirs.update(given.get("prefs") or {})
    ours = copy.deepcopy(flow.get("prefs") or {})
    ignored = [
        f"{block}.prefs.{name}"
        for name, value in theirs.items()
        if name in ours and ours[name] != value
    ]
    merged["prefs"] = {**theirs, **ours}
    return merged, sources, ignored


def build(
    browser: str | None,
    *,
    version: str | None = None,
    insecure: bool = False,
    record: bool = False,
    video_name: str | None = None,
    config: Mapping[str, Any] | None = None,
    raw: Mapping[str, Any] | None = None,
    option_args: Sequence[str] = (),
    version_from: str = "call",
) -> Built:
    """The capabilities an open of ``browser`` sends, and each one's sources.

    ``option_args`` is what a named option adds to the browser's own args
    (ruling 29); it is what ``headless`` adds (``HEADLESS_ARGS`` for the
    browser). Raises
    ``ValueError`` for an owned key or a vendor block of the wrong shape in
    ``config`` or ``raw``. Never mutates its inputs, and nothing it returns
    shares an object with them.

    ``version_from`` says whose ``version`` is ("config", "client", "call" or
    "remembered"); it is kept only when a version is sent, and decides whether
    a node's refusal may blame the caller for it.
    """
    name = normalize_browser(browser)
    check(config, where="session.capabilities")
    check(raw)
    config = config or {}
    raw = raw or {}
    own_block = VENDOR[name]
    sent_version = _version(version)

    entries: dict[str, tuple[Any, list[str]]] = {}

    # option
    entries["browserName"] = (name, ["option"])
    if sent_version is not None:
        entries["browserVersion"] = (sent_version, ["option"])
    # Always said, true or false, on every browser: Selenium's Firefox default
    # is true, so leaving it out made every Firefox accept any certificate and
    # get saved site data while insecure (ruling 23, Dr K).
    entries["acceptInsecureCerts"] = (bool(insecure), ["option"])
    if record:
        # The Grid's own recorder films this browser's whole life. The name
        # only makes the operator's inbox readable; nothing matches on it.
        entries["se:recordVideo"] = (True, ["option"])
        if video_name:
            entries["se:videoName"] = (video_name, ["option"])

    # config, then raw over it, in the order given; the own block waits.
    for source_name, given in (("config", config), ("raw", raw)):
        for key, value in given.items():
            if key != own_block:
                entries[key] = (copy.deepcopy(value), [source_name])

    block, block_sources, ignored = _merged(
        own_block, _flow_block(name), list(option_args), config, raw
    )
    entries[own_block] = (block, block_sources)

    # flow
    for key, value in _FLOW_KEYS.items():
        entries[key] = (value, ["flow"])

    send = {key: value for key, (value, _) in entries.items()}
    sent = [
        {"key": key, "value": copy.deepcopy(value), "sources": list(sources)}
        for key, (value, sources) in entries.items()
    ]
    return Built(
        browser=name,
        send=send,
        sent=sent,
        ignored=ignored,
        raw_keys=tuple(raw),
        version=sent_version,
        version_from=version_from if sent_version is not None else None,
    )


# ---- what a refused session means -------------------------------------------

# How Selenium Grid (4.48.0, `LocalNewSessionQueue`) says a request sat in its
# queue until `sessionRequestTimeout`: no node took it. Pinned by a test.
QUEUE_TIMEOUT = ("New session request timed out", "Timed out creating session")

# The Grid's own failures, from its router and queue rather than a node: they
# stay the plain 503 they always were (ruling 24). Pinned by a test.
GRID_SIDE = (
    "Unable to find owning node for slot",
    "Interrupted when creating the session",
    "An error occurred creating the session",
    "No capabilities found in session request payload",
    "Client has gone away",
)

# Extension capabilities the Grid never matches on (`DefaultSlotMatcher`).
_UNMATCHED = ("goog:", "moz:", "ms:", "safari:", "se:")

# The furniture appended to a node's message: the Grid's hosts, IPs and builds
# of the cluster, and Selenium's link to its own troubleshooting page — nothing
# a caller can act on.
_FURNITURE = re.compile(
    r"\s*(?:\b(?:Host|Build|System|Driver) info:"
    r"|;\s*For documentation on this error).*",
    re.DOTALL,
)

_SKILL = pointer("TROUBLESHOOTING.md")


def asked(built: Built) -> str:
    """What an open asked the Grid for, in the words it matches on.

    ``browserName`` and ``browserVersion``, then ``platformName`` and every
    extension the Grid matches (ruling 25): a scalar as ``key=value``,
    anything else as the key alone. Never a vendor block, ``proxy`` or a key
    the Grid ignores: those are where credentials live, and they cannot
    explain a miss anyway.
    """
    head = " ".join(
        str(built.send[key]) for key in ("browserName", "browserVersion")
        if key in built.send
    )
    parts = [head]
    for key, value in built.send.items():
        matched = key == "platformName" or (
            ":" in key and not key.startswith(_UNMATCHED)
        )
        if not matched:
            continue
        if isinstance(value, str):
            parts.append(f"{key}={value}")
        elif value is None or isinstance(value, (bool, int, float)):
            parts.append(f"{key}={json.dumps(value)}")
        else:
            parts.append(key)
    return ", ".join(parts)


def _reason(said: str) -> str:
    """A node's message without the Grid's furniture or a closing stop."""
    return _FURNITURE.sub("", said).strip().rstrip(".") or "no reason given"


def not_created(said: str, built: Built) -> Exception | None:
    """What a ``SessionNotCreatedException`` saying ``said`` means for ``built``.

    A queue timeout is `NoNodeTook`; a node's refusal is `NodeRefused`, blamed
    on the caller only when it asked for raw keys or a version of the
    caller's own (rulings 16, 4);
    the Grid's own failures are None, and the caller re-raises the original.
    Config capabilities are the operator's, so they never blame the caller.
    """
    if any(text in said for text in QUEUE_TIMEOUT):
        return NoNodeTook(
            "no Grid node took this browser before the Grid gave up waiting: "
            f"it asked for {asked(built)}. The Grid may have no node with these "
            f"capabilities, or none free; see {_SKILL}"
        )
    if any(text in said for text in GRID_SIDE):
        return None
    reason = _reason(said)
    # A config version is the operator's: it never blames the caller.
    callers_version = built.version is not None and built.version_from != "config"
    causes = []
    if built.raw_keys:
        causes.append(f"capabilities {', '.join(built.raw_keys)}")
    if callers_version:
        causes.append(f"version {built.version}, {VERSION_FROM[built.version_from]}")
    if causes:
        return NodeRefused(
            f"the Grid node refused this browser: {reason}. The usual cause is "
            f"what this open asked for: {' and '.join(causes)}; see {_SKILL}",
            blamed=True,
        )
    ours = (
        f"; the server's own session.version asks for {built.version}"
        if built.version is not None
        else ""
    )
    return NodeRefused(
        f"the Grid node could not start this browser: {reason}. Nothing this "
        f"open asked for is unusual, so trying again may succeed{ours}",
        blamed=False,
    )
