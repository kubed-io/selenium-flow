"""The admin session list, timed in-process at the size that hurts.

Ten sessions, each holding a 1 MB saved jar: the store hands back every jar
just to draw a row, so this is the payload at its heaviest. A fake Grid and a
fake Redis, so what is timed is ours — decoding the records, the per-row file
scans, the revision stamps. The broadcaster makes this once per tick however
many pages are open; the unit suite counts that, this times the one.
"""

from types import SimpleNamespace

import pytest

from kubed.selenium_flow.flows.store import LocalFlowStore
from kubed.selenium_flow.http.admin import workspaces as workspace_list
from kubed.selenium_flow.names import SCREENSHOTS_DIR
from kubed.selenium_flow.site_data.snapshot import _size
from kubed.selenium_flow.workspace.store import RedisStore, Workspace

from ..fakes import FakeRedis

pytestmark = pytest.mark.bench

SESSIONS = 10
JAR_BYTES = 1_000_000
FLOWS = 3
SCREENSHOTS = 10
DOWNLOADS = 5


def jar(index: int) -> dict:
    """A saved jar of about a megabyte: a few hundred cookies, and twenty
    origins' localStorage, which is where the weight really is."""
    return {
        "cookies": [
            {
                "name": f"c{n}",
                "value": f"{index}-{n}-" + "v" * 180,
                "domain": f".site{n % 20}.example.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
            }
            for n in range(200)
        ],
        "origins": {
            f"https://site{n}.example.com": {
                "local": {f"key-{k}": f"{index}-{n}-" + "x" * 1000 for k in range(45)}
            }
            for n in range(20)
        },
        "session": {},
        "saved_at": 1_760_000_000.0 + index,
    }


class Grid:
    """Every other session is live, and each live one has a few downloads."""

    def __init__(self, live):
        self.live = live

    def sessions(self):
        return [{"session_id": sid, "version": "140", "node": "n1"} for sid in self.live]

    def files(self, session_id):
        return [
            {"name": f"report-{n}.pdf", "creationTime": n} for n in range(DOWNLOADS)
        ]


class Routes:
    """Stands in for FastMCP: only the payload is timed, so routes go nowhere."""

    def custom_route(self, *_args, **_kwargs):
        return lambda handler: handler


@pytest.fixture(scope="module")
def workspaces_payload(tmp_path_factory):
    store = RedisStore(FakeRedis(), prefix="selenium-flow:")
    flow_store = LocalFlowStore(tmp_path_factory.mktemp("flows"))
    live = []
    for index in range(SESSIONS):
        key = f"agent-{index:02d}"
        sid = f"{index:032x}" if index % 2 else ""
        if sid:
            live.append(sid)
        record = Workspace(
            session_id=sid,
            opened_at=1_760_000_000.0 + index,
            settings={"browser": "chrome", "width": 1440, "height": 900},
            site_data=jar(index),
        )
        for page in range(5):
            record = record.visited(f"https://site{page}.example.com/p/{index}")
        store.set(key, record)
        for n in range(FLOWS):
            flow_store.save(key, f"flow-{n}", {"steps": [{"tool": "navigate",
                            "args": {"url": f"https://site{n}.example.com"}}]})
        for n in range(SCREENSHOTS):
            flow_store.write_file(key, f"shot-{n:02d}.png", bytes(1024), SCREENSHOTS_DIR)
    broadcast = workspace_list.mount(
        Routes(),
        SimpleNamespace(grid=Grid(live)),
        SimpleNamespace(store=store),
        flow_store,
        None,
        "",
        lambda handler: handler,
    )
    return broadcast.compute


def test_the_fixture_is_the_size_it_claims():
    """Guards the number below: a jar that shrank would still be fast."""
    assert 0.9 * JAR_BYTES < _size(jar(0)) < 1.2 * JAR_BYTES


def test_workspaces_payload(benchmark, workspaces_payload):
    rows = benchmark(workspaces_payload)["workspaces"]
    assert len(rows) == SESSIONS
    assert sum(r["live"] for r in rows) == SESSIONS // 2
