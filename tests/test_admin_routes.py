"""The admin package's route table, held still while its code moves."""

import pytest

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP

from . import golden_tools
from .conftest import TOKEN

pytestmark = pytest.mark.unit

# The routes `admin.register` mounts that are not named `admin_*`: the page at
# the root and the three signed file routes.
ALSO = {"file", "kept_file", "screenshot_file"}


def test_the_route_table_is_unchanged():
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN})
    )
    table = sorted(
        [method, route.path, route.name]
        for route in server.mcp.http_app().routes
        if route.name in ALSO or str(route.name).startswith("admin_")
        for method in sorted(route.methods - {"HEAD"})
    )
    golden_tools.compare("admin-routes.json", table)
