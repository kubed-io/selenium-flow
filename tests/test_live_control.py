"""Control is requested while the agent's call runs and granted once it ends
(spec 2026-10-27-live-control, rulings 1, 13, 14, 23-25)."""

import asyncio

import pytest

from kubed.selenium_flow.faults import Controlled
from kubed.selenium_flow.live.control import Controls, Gate, Waited, said

pytestmark = pytest.mark.unit

T = 1_793_113_931.0  # 15:12 UTC
HELD = (
    "{who} has control of this browser since {at} UTC. Call await_control "
    "(POST /browser/await-control), which returns when they hand back."
)
REQUESTED = (
    "{who} asked for control of this browser at {at} UTC. Call await_control "
    "(POST /browser/await-control), which returns when they hand back."
)


class Clock:
    def __init__(self, now=T):
        self.now = now

    def __call__(self):
        return self.now


class Behind:
    """`locks.behind`, whose answer the test flips; ``marked`` is every mark."""

    def __init__(self):
        self.value = True
        self.marked = []

    def __call__(self, grid_id):
        self.marked.append(grid_id)
        return lambda: self.value


class FakeTunnel:
    def __init__(self, who="drk", workspace="w1", grid_id="g1"):
        self.who = who
        self.workspace = workspace
        self.grid_id = grid_id
        self.granted = False

    async def grant(self):
        self.granted = True


@pytest.fixture
def passed():
    return Behind()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def controls(passed, clock):
    made = Controls(behind=passed, clock=clock, poll=0.01)
    yield made
    made.stop()


@pytest.fixture
def t1():
    return FakeTunnel("drk")


@pytest.fixture
def t2():
    return FakeTunnel("kim")


async def until(check, timeout=1.0):
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.005)


# ---- asking and the grant ------------------------------------------------------


async def test_with_no_call_running_control_is_granted_at_once(controls, passed, t1):
    assert controls.ask(t1) == "control"
    refused = controls.refusal("g1", "navigate")
    assert isinstance(refused, Controlled)
    assert str(refused) == HELD.format(who="drk", at="15:12")
    assert passed.marked == ["g1"]
    assert controls.controlling("w1", "drk")


async def test_with_a_call_running_control_is_requested_then_granted(
    controls, passed, clock, t1
):
    passed.value = False
    assert controls.ask(t1) == "requested"
    assert "asked for control" in str(controls.refusal("g1", "interact"))
    assert not controls.controlling("w1", "drk")
    await asyncio.sleep(0.05)
    assert not t1.granted
    clock.now = T + 60
    passed.value = True
    await until(lambda: t1.granted)
    assert str(controls.refusal("g1", "interact")) == HELD.format(who="drk", at="15:13")
    assert controls.controlling("w1", "drk")
    assert passed.marked == ["g1"]  # one mark per episode


async def test_reads_and_paste_are_never_refused(controls, t1):
    controls.ask(t1)
    for tool in ("extract", "outline", "screenshot", "print", "network_body", "paste"):
        assert controls.refusal("g1", tool) is None, tool
    assert controls.refusal("g1", "a_tool_nobody_declared") is not None
    assert controls.refusal("g2", "navigate") is None  # another browser


async def test_leaving_while_requested_ends_the_episode(controls, passed, t1):  # ruling 23
    passed.value = False
    controls.ask(t1)
    controls.withdraw(t1)
    assert controls.refusal("g1", "navigate") is None
    assert controls.notice("w1")["state"] == "handed_back"
    passed.value = True
    await asyncio.sleep(0.05)
    assert not t1.granted  # the pending grant went with it


async def test_control_is_held_while_any_viewer_holds_it(controls, t1, t2):
    controls.ask(t1)
    controls.ask(t2)
    assert str(controls.refusal("g1", "navigate")).startswith("drk and kim has control")
    controls.withdraw(t1)
    assert str(controls.refusal("g1", "navigate")).startswith("kim has control")
    assert not controls.controlling("w1", "drk") and controls.controlling("w1", "kim")
    controls.withdraw(t2)
    assert controls.refusal("g1", "navigate") is None
    assert not controls.controlling("w1", "kim")


async def test_a_second_viewer_joins_the_episode_in_its_state(controls, passed, t1, t2):
    passed.value = False
    assert controls.ask(t1) == "requested"
    assert controls.ask(t2) == "requested"
    assert controls.ask(t1) == "requested"  # asking again changes nothing
    passed.value = True
    await until(lambda: t1.granted and t2.granted)
    third = FakeTunnel("lee")
    assert controls.ask(third) == "control"
    assert not third.granted  # told by its answer, not by a grant
    assert passed.marked == ["g1"]


async def test_the_browser_ending_releases_with_ended(controls, t1):
    controls.ask(t1)
    controls.ended("g2")  # another browser: nothing
    assert controls.refusal("g1", "navigate") is not None
    controls.ended("g1")
    assert controls.refusal("g1", "navigate") is None
    waited = await controls.wait("w1", 1)
    assert waited.kind == "handed_back" and waited.episode.ended
    assert waited.episode.state == "released" and waited.episode.done.is_set()


async def test_the_browser_ending_cancels_a_pending_grant(controls, passed, t1):
    passed.value = False
    controls.ask(t1)
    controls.ended("g1")
    passed.value = True
    await asyncio.sleep(0.05)
    assert not t1.granted and controls.refusal("g1", "navigate") is None


async def test_a_new_browser_in_the_workspace_starts_a_new_episode(controls, t1):
    controls.ask(t1)
    other = FakeTunnel("kim", grid_id="g2")
    assert controls.ask(other) == "control"
    assert controls.refusal("g1", "navigate") is None
    assert str(controls.refusal("g2", "navigate")).startswith("kim has control")


async def test_stop_cancels_a_pending_grant_and_sets_every_event(controls, passed, t1):
    passed.value = False
    held = FakeTunnel("kim", workspace="w2", grid_id="g2")
    controls.ask(t1)
    passed.value = True
    controls.ask(held)  # w2 held at once
    passed.value = False
    waiters = [
        asyncio.create_task(controls.wait("w1", None)),
        asyncio.create_task(controls.wait("w2", None)),
    ]
    await asyncio.sleep(0.02)
    controls.stop()
    async with asyncio.timeout(1):
        results = await asyncio.gather(*waiters)
    assert [w.kind for w in results] == ["handed_back", "handed_back"]
    passed.value = True
    await asyncio.sleep(0.05)
    assert not t1.granted
    assert controls.refusal("g1", "navigate") is None
    assert controls.refusal("g2", "navigate") is None
    assert controls.notice("w1") is None and controls.notice("w2") is None


async def test_a_grant_that_fails_for_one_tunnel_still_grants_the_others(
    controls, passed, t1, t2
):
    class Broken(FakeTunnel):
        async def grant(self):
            raise RuntimeError("boom")

    passed.value = False
    controls.ask(Broken("lee"))
    controls.ask(t2)
    passed.value = True
    await until(lambda: t2.granted)
    assert controls.controlling("w1", "kim")


# ---- the words -----------------------------------------------------------------


def test_the_refusal_says_who_and_when():
    controls = Controls(behind=Behind(), clock=Clock())
    controls.ask(FakeTunnel("admin"))
    refusal = controls.refusal("g1", "navigate")
    assert str(refusal) == (
        "a person has control of this browser since 15:12 UTC. Call "
        "await_control (POST /browser/await-control), which returns when they hand back."
    )


async def test_the_requested_refusal_says_who_and_when(controls, passed):
    passed.value = False
    controls.ask(FakeTunnel("kelly"))
    assert str(controls.refusal("g1", "navigate")) == REQUESTED.format(
        who="kelly", at="15:12"
    )


def test_said_names_people():
    assert said(["admin"]) == "a person"
    assert said(["admin", "open"]) == "a person"
    assert said(["drk", "admin", "drk", "open", "kim"]) == "drk and a person and kim"


def test_a_gate_is_a_frozen_snapshot():
    gate = Gate("w1", ("drk",), T, "held")
    with pytest.raises(AttributeError):
        gate.state = "requested"


async def test_the_gate_is_read_from_a_worker_thread(controls, t1):
    controls.ask(t1)
    refused = await asyncio.to_thread(controls.refusal, "g1", "navigate")
    assert isinstance(refused, Controlled)


# ---- the notice ----------------------------------------------------------------


async def test_the_notice_while_requested(controls, passed):
    passed.value = False
    controls.ask(FakeTunnel("kelly"))
    assert controls.notice("w1") == {
        "state": "requested",
        "who": ["kelly"],
        "since": "2026-10-27T15:12:11Z",
        "hint": (
            "kelly asked for control of this browser at 15:12 UTC; it is theirs "
            "once this call ends. Call await_control, which returns when they hand back."
        ),
    }
    assert controls.notice("w1")["state"] == "requested"  # said every time


async def test_the_notice_while_held(controls, t1):
    controls.ask(t1)
    notice = controls.notice("w1")
    assert notice["state"] == "held" and notice["who"] == ["drk"]
    assert notice["hint"] == HELD.format(who="drk", at="15:12")


async def test_the_hand_back_is_said_once(controls, clock, t1):
    controls.ask(t1)
    clock.now = T + 180
    controls.withdraw(t1)
    assert controls.notice("w1") == {
        "state": "handed_back",
        "who": ["drk"],
        "since": "2026-10-27T15:15:11Z",
        "hint": (
            "control of this browser was handed back at 15:15 UTC; the page may "
            "have changed. Call await_control for what they did, or look at the "
            "page before carrying on."
        ),
    }
    assert controls.notice("w1") is None
    assert controls.notice("w2") is None


async def test_a_new_episode_forgets_an_unsaid_hand_back(controls, t1, t2):
    controls.ask(t1)
    controls.withdraw(t1)
    controls.ask(t2)
    assert controls.notice("w1")["state"] == "held"
    controls.withdraw(t2)
    assert controls.notice("w1")["who"] == ["kim"]
    assert controls.notice("w1") is None


async def test_a_fresh_controls_knows_nothing(controls):
    assert controls.notice("w1") is None
    assert controls.refusal("g1", "navigate") is None
    assert not controls.controlling("w1", "drk")


# ---- waiting -------------------------------------------------------------------


async def test_waiting_with_nobody_in_control_returns_at_once(controls):
    assert await controls.wait("w1", 1) == Waited("nobody", None)


async def test_waiting_returns_an_unsaid_hand_back_once(controls, t1):
    controls.ask(t1)
    controls.withdraw(t1)
    first = await controls.wait("w1", 1)
    assert first.kind == "handed_back" and first.episode.who == ["drk"]
    assert controls.notice("w1") is None  # said by the wait
    assert (await controls.wait("w1", 1)).kind == "nobody"


async def test_waiting_returns_at_the_hand_back(controls, t1):
    controls.ask(t1)
    waiter = asyncio.create_task(controls.wait("w1", None))
    await asyncio.sleep(0.02)
    assert not waiter.done()
    controls.withdraw(t1)
    async with asyncio.timeout(1):
        waited = await waiter
    assert waited.kind == "handed_back" and waited.episode.state == "released"
    assert not waited.episode.ended
    assert controls.notice("w1") is None  # the wait said it


async def test_waiting_answers_still_at_its_timeout(controls, t1):
    controls.ask(t1)
    waited = await controls.wait("w1", 0.05)
    assert waited.kind == "still" and waited.episode.state == "held"
    assert controls.refusal("g1", "navigate") is not None


async def test_a_cancelled_wait_leaves_the_episode_holding(controls, t1):  # Review Focus 5
    controls.ask(t1)
    waiter = asyncio.create_task(controls.wait("w1", None))
    await asyncio.sleep(0.02)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert controls.refusal("g1", "navigate") is not None
    assert controls.controlling("w1", "drk")
    again = asyncio.create_task(controls.wait("w1", None))
    await asyncio.sleep(0.02)
    controls.withdraw(t1)
    async with asyncio.timeout(1):
        assert (await again).kind == "handed_back"


async def test_a_release_during_the_grant_tells_no_further_viewer(controls, passed):
    class Releasing(FakeTunnel):
        async def grant(self):
            self.granted = True
            controls.stop()

    passed.value = False
    first, second = Releasing("drk"), FakeTunnel("kim")
    controls.ask(first)
    controls.ask(second)
    passed.value = True
    await until(lambda: first.granted)
    await asyncio.sleep(0.02)
    assert not second.granted
    assert controls.refusal("g1", "navigate") is None
