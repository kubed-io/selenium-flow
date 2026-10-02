"""Every action's calls to the Grid reuse connections rather than open them.

Each action asks the Grid whether the session is alive and then reattaches a
driver to it. Both used to open a new TCP connection per call: the liveness
probe was a module-level `requests.get`, and every reattached driver built its
own urllib3 pool. These count the connections a real localhost socket accepts.
"""

import pytest
import requests

from kubed.selenium_flow.core.browser import Grid

from .fakes import CountingGridServer

pytestmark = pytest.mark.unit


@pytest.fixture
def fake():
    with CountingGridServer() as server:
        yield server


def test_the_grids_own_calls_share_one_connection(fake):
    grid = Grid(fake.url)
    assert grid.is_alive("abc") is True
    assert grid.files("abc") == []
    assert grid.status()["value"]["ready"] is True
    assert fake.connections == 1


def test_two_reconnects_share_one_webdriver_connection(fake):
    grid = Grid(fake.url)
    first, second = grid.reconnect("one"), grid.reconnect("two")
    assert first.current_url == second.current_url == "https://example.test/"
    assert first.command_executor is second.command_executor
    assert fake.connections == 1
    assert fake.requests == [("GET", "/session/one/url"), ("GET", "/session/two/url")]


def test_a_warm_action_opens_no_connection(fake):
    """Liveness, reattach and a WebDriver read, twice: one connection per pool,
    and the second action opens none."""
    grid = Grid(fake.url)
    for _ in range(2):
        assert grid.is_alive("abc")
        assert grid.reconnect("abc").current_url == "https://example.test/"
    assert fake.connections == 2


def test_a_connection_the_grid_dropped_is_asked_again_once(fake):
    """A Grid restart leaves the pool holding a connection the Grid no longer
    has; the first request on it is answered with a reset."""
    grid = Grid(fake.url)
    grid.status()
    fake.drop = 1
    assert grid.status()["value"]["ready"] is True
    assert len(fake.requests) == 3
    assert fake.connections == 2


def test_a_second_failure_raises_as_a_single_request_did(fake):
    grid = Grid(fake.url)
    grid.status()
    fake.drop = 2
    with pytest.raises(requests.ConnectionError):
        grid.status()
    assert len(fake.requests) == 3, "asked again once, not until it works"


def test_liveness_still_assumes_alive_when_the_grid_cannot_answer(fake):
    grid = Grid(fake.url)
    grid.status()
    fake.drop = 2
    assert grid.is_alive("abc") is True


def test_a_grid_that_refuses_the_connection_is_not_asked_twice(monkeypatch):
    """Only a dropped connection is worth a second ask; a refused one is the
    Grid's answer."""
    grid = Grid("http://grid.invalid:4444")
    asked = []

    def refuse(url, **_):
        asked.append(url)
        raise requests.ConnectionError("Failed to establish a new connection")

    monkeypatch.setattr(grid.http, "get", refuse)
    with pytest.raises(requests.ConnectionError):
        grid.status()
    assert len(asked) == 1


def test_the_pool_keeps_no_cookies(fake):
    """Each call used to be its own session; a cookie set by whatever fronts the
    Grid must not ride on every later call."""
    fake.cookie = "sticky=replica-2; Path=/"
    grid = Grid(fake.url)
    grid.status()
    grid.status()
    assert fake.cookies == [None, None]


def test_both_pools_open_sockets_that_notice_a_dead_hub():
    """A pooled connection tied to a hub that died with its node is never
    closed by anyone; keepalive and a user timeout make the kernel find out in
    about a minute instead of about fifteen."""
    import socket

    from kubed.selenium_flow.core.browser import SOCKET_OPTIONS

    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in SOCKET_OPTIONS
    assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in SOCKET_OPTIONS
    if hasattr(socket, "TCP_USER_TIMEOUT"):
        assert (socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 60_000) in SOCKET_OPTIONS
    grid = Grid("http://grid.invalid:4444")
    webdriver = grid.reconnect("abc").command_executor._conn
    adapter = grid.http.get_adapter("http://grid.invalid:4444")
    for manager in (webdriver, adapter.poolmanager):
        assert manager.connection_pool_kw["socket_options"] == SOCKET_OPTIONS

