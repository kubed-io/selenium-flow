"""Session settings, and where a value for one comes from.

Five things can be set when a browser opens: which browser it is, its window
size, and the two timeouts WebDriver lets you change after creation. Each can
come from three places, and the useful part is the order:

    server default (env)  <  client default (param/header)  <  this session's
    last values  <  explicit

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
import os

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


def _as_browser(value) -> str | None:
    """A supported browser name, or None for anything unusable.

    Lenient, like ``_as_int``, because this is the coercion the two *default*
    sources use. `DEFAULT_BROWSER` is deliberately not spelled `BROWSER`: that
    name is a long-standing Unix convention for the user's preferred web
    browser command, and plenty of environments — code-server among them — set
    it to a shell script. A server that refused to open a session because of
    that would be broken by something that has nothing to do with it.
    """
    if value is None or value == "":
        return None
    from .browser import normalize_browser  # local: keeps this module importable

    try:
        return normalize_browser(value)
    except ValueError as exc:
        log.warning("ignoring unusable browser default: %s", exc)
        return None


# name -> (env var, query parameter, header, coercion)
SETTINGS = {
    "browser": ("DEFAULT_BROWSER", "browser", "x-browser", _as_browser),
    "width": ("WINDOW_WIDTH", "width", "x-window-width", _as_int),
    "height": ("WINDOW_HEIGHT", "height", "x-window-height", _as_int),
    "page_load_timeout": (
        "PAGE_LOAD_TIMEOUT",
        "page_load_timeout",
        "x-page-load-timeout",
        _as_int,
    ),
    "script_timeout": (
        "SCRIPT_TIMEOUT",
        "script_timeout",
        "x-script-timeout",
        _as_int,
    ),
}


def from_env(env: dict | None = None) -> dict:
    """The operator's defaults."""
    env = os.environ if env is None else env
    resolved = {}
    for name, (var, _param, _header, coerce) in SETTINGS.items():
        value = coerce(env.get(var))
        if value is not None:
            resolved[name] = value
    return resolved


def from_client(params: dict | None, headers: dict | None) -> dict:
    """A client's defaults, set once in its connection config.

    The header wins over the query parameter, for the same reason it does for
    session names: the header lives in the credential an admin controls, while
    the URL is written by whoever wires up the call.
    """
    params = params or {}
    headers = headers or {}
    resolved = {}
    for name, (_var, param, header, coerce) in SETTINGS.items():
        value = coerce(headers.get(header))
        if value is None:
            value = coerce(params.get(param))
        if value is not None:
            resolved[name] = value
    return resolved


def resolve(
    explicit: dict | None = None,
    env: dict | None = None,
    previous: dict | None = None,
) -> dict:
    """The settings a new session should open with.

    Reads the current request for client defaults, so it must be called while
    one is in flight. Off HTTP there simply are none.

    ``previous`` is what this flow session was last opened with, and it is
    applied over the two default sources — see the cascade at the top of this
    module. It is taken as already-resolved rather than re-coerced: it came out
    of this function, and a value that was good enough to open a browser with is
    not something to second-guess on the way back in.
    """
    from .sessions import http_request  # local: avoids a circular import

    http = http_request()
    params, headers = http if http else (None, None)

    merged = from_env(env)
    merged.update(from_client(params, headers))
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
            from .browser import normalize_browser

            merged[name] = normalize_browser(value)
            continue
        coerced = SETTINGS[name][3](value)
        if coerced is not None:
            merged[name] = coerced
    return merged
