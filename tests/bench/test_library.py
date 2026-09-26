"""The flow and file reads §F4.19 made fast, timed in-process.

Sized like the pod they were measured in: ~150 screenshots, and 30 flows of a
few KB each with real steps, a few of which type a secret. Built once per
module, because the fixture is not what is being timed.

A benchmark's name is its history in bench.yml's baseline: rename one and its
chart starts again from nothing.
"""

import os

import pytest

from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.http import secret_uses

pytestmark = pytest.mark.bench

SESSION = "desktop"
FLOWS = 30
SCREENSHOTS = 150


def flow(index: int) -> dict:
    """A deterministic flow of roughly 3KB: sign in, then walk a few pages."""
    steps = [
        {"tool": "navigate", "args": {"url": "${origin}/login"}},
        {
            "tool": "write",
            "args": {
                "selector": {"css": "#username"},
                "text": "${user}",
            },
        },
        {
            "tool": "write",
            "args": {
                "selector": {"css": "#password"},
                # One secret in three flows, so the backlinks have something
                # to find without every flow finding it.
                "secret": {"name": f"app-{index % 3}", "key": "password"},
            },
        },
        {
            "tool": "interact",
            "args": {"action": "click", "selector": {"css": "form button[type=submit]"}},
        },
    ]
    for page in range(6):
        steps += [
            {"tool": "navigate", "args": {"url": f"${{origin}}/section/{index}/{page}"}},
            {
                "tool": "extract",
                "args": {"selector": {"xpath": f"//main//section[@id='part-{page}']"}},
            },
            {
                "tool": "assert",
                "args": {
                    "script": (
                        "return document.querySelectorAll('main .row').length > 0"
                    ),
                    "message": (
                        f"Section {page} rendered no rows. Either the listing "
                        "came back empty or the page did not draw what it got."
                    ),
                },
            },
        ]
    return {
        "description": (
            f"Flow {index}: sign in, then read each section of the report and "
            "stop at the first one that rendered nothing."
        ),
        "parameters": {
            "type": "object",
            "required": ["origin", "user"],
            "properties": {
                "origin": {"type": "string", "description": "Where the app is"},
                "user": {"type": "string", "description": "Who signs in"},
            },
        },
        "steps": steps,
    }


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("flows")
    store = flows.LocalFlowStore(root)
    for index in range(FLOWS):
        store.save(SESSION, f"flow-{index:02d}", flow(index))
    for index in range(SCREENSHOTS):
        entry = store.write_file(
            SESSION,
            f"screenshot-{index:03d}.png",
            bytes(2048),
            flows.SCREENSHOTS_DIR,
        )
        # Distinct mtimes, so the newest-first sort has an order to find.
        stamp = 1_760_000_000 + index
        os.utime(root / SESSION / flows.SCREENSHOTS_DIR / entry["name"], (stamp, stamp))
    return store


def test_the_fixture_is_the_size_it_claims(store):
    """Guards the numbers below: a fixture that shrank would still be fast."""
    names = store.names(SESSION)
    assert len(names) == FLOWS
    sizes = [len(store.read_text(SESSION, name)) for name in names]
    assert min(sizes) > 2000 and max(sizes) < 5000, sizes
    assert len(store.files(SESSION, flows.SCREENSHOTS_DIR)) == SCREENSHOTS
    assert len(secret_uses.uses(store)) == 3


def test_files(benchmark, store):
    found = benchmark(store.files, SESSION, flows.SCREENSHOTS_DIR)
    assert len(found) == SCREENSHOTS


def test_revision(benchmark, store):
    assert benchmark(store.revision, SESSION).count(";") == FLOWS - 1


def warmed(cache: str):
    """Setup for one round: an empty parse cache, or the one already there."""

    def setup():
        if cache == "cold":
            flows._parsed.cache_clear()

    return setup


@pytest.mark.parametrize("cache", ["cold", "warm"])
def test_summaries(benchmark, store, cache):
    store.summaries(SESSION)  # warm has to start warm
    found = benchmark.pedantic(
        store.summaries, args=(SESSION,), setup=warmed(cache), rounds=30
    )
    assert len(found) == FLOWS


@pytest.mark.parametrize("cache", ["cold", "warm"])
def test_secret_uses(benchmark, store, cache):
    secret_uses.uses(store)
    found = benchmark.pedantic(
        secret_uses.uses, args=(store,), setup=warmed(cache), rounds=30
    )
    assert sum(len(by) for by in found.values()) == FLOWS
