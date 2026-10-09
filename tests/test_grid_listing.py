"""What `/status` says about running browsers, and where a browser's BiDi is."""

import pytest

from kubed.selenium_flow.core.browser import Grid

pytestmark = pytest.mark.unit

# The shape the cluster's Grid 4.48.0 answered with on 2026-10-09, trimmed.
STATUS = {
    "value": {
        "ready": True,
        "nodes": [
            {
                "id": "node-a",
                "sessionTimeout": 300000,
                "slots": [
                    {"session": {"sessionId": "aaa"}},
                    {"session": None},
                ],
            },
            {
                "id": "node-b",
                "sessionTimeout": 90000,
                "slots": [{"session": {"sessionId": "bbb"}}],
            },
            {"id": "node-c", "slots": [{"session": {"sessionId": "ccc"}}]},
        ],
    }
}


def grid(payload=STATUS, url="http://grid:4444"):
    g = Grid(url)
    g.status = lambda: payload
    return g


def test_the_listing_counts_nodes_and_maps_each_browser_to_its_timeout():
    assert grid().listing() == (3, {"aaa": 300, "bbb": 90, "ccc": None})


def test_a_grid_with_no_nodes_lists_nothing():
    assert grid({"value": {"ready": False, "nodes": []}}).listing() == (0, {})


@pytest.mark.parametrize("value", [0, -1, "300000", True, None])
def test_a_timeout_that_is_not_a_positive_number_is_none(value):
    payload = {
        "value": {
            "nodes": [
                {"sessionTimeout": value, "slots": [{"session": {"sessionId": "a"}}]}
            ]
        }
    }
    assert grid(payload).listing() == (1, {"a": None})


def test_session_timeout_reads_the_browsers_own_node():
    g = grid()
    assert g.session_timeout("bbb") == 90
    assert g.session_timeout("gone") is None


@pytest.mark.parametrize(
    ("url", "socket"),
    [
        ("http://grid:4444", "ws://grid:4444/session/abc/se/bidi"),
        ("https://grid.example/wd/", "wss://grid.example/wd/session/abc/se/bidi"),
    ],
)
def test_the_bidi_url_is_derived_from_the_grids_own(url, socket):
    assert Grid(url).bidi_url("abc") == socket
