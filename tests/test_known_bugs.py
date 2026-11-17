"""Likely bugs from issue #162, each pinned by a strict xfail.

A test here asserts the behaviour the code should have. It fails today, and
the day the bug is fixed it passes, which strict xfail reports as a failure:
remove the marker then.
"""

import time
from dataclasses import replace

import pytest

from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .conftest import NAMED, RecordingActions

pytestmark = pytest.mark.unit


def bug(reason):
    return pytest.mark.xfail(strict=True, reason=f"#162: {reason}")


@bug("a reap after every keep-alive ping failed reads lost")
async def test_a_reap_after_every_keep_alive_ping_failed_is_reaped():
    """The keep-alive was owed but never reached the browser, so the node's own
    timeout is the window that applied."""
    from kubed.selenium_flow.monitor.events import LocalBus
    from kubed.selenium_flow.monitor.monitor import LISTING_TICK, Monitor

    from .fakes import Clock
    from .test_monitor import Listing

    pinged = []

    def ping(session_id):
        pinged.append(session_id)
        raise ConnectionError("grid down")

    clock, listing = Clock(), Listing()
    monitor = Monitor(
        LocalBus(),
        listing=listing,
        ping=ping,
        keep_alive=900,
        clock=clock,
        tick=LISTING_TICK,
    )
    workspaces = Workspaces(RecordingActions(), monitor=monitor, keep_alive=900)
    workspaces.open_browser(Caller(NAMED), url="https://a.test/")
    session_id = workspaces.store.get(NAMED).session_id
    listing.running[session_id] = 300
    for _ in range(2):
        clock.now += 240
        await monitor.look()
    assert pinged == [session_id, session_id]
    now = time.time()
    workspaces.booted = now - 1000
    workspaces.store.update(
        NAMED,
        lambda r: replace(
            r, grid_timeout=300, called_at=now - 500, opened_at=now - 600
        ),
    )
    workspaces.actions.grid.alive.clear()
    workspaces.resolve(NAMED)
    assert workspaces.store.get(NAMED).reopened["cause"] == "reaped"
