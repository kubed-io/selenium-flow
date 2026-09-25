"""The Redis session listing every open admin page polls, timed in-process.

A fake client, so what is timed is ours: the SCAN filter and one MGET decoded
into records (§F4.19). The network hop that MGET saves is exactly the part a
fake cannot show, which is why the unit suite counts the calls instead.
"""

import pytest

from kubed.selenium_flow.session.store import RedisStore, SessionRecord

pytestmark = pytest.mark.bench

SESSIONS = 50


class FakeRedis:
    """What `records()` calls, answered from a dict the way redis-py does."""

    def __init__(self):
        self.data = {}

    def set(self, key, value, ex=None):
        self.data[key] = value.encode()

    def scan_iter(self, match="*", count=None):
        prefix = match.rstrip("*")
        return [key.encode() for key in self.data if key.startswith(prefix)]

    def mget(self, keys):
        return [self.data.get(key) for key in keys]


@pytest.fixture(scope="module")
def store():
    store = RedisStore(FakeRedis(), prefix="selenium-flow:")
    for index in range(SESSIONS):
        store.set(
            f"agent-{index:02d}",
            SessionRecord(
                session_id=f"{index:032x}" if index % 4 else "",
                url=f"https://app.example.com/section/{index}?tab=overview",
                opened_at=1_760_000_000.0 + index,
                settings={"browser": "chrome", "width": 1440, "height": 900},
            ),
        )
    return store


def test_records(benchmark, store):
    assert len(benchmark(store.records)) == SESSIONS
