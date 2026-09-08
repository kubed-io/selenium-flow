"""Session settings, and where a value for one comes from.

Four things can be set when a browser opens: its window size, and the two
timeouts WebDriver lets you change after creation. Each can come from three
places, and the useful part is the order:

    server default (env var)  <  client default (URL param / header)  <  explicit

The server default is the operator's floor. The client default is set once in a
client's connection config, so an agent never has to think about it. The
explicit argument is the caller overriding both for one session.

Only settings that are actually *set* are returned, so "unset" stays
distinguishable from "set to the same value as the default" — the browser's own
default window size is not something this module should invent a number for.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# name -> (env var, query parameter, header)
SETTINGS = {
    "width": ("WINDOW_WIDTH", "width", "x-window-width"),
    "height": ("WINDOW_HEIGHT", "height", "x-window-height"),
    "page_load_timeout": (
        "PAGE_LOAD_TIMEOUT",
        "page_load_timeout",
        "x-page-load-timeout",
    ),
    "script_timeout": ("SCRIPT_TIMEOUT", "script_timeout", "x-script-timeout"),
}


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


def from_env(env: dict | None = None) -> dict:
    """The operator's defaults."""
    env = os.environ if env is None else env
    resolved = {}
    for name, (var, _param, _header) in SETTINGS.items():
        value = _as_int(env.get(var))
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
    for name, (_var, param, header) in SETTINGS.items():
        value = _as_int(headers.get(header))
        if value is None:
            value = _as_int(params.get(param))
        if value is not None:
            resolved[name] = value
    return resolved


def resolve(explicit: dict | None = None, env: dict | None = None) -> dict:
    """The settings a new session should open with.

    Reads the current request for client defaults, so it must be called while
    one is in flight. Off HTTP there simply are none.
    """
    from .sessions import http_request  # local: avoids a circular import

    http = http_request()
    params, headers = http if http else (None, None)

    merged = from_env(env)
    merged.update(from_client(params, headers))
    for name, value in (explicit or {}).items():
        if name in SETTINGS and value is not None:
            coerced = _as_int(value)
            if coerced is not None:
                merged[name] = coerced
    return merged
