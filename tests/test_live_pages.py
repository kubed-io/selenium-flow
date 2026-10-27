"""The pages a person reaches while they hold Control, heard on the monitor's
connection (spec 2026-10-27-live-control, rulings 19, 20 and 26)."""

import asyncio
import logging

import pytest

from kubed.selenium_flow.live import pages as pages_module
from kubed.selenium_flow.live.pages import EVENTS, Pages

pytestmark = pytest.mark.unit

GID = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"
LOAD = "browsingContext.load"
HISTORY = "browsingContext.historyUpdated"
CREATED = "browsingContext.contextCreated"


class TitleConnection:
    """`bidi.Connection.command` for `script.evaluate`: each title in turn;
    ``hang`` never answers, ``fail`` raises as a dead socket does."""

    def __init__(self, titles=(), *, hang=False, fail=False):
        self.titles = list(titles)
        self.hang, self.fail = hang, fail
        self.commands = []

    async def command(self, method, params=None, *, timeout=5.0):
        self.commands.append((method, params))
        if self.hang:
            await asyncio.Event().wait()
        if self.fail:
            raise ConnectionError("the BiDi socket closed")
        title = self.titles.pop(0) if self.titles else ""
        return {"type": "success", "result": {"type": "string", "value": title}, "realm": "r"}


def following(conn=None, top="top-1", again=False, *, subscribed=True):
    """Pages following GID, attached and, unless asked not to, subscribed."""
    pages = Pages()
    pages.connection = lambda grid_id: conn if grid_id == GID else None
    pages.follow(GID)
    pages.attached("w1", GID, "chrome", top, again=again)
    if subscribed:
        pages.subscribed("w1", GID)
    return pages


async def settled(pages):
    """Every title read under way has answered or given up."""
    async with asyncio.timeout(2):
        while pages._lists[GID].reading:
            await asyncio.sleep(0.005)


def test_the_events_are_ruling_nineteens():
    assert EVENTS == (LOAD, HISTORY, CREATED)


async def test_pages_in_order_with_titles():
    conn = TitleConnection(["Sign in", "Inbox"])
    pages = following(conn)
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/login?next=1"})
    await settled(pages)
    pages.ingest("w1", GID, HISTORY, {"context": "top-1", "url": "https://a.example/inbox#3"})
    await settled(pages)
    assert pages.listed(GID) == (
        [
            {"url": "https://a.example/login?next=1", "title": "Sign in"},
            {"url": "https://a.example/inbox#3", "title": "Inbox"},
        ],
        0,
    )
    method, params = conn.commands[0]
    assert method == "script.evaluate"
    assert params == {
        "expression": "document.title",
        "target": {"context": "top-1"},
        "awaitPromise": False,
    }


async def test_the_same_page_twice_in_a_row_is_one_entry():
    pages = following(TitleConnection(["A", "A"]))
    for method in (LOAD, HISTORY):
        pages.ingest("w1", GID, method, {"context": "top-1", "url": "https://a.example/"})
        await settled(pages)
    assert pages.listed(GID) == ([{"url": "https://a.example/", "title": "A"}], 0)


async def test_a_frames_load_is_not_a_page():
    pages = following(TitleConnection(["x"]))
    pages.ingest("w1", GID, CREATED, {"context": "frame-1", "parent": "top-1", "url": "about:blank"})
    pages.ingest("w1", GID, LOAD, {"context": "frame-1", "url": "https://ads.example/frame"})
    pages.ingest("w1", GID, LOAD, {"context": "old-tab", "url": "https://a.example/elsewhere"})
    await settled(pages)
    assert pages.listed(GID) == ([], 0)


async def test_a_new_tab_is_followed():
    pages = following(TitleConnection(["New"]))
    pages.ingest("w1", GID, CREATED, {"context": "tab-2", "parent": None, "url": "about:blank"})
    pages.ingest("w1", GID, LOAD, {"context": "tab-2", "url": "https://b.example/"})
    await settled(pages)
    assert pages.listed(GID) == ([{"url": "https://b.example/", "title": "New"}], 0)


async def test_a_title_that_does_not_come_back_is_left_out(monkeypatch):
    monkeypatch.setattr(pages_module, "REPLY_TIMEOUT", 0.05)
    pages = following(TitleConnection(hang=True))
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})
    await settled(pages)
    assert pages.listed(GID) == ([{"url": "https://a.example/"}], 0)

    broken = following(TitleConnection(fail=True))
    broken.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})
    await settled(broken)
    assert broken.listed(GID) == ([{"url": "https://a.example/"}], 0)

    unheld = following(None)  # no connection held: nothing to ask
    unheld.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})
    assert unheld.listed(GID) == ([{"url": "https://a.example/"}], 0)


async def test_a_url_with_no_origin_records_nothing():
    pages = following(TitleConnection())
    for url in ("about:blank", "data:text/html,<p>hi</p>", "", None, "javascript:void(0)"):
        pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": url})
    await settled(pages)
    assert pages.listed(GID) == ([], 0)


async def test_at_most_a_hundred_pages_with_dropped():
    pages = following(None)
    for n in range(105):
        pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": f"https://a.example/{n}"})
    listed, dropped = pages.listed(GID)
    assert len(listed) == 100 and dropped == 5
    assert listed[0]["url"] == "https://a.example/5"
    assert listed[-1]["url"] == "https://a.example/104"


async def test_nothing_is_heard_for_a_browser_not_followed():
    pages = Pages()
    pages.attached("w1", GID, "chrome", "top-1", again=False)
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})
    assert pages.listed(GID) == ([], 0)
    assert pages.stop(GID) == []


async def test_stop_ends_the_list_and_its_title_reads():
    conn = TitleConnection(hang=True)
    pages = following(conn)
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})
    [reading] = pages._lists[GID].reading
    assert pages.stop(GID) == [{"url": "https://a.example/"}]
    await asyncio.sleep(0)
    assert reading.cancelled() or reading.done()
    assert pages.listed(GID) == ([], 0)
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/2"})
    assert pages.listed(GID) == ([], 0)


@pytest.mark.parametrize("again", [False, True], ids=["first hold", "hold again"])
async def test_nothing_is_heard_until_the_subscribe_is_behind(again):
    pages = following(None, again=again, subscribed=False)
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/replayed"})
    pages.replayed("w1", GID)  # a hold again's replay is over: not yet the subscribe
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/still"})
    pages.subscribed("w1", GID)
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/new"})
    assert pages.listed(GID) == ([{"url": "https://a.example/new"}], 0)


async def test_a_tab_open_before_the_hold_is_not_followed_and_a_new_one_is():
    """Ruling 26. Subscribing contextCreated re-sends it, parent null, for every
    tab already open: heard during the subscribe, it must not make one a top."""
    pages = following(None, subscribed=False)
    pages.ingest("w1", GID, CREATED, {"context": "old-tab", "parent": None, "url": "https://o.example/"})
    pages.subscribed("w1", GID)
    pages.ingest("w1", GID, LOAD, {"context": "old-tab", "url": "https://o.example/next"})
    assert pages.listed(GID) == ([], 0)
    pages.ingest("w1", GID, CREATED, {"context": "new-tab", "parent": None, "url": "about:blank"})
    pages.ingest("w1", GID, LOAD, {"context": "new-tab", "url": "https://n.example/"})
    assert pages.listed(GID) == ([{"url": "https://n.example/"}], 0)


async def test_a_page_left_and_come_back_to_is_listed_again():
    pages = following(None)
    for url in ("https://a.example/", "https://b.example/", "https://a.example/"):
        pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": url})
    assert [e["url"] for e in pages.listed(GID)[0]] == [
        "https://a.example/", "https://b.example/", "https://a.example/"
    ]


async def test_the_same_page_in_two_tabs_is_two_entries():
    pages = following(None)
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})
    pages.ingest("w1", GID, CREATED, {"context": "tab-2", "parent": None})
    pages.ingest("w1", GID, LOAD, {"context": "tab-2", "url": "https://a.example/"})
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})  # a reload
    assert pages.listed(GID) == ([{"url": "https://a.example/"}, {"url": "https://a.example/"}], 0)


async def test_follow_starts_a_fresh_list():
    pages = following(None)
    pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})
    pages.follow(GID)
    assert pages.listed(GID) == ([], 0)


async def test_no_url_or_title_is_logged(caplog):
    with caplog.at_level(logging.DEBUG):
        pages = following(TitleConnection(fail=True))
        pages.ingest("w1", GID, LOAD, {"context": "top-1", "url": "https://secret.example/t?k=v"})
        await settled(pages)
        pages.stop(GID)
    assert "secret.example" not in caplog.text and GID not in caplog.text
