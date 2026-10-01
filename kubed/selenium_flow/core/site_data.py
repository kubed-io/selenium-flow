"""Site data: the cookies and storage a session keeps for the sites it uses.

A session outlives its browser, and used to come back signed out: the page it
was on survived a reap, the sign-in did not. An agent saves when it knows the
browser is worth keeping — after a sign-in is confirmed — and every browser
opened for the session gets it back (spec 2026-09-30, rounds 1 and 2).

Saved, not captured: every call paying for a snapshot was rejected, and a reap
gives no warning, so only an explicit save is dependable.

**A save is a snapshot.** It replaces the last one whole: the jar, the
localStorage of every origin the session has been to, and the sessionStorage
of the page it is on. Other origins are reached through a spare tab
(`browser.spare_tab`), which is also how a restore writes them back before the
first page loads.

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

# The private key an action hands its capture back under. `SessionManager.settle`
# removes it and stores it as the snapshot; no caller ever sees it.
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


# Why an origin's storage was left out of a snapshot.
LEFT_OUT = f"left out: the snapshot would pass {MAX_BYTES} bytes"


def snapshot(previous: dict, captured: dict, history, now: float) -> tuple[dict, dict]:
    """One save, replacing the last whole: nothing is merged, so a sign-out
    saved after a sign-in is what comes back.

    ``captured`` is what :func:`capture` read. ``origins`` keeps the
    localStorage of every origin read that has any, newest first — the page,
    then the history. ``session`` is the page's sessionStorage, for its
    origin. An origin that could not be read keeps its storage from
    ``previous``, so a blip does not sign the agent out. Over
    :data:`MAX_BYTES` the origins visited longest ago go first and the page's
    own storage last; a jar that alone passes it is a ValueError, and nothing
    is saved.

    Returns ``(data, receipt)``; the receipt names what was saved, never a
    value.
    """
    here = captured.get("origin") or ""
    read = dict(captured.get("others") or {})
    if here:
        read[here] = captured.get("local") or {}
    skipped = [dict(f) for f in captured.get("failed") or []]
    last = (previous or {}).get("origins") or {}
    kept = {
        f["site"]: (last.get(f["site"]) or {}).get("local")
        for f in skipped if f.get("site")
    }
    newest = (here, *(v["origin"] for v in history or ()), *read, *kept)
    origins = {}
    for o in dict.fromkeys(o for o in newest if o):
        local = read.get(o) or kept.get(o)
        if local:
            origins[o] = {"local": dict(local)}
    items = dict(captured.get("session") or {})
    data = {
        "cookies": list(captured.get("cookies") or []),
        "origins": origins,
        "session": {"origin": here, "items": items} if here else {},
        "saved_at": now,
    }
    if _size({**data, "origins": {}, "session": {}}) > MAX_BYTES:
        # The jar is the one thing a save cannot leave out.
        raise ValueError(
            f"the cookie jar is over {MAX_BYTES} bytes; nothing was saved"
        )
    while _size(data) > MAX_BYTES:
        if data["origins"]:
            gone = list(data["origins"])[-1]
            data["origins"] = {o: e for o, e in data["origins"].items() if o != gone}
        else:
            gone, data["session"] = data["session"]["origin"], {}
        skipped.append({"site": gone, "reason": LEFT_OUT})
    sites = [o for o in data["origins"] if o in read]
    if data["session"].get("items") and here not in sites:
        sites.insert(0, here)
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
        # host -> origin -> its two storages. sessionStorage is one tab's, for
        # the one origin the save was made on; empty, it makes no row.
        self.origins: dict[str, dict] = {}
        for o, e in (self.data.get("origins") or {}).items():
            self.origins.setdefault(host_of(o), {})[o] = {
                "local": dict((e or {}).get("local") or {}), "session": {},
            }
        session = self.data.get("session") or {}
        if session.get("origin") and session.get("items"):
            entry = self.origins.setdefault(host_of(session["origin"]), {}).setdefault(
                session["origin"], {"local": {}, "session": {}}
            )
            entry["session"] = dict(session["items"])

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


def _site(jar: _Jar, host: str) -> dict:
    origins = jar.origins.get(host, {})
    cookies = jar.covering(host)
    # Per origin, never merged: the same host on two ports or schemes is two
    # origins, each restored with its own storage.
    storage = [
        {
            "origin": o,
            "local_storage": origins[o]["local"],
            "session_storage": origins[o]["session"],
        }
        for o in sorted(origins)
    ]
    return {
        "site": host,
        "uri": site_uri(host),
        "_cookies": cookies,
        "_own": [c for c in cookies if _own(c.get("domain") or "", host)],
        "storage": storage,
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


def history_hosts(history) -> list[str]:
    """The hosts of a history, most recent first, each once."""
    hosts = (host_of(v["origin"]) for v in history or ())
    return list(dict.fromkeys(h for h in hosts if h))


def _rows(data: dict, history=()) -> tuple[dict, dict]:
    """Every host the snapshot holds data for, once: ``(listing, details)``
    from a single grouping of the jar. Hosts the session went to come first,
    most recent first, then the rest alphabetically."""
    jar = _Jar(data)
    hosts = jar.hosts()
    stored = set(hosts)
    visited = [h for h in history_hosts(history) if h in stored]
    first = set(visited)
    ordered = visited + [h for h in hosts if h not in first]
    rows, details = [], {}
    for host in ordered:
        site = _site(jar, host)
        rows.append({
            "site": host, "uri": site["uri"], "cookies": len(site["_cookies"]),
            "storage": [
                {
                    "origin": e["origin"],
                    "local_storage": len(e["local_storage"]),
                    "session_storage": len(e["session_storage"]),
                }
                for e in site["storage"]
            ],
        })
        details[host] = {
            "site": host, "uri": site["uri"],
            "cookies": [_cookie_view(c, host) for c in site["_cookies"]],
            "storage": site["storage"],
            # What Forget would do, by the rule Forget itself uses.
            "own_cookies": [c["name"] for c in site["_own"]],
            "kept_shared": [_identity(c) for c in _shared(site["_cookies"], host)],
        }
    listing = {"sites": rows, "saved_at": (data or {}).get("saved_at"), "uri": LIST_URI}
    return listing, details


def view(data: dict, history=()) -> dict:
    """The listing: one entry per host the snapshot holds data for, counts
    only, never a value. Every row has something saved."""
    return _rows(data, history)[0]


def views(data: dict, history=()) -> tuple[dict, dict]:
    """``(listing, details)``: the listing and every host in full, from one
    pass over the jar."""
    return _rows(data, history)


def site_view(data: dict, site: str) -> dict | None:
    """One host in full: cookies (httpOnly masked) and both storages, per
    origin."""
    return _rows(data)[1].get((site or "").lower())


def history_view(history, data: dict, secrets: list[dict] | None = None) -> dict:
    """The History tab: one row per host the session landed on, the current
    one first, each with its latest URL and when, what the snapshot holds for
    it (None when nothing), and the secrets allowed there.

    Secrets join a row and never make one: a host the session never reached
    is not listed, whatever a secret allows.
    """
    saved = {r["site"]: r for r in view(data, history)["sites"]}
    latest: dict[str, dict] = {}
    for v in history or ():
        latest.setdefault(host_of(v["origin"]), v)
    rows = []
    for host in history_hosts(history):
        row = saved.get(host)
        rows.append({
            "site": host,
            "url": latest[host]["url"],
            "at": latest[host]["at"],
            "saved": {
                "cookies": row["cookies"],
                "local": sum(e["local_storage"] for e in row["storage"]),
                "session": sum(e["session_storage"] for e in row["storage"]),
            } if row else None,
            "secrets": matching_secrets(secrets, host),
        })
    return {"sites": rows}


def summary(data: dict) -> dict | None:
    sites = _hosts(data or {})
    return {"sites": len(sites), "uri": LIST_URI} if sites else None


def forget(data: dict, host: str) -> tuple[dict, dict]:
    """Remove one host from the snapshot: its storage, sessionStorage
    included, and the cookies named for it (``host`` or ``.host``). A
    parent-domain cookie stays — other hosts use it — and so does
    ``saved_at``: forgetting is not a save."""
    host = (host or "").lower()
    jar = _Jar(data)
    covering = jar.covering(host)
    gone_ids = {id(c) for c in covering if _own(c.get("domain") or "", host)}
    stored = data.get("origins") or {}
    origins = [o for o in stored if host_of(o) == host]
    session = data.get("session") or {}
    ours = host_of(session.get("origin") or "") == host
    if ours and session.get("items") and session["origin"] not in origins:
        origins.append(session["origin"])
    left = {
        **data,
        "cookies": [c for c in jar.cookies if id(c) not in gone_ids],
        "origins": {o: e for o, e in stored.items() if host_of(o) != host},
        **({"session": {}} if ours else {}),
    }
    return left, {
        "site": host,
        "cookies": [c["name"] for c in covering if id(c) in gone_ids],
        "origins": origins,
        "kept_shared": [_identity(c) for c in _shared(covering, host)],
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


def capture(bidi, driver, origins=()) -> dict:
    """The whole cookie jar (BiDi sees httpOnly ones), the page's own two
    storages, and the localStorage of every other origin in ``origins`` — the
    history's, newest first — read one at a time in a spare tab.

    The browser answered classic WebDriver to get here, so a jar that cannot
    be read is the BiDi channel failing: :class:`BidiUnavailable`, a 503, and
    nothing is saved. An origin that cannot be read is named in ``failed``;
    the snapshot keeps its storage from the last save.
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
    here = page.get("origin") or ""
    # Only http(s) can be stood on; the page's own origin is read in place.
    wanted = [
        o for o in dict.fromkeys(origins)
        if o != here and o.startswith(("http://", "https://"))
    ]
    others: dict[str, dict] = {}
    failed: list[dict] = []
    if wanted:
        try:
            with spare_tab(bidi) as run:
                for origin in wanted:
                    try:
                        found = run(origin, READ_LOCAL)
                        others[origin] = found if isinstance(found, dict) else {}
                    except Exception as e:  # noqa: BLE001 - one origin keeps its last storage
                        failed.append({"site": origin, "reason": _why(e)})
        except Exception as e:  # noqa: BLE001 - no tab: each origin not read keeps its last
            done = set(others) | {f["site"] for f in failed}
            failed.extend(
                {"site": o, "reason": failure_text(e)} for o in wanted if o not in done
            )
    return {
        "cookies": cookies,
        "origin": here,
        "local": page.get("local") or {},
        "session": page.get("session") or {},
        "others": others,
        "failed": failed,
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
