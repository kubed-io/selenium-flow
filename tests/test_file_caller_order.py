"""Which refusal an unnamed caller is given by the file tools.

Two refusals can apply to one call and only one is the true answer. With Files
off, or a URI that is not a file, naming the workspace would not help, so those
sentences come first and the "name your workspace" one only when the call could
otherwise have gone ahead.
"""

import pytest

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.http import files
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN, calling_as

pytestmark = pytest.mark.unit


def server_for(monkeypatch, tmp_path, *, files_on: bool) -> SeleniumMCP:
    monkeypatch.delenv("DATA_DIR", raising=False)
    data = {"dir": str(tmp_path)} if files_on else {}
    server = SeleniumMCP(
        Settings(
            grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}, data=data
        )
    )
    calling_as(monkeypatch, None)
    return server


async def test_files_off_is_said_before_an_unnamed_caller_is_told_to_name_itself(
    monkeypatch, tmp_path
):
    server = server_for(monkeypatch, tmp_path, files_on=False)
    keep = await server.mcp.get_tool(files.KEEP_TOOL)
    with pytest.raises(ValueError, match="DATA_DIR"):
        keep.fn(uri="workspace://files/a.txt")
    with pytest.raises(Exception, match="DATA_DIR"):  # a resource wraps it
        await server.mcp.read_resource("workspace://files/a.txt")


async def test_a_bad_uri_is_said_before_an_unnamed_caller_is_told_to_name_itself(
    monkeypatch, tmp_path
):
    server = server_for(monkeypatch, tmp_path, files_on=True)
    keep = await server.mcp.get_tool(files.KEEP_TOOL)
    with pytest.raises(ValueError, match="is not a file"):
        keep.fn(uri="nonsense")


async def test_a_good_call_still_asks_an_unnamed_caller_to_name_itself(
    monkeypatch, tmp_path
):
    server = server_for(monkeypatch, tmp_path, files_on=True)
    keep = await server.mcp.get_tool(files.KEEP_TOOL)
    with pytest.raises(ValueError, match="name your workspace"):
        keep.fn(uri="workspace://files/a.txt")
