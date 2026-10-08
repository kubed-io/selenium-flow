"""The Recordings folder on every surface: the Screenshots folder's twin."""

import asyncio
import json

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.http import files, links
from kubed.selenium_flow.http.admin import signed
from kubed.selenium_flow.names import FILES_DIR, RECORDINGS_DIR
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.session.sessions import STDIO_NAME

pytestmark = pytest.mark.unit

S = "desktop"


class Grid:
    def files(self, _sid):
        return []

    def read_file(self, _sid, _name):
        return b""


class Actions:
    grid = Grid()


class Sessions:
    def browser(self, _name):
        return ""


@pytest.fixture
def store(tmp_path):
    s = flows.LocalFlowStore(tmp_path)
    s.write_file(S, "rec-20261008-1403.mp4", b"\x00" * 64, RECORDINGS_DIR)
    return s


def test_addresses():
    assert files.uri_of(RECORDINGS_DIR, "a.mp4") == "session://files/recordings/a.mp4"
    assert files.parse_uri("session://files/recordings/a.mp4") == (RECORDINGS_DIR, "a.mp4")
    assert "recordings" in files.RESERVED


def test_the_root_names_a_third_folder(store):
    root = files.root(Actions(), Sessions(), store, None, S)
    named = {f["name"]: f for f in root["folders"]}
    assert named["recordings"]["count"] == 1
    assert named["recordings"]["uri"] == "session://files/recordings"


def test_a_recording_entry_is_a_file_entry_with_keep_with(store):
    listing = files.folder(Actions(), Sessions(), store, None, S, RECORDINGS_DIR)
    entry = listing["files"][0]
    assert entry["content_type"] == "video/mp4" and entry["image"] is False
    assert entry["keep_with"] == 'keep_file("session://files/recordings/rec-20261008-1403.mp4")'
    assert entry["url"].startswith("/recordings/desktop/")


def test_keeping_a_recording_moves_it_into_files(store):
    kept = files.keep(Actions(), Sessions(), store, "session://files/recordings/rec-20261008-1403.mp4", S)
    assert kept["uri"] == "session://files/rec-20261008-1403.mp4"
    assert store.files(S, RECORDINGS_DIR) == []
    assert [f["name"] for f in store.files(S, FILES_DIR)] == ["rec-20261008-1403.mp4"]


def test_clearing_recordings_leaves_files_and_notes(store):
    store.write_note(S, "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d", {"opened": 1})
    store.write_file(S, "keep.txt", b"k")
    assert files.clear_recordings(store, S)["cleared"] == 1
    assert store.files(S, RECORDINGS_DIR) == []
    assert len(store.notes()) == 1 and len(store.files(S)) == 1


def test_a_video_is_served_from_disk_with_range_and_no_sandbox(store):
    from fastmcp import FastMCP

    mcp = FastMCP("t")
    signed.mount(mcp, Actions(), store, None, "")
    client = TestClient(mcp.http_app())
    path = links.recording_path(S, "rec-20261008-1403.mp4")
    resp = client.get(path, headers={"Range": "bytes=0-9"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 0-9/64"
    assert resp.headers["content-security-policy"] == signed.MEDIA_ONLY
    assert "sandbox" not in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["content-disposition"] == signed.disposition("rec-20261008-1403.mp4")


def test_a_kept_video_in_files_is_served_the_same_way(store):
    from fastmcp import FastMCP

    files.keep(Actions(), Sessions(), store, "session://files/recordings/rec-20261008-1403.mp4", S)
    mcp = FastMCP("t")
    signed.mount(mcp, Actions(), store, None, "")
    resp = TestClient(mcp.http_app()).get(
        links.kept_path(S, "rec-20261008-1403.mp4"), headers={"Range": "bytes=0-0"}
    )
    assert resp.status_code == 206


def test_a_missing_recording_is_404(store):
    from fastmcp import FastMCP

    mcp = FastMCP("t")
    signed.mount(mcp, Actions(), store, None, "")
    resp = TestClient(mcp.http_app()).get(links.recording_path(S, "nope.mp4"))
    assert resp.status_code == 404


@pytest.fixture
def srv(tmp_path):
    server = SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}, data={"dir": str(tmp_path)}))
    server.flows.write_file(STDIO_NAME, "rec-20261008-1403.mp4", b"\x00" * 64, RECORDINGS_DIR)
    return server


def test_the_folder_and_its_template_are_resources(srv):
    async def go():
        async with Client(srv.mcp) as c:
            listed = {str(r.uri) for r in await c.list_resources()}
            templated = {t.uri_template for t in await c.list_resource_templates()}
            return listed, templated
    listed, templated = asyncio.run(go())
    assert "session://files/recordings" in listed
    assert "session://files/recordings/{name}" in templated


def test_reading_one_recording_answers_its_entry_not_the_video(srv):
    async def go():
        async with Client(srv.mcp) as c:
            return await c.read_resource("session://files/recordings/rec-20261008-1403.mp4")
    got = asyncio.run(go())
    entry = json.loads(got[0].text)
    assert entry["name"] == "rec-20261008-1403.mp4" and entry["url"]
    assert entry["content_type"] == "video/mp4"


def test_keep_file_moves_a_recording(srv):
    async def go():
        async with Client(srv.mcp) as c:
            return await c.call_tool("keep_file", {"uri": "session://files/recordings/rec-20261008-1403.mp4"})
    result = asyncio.run(go())
    assert result.structured_content["uri"] == "session://files/rec-20261008-1403.mp4"
    assert srv.flows.files(STDIO_NAME, RECORDINGS_DIR) == []


def test_a_kept_recording_read_from_files_answers_its_entry_not_the_video(srv):
    """The Files item resource holds the Recordings rule for any video: a model
    cannot watch one, and base64 of a whole video is all a read would carry."""
    srv.flows.write_file(STDIO_NAME, "notes.txt", b"plain", FILES_DIR)

    async def go():
        async with Client(srv.mcp) as c:
            await c.call_tool(
                "keep_file", {"uri": "session://files/recordings/rec-20261008-1403.mp4"}
            )
            video = await c.read_resource("session://files/rec-20261008-1403.mp4")
            text = await c.read_resource("session://files/notes.txt")
            return video, text

    video, text = asyncio.run(go())
    entry = json.loads(video[0].text)
    assert video[0].mime_type == "application/json"
    assert entry["name"] == "rec-20261008-1403.mp4" and entry["url"]
    assert entry["content_type"] == "video/mp4"
    assert entry["uri"] == "session://files/rec-20261008-1403.mp4"
    assert text[0].blob  # anything else is still its bytes
