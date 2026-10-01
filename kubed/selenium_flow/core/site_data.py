"""Site data: the cookies and storage a session keeps for the sites it uses.

A session outlives its browser, and used to come back signed out: the page it
was on survived a reap, the sign-in did not. An agent now saves a site's data
when it knows it is worth keeping — after a sign-in is confirmed — and every
browser opened for the session gets it back (spec 2026-09-30).

Saved, not captured: every call paying for a snapshot was rejected, and a reap
gives no warning, so only an explicit save is dependable.

**A site is a host.** Cookies carry a domain and no scheme or port, so the view
groups by host. Storage is kept per origin, as ``location.origin`` spells it.

Values are credentials. They live in the session store and nowhere else, are
never logged, and an httpOnly cookie's value is never shown on any surface.
"""

from __future__ import annotations

import contextlib
import json
from urllib.parse import urlsplit

from ..errors import BidiUnavailable
from ..errors import message as failure_text
from .browser import ServiceWorkerAnswered, spare_tab

# The private key an action hands its capture back under. `SessionManager.act`
# merges it into the record and removes it; no caller ever sees it.
CAPTURED = "_site_data_captured"
MAX_BYTES = 1_000_000
MASK = "•••"
BIDI_DOWN = (
    "the browser's BiDi channel is unavailable, so nothing was saved: retry, "
    "and if it persists the Grid's /se/bidi route is down"
)
# Why an origin's storage was not read: its own service worker answered the
# spare tab (spec round 2, *Service workers at save time*).
SW_REASON = "a service worker answered: save while on this site"
LIST_URI = "session://site-data"
DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str) -> str:
    """``url``'s origin as ``location.origin`` spells it, or "" when it has none."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if not parts.scheme or not host:
        return ""
    scheme = parts.scheme.lower()
    # `hostname` drops an IPv6 literal's brackets; `location.origin` keeps them.
    if ":" in host:
        host = f"[{host}]"
    shown = f":{port}" if port is not None and port != DEFAULT_PORTS.get(scheme) else ""
    return f"{scheme}://{host}{shown}"


def host_of(url: str) -> str:
    try:
        return (urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def site_uri(host: str) -> str:
    return f"{LIST_URI}/{host}"


def merge(existing: dict, captured: dict, now: float) -> tuple[dict, dict]:
    """Fold one save into what the session already had.

    The cookies are the whole jar and replace the last save's; storage is the
    one origin the page was on, added beside the others. A save that would
    pass the cap keeps its cookies and leaves that origin's storage out,
    saying so, rather than failing or truncating.
    """
    data = {
        "cookies": list(captured.get("cookies") or []),
        "origins": {},
        "saved_at": now,
    }
    if (existing or {}).get("pending"):
        data["pending"] = existing["pending"]
    cookies_only = data
    data = {**data, "origins": dict((existing or {}).get("origins") or {})}
    if _size(cookies_only) > MAX_BYTES:
        # The jar is the one thing a save cannot leave out, and evicting other
        # sites would not make it fit.
        raise ValueError(
            f"the cookie jar is over {MAX_BYTES} bytes; nothing was saved"
        )
    origin = captured.get("origin") or ""
    sites, skipped, protect = [], [], None
    if origin:
        entry = {
            "local": dict(captured.get("local") or {}),
            "session": dict(captured.get("session") or {}),
            "saved_at": now,
        }
        alone = {**cookies_only, "origins": {origin: entry}}
        if _size(alone) > MAX_BYTES:
            reason = f"storage over {MAX_BYTES} bytes"
            skipped.append({"site": origin, "reason": reason})
        else:
            data = {**data, "origins": {**data["origins"], origin: entry}}
            sites.append(origin)
            protect = origin
    # The cap is on what is stored, not on this one origin: the oldest other
    # sites make room, each said so.
    while _size(data) > MAX_BYTES:
        others = [o for o in data["origins"] if o != protect]
        oldest = min(others, key=lambda o: data["origins"][o].get("saved_at") or 0)
        data["origins"] = {o: e for o, e in data["origins"].items() if o != oldest}
        skipped.append({"site": oldest, "reason": f"evicted: over {MAX_BYTES} bytes"})
    return data, {"cookies": len(data["cookies"]), "sites": sites, "skipped": skipped}


def _size(data: dict) -> int:
    return len(json.dumps(data))


def live_cookies(cookies: list[dict], now: float) -> list[dict]:
    """Every cookie a restore should set: expired ones dropped, session
    cookies (no expiry) kept, because the session they belong to is ours and
    outlived the browser."""
    return [c for c in cookies if c.get("expiry") is None or c["expiry"] > now]


# Evaluated in a spare tab standing on an origin: its localStorage, whole.
READ_LOCAL = (
    "(() => { const out = {}; for (let i = 0; i < localStorage.length; i++) "
    "{ const k = localStorage.key(i); out[k] = localStorage.getItem(k); } "
    "return out; })()"
)


def fill(store: str, items: dict) -> str:
    """An expression that sets every item in ``store`` (``localStorage`` or
    ``sessionStorage``) and evaluates to how many it set."""
    return (
        "(() => { const items = " + json.dumps(items) + "; "
        "for (const [k, v] of Object.entries(items)) " + store + ".setItem(k, v); "
        "return Object.keys(items).length; })()"
    )


def restorable(data: dict) -> bool:
    """Whether a snapshot holds anything a restore could put back."""
    if not isinstance(data, dict):
        return False
    session = data.get("session")
    return bool(
        data.get("cookies") or data.get("origins")
        or (isinstance(session, dict) and session.get("items"))
    )


def _why(exc: Exception) -> str:
    return SW_REASON if isinstance(exc, ServiceWorkerAnswered) else failure_text(exc)


def _covers(domain: str, host: str) -> bool:
    bare = domain.lstrip(".")
    return host == bare or (domain.startswith(".") and host.endswith(domain))


def _own(domain: str, host: str) -> bool:
    """A cookie a host's row owns: set for the host itself, or ``.host``.
    A parent's leading-dot cookie only covers it; other rows use that one."""
    return domain in (host, "." + host)


def _suffixes(host: str) -> list[str]:
    """``a.b.com`` -> ``b.com``, ``com``: the parents whose dotted cookies
    cover it."""
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(1, len(parts))]


class _Jar:
    """One pass over the cookies and origins, so every host's slice is a few
    dict lookups instead of a scan of the whole jar per host."""

    def __init__(self, data: dict):
        self.data = data or {}
        self.cookies = self.data.get("cookies") or []
        self.by_domain: dict[str, list[int]] = {}
        for i, c in enumerate(self.cookies):
            if c.get("domain"):
                self.by_domain.setdefault(c["domain"], []).append(i)
        self.origins: dict[str, dict] = {}
        for o, e in (self.data.get("origins") or {}).items():
            self.origins.setdefault(host_of(o), {})[o] = e

    def covering(self, host: str) -> list[dict]:
        """Cookies that reach ``host``, in the order the jar holds them."""
        keys = [host, "." + host, *("." + s for s in _suffixes(host))]
        found = sorted({i for k in keys for i in self.by_domain.get(k, ())})
        return [self.cookies[i] for i in found]

    def hosts(self) -> list[str]:
        # Storage hosts first and never grown while the cookies are read, so
        # the rows do not depend on the order the browser listed its cookies in.
        stored = set(self.origins)
        reached = set(stored)
        for h in stored:
            reached.update("." + s for s in _suffixes(h))
        bare = {
            d.lstrip(".")
            for d in self.by_domain
            if not (d in reached or (d.startswith(".") and d.lstrip(".") in stored))
        }
        return sorted(h for h in stored | bare if h)


def _hosts(data: dict) -> list[str]:
    return _Jar(data).hosts()


def _cookie_view(c: dict, host: str) -> dict:
    return {
        "name": c.get("name"),
        "value": MASK if c.get("http_only") else c.get("value"),
        "domain": c.get("domain"),
        "path": c.get("path"),
        "expiry": c.get("expiry"),
        "http_only": bool(c.get("http_only")),
        "secure": bool(c.get("secure")),
        "same_site": c.get("same_site"),
        # Shared with other sites only when a parent domain set it; `.host`
        # is this row's own (Forget removes it).
        "shared": (c.get("domain") or "").startswith(".")
        and not _own(c.get("domain") or "", host),
    }


def matching_secrets(secrets: list[dict] | None, host: str) -> list[dict]:
    """The secrets allowed on ``host``, names and keys only. A secret with no
    `allowed_urls` is usable anywhere and is not listed under every site."""
    return [
        {
            "name": s["name"],
            "description": s.get("description") or "",
            "keys": list(s.get("keys") or []),
        }
        for s in secrets or []
        if any(host_of(u) == host for u in s.get("allowed_urls") or [])
    ]


def _site(jar: _Jar, host: str, secrets) -> dict:
    origins = jar.origins.get(host, {})
    cookies = jar.covering(host)
    own = [c for c in cookies if _own(c.get("domain") or "", host)]
    # Per origin, never merged: the same host on two ports or schemes is two
    # origins, each restored with its own storage.
    storage = [
        {
            "origin": o,
            "local_storage": dict(origins[o].get("local") or {}),
            "session_storage": dict(origins[o].get("session") or {}),
        }
        for o in sorted(origins)
    ]
    saved = [e.get("saved_at") for e in origins.values() if e.get("saved_at")]
    return {
        "site": host,
        # Only what Forget would remove: a row that merely sits under a
        # parent's shared cookie has nothing of its own to forget.
        "saved": bool(origins or own),
        "saved_at": max(saved) if saved else jar.data.get("saved_at") if own else None,
        "uri": site_uri(host),
        "_cookies": cookies,
        "_own": own,
        "storage": storage,
        "secrets": matching_secrets(secrets, host),
    }


def _shared(cookies: list[dict], host: str) -> list[dict]:
    """Covering cookies that stay when ``host`` is forgotten: a parent's
    leading-dot cookie, which other rows use."""
    return [
        c for c in cookies
        if (c.get("domain") or "").startswith(".") and not _own(c["domain"], host)
    ]


def _identity(c: dict) -> dict:
    # Two cookies can share a name (`sid` on .example.com and .example.org), so
    # a name alone cannot say which one stays.
    return {"name": c["name"], "domain": c.get("domain"), "path": c.get("path")}


def _rows(data: dict, secrets: list[dict] | None):
    """Every site once: ``(listing, details)`` from a single grouping of the
    jar, so neither costs a scan per host."""
    jar = _Jar(data)
    hosts = set(jar.hosts())
    for s in secrets or []:
        hosts.update(host_of(u) for u in s.get("allowed_urls") or [])
    rows, details = [], {}
    for host in sorted(h for h in hosts if h):
        site = _site(jar, host, secrets)
        rows.append({
            "site": site["site"], "saved": site["saved"],
            "saved_at": site["saved_at"], "uri": site["uri"],
            "cookies": len(site["_cookies"]),
            "storage": [
                {
                    "origin": e["origin"],
                    "local_storage": len(e["local_storage"]),
                    "session_storage": len(e["session_storage"]),
                }
                for e in site["storage"]
            ],
            "secrets": site["secrets"],
        })
        details[host] = {
            "site": host, "saved": site["saved"],
            "saved_at": site["saved_at"], "uri": site["uri"],
            "cookies": [_cookie_view(c, host) for c in site["_cookies"]],
            "storage": site["storage"],
            # What Forget would do, by the rule Forget itself uses.
            "own_cookies": [c["name"] for c in site["_own"]],
            "kept_shared": [_identity(c) for c in _shared(site["_cookies"], host)],
            "secrets": site["secrets"],
        }
    unleashed = sum(1 for s in secrets or [] if not s.get("restricted"))
    listing = {
        "sites": rows,
        "saved_sites": sum(1 for r in rows if r["saved"]),
        "unleashed_secrets": unleashed,
        "uri": LIST_URI,
    }
    return listing, details


def view(data: dict, secrets: list[dict] | None = None) -> dict:
    """The listing: one entry per site, counts only, never a value.

    With ``secrets`` (the admin catalogue listing), a site a secret is allowed
    on is listed even with nothing saved — secrets are not ephemeral, so their
    rows are not either.
    """
    return _rows(data, secrets)[0]


def views(data: dict, secrets: list[dict] | None = None) -> tuple[dict, dict]:
    """``(listing, details)``: the listing and every site in full, from one
    pass over the jar."""
    return _rows(data, secrets)


def site_view(data: dict, site: str, secrets: list[dict] | None = None) -> dict | None:
    """One site in full: cookies (httpOnly masked) and both storages, per
    origin."""
    return _rows(data, secrets)[1].get((site or "").lower())


def summary(data: dict) -> dict | None:
    sites = _hosts(data or {})
    return {"sites": len(sites), "uri": LIST_URI} if sites else None


def forget(data: dict, host: str) -> tuple[dict, dict]:
    """Remove one site: its origins and the cookies named for it (``host`` or
    ``.host``). A parent-domain cookie of another row stays — others use it."""
    host = (host or "").lower()
    jar = _Jar(data)
    covering = jar.covering(host)
    gone = [c for c in covering if _own(c.get("domain") or "", host)]
    shared = _shared(covering, host)
    gone_ids = {id(c) for c in gone}
    stored = data.get("origins") or {}
    origins = [o for o in stored if host_of(o) == host]
    left = {
        **data,
        "cookies": [c for c in jar.cookies if id(c) not in gone_ids],
        "origins": {o: e for o, e in stored.items() if o not in origins},
    }
    return left, {
        "site": host, "cookies": [c["name"] for c in gone], "origins": origins,
        "kept_shared": [_identity(c) for c in shared],
    }


# Each store is reached inside the try: on a page with no usable storage
# (about:blank, data:) merely naming `localStorage` throws SecurityError, and
# that page still saves its cookies.
READ_STORAGE = (
    "const dump = (get) => { const out = {}; try { const s = get(); "
    "for (let i = 0; i < s.length; i++) "
    "{ const k = s.key(i); out[k] = s.getItem(k); } } catch (e) {} return out; }; "
    "return {origin: location.origin === 'null' ? '' : location.origin, "
    "local: dump(() => localStorage), session: dump(() => sessionStorage)};"
)


def capture(bidi, driver) -> dict:
    """The whole cookie jar (BiDi sees httpOnly ones) and the page's storage.

    The browser answered classic WebDriver to get here, so a jar that cannot
    be read is the BiDi channel failing: :class:`BidiUnavailable`, a 503.
    """
    from selenium.common import WebDriverException
    from selenium.webdriver.common.bidi.storage import CookieFilter
    from websocket import WebSocketException

    try:
        jar = bidi.storage.get_cookies(CookieFilter()).cookies
    except (WebDriverException, WebSocketException, OSError) as e:
        # Only a channel that did not answer is a 503. Anything else - an
        # unexpected return shape, a bug here - stays a 500, as errors.py
        # decides for every failure it does not recognise (Copilot, #50).
        raise BidiUnavailable(BIDI_DOWN) from e
    cookies = [
        {
            "name": c.name, "value": c.value.value, "value_type": c.value.type,
            "domain": c.domain, "path": c.path, "http_only": c.http_only,
            "secure": c.secure, "same_site": c.same_site, "expiry": c.expiry,
        }
        for c in jar
    ]
    page = driver.execute_script(READ_STORAGE) or {}
    return {
        "cookies": cookies,
        "origin": page.get("origin") or "",
        "local": page.get("local") or {},
        "session": page.get("session") or {},
    }


def _same_site(c: dict):
    # Chrome reports an unspecified SameSite as "none" and silently refuses
    # None without Secure, so the cookie would never come back.
    if c.get("same_site") == "none" and not c.get("secure"):
        return "lax"
    return c.get("same_site")


def _key(name, domain, path) -> tuple:
    return (name, domain, path or "/")


def _kept(bidi, cookies: list[dict]) -> list[dict] | None:
    """The cookies the browser actually holds after setting, or None when the
    jar cannot be read back (then the set is trusted)."""
    from selenium.webdriver.common.bidi.storage import CookieFilter

    try:
        jar = bidi.storage.get_cookies(CookieFilter()).cookies
    except Exception:  # noqa: BLE001 - an unverified restore is still a restore
        return None
    held = {_key(c.name, c.domain, c.path) for c in jar}
    return [
        c for c in cookies
        if _key(c.get("name"), c.get("domain"), c.get("path")) in held
    ]


MALFORMED = "a stored cookie with no name or domain"
BAD_EXPIRY = "a stored cookie whose expiry is not a time"


def _usable(c) -> bool:
    return (
        isinstance(c, dict)
        and isinstance(c.get("name"), str) and bool(c["name"])
        and isinstance(c.get("domain"), str) and bool(c["domain"])
        # Compared with `now` before any per-cookie guard: a string here took
        # the whole restore down instead of skipping one cookie (Copilot, #50).
        and (c.get("expiry") is None or (
            isinstance(c["expiry"], (int, float)) and not isinstance(c["expiry"], bool)
        ))
    )


def _malformed(c) -> dict:
    """A skip entry for a stored cookie restore cannot use: only the fields
    that are strings, since the published shape says so (Copilot, #50)."""
    entry = {"reason": MALFORMED}
    if isinstance(c, dict):
        for field, key in (("cookie", "name"), ("domain", "domain")):
            if isinstance(c.get(key), str):
                entry[field] = c[key]
        if entry.get("cookie") and entry.get("domain"):
            # Name and domain are fine, so it was the expiry: say which field
            # is corrupt rather than blaming the two that are not (Copilot, #50).
            entry["reason"] = BAD_EXPIRY
    return entry


def _restore_cookies(bidi, stored: list, now: float, skipped: list) -> list[dict]:
    """Set every live, usable cookie; return the ones the browser kept. Each
    one refused, dropped or unusable is added to ``skipped``."""
    from selenium.webdriver.common.bidi.storage import BytesValue, PartialCookie

    cookies = live_cookies([c for c in stored if _usable(c)], now)
    # One bad entry is skipped, not the whole restore.
    skipped.extend(_malformed(c) for c in stored if not _usable(c))
    sent = []
    for c in cookies:
        try:
            bidi.storage.set_cookie(PartialCookie(
                c["name"],
                BytesValue(c.get("value_type") or "string", c.get("value")),
                c["domain"], path=c.get("path"), http_only=c.get("http_only"),
                secure=c.get("secure"), same_site=_same_site(c),
                expiry=c.get("expiry"),
            ))
            sent.append(c)
        except Exception as e:  # noqa: BLE001 - one refusal must not stop the rest
            skipped.append({
                "cookie": c.get("name"), "domain": c.get("domain"),
                "reason": failure_text(e),
            })
    # A browser can refuse a cookie without an error; only the jar says.
    kept = _kept(bidi, sent)
    if kept is None:
        kept = sent
    skipped.extend(
        {"cookie": c.get("name"), "domain": c.get("domain"),
         "reason": "the browser did not keep it"}
        for c in sent if c not in kept
    )
    return kept


def restore(bidi, data: dict, now: float) -> dict:
    """Put a snapshot into a browser before its first page: every live
    cookie, then each origin's localStorage in a spare tab, then the saved
    sessionStorage in the main tab, which is left on about:blank for the
    landing. Everything is in place when this returns.

    Best effort, and nothing raises: the report names each host that came
    back and each item that did not, with why.
    """
    skipped: list[dict] = []
    kept: list[dict] = []
    filled: list[str] = []
    try:
        kept = _restore_cookies(bidi, data.get("cookies") or [], now, skipped)
        local = {
            o: e["local"] for o, e in (data.get("origins") or {}).items()
            if isinstance(e, dict) and e.get("local")
        }
        if local:
            try:
                with spare_tab(bidi) as run:
                    for origin, items in local.items():
                        try:
                            run(origin, fill("localStorage", items))
                            filled.append(origin)
                        except Exception as e:  # noqa: BLE001 - one origin, not the rest
                            skipped.append({"site": origin, "reason": _why(e)})
            except Exception as e:  # noqa: BLE001 - no tab: each origin not yet filled
                done = {s.get("site") for s in skipped}
                skipped.extend(
                    {"site": o, "reason": failure_text(e)}
                    for o in local if o not in filled and o not in done
                )
        session = data.get("session") or {}
        if session.get("origin") and session.get("items"):
            try:
                main = bidi.current_window_handle
                with spare_tab(bidi, context=main) as run:
                    try:
                        run(session["origin"], fill("sessionStorage", session["items"]))
                    finally:
                        # The stand-in page must never be what an open with no
                        # url leaves on screen, filled or not; the tab keeps its
                        # sessionStorage.
                        with contextlib.suppress(Exception):
                            bidi.browsing_context.navigate(
                                context=main, url="about:blank", wait="complete"
                            )
                filled.append(session["origin"])
            except Exception as e:  # noqa: BLE001 - the rest stands
                skipped.append({"site": session["origin"], "reason": _why(e)})
    except Exception as e:  # noqa: BLE001 - BiDi failing is a report, not a crash
        skipped.append({"reason": failure_text(e)})
    return {
        "restored": _hosts({"cookies": kept, "origins": {o: {} for o in filled}}),
        "skipped": skipped,
        "uri": LIST_URI,
    }
