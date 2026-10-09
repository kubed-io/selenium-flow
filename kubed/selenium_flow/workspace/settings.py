"""Session settings, and where a value for one comes from.

Six things can be set when a browser opens: which browser it is, its window
size, the two timeouts WebDriver lets you change after creation, and whether it
accepts an insecure site. Each but the last can come from three places, and the
useful part is the order:

    server default (config: session.*)  <  client default (param/header)  <
    this session's last values  <  explicit

The server default is the operator's floor. The client default is set once in a
client's connection config, so an agent never has to think about it. A flow
session's own last values come next: a caller that names nothing after its
browser was reaped or ended means "carry on where I was", which is a stronger
signal than any default and a weaker one than an argument it just typed. The
explicit argument overrides all of them.

Only settings that are actually *set* are returned, so "unset" stays
distinguishable from "set to the same value as the default" — the browser's own
default window size is not something this module should invent a number for.

What comes out of here is also what gets *stored* against a caller's session, so
a refresh after the Grid reaps a browser reopens the same one. That is why
``browser`` belongs in this cascade rather than beside it: a session that came
back as Chrome because the refresh path did not know it was Firefox would be the
same class of silent shape change the stored settings exist to prevent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..core.coerce import as_bool
from ..core.defaults import normalize_browser

if TYPE_CHECKING:
    from ..config import WorkspaceSettings

log = logging.getLogger(__name__)


def _as_int(value) -> int | None:
    """An int, or None for anything unusable.

    A bad value is ignored rather than fatal: a typo in a client's URL should
    not stop a browser from opening, and the resolved settings are reported on
    the session resource where the omission is visible.
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
    remembered true, or a session that once accepted a bad certificate could
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


# name -> (query parameter, header, coercion)
SETTINGS = {
    "browser": ("browser", "x-browser", _as_client_browser),
    "width": ("width", "x-window-width", _as_int),
    "height": ("height", "x-window-height", _as_int),
    "page_load_timeout": ("page_load_timeout", "x-page-load-timeout", _as_int),
    "script_timeout": ("script_timeout", "x-script-timeout", _as_int),
    # Explicit only (§F3.8): no parameter, no header, no default.
    "insecure": (None, None, _as_flag),
    # Explicit only, like insecure: no parameter, no header, no default. Stored
    # so a reap replays it, but `open_browser` never inherits it (recordings
    # spec, ruling 3): video is costly, and ending the browser ends it.
    "record": (None, None, _as_flag),
}

# The config's session section, as the operator's floor. `store` and `ttl` are
# about keeping sessions, not about the browser a session opens.
FROM_CONFIG = ("browser", "width", "height", "page_load_timeout", "script_timeout")


def from_settings(session: WorkspaceSettings) -> dict:
    """The operator's defaults: only what is set, so unset stays unset."""
    return {
        name: getattr(session, name)
        for name in FROM_CONFIG
        if getattr(session, name) is not None
    }


def from_client(params: dict | None, headers: dict | None) -> dict:
    """A client's defaults, set once in its connection config.

    The header wins over the query parameter, for the same reason it does for
    session names: the header lives in the credential an admin controls, while
    the URL is written by whoever wires up the call.
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


def resolve(
    explicit: dict | None = None,
    defaults: dict | None = None,
    previous: dict | None = None,
    client: dict | None = None,
) -> dict:
    """The settings a new session opens with (the cascade at the top of this module).

    ``client`` is the caller's own defaults (:func:`from_client`, read off its
    request by whoever holds it). Off HTTP there are none, and None says so.

    ``previous`` is what this flow session was last opened with, and it is
    applied over the two default sources — see the cascade at the top of this
    module. It is taken as already-resolved rather than re-coerced: it came out
    of this function, and a value that was good enough to open a browser with is
    not something to second-guess on the way back in.
    """
    merged = dict(defaults or {})
    merged.update(client or {})
    merged.update({k: v for k, v in (previous or {}).items() if k in SETTINGS})
    for name, value in (explicit or {}).items():
        if name not in SETTINGS or value is None:
            continue
        if name == "browser":
            # Strict, unlike every other setting and unlike the two default
            # sources above. A default is a preference and dropping a bad one
            # costs nothing; an explicit argument is this caller naming a
            # browser for this session, and quietly running it on a different
            # one is not a fallback, it is the wrong answer.
            merged[name] = normalize_browser(value)  # strict for an explicit argument
            continue
        coerced = SETTINGS[name][2](value)
        if coerced is not None:
            merged[name] = coerced
    return merged
