"""Shared fixtures: a server wired to a Grid that is never actually dialled."""

import pytest

from kubed.selenium_flow.actions import Actions
from kubed.selenium_flow.browser import Grid
from kubed.selenium_flow.server import SeleniumMCP

TOKEN = "test-token-abc123"


@pytest.fixture
def grid():
    """A Grid pointed at an address nothing listens on.

    Tests here exercise wiring and coercion, never a real browser; anything that
    needs a live Grid is an integration test and is marked as one.
    """
    return Grid("http://grid.invalid:4444")


@pytest.fixture
def actions(grid):
    return Actions(grid)


@pytest.fixture
def server():
    """An authenticated server, since that is how it is deployed."""
    return SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)


@pytest.fixture
def open_server():
    """A server with auth disabled, as `docker compose up` runs it."""
    return SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=None)
