import pytest

from kubed.selenium_flow.live.tunnel import BROWSER_ENDED, NOBODY, Tunnel
from kubed.selenium_flow.live.tunnels import HANDED_BACK, TAKEN, Control, Tunnels
from kubed.selenium_flow.monitor.events import SessionEnded

from .fakes import FakePeer

pytestmark = pytest.mark.unit

T = 1_793_109_731.0  # 14:02 UTC


class Clock:
    def __init__(self, now=T):
        self.now = now

    def __call__(self):
        return self.now


def held(tunnels, workspace, who, mode, grid_id="g1"):
    tunnel = Tunnel(FakePeer(), who=who, workspace=workspace, session="s", grace=1)
    tunnels.add(grid_id, tunnel)
    tunnel.mode = mode
    tunnel.on_mode()
    return tunnel


def test_a_workspace_is_controlled_while_any_viewer_is_in_control():
    tunnels = Tunnels(clock=lambda: T)
    held(tunnels, "w1", "drk", "control")
    held(tunnels, "w1", "admin", "control")
    assert tunnels.controlled("w1") == Control(("drk", "admin"), T)
    assert tunnels.notice("w1") == TAKEN.format(who="drk and a person", at="14:02")
    assert tunnels.notice("w1") == TAKEN.format(who="drk and a person", at="14:02")
    assert tunnels.controlled("w2") is None and tunnels.notice("w2") is None
    assert tunnels.controlling("w1", "drk") and not tunnels.controlling("w1", "kim")


def test_admin_and_open_read_as_a_person_once():
    tunnels = Tunnels(clock=lambda: T)
    held(tunnels, "w1", "admin", "control")
    held(tunnels, "w1", "open", "control")
    assert tunnels.notice("w1") == TAKEN.format(who="a person", at="14:02")


def test_handed_back_is_said_once_after_the_last_controller_goes():
    clock = Clock()
    tunnels = Tunnels(clock=clock)
    back = held(tunnels, "w1", "drk", "control", "g1")
    gone = held(tunnels, "w1", "kim", "control", "g2")
    clock.now = T + 120
    back.mode = "watch"
    back.on_mode()
    assert tunnels.controlled("w1") == Control(("kim",), T)
    assert tunnels.notice("w1").startswith("kim took control")
    clock.now = T + 180
    tunnels.discard("g2", gone)
    assert tunnels.controlled("w1") is None
    assert tunnels.notice("w1") == HANDED_BACK.format(at="14:05")
    assert tunnels.notice("w1") is None


def test_a_dropped_controller_is_a_release():
    tunnels = Tunnels(clock=lambda: T)
    a = held(tunnels, "w1", "drk", "control")
    a.end(NOBODY)
    tunnels.discard("g1", a)
    assert tunnels.open("g1") == 0
    assert tunnels.notice("w1") == HANDED_BACK.format(at="14:02")


def test_a_new_take_forgets_an_unsaid_hand_back():
    clock = Clock()
    tunnels = Tunnels(clock=clock)
    a = held(tunnels, "w1", "drk", "control")
    tunnels.discard("g1", a)
    clock.now = T + 600
    held(tunnels, "w1", "kim", "control", "g2")
    assert tunnels.notice("w1") == TAKEN.format(who="kim", at="14:12")
    assert tunnels.controlled("w1").since == T + 600


def test_a_watcher_is_no_controller():
    tunnels = Tunnels(clock=lambda: T)
    a = held(tunnels, "w1", "drk", "watch")
    assert tunnels.controlled("w1") is None and tunnels.notice("w1") is None
    assert not tunnels.controlling("w1", "drk")
    tunnels.discard("g1", a)
    assert tunnels.notice("w1") is None


def test_a_fresh_registry_knows_no_controller():
    tunnels = Tunnels()
    assert tunnels.controlled("w1") is None and tunnels.notice("w1") is None
    assert tunnels.open("g1") == 0


def test_ended_and_stop_reach_the_right_tunnels():
    tunnels = Tunnels()
    a = held(tunnels, "w1", "drk", "watch", "g1")
    b = held(tunnels, "w2", "kim", "watch", "g2")
    assert tunnels.open("g1") == 1
    tunnels.ended(SessionEnded("w1", "g1", "ended", 0.0))
    assert a._ending == BROWSER_ENDED and b._ending is None
    tunnels.stop()
    assert b._ending.code == 1001
