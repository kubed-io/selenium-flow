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

import json
from urllib.parse import urlsplit

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
        "origins": dict((existing or {}).get("origins") or {}),
        "saved_at": now,
    }
    if (existing or {}).get("pending"):
        data["pending"] = existing["pending"]
    origin = captured.get("origin") or ""
    sites, skipped = [], []
    if origin:
        entry = {
            "local": dict(captured.get("local") or {}),
            "session": {
                k: v for k, v in (captured.get("session") or {}).items()
                if not k.startswith(MARKER)
            },
            "saved_at": now,
        }
        trial = {**data, "origins": {**data["origins"], origin: entry}}
        if len(json.dumps(trial)) > MAX_BYTES:
            reason = f"storage over {MAX_BYTES} bytes"
            skipped.append({"site": origin, "reason": reason})
        else:
            data = trial
            sites.append(origin)
    return data, {"cookies": len(data["cookies"]), "sites": sites, "skipped": skipped}


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


def _hosts(data: dict) -> list[str]:
    stored = {host_of(o) for o in (data.get("origins") or {})}
    for c in data.get("cookies") or []:
        domain = c.get("domain") or ""
        if domain and not any(_covers(domain, h) for h in stored):
            stored.add(domain.lstrip("."))
    return sorted(h for h in stored if h)


def _cookie_view(c: dict) -> dict:
    return {
        "name": c.get("name"),
        "value": MASK if c.get("http_only") else c.get("value"),
        "domain": c.get("domain"),
        "path": c.get("path"),
        "expiry": c.get("expiry"),
        "http_only": bool(c.get("http_only")),
        "secure": bool(c.get("secure")),
        "same_site": c.get("same_site"),
        "shared": (c.get("domain") or "").startswith("."),
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


def _site(data: dict, host: str, secrets) -> dict:
    stored = data.get("origins") or {}
    origins = {o: e for o, e in stored.items() if host_of(o) == host}
    cookies = [
        c for c in data.get("cookies") or [] if _covers(c.get("domain") or "", host)
    ]
    local, session = {}, {}
    for entry in origins.values():
        local.update(entry.get("local") or {})
        session.update(entry.get("session") or {})
    saved = [e.get("saved_at") for e in origins.values() if e.get("saved_at")]
    return {
        "site": host,
        "origin": next(iter(sorted(origins)), None),
        "saved": bool(origins or cookies),
        "saved_at": max(saved) if saved else data.get("saved_at") if cookies else None,
        "uri": site_uri(host),
        "_cookies": cookies,
        "_local": local,
        "_session": session,
        "secrets": matching_secrets(secrets, host),
    }


def view(data: dict, secrets: list[dict] | None = None) -> dict:
    """The listing: one entry per site, counts only, never a value.

    With ``secrets`` (the admin catalogue listing), a site a secret is allowed
    on is listed even with nothing saved — secrets are not ephemeral, so their
    rows are not either.
    """
    hosts = set(_hosts(data or {}))
    for s in secrets or []:
        hosts.update(host_of(u) for u in s.get("allowed_urls") or [])
    rows = []
    for host in sorted(h for h in hosts if h):
        site = _site(data or {}, host, secrets)
        rows.append({
            "site": site["site"], "origin": site["origin"], "saved": site["saved"],
            "saved_at": site["saved_at"], "uri": site["uri"],
            "cookies": len(site["_cookies"]), "local_storage": len(site["_local"]),
            "session_storage": len(site["_session"]), "secrets": site["secrets"],
        })
    unleashed = sum(1 for s in secrets or [] if not s.get("allowed_urls"))
    return {
        "sites": rows,
        "saved_sites": sum(1 for r in rows if r["saved"]),
        "unleashed_secrets": unleashed,
        "uri": LIST_URI,
    }


def site_view(data: dict, site: str, secrets: list[dict] | None = None) -> dict | None:
    """One site in full: cookies (httpOnly masked) and both storages."""
    host = (site or "").lower()
    listed = {r["site"] for r in view(data, secrets)["sites"]}
    if host not in listed:
        return None
    s = _site(data or {}, host, secrets)
    return {
        "site": host, "origin": s["origin"], "saved": s["saved"],
        "saved_at": s["saved_at"], "uri": s["uri"],
        "cookies": [_cookie_view(c) for c in s["_cookies"]],
        "local_storage": s["_local"], "session_storage": s["_session"],
        "secrets": s["secrets"],
    }


def summary(data: dict) -> dict | None:
    sites = _hosts(data or {})
    return {"sites": len(sites), "uri": LIST_URI} if sites else None


def forget(data: dict, host: str) -> tuple[dict, dict]:
    """Remove one site: its origins and the cookies that are its own. A
    parent-domain cookie stays — other sites use it."""
    host = (host or "").lower()
    cookies = data.get("cookies") or []
    gone = [c for c in cookies if (c.get("domain") or "") == host]
    shared = [
        c for c in cookies
        if (c.get("domain") or "").startswith(".") and _covers(c["domain"], host)
    ]
    stored = data.get("origins") or {}
    origins = [o for o in stored if host_of(o) == host]
    left = {
        **data,
        "cookies": [c for c in cookies if c not in gone],
        "origins": {o: e for o, e in stored.items() if o not in origins},
    }
    return left, {
        "site": host, "cookies": [c["name"] for c in gone], "origins": origins,
        "kept_shared": [c["name"] for c in shared],
    }
