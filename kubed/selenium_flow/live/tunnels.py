"""The live tunnels that are open, wired to who has control of which browser.

The registry is what the session monitor's bus reaches (a browser ending closes
its viewers and releases its control) and what the next agent call asks: a
workspace someone is typing into is not the workspace the agent last left
(spec 2026-10-27, ruling 35). Control itself is `live.control`'s; a tunnel asks
it through ``on_ask`` and hands back through ``on_mode`` and ``on_end``. It is
process memory, like the monitor: a restart drops every tunnel, so a fresh
registry knows no controller, which is the truth.
"""

from __future__ import annotations

from ..monitor.events import SessionEnded
from .control import Controls
from .tunnel import BROWSER_ENDED, STOPPING, Tunnel


class Tunnels:
    def __init__(self, controls: Controls):
        self.controls = controls
        self._by_grid: dict[str, list[Tunnel]] = {}

    def add(self, grid_id: str, tunnel: Tunnel) -> None:
        self._by_grid.setdefault(grid_id, []).append(tunnel)
        tunnel.grid_id = grid_id
        tunnel.on_ask = lambda: self.controls.ask(tunnel)
        tunnel.on_mode = lambda: self._moded(tunnel)
        # Control is handed back when the tunnel ends, not once its sockets close.
        tunnel.on_end = lambda: self.controls.withdraw(tunnel)

    def discard(self, grid_id: str, tunnel: Tunnel) -> None:
        tunnels = self._by_grid.get(grid_id, [])
        if tunnel in tunnels:
            tunnels.remove(tunnel)
        if not tunnels:
            self._by_grid.pop(grid_id, None)
        self.controls.withdraw(tunnel)

    def open(self, grid_id: str) -> int:
        return len(self._by_grid.get(grid_id, ()))

    def ended(self, event: SessionEnded) -> None:
        for tunnel in list(self._by_grid.get(event.session_id, ())):
            tunnel.end(BROWSER_ENDED)
        self.controls.ended(event.session_id)

    def stop(self) -> None:
        for tunnels in list(self._by_grid.values()):
            for tunnel in list(tunnels):
                tunnel.end(STOPPING)

    def _moded(self, tunnel: Tunnel) -> None:
        if tunnel.mode == "watch":
            self.controls.withdraw(tunnel)

    # control, as the callers ask it ----------------------------------------

    def controlling(self, workspace: str, who: str) -> bool:
        return self.controls.controlling(workspace, who)

    def notice(self, workspace: str) -> str | None:
        """The notice's sentence, or None; a hand-back is said once."""
        said = self.controls.notice(workspace)
        return None if said is None else said["hint"]
