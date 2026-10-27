"""The live tunnels that are open, and who has control of which browser.

The registry is what the session monitor's bus reaches (a browser ending closes
its viewers) and what the next agent call asks: a workspace someone is typing
into is not the workspace the agent last left (spec 2026-10-27, ruling 35). It
is process memory, like the monitor: a restart drops every tunnel, so a fresh
registry knows no controller, which is the truth.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..monitor.events import SessionEnded
from .tunnel import BROWSER_ENDED, STOPPING, Tunnel

TAKEN = (
    "{who} took control of this browser at {at} UTC and may be typing in it; "
    "check with them before acting."
)
HANDED_BACK = (
    "control of this browser was handed back at {at} UTC; the page may have "
    "changed, so look at it before carrying on."
)

PERSON = "a person"
ANONYMOUS = ("admin", "open")


@dataclass(frozen=True)
class Control:
    who: tuple[str, ...]  # viewer names in the order they took control, no repeats
    since: float  # clock() when the workspace became controlled


def _hhmm(at: float) -> str:
    return time.strftime("%H:%M", time.gmtime(at))


class Tunnels:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._by_grid: dict[str, list[Tunnel]] = {}
        self._controllers: dict[str, list[Tunnel]] = {}
        self._since: dict[str, float] = {}
        self._released: dict[str, float] = {}

    def add(self, grid_id: str, tunnel: Tunnel) -> None:
        self._by_grid.setdefault(grid_id, []).append(tunnel)
        tunnel.on_mode = lambda: self._moded(tunnel)

    def discard(self, grid_id: str, tunnel: Tunnel) -> None:
        tunnels = self._by_grid.get(grid_id, [])
        if tunnel in tunnels:
            tunnels.remove(tunnel)
        if not tunnels:
            self._by_grid.pop(grid_id, None)
        self._release(tunnel)

    def open(self, grid_id: str) -> int:
        return len(self._by_grid.get(grid_id, ()))

    def ended(self, event: SessionEnded) -> None:
        for tunnel in list(self._by_grid.get(event.session_id, ())):
            tunnel.end(BROWSER_ENDED)

    def stop(self) -> None:
        for tunnels in list(self._by_grid.values()):
            for tunnel in list(tunnels):
                tunnel.end(STOPPING)

    # control -----------------------------------------------------------

    def _moded(self, tunnel: Tunnel) -> None:
        if tunnel.mode == "control":
            holders = self._controllers.setdefault(tunnel.workspace, [])
            if tunnel not in holders:
                if not holders:
                    self._since[tunnel.workspace] = self._clock()
                    self._released.pop(tunnel.workspace, None)
                holders.append(tunnel)
        else:
            self._release(tunnel)

    def _release(self, tunnel: Tunnel) -> None:
        holders = self._controllers.get(tunnel.workspace)
        if not holders or tunnel not in holders:
            return
        holders.remove(tunnel)
        if not holders:
            del self._controllers[tunnel.workspace]
            del self._since[tunnel.workspace]
            self._released[tunnel.workspace] = self._clock()

    def controlled(self, workspace: str) -> Control | None:
        holders = self._controllers.get(workspace)
        if not holders:
            return None
        return Control(
            tuple(dict.fromkeys(t.who for t in holders)), self._since[workspace]
        )

    def controlling(self, workspace: str, who: str) -> bool:
        return any(t.who == who for t in self._controllers.get(workspace, ()))

    def notice(self, workspace: str) -> str | None:
        control = self.controlled(workspace)
        if control is not None:
            names: list[str] = []
            for name in control.who:
                said = PERSON if name in ANONYMOUS else name
                if said not in names:
                    names.append(said)
            return TAKEN.format(who=" and ".join(names), at=_hhmm(control.since))
        at = self._released.pop(workspace, None)
        return None if at is None else HANDED_BACK.format(at=_hhmm(at))
