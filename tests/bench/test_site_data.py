"""The save's size cap at the worst a workspace reaches: 400 origins of 20 KB,
every one over the cap together (Task 20: 1.2 s before the cap sized once)."""

import pytest

from kubed.selenium_flow.site_data import snapshot as sd

pytestmark = pytest.mark.bench

ORIGINS = 400
BLOB = "x" * 20_000


def _captured():
    others = {f"https://site-{i:03d}.example.com": {"blob": BLOB} for i in range(ORIGINS)}
    return {
        "origin": "https://app.example.com",
        "local": {"blob": BLOB},
        "others": others,
        "cookies": [],
        "session": {"t": "1"},
    }


def test_snapshot_over_the_cap(benchmark):
    captured = _captured()
    history = [{"origin": o} for o in captured["others"]]

    data, receipt = benchmark(sd.snapshot, {}, captured, history, 1.0)
    assert len(receipt["skipped"]) > 300 and data["origins"]
