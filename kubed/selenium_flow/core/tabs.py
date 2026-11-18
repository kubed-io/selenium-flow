"""Which tabs a browser has, which one the session is on, and how that follows.

WebDriver lists window handles but says nothing about which tab the user or a
page opened last, or where a closed tab leaves the session. This keeps a small
numbered view of the browser, `t1`, `t2`, ... (ids are never reused while the
browser lives), and `reconcile` compares it with the handles the browser has
now: new handles are opened tabs and become current, a closed current tab hands
the session back to the tab it came from.

Keyed by the Grid's session id, like the pointer, and kept in Redis when Redis
is configured. Unlike the pointer, a failed read or write raises: a tab id the
store forgot would be handed out again.

No Selenium here: the callers list the handles and switch to the one named.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import NamedTuple

# Long enough to outlive any browser.
DEFAULT_TTL_SECONDS = 86400


@dataclass(frozen=True)
class Known:
    """One tab: its id, the window handle it has now, and where it came from."""

    tab: str
    handle: str
    from_: str | None = None


@dataclass(frozen=True)
class TabState:
    """What is known of a browser's tabs: the next id, the current tab, all tabs."""

    next: int
    current: str
    known: tuple[Known, ...]

    def by_id(self, tab: str) -> Known | None:
        return next((k for k in self.known if k.tab == tab), None)

    def by_handle(self, handle: str) -> Known | None:
        return next((k for k in self.known if k.handle == handle), None)

    def newest(self) -> Known:
        return max(self.known, key=lambda k: _number(k.tab))

    def to_json(self) -> str:
        return json.dumps(
            {
                "next": self.next,
                "current": self.current,
                "known": [
                    {"tab": k.tab, "handle": k.handle, "from": k.from_}
                    for k in self.known
                ],
            }
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> TabState | None:
        """The state ``raw`` holds, or None when it is not one."""
        try:
            data = json.loads(raw)
            known = tuple(
                Known(str(k["tab"]), str(k["handle"]), k.get("from"))
                for k in data["known"]
            )
            return cls(int(data["next"]), str(data["current"]), known)
        except (ValueError, KeyError, TypeError, AttributeError):
            return None


class Reconciled(NamedTuple):
    state: TabState
    opened: list[str]
    closed: list[str]
    moved: bool
    handle: str  # the current tab's handle


def _number(tab: str) -> int:
    return int(tab[1:])


def reconcile(
    state: TabState | None, handles: list[str], current_handle: str | None = None
) -> Reconciled:
    """Bring ``state`` up to date with the browser's ``handles``.

    ``current_handle`` only matters the first time a browser is seen, when it
    says which tab the session is on.
    """
    if state is None:
        known = tuple(Known(f"t{i}", h) for i, h in enumerate(handles, 1))
        seeded = TabState(len(handles) + 1, "", known)
        if not known:
            return Reconciled(seeded, [], [], False, "")
        at = seeded.by_handle(current_handle) if current_handle else None
        chosen = at or seeded.newest()
        out = TabState(seeded.next, chosen.tab, known)
        return Reconciled(out, [], [], at is None, chosen.handle)

    live = set(handles)
    closed = sorted((k.tab for k in state.known if k.handle not in live), key=_number)
    kept = [k for k in state.known if k.handle in live]
    seen = {k.handle for k in kept}
    number = state.next
    opened: list[str] = []
    for handle in handles:
        if handle in seen:
            continue
        kept.append(Known(f"t{number}", handle, state.current))
        opened.append(f"t{number}")
        number += 1
    out = TabState(number, state.current, tuple(kept))
    current = state.current
    if opened:
        current = opened[-1]
    elif current in closed and kept:
        back = state.by_id(current).from_
        target = out.by_id(back) if back else None
        current = (target or out.newest()).tab
    out = TabState(number, current, out.known)
    chosen = out.by_id(current)
    return Reconciled(
        out, opened, closed, current != state.current, chosen.handle if chosen else ""
    )


def merged(result, before: dict, after: dict):
    """``result`` with what the checks before and after the call found.

    The tab the call ended on wins over the one it began on, and any move
    leaves the session at the top of a page, so ``in_frame`` is False unless a
    frame result says otherwise.
    """
    if not isinstance(result, dict):
        return result
    for key in ("opened", "closed"):
        both = [*before.get(key, []), *after.get(key, [])]
        if both:
            result[key] = both
    if "tab" in after:
        for key in ("tab", "url", "title"):
            if key in after:
                result[key] = after[key]
        result["in_frame"] = False
    elif "tab" in before:
        # A result's own `tab` (a `tab` call's) is where the call left it.
        result.setdefault("tab", before["tab"])
        result.setdefault("in_frame", False)
    return result


class MemoryTabs:
    """Process-local tab state. Correct for one replica, lost on restart."""

    kind = "memory"

    def __init__(self, ttl: int = DEFAULT_TTL_SECONDS, clock=time.time):
        self._data: dict[str, tuple[float, TabState]] = {}
        self._ttl = ttl
        self._clock = clock

    def get(self, session_id: str) -> TabState | None:
        entry = self._data.get(session_id)
        if entry is None:
            return None
        expires_at, state = entry
        if self._clock() >= expires_at:
            del self._data[session_id]
            return None
        return state

    def set(self, session_id: str, state: TabState) -> None:
        self._data[session_id] = (self._clock() + self._ttl, state)

    def forget(self, session_id: str) -> None:
        self._data.pop(session_id, None)


class RedisTabs:
    """Shared tab state, so any replica numbers a browser's tabs the same way."""

    kind = "redis"

    def __init__(self, client, prefix: str, ttl: int):
        self._client = client
        self._prefix = prefix
        self._ttl = ttl

    def _k(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def get(self, session_id: str) -> TabState | None:
        raw = self._client.get(self._k(session_id))
        return TabState.from_json(raw) if raw else None

    def set(self, session_id: str, state: TabState) -> None:
        self._client.set(self._k(session_id), state.to_json(), ex=self._ttl)

    def forget(self, session_id: str) -> None:
        self._client.delete(self._k(session_id))


def matching(store):
    """A tab store on the same backend as the workspace ``store``.

    Built from the store object, as ``pointer.matching`` is, and for the same
    reason: an injected shared store never gets process-local tab state.
    """
    ttl = getattr(store, "ttl", DEFAULT_TTL_SECONDS)
    if store.kind != "redis":
        return MemoryTabs(ttl=ttl)
    from ..workspace.store import TABS_NAMESPACE

    return RedisTabs(store.client, prefix=store.prefix + TABS_NAMESPACE, ttl=ttl)
