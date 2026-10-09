"""Site data: the cookies and storage a workspace keeps for the sites it uses.

A workspace outlives its sessions, and used to come back signed out: the page it
was on survived a reap, the sign-in did not. An agent saves when it knows the
browser is worth keeping — after a sign-in is confirmed — and every browser
opened in the workspace gets it back (spec 2026-09-30, rounds 1 and 2).

Saved, not captured: every call paying for a snapshot was rejected, and a reap
gives no warning, so only an explicit save is dependable.

**A save is a snapshot.** It replaces the last one whole: the jar, the
localStorage of every origin the session has been to, and the sessionStorage
of the page it is on. Other origins are reached through a spare tab
(`spare.spare_tab`, driven by `transfer`), which is also how a restore writes
them back before the first page loads.

**A site is a host.** Cookies carry a domain and no scheme or port, so the view
groups by host. Storage is kept per origin, as ``location.origin`` spells it.

Values are credentials. They live in the workspace store and nowhere else, are
never logged, and an httpOnly cookie's value is never shown on any surface.
"""


from __future__ import annotations

import json

from ..secrets import matching_secrets
from ..urls import host_of

# The private key an action hands its capture back under. `Workspaces.settle`
# removes it and stores it as the snapshot; no caller ever sees it.
CAPTURED = "_site_data_captured"
MAX_BYTES = 1_000_000
MASK = "•••"
# Why an origin's storage was not read: its own service worker answered the
# spare tab (spec round 2, *Service workers at save time*).
SW_REASON = "a service worker answered: save while on this site"
LIST_URI = "workspace://site-data"


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
    # Sized once, then evicted by subtracting: re-serialising the whole payload
    # per eviction was quadratic (1.2 s at 400 origins of 20 KB). Every piece is
    # sized with the same ``json.dumps`` the payload is, and the separators
    # ("key": value joined by ", ") are counted, so the running total is the
    # serialised length exactly — and so never below it.
    entries = {o: len(json.dumps(o)) + 2 + _size(e) for o, e in origins.items()}
    total = _size({**data, "origins": {}, "session": {}})
    total += _joined(entries.values()) - 2
    total += _size(data["session"]) - 2
    while total > MAX_BYTES:
        if data["origins"]:
            gone, _ = data["origins"].popitem()
            total -= entries.pop(gone) + (2 if entries else 0)
        else:
            gone = data["session"]["origin"]
            total -= _size(data["session"]) - 2
            data["session"] = {}
        skipped.append({"site": gone, "reason": LEFT_OUT})
    sites = [o for o in data["origins"] if o in read]
    if data["session"].get("items") and here not in sites:
        sites.insert(0, here)
    return data, {"cookies": len(data["cookies"]), "sites": sites, "skipped": skipped}


def _size(data: dict) -> int:
    return len(json.dumps(data))


def _joined(entry_sizes) -> int:
    """The length of a JSON object whose entries serialise to ``entry_sizes``:
    the braces and a ", " between each pair."""
    sizes = list(entry_sizes)
    return 2 + sum(sizes) + 2 * max(len(sizes) - 1, 0)


def live_cookies(cookies: list[dict], now: float) -> list[dict]:
    """Every cookie a restore should set: expired ones dropped, session
    cookies (no expiry) kept, because the workspace they belong to is ours and
    outlived the browser."""
    return [c for c in cookies if c.get("expiry") is None or c["expiry"] > now]


def restorable(data: dict) -> bool:
    """Whether a snapshot holds anything a restore could put back."""
    if not isinstance(data, dict):
        return False
    session = data.get("session")
    return bool(
        data.get("cookies") or data.get("origins")
        or (isinstance(session, dict) and session.get("items"))
    )


def _own(domain: str, host: str) -> bool:
    """A cookie a host's row owns: set for the host itself, or ``.host``.
    A parent's leading-dot cookie only covers it; other rows use that one."""
    return domain in (host, "." + host)


def _suffixes(host: str) -> list[str]:
    """``a.b.com`` -> ``b.com``, ``com``: the parents whose dotted cookies
    cover it."""
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(1, len(parts))]


def _a(value, kind):
    """``value`` when it is a ``kind``, else an empty one."""
    return value if isinstance(value, kind) else kind()


class _Jar:
    """One pass over the cookies and origins, so every host's slice is a few
    dict lookups instead of a scan of the whole jar per host."""

    def __init__(self, data: dict):
        # Read as a stored record may be, not as a save writes it: a corrupt
        # shape counts as nothing rather than failing a view, or the clean open
        # that is the way out of it (Copilot, #51).
        self.data = _a(data, dict)
        self.cookies = [
            c for c in _a(self.data.get("cookies"), list)
            if isinstance(c, dict)
            and all(isinstance(c.get(k), str) and c[k] for k in ("name", "domain"))
        ]
        self.by_domain: dict[str, list[int]] = {}
        for i, c in enumerate(self.cookies):
            if c.get("domain"):
                self.by_domain.setdefault(c["domain"], []).append(i)
        # host -> origin -> its two storages. sessionStorage is one tab's, for
        # the one origin the save was made on; empty, it makes no row.
        self.origins: dict[str, dict] = {}
        for o, e in _a(self.data.get("origins"), dict).items():
            self.origins.setdefault(host_of(o), {})[o] = {
                "local": dict(_a(_a(e, dict).get("local"), dict)), "session": {},
            }
        session = _a(self.data.get("session"), dict)
        items = _a(session.get("items"), dict)
        if isinstance(session.get("origin"), str) and session["origin"] and items:
            entry = self.origins.setdefault(host_of(session["origin"]), {}).setdefault(
                session["origin"], {"local": {}, "session": {}}
            )
            entry["session"] = dict(items)

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


def hosts(data: dict) -> list[str]:
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


def _ordered(jar: _Jar, history=()) -> list[str]:
    """Every host the snapshot holds data for, once: the hosts the workspace went
    to first, most recent first, then the rest alphabetically."""
    hosts = jar.hosts()
    stored = set(hosts)
    visited = [h for h in history_hosts(history) if h in stored]
    first = set(visited)
    return visited + [h for h in hosts if h not in first]


def _row(site: dict) -> dict:
    return {
        "site": site["site"], "uri": site["uri"], "cookies": len(site["_cookies"]),
        "storage": [
            {
                "origin": e["origin"],
                "local_storage": len(e["local_storage"]),
                "session_storage": len(e["session_storage"]),
            }
            for e in site["storage"]
        ],
    }


def _detail(site: dict) -> dict:
    host = site["site"]
    return {
        "site": host, "uri": site["uri"],
        "cookies": [_cookie_view(c, host) for c in site["_cookies"]],
        "storage": site["storage"],
        # What Forget would do, by the rule Forget itself uses.
        "own_cookies": [c["name"] for c in site["_own"]],
        "kept_shared": [_identity(c) for c in _shared(site["_cookies"], host)],
    }


def _listing(data: dict, rows: list[dict]) -> dict:
    return {"sites": rows, "saved_at": (data or {}).get("saved_at"), "uri": LIST_URI}


def view(data: dict, history=()) -> dict:
    """The listing: one entry per host the snapshot holds data for, counts
    only, never a value. Every row has something saved."""
    jar = _Jar(data)
    return _listing(data, [_row(_site(jar, h)) for h in _ordered(jar, history)])


def views(data: dict, history=()) -> tuple[dict, dict]:
    """``(listing, details)``: the listing and every host in full, from one
    pass over the jar."""
    jar = _Jar(data)
    sites = [_site(jar, h) for h in _ordered(jar, history)]
    return (
        _listing(data, [_row(s) for s in sites]),
        {s["site"]: _detail(s) for s in sites},
    )


def site_view(data: dict, site: str) -> dict | None:
    """One host in full: cookies (httpOnly masked) and both storages, per
    origin."""
    return views(data)[1].get((site or "").lower())


def history_view(history, data: dict, secrets: list[dict] | None = None) -> dict:
    """The History tab: one row per host the workspace landed on, the current
    one first, each with its latest URL and when, what the snapshot holds for
    it (None when nothing), and the secrets allowed there.

    Secrets join a row and never make one: a host the workspace never reached
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
    sites = hosts(data or {})
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

