import pytest

from kubed.selenium_flow.live.control import HANDED_BACK, HELD, Controls
from kubed.selenium_flow.live.tunnel import BROWSER_ENDED, NOBODY, Tunnel
from kubed.selenium_flow.live.tunnels import Tunnels
from kubed.selenium_flow.monitor.events import SessionEnded

from .fakes import FakePeer

pytestmark = pytest.mark.unit

T = 1_793_109_731.0  # 14:02 UTC


class Clock:
    def __init__(self, now=T):
        self.now = now

    def __call__(self):
        return self.now


def at_once(_grid_id):
    """`locks.behind` with no call running: the mark has passed."""
    return lambda: True


def registry(clock=lambda: T):
    return Tunnels(Controls(behind=at_once, clock=clock))


def held(tunnels, workspace, who, mode, grid_id="g1"):
    """A viewer of ``workspace`` in ``mode``, asked through the registry as the
    tunnel asks once it pipes. Control is granted at once when no call runs."""
    tunnel = Tunnel(FakePeer(), who=who, workspace=workspace, session="s", grace=1)
    tunnels.add(grid_id, tunnel)
    if mode == "control":
        tunnel.mode = tunnel.on_ask()
    return tunnel


def watch(tunnel):
    tunnel.mode = "watch"
    tunnel.on_mode()


def test_a_workspace_is_controlled_while_any_viewer_is_in_control():
    tunnels = registry()
    held(tunnels, "w1", "drk", "control")
    held(tunnels, "w1", "admin", "control")
    said = HELD.format(who="drk and a person", at="14:02")
    assert tunnels.notice("w1") == said
    assert tunnels.notice("w1") == said
    assert tunnels.notice("w2") is None
    assert tunnels.controlling("w1", "drk") and not tunnels.controlling("w1", "kim")
    assert tunnels.controls.refusal("g1", "navigate") is not None


def test_admin_and_open_read_as_a_person_once():
    tunnels = registry()
    held(tunnels, "w1", "admin", "control")
    held(tunnels, "w1", "open", "control")
    assert tunnels.notice("w1") == HELD.format(who="a person", at="14:02")


def test_handed_back_is_said_once_after_the_last_controller_goes():
    clock = Clock()
    tunnels = registry(clock)
    back = held(tunnels, "w1", "drk", "control")
    gone = held(tunnels, "w1", "kim", "control")
    clock.now = T + 120
    watch(back)
    assert tunnels.notice("w1").startswith("kim has control")
    assert not tunnels.controlling("w1", "drk")
    clock.now = T + 180
    tunnels.discard("g1", gone)
    assert not tunnels.controlling("w1", "kim")
    assert tunnels.notice("w1") == HANDED_BACK.format(at="14:05")
    assert tunnels.notice("w1") is None


def test_a_dropped_controller_is_a_release():
    tunnels = registry()
    a = held(tunnels, "w1", "drk", "control")
    a.end(NOBODY)
    tunnels.discard("g1", a)
    assert tunnels.open("g1") == 0
    assert tunnels.notice("w1") == HANDED_BACK.format(at="14:02")
    assert tunnels.controls.refusal("g1", "navigate") is None


def test_a_tunnel_ending_hands_back_before_it_is_discarded():
    tunnels = registry()
    a = held(tunnels, "w1", "drk", "control")
    a.on_end()
    assert tunnels.notice("w1") == HANDED_BACK.format(at="14:02")


def test_a_new_take_forgets_an_unsaid_hand_back():
    clock = Clock()
    tunnels = registry(clock)
    a = held(tunnels, "w1", "drk", "control")
    tunnels.discard("g1", a)
    clock.now = T + 600
    held(tunnels, "w1", "kim", "control")
    assert tunnels.notice("w1") == HELD.format(who="kim", at="14:12")


def test_a_watcher_is_no_controller():
    tunnels = registry()
    a = held(tunnels, "w1", "drk", "watch")
    assert tunnels.notice("w1") is None
    assert not tunnels.controlling("w1", "drk")
    tunnels.discard("g1", a)
    assert tunnels.notice("w1") is None


def test_a_fresh_registry_knows_no_controller():
    tunnels = registry()
    assert tunnels.notice("w1") is None and not tunnels.controlling("w1", "drk")
    assert tunnels.open("g1") == 0


def test_ended_and_stop_reach_the_right_tunnels():
    tunnels = registry()
    a = held(tunnels, "w1", "drk", "control", "g1")
    b = held(tunnels, "w2", "kim", "watch", "g2")
    assert tunnels.open("g1") == 1
    tunnels.ended(SessionEnded("w1", "g1", "ended", 0.0))
    assert a._ending == BROWSER_ENDED and b._ending is None
    assert tunnels.controls.refusal("g1", "navigate") is None  # released, ended
    assert tunnels.notice("w1").startswith("control of this browser was handed back")
    tunnels.stop()
    assert b._ending.code == 1001
