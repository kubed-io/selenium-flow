"""Session settings, and where a value for one comes from.

These can be set when a browser opens: which browser it is and which version,
its window size, the two timeouts WebDriver lets you change after creation,
whether it accepts an insecure site, whether it runs headless, whether it is
recorded or captured, and the raw capabilities nothing else covers. Most can
come from four places, and the useful part is the order:

    server default (config: session.*)  <  client default (param/header)  <
    what this workspace's caller passed before  <  explicit

Only what a caller passed is remembered; the two defaults are read fresh at
each open, so a changed config reaches every workspace at its next open. The
server default is the operator's floor. The client default is set once in a
client's connection config, so an agent never has to think about it. What the
caller passed before comes next: a caller that names nothing after its browser
was reaped or ended means "carry on where I was", which is a stronger signal
than any default and a weaker one than an argument it just typed. The explicit
argument overrides all of them. :func:`sourced` also says which layer each value
came from.

Only settings that are actually *set* are returned, so "unset" stays
distinguishable from "set to the same value as the default" — the browser's own
default window size is not something this module should invent a number for.

What comes out of here is also what gets *stored* against a caller's workspace, so
a refresh after the Grid reaps a browser reopens the same one. That is why
``browser`` belongs in this cascade rather than beside it: a session that came
back as Chrome because the refresh path did not know it was Firefox would be the
same class of silent shape change the stored settings exist to prevent.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.coerce import as_bool
from ..core.defaults import normalize_browser
from ..core.session_caps import LATEST

if TYPE_CHECKING:
    from ..config import SessionSettings

log = logging.getLogger(__name__)


def _as_int(value) -> int | None:
    """An int, or None for anything unusable.

    A bad value is ignored rather than fatal: a typo in a client's URL should
    not stop a browser from opening, and the resolved settings are reported on
    ``workspace://current`` where the omission is visible.
    """
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        log.warning("ignoring unusable setting value %r", value)
        return None


def _as_flag(value) -> bool | None:
    """True or False when said, None when not.

    False is kept, not dropped: an explicit `insecure=false` has to beat a
    remembered true, or a workspace that once accepted a bad certificate could
    never stop (Copilot, #40).
    """
    return None if value in (None, "") else as_bool(value, False)


def _as_client_browser(value) -> str | None:
    """Lenient, like _as_int: a typo in a client's URL must not stop a browser
    opening."""
    if value in (None, ""):
        return None
    try:
        return normalize_browser(value)
    except ValueError as exc:
        log.warning("ignoring unusable browser default: %s", exc)
        return None


def _as_version(value) -> str | None:
    """A browser version, stripped, or None when blank.

    Any casing of `latest` is kept as `latest`: it is a value, so an explicit
    one beats a remembered version (spec 2026-10-26-session-capabilities,
    ruling 19). Lenient in one way only: a client's version is any string,
    since what the Grid serves is the Grid's to say.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return LATEST if text.lower() == LATEST else text


CAPABILITIES_NOT_AN_OBJECT = "capabilities must be an object"


def _as_capabilities(value) -> dict | None:
    """The caller's raw capabilities, as a fresh dict, or None when not given.

    `{}` is kept: it clears a remembered object, the way `insecure=false`
    beats a remembered true (ruling 13). A JSON string is read, for a caller
    that could only send text; anything that is not an object is refused,
    since sending it would be guessing.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError(CAPABILITIES_NOT_AN_OBJECT) from None
    if not isinstance(value, Mapping):
        raise ValueError(CAPABILITIES_NOT_AN_OBJECT)
    return copy.deepcopy(dict(value))


# name -> (query parameter, header, coercion)
SETTINGS = {
    "browser": ("browser", "x-browser", _as_client_browser),
    # `browser`'s twin (ruling 10): a client may pin a version as it pins a
    # browser. `latest` clears a remembered one.
    "version": ("version", "x-version", _as_version),
    "width": ("width", "x-window-width", _as_int),
    "height": ("height", "x-window-height", _as_int),
    "page_load_timeout": ("page_load_timeout", "x-page-load-timeout", _as_int),
    "script_timeout": ("script_timeout", "x-script-timeout", _as_int),
    # Explicit only (§F3.8): no parameter, no header, no default.
    "insecure": (None, None, _as_flag),
    # Explicit only, like insecure: no parameter, no header, no default, and not
    # in FROM_CONFIG. Remembered until `headless=false` is passed.
    "headless": (None, None, _as_flag),
    # Explicit only, like insecure: no parameter, no header, no default. Stored
    # so a reap replays it, but `open_browser` never inherits it (recordings
    # spec, ruling 3): video is costly, and ending the browser ends it.
    "record": (None, None, _as_flag),
    # Explicit only, like record: stored so a reap replays it, never inherited
    # by an explicit open (capture spec, ruling 3): ask again for the next one.
    "capture": (None, None, _as_flag),
    # Explicit only: no parameter and no header (ruling 10). Remembered whole,
    # and `{}` clears it (ruling 13). The config's `session.capabilities` is
    # not its floor but a source of its own, merged under it at every open.
    "capabilities": (None, None, _as_capabilities),
}

# The config's session section, as the operator's floor. `capabilities` is not
# here: it is its own source, beside the cascade (`Actions.config_capabilities`).
FROM_CONFIG = (
    "browser",
    "version",
    "width",
    "height",
    "page_load_timeout",
    "script_timeout",
)


def from_settings(session: SessionSettings) -> dict:
    """The operator's defaults: only what is set, so unset stays unset."""
    return {
        name: getattr(session, name)
        for name in FROM_CONFIG
        if getattr(session, name) is not None
    }


def from_client(params: dict | None, headers: dict | None) -> dict:
    """A client's defaults, set once in its connection config.

    The header wins over the query parameter: the header lives in the
    credential an admin controls, while the URL is written by whoever wires up
    the call. (A workspace name is stricter: both at once is refused.)
    """
    params = params or {}
    headers = headers or {}
    resolved = {}
    for name, (param, header, coerce) in SETTINGS.items():
        if header is None:
            continue
        value = coerce(headers.get(header))
        if value is None:
            value = coerce(params.get(param))
        if value is not None:
            resolved[name] = value
    return resolved


CONFIG, CLIENT, CALLER = "config", "client", "caller"
SET_BY = (CONFIG, CLIENT, CALLER)


@dataclass(frozen=True)
class Sourced:
    """The settings a session opens with, and which layer each one came from.

    ``set_by`` has the same keys as ``settings``. ``version_from`` is where the
    version in effect came from (``config``, ``client``, ``call`` or
    ``remembered``), None when there is none.
    """

    settings: dict
    set_by: dict[str, str]
    version_from: str | None


def version_from(set_by: Mapping[str, str]) -> str | None:
    """Where a stored version came from, as a reap sees it: what the caller
    passed is now remembered, the rest is the layer that supplied it."""
    source = set_by.get("version")
    if source is None:
        return None
    return "remembered" if source == CALLER else source


def sourced(
    explicit: dict | None = None,
    defaults: dict | None = None,
    previous: dict | None = None,
    client: dict | None = None,
) -> Sourced:
    """The settings a new session opens with (the cascade at the top of this module).

    ``client`` is the caller's own defaults (:func:`from_client`, read off its
    request by whoever holds it). Off HTTP there are none, and None says so.

    ``previous`` is what this workspace's caller passed before, and it is applied
    over the two default sources. It is taken as already-resolved rather than
    re-coerced: it came out of this function, and a value that was good enough to
    open a browser with is not something to second-guess on the way back in.
    So are ``defaults`` and ``client``, which come from :func:`from_settings` and
    :func:`from_client`.

    The layers are walked in order, and a layer that names a different browser
    than the one in effect drops the version in effect: a version belongs to its
    browser (ruling 2). A version passed alone pins the browser in effect as the
    caller's own, so a later change of the configured browser does not move it
    (ruling 12).
    """
    chosen: dict = {}
    for name, value in (explicit or {}).items():
        if name not in SETTINGS or value is None:
            continue
        if name == "browser":
            # Strict, unlike every other setting and unlike the two default
            # sources. A default is a preference and dropping a bad one costs
            # nothing; an explicit argument is this caller naming a browser for
            # this session, and quietly running it on a different one is not a
            # fallback, it is the wrong answer.
            chosen[name] = normalize_browser(value)
            continue
        coerced = SETTINGS[name][2](value)
        if coerced is not None:
            chosen[name] = coerced
    remembered = {k: v for k, v in (previous or {}).items() if k in SETTINGS}

    settings: dict = {}
    set_by: dict[str, str] = {}
    layers = (
        (CONFIG, defaults or {}),
        (CLIENT, client or {}),
        (CALLER, remembered),
        (CALLER, chosen),
    )
    for source, layer in layers:
        named = layer.get("browser")
        if named is not None and normalize_browser(named) != normalize_browser(
            settings.get("browser")
        ):
            settings.pop("version", None)
            set_by.pop("version", None)
        for key, value in layer.items():
            settings[key] = value
            set_by[key] = source

    version = chosen.get("version")
    if version is not None and version != LATEST and "browser" not in chosen:
        settings["browser"] = normalize_browser(settings.get("browser"))
        set_by["browser"] = CALLER

    if "version" not in settings:
        came = None
    elif version is not None:
        came = "call"
    else:
        came = version_from(set_by)
    return Sourced(settings, set_by, came)


def resolve(
    explicit: dict | None = None,
    defaults: dict | None = None,
    previous: dict | None = None,
    client: dict | None = None,
) -> dict:
    """The settings alone; :func:`sourced` says where each came from."""
    return sourced(explicit, defaults, previous, client).settings
