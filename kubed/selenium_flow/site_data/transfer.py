"""The BiDi side of site data: reading a browser into a snapshot and putting a
snapshot back before its first page.

The model it fills and reads is `snapshot`; the tab that stands on another
origin is `spare`. Values are credentials and are never logged.
"""

from __future__ import annotations

import contextlib
import json

from ..bidi import BidiError, Channel
from ..faults import BidiUnavailable
from ..faults import message as failure_text
from .snapshot import LIST_URI, SW_REASON, hosts, live_cookies
from .spare import ServiceWorkerAnswered, spare_tab

BIDI_DOWN = (
    "the browser's BiDi channel is unavailable, so nothing was saved: retry, "
    "and if it persists the Grid's /se/bidi route is down"
)

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


def _why(exc: Exception) -> str:
    return SW_REASON if isinstance(exc, ServiceWorkerAnswered) else failure_text(exc)


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


def capture(channel: Channel, driver, origins=(), *, hidden=None) -> dict:
    """The whole cookie jar (BiDi sees httpOnly ones), the page's own two
    storages, and the localStorage of every other origin in ``origins`` — the
    history's, newest first — read one at a time in a spare tab.

    The browser answered classic WebDriver to get here, so a jar that cannot
    be read is the BiDi channel failing: :class:`BidiUnavailable`, a 503, and
    nothing is saved. An origin that cannot be read is named in ``failed``;
    the snapshot keeps its storage from the last save. ``hidden`` is handed to
    the spare tab (see `spare_tab`).
    """
    try:
        jar = channel.call("storage.getCookies", {})["cookies"]
    except (BidiUnavailable, BidiError) as e:
        # Only a channel that did not answer, or a browser that refused, is a
        # 503. Anything else - an unexpected return shape, a bug here - stays
        # a 500, as errors.py decides for every failure it does not recognise
        # (Copilot, #50).
        raise BidiUnavailable(BIDI_DOWN) from e
    cookies = [
        {
            "name": c["name"], "value": c["value"]["value"],
            "value_type": c["value"]["type"], "domain": c["domain"],
            "path": c["path"], "http_only": c["httpOnly"], "secure": c["secure"],
            "same_site": c["sameSite"], "expiry": c.get("expiry"),
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
            with spare_tab(channel, hidden=hidden) as run:
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


def _kept(channel: Channel, cookies: list[dict]) -> list[dict] | None:
    """The cookies the browser actually holds after setting, or None when the
    jar cannot be read back (then the set is trusted)."""
    try:
        jar = channel.call("storage.getCookies", {})["cookies"]
    except Exception:  # noqa: BLE001 - an unverified restore is still a restore
        return None
    held = {_key(c["name"], c["domain"], c["path"]) for c in jar}
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


def _bidi_cookie(c: dict) -> dict:
    """A stored cookie as ``storage.setCookie`` takes it; unset fields are
    left out, not sent as null."""
    cookie = {
        "name": c["name"],
        "value": {"type": c.get("value_type") or "string", "value": c.get("value")},
        "domain": c["domain"],
    }
    optional = {
        "path": c.get("path"), "httpOnly": c.get("http_only"),
        "secure": c.get("secure"), "sameSite": _same_site(c),
        "expiry": c.get("expiry"),
    }
    cookie.update({k: v for k, v in optional.items() if v is not None})
    return cookie


def _restore_cookies(
    channel: Channel, stored: list, now: float, skipped: list
) -> list[dict]:
    """Set every live, usable cookie; return the ones the browser kept. Each
    one refused, dropped or unusable is added to ``skipped``."""
    cookies = live_cookies([c for c in stored if _usable(c)], now)
    # One bad entry is skipped, not the whole restore.
    skipped.extend(_malformed(c) for c in stored if not _usable(c))
    sent = []
    for c in cookies:
        try:
            channel.call("storage.setCookie", {"cookie": _bidi_cookie(c)})
            sent.append(c)
        except Exception as e:  # noqa: BLE001 - one refusal must not stop the rest
            skipped.append({
                "cookie": c.get("name"), "domain": c.get("domain"),
                "reason": failure_text(e),
            })
    # A browser can refuse a cookie without an error; only the jar says.
    kept = _kept(channel, sent)
    if kept is None:
        kept = sent
    skipped.extend(
        {"cookie": c.get("name"), "domain": c.get("domain"),
         "reason": "the browser did not keep it"}
        for c in sent if c not in kept
    )
    return kept


def restore(channel: Channel, data: dict, now: float) -> dict:
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
        kept = _restore_cookies(channel, data.get("cookies") or [], now, skipped)
        local = {
            o: e["local"] for o, e in (data.get("origins") or {}).items()
            if isinstance(e, dict) and e.get("local")
        }
        if local:
            try:
                with spare_tab(channel) as run:
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
                main = channel.call("browsingContext.getTree", {"maxDepth": 0})[
                    "contexts"
                ][0]["context"]
                with spare_tab(channel, context=main) as run:
                    try:
                        run(session["origin"], fill("sessionStorage", session["items"]))
                    finally:
                        # The stand-in page must never be what an open with no
                        # url leaves on screen, filled or not; the tab keeps its
                        # sessionStorage.
                        with contextlib.suppress(Exception):
                            channel.call("browsingContext.navigate", {
                                "context": main, "url": "about:blank",
                                "wait": "complete",
                            })
                filled.append(session["origin"])
            except Exception as e:  # noqa: BLE001 - the rest stands
                skipped.append({"site": session["origin"], "reason": _why(e)})
    except Exception as e:  # noqa: BLE001 - BiDi failing is a report, not a crash
        skipped.append({"reason": failure_text(e)})
    return {
        "restored": hosts({"cookies": kept, "origins": {o: {} for o in filled}}),
        "skipped": skipped,
        "uri": LIST_URI,
    }

