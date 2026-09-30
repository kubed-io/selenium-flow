"""Site data: the cookies and storage a session keeps for the sites it uses.

A session outlives its browser, and used to come back signed out: the page it
was on survived a reap, the sign-in did not. An agent now saves a site's data
when it knows it is worth keeping — after a sign-in is confirmed — and every
browser opened for the session gets it back (spec 2026-09-30).

Saved, not captured: every call paying for a snapshot was rejected, and a reap
gives no warning, so only an explicit save is dependable.

**A site is a host.** Cookies carry a domain and no scheme or port, so the view
groups by host. Storage is kept per origin, keyed by the page's own
``location.origin`` because that is the string the preload script compares.

Values are credentials. They live in the session store and nowhere else, are
never logged, and an httpOnly cookie's value is never shown on any surface.
"""

from __future__ import annotations

import contextlib
import json
from urllib.parse import urlsplit

from ..errors import message as failure_text

# The private key an action hands its capture back under. `SessionManager.act`
# merges it into the record and removes it; no caller ever sees it.
CAPTURED = "_site_data_captured"
# Marks a tab whose storage was already filled, so a later page on the same
# origin never overwrites what the app changed since. Never saved.
MARKER = "selenium-flow:restored:"
MAX_BYTES = 1_000_000
MASK = "•••"
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
            "session": {
                k: v for k, v in (captured.get("session") or {}).items()
                if not k.startswith(MARKER)
            },
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


def preload_source(origins: dict[str, dict]) -> str:
    """The script that fills each origin's storage before the page's own
    scripts run, once per tab. Every origin rides in one script because a
    preload script cannot be scoped to a URL."""
    payload = json.dumps(
        {o: {"local": e.get("local") or {}, "session": e.get("session") or {}}
         for o, e in origins.items()}
    )
    marker = json.dumps(MARKER)
    return (
        "() => { const all = " + payload + "; const o = location.origin; "
        "const s = all[o]; if (!s) return; const m = " + marker + " + o; "
        "try { if (sessionStorage.getItem(m)) return; "
        "for (const [k, v] of Object.entries(s.local)) localStorage.setItem(k, v); "
        "for (const [k, v] of Object.entries(s.session)) sessionStorage.setItem(k, v); "
        "sessionStorage.setItem(m, '1'); } catch (e) {} }"
    )


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
    local, session = {}, {}
    for entry in origins.values():
        local.update(entry.get("local") or {})
        session.update(entry.get("session") or {})
    saved = [e.get("saved_at") for e in origins.values() if e.get("saved_at")]
    return {
        "site": host,
        "origin": next(iter(sorted(origins)), None),
        # Only what Forget would remove: a row that merely sits under a
        # parent's shared cookie has nothing of its own to forget.
        "saved": bool(origins or own),
        "saved_at": max(saved) if saved else jar.data.get("saved_at") if own else None,
        "uri": site_uri(host),
        "_cookies": cookies,
        "_own": own,
        "_local": local,
        "_session": session,
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
            "site": site["site"], "origin": site["origin"], "saved": site["saved"],
            "saved_at": site["saved_at"], "uri": site["uri"],
            "cookies": len(site["_cookies"]), "local_storage": len(site["_local"]),
            "session_storage": len(site["_session"]), "secrets": site["secrets"],
        })
        details[host] = {
            "site": host, "origin": site["origin"], "saved": site["saved"],
            "saved_at": site["saved_at"], "uri": site["uri"],
            "cookies": [_cookie_view(c, host) for c in site["_cookies"]],
            "local_storage": site["_local"], "session_storage": site["_session"],
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
    """One site in full: cookies (httpOnly masked) and both storages."""
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
    """The whole cookie jar (BiDi sees httpOnly ones) and the page's storage."""
    from selenium.webdriver.common.bidi.storage import CookieFilter

    cookies = [
        {
            "name": c.name, "value": c.value.value, "value_type": c.value.type,
            "domain": c.domain, "path": c.path, "http_only": c.http_only,
            "secure": c.secure, "same_site": c.same_site, "expiry": c.expiry,
        }
        for c in bidi.storage.get_cookies(CookieFilter()).cookies
    ]
    page = driver.execute_script(READ_STORAGE) or {}
    return {
        "cookies": cookies,
        "origin": page.get("origin") or "",
        "local": page.get("local") or {},
        "session": page.get("session") or {},
    }


def _add_preload(bidi, origins: dict) -> str:
    added = bidi.script.add_preload_script(preload_source(origins))
    return added.get("script", "") if isinstance(added, dict) else added


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
    return [c for c in cookies if _key(c["name"], c["domain"], c.get("path")) in held]


def restore(bidi, data: dict, landing_url: str | None, now: float) -> tuple[dict, dict]:
    """Put a session's saved site data into a browser, before its first page.

    Cookies go in for every host, even ones never visited. Storage cannot, so
    one preload script fills each origin as it is first reached. Nothing raises:
    a report says what came back and what is waiting.
    """
    from selenium.webdriver.common.bidi.storage import BytesValue, PartialCookie

    report = {"restored": [], "waiting": [], "skipped": [], "uri": LIST_URI}
    pending = {"origins": [], "script": ""}
    try:
        cookies = live_cookies(data.get("cookies") or [], now)
        sent = []
        for c in cookies:
            try:
                bidi.storage.set_cookie(PartialCookie(
                    c["name"], BytesValue(c.get("value_type") or "string", c["value"]),
                    c["domain"], path=c.get("path"), http_only=c.get("http_only"),
                    secure=c.get("secure"), same_site=_same_site(c),
                    expiry=c.get("expiry"),
                ))
                sent.append(c)
            except Exception as e:  # noqa: BLE001 - one refusal must not stop the rest
                report["skipped"].append({
                    "cookie": c.get("name"), "domain": c.get("domain"),
                    "reason": failure_text(e),
                })
        # A browser can refuse a cookie without an error; only the jar says.
        kept = _kept(bidi, sent)
        if kept is None:
            kept = sent
        for c in sent:
            if c not in kept:
                report["skipped"].append({
                    "cookie": c.get("name"), "domain": c.get("domain"),
                    "reason": "the browser did not keep it",
                })
        origins = data.get("origins") or {}
        script = _add_preload(bidi, origins) if origins else ""
        landing = origin_of(landing_url or "")
        stored_hosts = {host_of(o) for o in origins}
        cookie_only = sorted({
            c["domain"].lstrip(".") for c in kept
            if not any(_covers(c["domain"], h) for h in stored_hosts)
        })
        first = [landing] if landing in origins else []
        report["restored"] = first + cookie_only
        report["waiting"] = sorted(o for o in origins if o not in first)
        pending = {"origins": list(report["waiting"]), "script": script}
        if first and report["waiting"]:
            # The script still carries the landing origin; it is swapped for
            # one that does not once the browser settles (see ``replace``).
            pending["arrived"] = first
    except Exception as e:  # noqa: BLE001 - BiDi failing is a report, not a crash
        return (
            {"restored": [], "waiting": [], "skipped": [{"reason": failure_text(e)}],
             "uri": LIST_URI},
            {"origins": [], "script": ""},
        )
    return report, pending


def retire(bidi, script: str) -> None:
    """Drop the preload script once every origin it waited on was filled.
    Best effort: it dies with the browser anyway."""
    with contextlib.suppress(Exception):
        bidi.script.remove_preload_script(script=script)


def replace(bidi, script: str, keep: dict | None) -> str:
    """Swap the preload script for one carrying only ``keep``'s origins.

    A preload script runs on every new document, and its once-per-tab marker
    lives in one tab's sessionStorage: left in place, it would refill an
    origin that already arrived the moment the app opened it in a new tab,
    over whatever the app changed since.

    Returns the id of the script now in the browser. A failed remove leaves the
    old one running, so its id is returned and nothing else happens; "" means
    none is left — nothing to keep, or the new one could not be added, in which
    case those origins cannot fill and the caller must stop waiting for them.
    """
    try:
        bidi.script.remove_preload_script(script=script)
    except Exception:  # noqa: BLE001 - the old script is still live; keep its id
        return script
    if not keep:
        return ""
    try:
        return _add_preload(bidi, keep)
    except Exception:  # noqa: BLE001 - those origins then come back signed out
        return ""
