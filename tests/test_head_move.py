"""An `open_session` that opens a browser while the head of the list is idle
takes the head, and the old head is told once, on its next result (WS-86)."""

from __future__ import annotations

import pytest

from kubed.selenium_flow.flows import api as flowapi
from kubed.selenium_flow.flows import store as flow_store
from kubed.selenium_flow.secrets import perform_write
from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace.store import MemoryStore, RedisStore

from .fakes import Anywhere, Clock, FakeRedis, RecordingActions, record_of, shared_of
from .test_locks import in_thread
from .test_session_holder import FIRST, SECOND, as_, built, pinned

pytestmark = pytest.mark.unit

FIRST_KEY = sessions.key_of("shop", "first")
PAGE = "https://example.com/"
PREFIX = "selenium-flow:workspace:"


class Driving(RecordingActions):
    """`RecordingActions`, with the reads a bound write and a hand-back make."""

    def page(self, session_id):
        return {"url": PAGE, "title": "Example"}

    def write(self, session_id, text, **kwargs):
        return {"url": PAGE, "value": None}

    def outline(self, session_id, **kwargs):
        return {"url": PAGE, "title": "Example", "count": 0, "total": 0, "elements": []}

    def paste(self, session_id, text):
        return {"typed": len(text)}

    def save_site_data(self, session_id, url=None):
        return {"url": PAGE, "title": "Example"}


def memory():
    return MemoryStore(clock=Clock())


def grid_id(store, name):
    return record_of(store, "shop", name).session_id


def reaped(store=None, actions=None):
    """FIRST opened and the Grid reaped its browser, then SECOND opened: the
    move is made, and FIRST's next call reopens its browser silently."""
    workspaces, store, actions = built(store or memory(), actions)
    workspaces.open_browser(as_(FIRST))
    actions.grid.alive.discard(grid_id(store, "first"))
    opened = workspaces.open_browser(as_(SECOND))
    return workspaces, store, actions, opened


def call(workspaces, principal=FIRST, session=None):
    return workspaces.act(
        as_(principal), lambda sid: {"url": PAGE}, session=session, tool="navigate"
    )


def told_of(result):
    return result.get("main_moved")


def test_an_idle_head_moves():
    workspaces, store, _ = built(memory())
    workspaces.open_browser(as_(FIRST))
    workspaces.end_browser(as_(FIRST))
    opened = workspaces.open_browser(as_(SECOND))
    assert shared_of(store, "shop").sessions == ["second", "first"]
    assert opened["main"] is True
    assert "main_moved" not in opened


def test_a_live_head_stays():
    workspaces, store, _ = built(memory())
    workspaces.open_browser(as_(FIRST))
    opened = workspaces.open_browser(as_(SECOND))
    assert shared_of(store, "shop").sessions == ["first", "second"]
    assert opened["main"] is False
    assert shared_of(store, "shop").main_moved == {}


def test_a_reaped_head_is_idle():
    workspaces, store, actions = built(memory())
    workspaces.open_browser(as_(FIRST))
    first = grid_id(store, "first")
    actions.grid.alive.discard(first)
    actions.grid.checked.clear()
    workspaces.open_browser(as_(SECOND))
    assert shared_of(store, "shop").sessions == ["second", "first"]
    assert actions.grid.checked.count(first) == 1


def test_the_order_behind_the_head_is_kept():
    workspaces, store, _ = built(memory())
    for name in ("a", "b", "c"):
        workspaces.open_browser(as_(FIRST), session=name)
    workspaces.end_browser(as_(FIRST), session="a")
    workspaces.open_browser(as_(FIRST), session="c", fresh=True)
    assert shared_of(store, "shop").sessions == ["c", "a", "b"]


def test_a_join_moves_nothing():
    workspaces, store, actions = built(memory())
    workspaces.open_browser(as_(FIRST))
    workspaces.open_browser(as_(SECOND))
    actions.grid.alive.discard(grid_id(store, "first"))
    joined = workspaces.open_browser(as_(SECOND))
    assert joined["joined"] is True
    assert shared_of(store, "shop").sessions == ["first", "second"]
    assert shared_of(store, "shop").main_moved == {}


def test_the_notice_is_told_once_to_from_only():
    workspaces, _, _, _ = reaped()
    assert told_of(call(workspaces, SECOND)) is None
    first = call(workspaces)
    assert first["main"] is False
    assert first["main_moved"] == {"from": "first", "to": "second", "at": first[
        "main_moved"]["at"]}
    assert isinstance(first["main_moved"]["at"], int)
    again = call(workspaces)
    assert "main" not in again and "main_moved" not in again


def test_resource_reads_do_not_tell():
    workspaces, store, _, _ = reaped()
    read = workspaces.describe(as_(FIRST))
    pinned_read = workspaces.describe(pinned("first", FIRST), at="session://first")
    assert "main_moved" not in read and "main_moved" not in pinned_read
    assert "first" in shared_of(store, "shop").main_moved
    assert call(workspaces)["main_moved"]["from"] == "first"


@pytest.mark.parametrize("how", ["open", "join"])
def test_open_session_tells_it(how):
    workspaces, store, actions = built(memory())
    workspaces.open_browser(as_(FIRST))
    if how == "open":
        workspaces.end_browser(as_(FIRST))
    else:
        actions.grid.alive.discard(grid_id(store, "first"))
    workspaces.open_browser(as_(SECOND))
    assert shared_of(store, "shop").sessions == ["second", "first"]
    if how == "join":
        workspaces.resolve(FIRST_KEY)  # a silent reopen, which tells nothing
    opened = workspaces.open_browser(as_(FIRST))
    assert ("joined" in opened) is (how == "join")
    assert opened["main"] is False
    assert opened["main_moved"]["from"] == "first"
    assert opened["main_moved"]["to"] == "second"
    assert "main_moved" not in call(workspaces)


def moved_twice():
    """``a`` moved behind ``b``, then ``b``, once idle, behind ``c``."""
    workspaces, store, actions = built(memory())
    workspaces.open_browser(as_(FIRST), session="a")
    actions.grid.alive.discard(grid_id(store, "a"))
    workspaces.open_browser(as_(FIRST), session="b")
    assert shared_of(store, "shop").main_moved["a"]["to"] == "b"
    actions.grid.alive.discard(grid_id(store, "b"))
    workspaces.open_browser(as_(FIRST), session="c")
    assert shared_of(store, "shop").sessions == ["c", "b", "a"]
    return workspaces, store


def test_two_moves_keep_two_notices():
    workspaces, store = moved_twice()
    assert set(shared_of(store, "shop").main_moved) == {"a", "b"}
    a = call(workspaces, session="a")["main_moved"]
    b = call(workspaces, session="b")["main_moved"]
    assert (a["from"], a["to"]) == ("a", "c")
    assert (b["from"], b["to"]) == ("b", "c")


def test_to_is_the_current_head_when_told():
    workspaces, store = moved_twice()
    assert shared_of(store, "shop").main_moved["a"]["to"] == "b"
    assert call(workspaces, session="a")["main_moved"]["to"] == "c"


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_racing_calls_tell_it_once(backend):
    store = memory() if backend == "memory" else RedisStore(FakeRedis())
    workspaces, _, _, _ = reaped(store)
    workspaces.resolve(FIRST_KEY)
    runs = [in_thread(lambda: call(workspaces)) for _ in range(2)]
    results = []
    for thread, outcome in runs:
        thread.join(5)
        results.extend(outcome)
    assert all(isinstance(r, dict) for r in results), results
    assert sum("main_moved" in r for r in results) == 1


def test_a_failed_call_keeps_the_notice(monkeypatch):
    workspaces, store, _, _ = reaped()
    real = workspaces._save_site_data
    failures = [RuntimeError("the store failed")]

    def save_once(*args, **kwargs):
        if failures:
            raise failures.pop()
        return real(*args, **kwargs)

    monkeypatch.setattr(workspaces, "_save_site_data", save_once)
    with pytest.raises(RuntimeError):
        call(workspaces)
    assert "first" in shared_of(store, "shop").main_moved
    assert call(workspaces)["main_moved"]["from"] == "first"


def run_flow(workspaces, actions, tmp_path):
    flows = flow_store.LocalFlowStore(tmp_path)
    flows.save(
        "shop", "trip", {"steps": [{"tool": "navigate", "args": {"url": PAGE}}]}
    )
    return flowapi.run_for(
        flows, actions, workspaces, "shop", "trip", key=FIRST_KEY, own=FIRST_KEY
    )


def secret_write(workspaces, actions, tmp_path):
    return perform_write(
        Anywhere(), actions, workspaces, FIRST_KEY,
        {"secret": {"name": "demo", "key": "password"}, "selector": {"css": "#p"}},
    )


def await_control(workspaces, actions, tmp_path):
    return workspaces.handed_back(FIRST_KEY, None, own=FIRST_KEY)


@pytest.mark.parametrize("tool", [run_flow, secret_write, await_control])
def test_the_notice_rides_every_tools_result(tool, tmp_path):
    workspaces, store, actions, _ = reaped(actions=Driving())
    workspaces.resolve(FIRST_KEY)
    result = tool(workspaces, actions, tmp_path)
    assert result["main"] is False
    assert result["main_moved"]["from"] == "first"
    assert result["main_moved"]["to"] == "second"
    assert shared_of(store, "shop").main_moved == {}
    assert "main_moved" not in tool(workspaces, actions, tmp_path)


def test_lifetimes_follow_the_move():
    fake = FakeRedis()
    reaped(RedisStore(fake, ttl=100, sub_ttl=10))
    assert fake.expiries[PREFIX + "shop/second"] == 100
    assert fake.expiries[PREFIX + "shop/first"] == 10


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_the_old_head_takes_sub_ttl_at_the_move(backend):
    clock = Clock()
    fake = FakeRedis()
    if backend == "memory":
        store = MemoryStore(ttl=100, sub_ttl=10, clock=clock)
    else:
        store = RedisStore(fake, ttl=100, sub_ttl=10)
    store.kept = lambda workspace: True
    reaped(store)
    if backend == "redis":
        assert fake.expiries[PREFIX + "shop/second"] is None
        assert fake.expiries[PREFIX + "shop/first"] == 10
        return
    clock.now += 9
    assert store.get("shop/first") is not None
    clock.now += 2
    assert store.get("shop/first") is None
    clock.now += 10**9
    assert store.get("shop/second") is not None


def test_a_head_that_is_main_again_is_never_told():
    """The new head expired and the list was pruned, so the old head heads it
    again with no open: it is main, and nothing tells it otherwise."""
    workspaces, store, _, _ = reaped()
    store.delete(sessions.key_of("shop", "second"))
    workspaces.listed("shop")
    assert shared_of(store, "shop").head == "first"
    acted = call(workspaces)
    assert "main_moved" not in acted and "main" not in acted
    assert shared_of(store, "shop").main_moved == {}


def test_touch_never_tells_a_move():
    workspaces, store, _, _ = reaped()
    workspaces.resolve(FIRST_KEY)
    workspaces.touch(FIRST_KEY, PAGE)
    assert "first" in shared_of(store, "shop").main_moved
    assert call(workspaces)["main_moved"]["from"] == "first"


def test_a_paste_leaves_the_notice_for_the_next_result():
    workspaces, store, _, _ = reaped(actions=Driving())
    workspaces.resolve(FIRST_KEY)
    assert workspaces.paste(FIRST_KEY, "hello") == {"typed": 5}
    assert "first" in shared_of(store, "shop").main_moved
    assert call(workspaces)["main_moved"]["from"] == "first"


def test_a_runs_flush_before_a_save_leaves_the_notice_for_its_result(tmp_path):
    workspaces, _, actions, _ = reaped(actions=Driving())
    workspaces.resolve(FIRST_KEY)
    flows = flow_store.LocalFlowStore(tmp_path)
    flows.save("shop", "keep", {"steps": [
        {"tool": "navigate", "args": {"url": PAGE}},
        {"tool": "save_site_data", "args": {}},
    ]})
    flushed = []
    settle = workspaces.settle

    def noting(name, result, **kwargs):
        if kwargs.get("deliver") is False:
            flushed.append(name)
        return settle(name, result, **kwargs)

    workspaces.settle = noting
    report = flowapi.run_for(
        flows, actions, workspaces, "shop", "keep", key=FIRST_KEY, own=FIRST_KEY
    )
    assert report["status"] == "ok", report
    assert flushed == [FIRST_KEY], "the run flushed its pages before the save"
    assert report["main_moved"]["from"] == "first"
