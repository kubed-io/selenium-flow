"""The Recordings folder on every surface: the Screenshots folder's twin."""

import asyncio
import errno
import json
import os
from pathlib import Path

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.http import files, links
from kubed.selenium_flow.http.admin import signed
from kubed.selenium_flow.names import FILES_DIR, RECORDINGS_DIR
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace.workspaces import STDIO_NAME

pytestmark = pytest.mark.unit

S = "desktop"


class Grid:
    def files(self, _sid):
        return []

    def read_file(self, _sid, _name):
        return b""


class Actions:
    grid = Grid()


class FakeWorkspaces:
    def browser(self, _name):
        return ""


@pytest.fixture
def store(tmp_path):
    s = flows.LocalFlowStore(tmp_path)
    s.write_file(S, "rec-20261008-1403.mp4", b"\x00" * 64, RECORDINGS_DIR)
    return s


def test_addresses():
    assert files.uri_of(RECORDINGS_DIR, "a.mp4") == "workspace://files/recordings/a.mp4"
    assert files.parse_uri("workspace://files/recordings/a.mp4") == (RECORDINGS_DIR, "a.mp4")
    assert "recordings" in files.RESERVED


def test_the_root_names_a_third_folder(store):
    root = files.root(Actions(), FakeWorkspaces(), store, None, S)
    named = {f["name"]: f for f in root["folders"]}
    assert named["recordings"]["count"] == 1
    assert named["recordings"]["uri"] == "workspace://files/recordings"


def test_a_recording_entry_is_a_file_entry_with_keep_with(store):
    listing = files.folder(Actions(), FakeWorkspaces(), store, None, S, RECORDINGS_DIR)
    entry = listing["files"][0]
    assert entry["content_type"] == "video/mp4" and entry["image"] is False
    assert entry["keep_with"] == 'keep_file("workspace://files/recordings/rec-20261008-1403.mp4")'
    assert entry["url"].startswith("/recordings/desktop/")


def test_keeping_a_recording_moves_it_into_files(store):
    kept = files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/recordings/rec-20261008-1403.mp4", S)
    assert kept["uri"] == "workspace://files/rec-20261008-1403.mp4"
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

    files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/recordings/rec-20261008-1403.mp4", S)
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
    assert "workspace://files/recordings" in listed
    assert "workspace://files/recordings/{name}" in templated


def test_reading_one_recording_answers_its_entry_not_the_video(srv):
    async def go():
        async with Client(srv.mcp) as c:
            return await c.read_resource("workspace://files/recordings/rec-20261008-1403.mp4")
    got = asyncio.run(go())
    entry = json.loads(got[0].text)
    assert entry["name"] == "rec-20261008-1403.mp4" and entry["url"]
    assert entry["content_type"] == "video/mp4"


def test_keep_file_moves_a_recording(srv):
    async def go():
        async with Client(srv.mcp) as c:
            return await c.call_tool("keep_file", {"uri": "workspace://files/recordings/rec-20261008-1403.mp4"})
    result = asyncio.run(go())
    assert result.structured_content["uri"] == "workspace://files/rec-20261008-1403.mp4"
    assert srv.flows.files(STDIO_NAME, RECORDINGS_DIR) == []


def test_a_kept_recording_read_from_files_answers_its_entry_not_the_video(srv):
    """The Files item resource holds the Recordings rule for any video: a model
    cannot watch one, and base64 of a whole video is all a read would carry."""
    srv.flows.write_file(STDIO_NAME, "notes.txt", b"plain", FILES_DIR)

    async def go():
        async with Client(srv.mcp) as c:
            await c.call_tool(
                "keep_file", {"uri": "workspace://files/recordings/rec-20261008-1403.mp4"}
            )
            video = await c.read_resource("workspace://files/rec-20261008-1403.mp4")
            text = await c.read_resource("workspace://files/notes.txt")
            return video, text

    video, text = asyncio.run(go())
    entry = json.loads(video[0].text)
    assert video[0].mime_type == "application/json"
    assert entry["name"] == "rec-20261008-1403.mp4" and entry["url"]
    assert entry["content_type"] == "video/mp4"
    assert entry["uri"] == "workspace://files/rec-20261008-1403.mp4"
    assert text[0].blob  # anything else is still its bytes


def _fault_on(monkeypatch, fn_name, needle, exc):
    real = getattr(os, fn_name)

    def faulty(path, *a, **k):
        if needle in str(path):
            raise exc
        return real(path, *a, **k)

    monkeypatch.setattr(os, fn_name, faulty)


def test_a_storage_fault_reading_the_recording_is_not_a_missing_recording(store, monkeypatch):
    """Path.is_file() hides this on 3.14: it must stay an OSError (a retryable
    5xx), never the caller-fixable 'no recording called'."""
    _fault_on(monkeypatch, "stat", "rec-20261008-1403.mp4", OSError(errno.EIO, "EIO"))
    with pytest.raises(OSError):
        files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/recordings/rec-20261008-1403.mp4", S)


def test_a_recording_that_is_not_there_is_still_a_no_recording_error(store):
    with pytest.raises(ValueError, match="no recording called"):
        files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/recordings/gone.mp4", S)


def test_keeping_a_recording_that_cannot_be_removed_leaves_it_where_it_was(store, monkeypatch):
    real = Path.unlink

    def unlink(self, *a, **k):
        if self.parent.name == RECORDINGS_DIR and self.name.endswith(".mp4"):
            raise PermissionError(errno.EACCES, "denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError):
        files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/recordings/rec-20261008-1403.mp4", S)
    assert store.files(S, FILES_DIR) == []
    assert [f["name"] for f in store.files(S, RECORDINGS_DIR)] == ["rec-20261008-1403.mp4"]


def _client(store):
    from fastmcp import FastMCP

    mcp = FastMCP("t")
    signed.mount(mcp, Actions(), store, None, "")
    return TestClient(mcp.http_app())


def _fds():
    return len(list(Path("/proc/self/fd").iterdir()))


def _before_send(monkeypatch, action):
    """Run ``action`` after the handler validated the file, before bytes stream."""
    real = signed._pinned

    def pinned(resolved):
        found = real(resolved)
        action()
        return found

    monkeypatch.setattr(signed, "_pinned", pinned)


def test_a_file_unlinked_after_the_check_is_still_served_whole(store, tmp_path, monkeypatch):
    target = store.file_path(S, "rec-20261008-1403.mp4", RECORDINGS_DIR)
    target.write_bytes(b"A" * 64)
    _before_send(monkeypatch, target.unlink)
    resp = _client(store).get(links.recording_path(S, "rec-20261008-1403.mp4"))
    assert resp.status_code == 200
    assert resp.content == b"A" * 64
    assert resp.headers["content-length"] == "64"


def test_a_file_replaced_after_the_check_serves_the_original(store, monkeypatch):
    target = store.file_path(S, "rec-20261008-1403.mp4", RECORDINGS_DIR)
    target.write_bytes(b"A" * 64)

    def swap():
        target.unlink()
        target.write_bytes(b"B" * 500)

    _before_send(monkeypatch, swap)
    resp = _client(store).get(
        links.recording_path(S, "rec-20261008-1403.mp4"), headers={"Range": "bytes=4-9"}
    )
    assert resp.status_code == 206
    assert resp.content == b"A" * 6
    assert resp.headers["content-range"] == "bytes 4-9/64"


def test_streaming_leaks_no_descriptor(store):
    client = _client(store)
    good = links.recording_path(S, "rec-20261008-1403.mp4")
    client.get(good)  # warm anything lazily opened
    before = _fds()
    for _ in range(5):
        assert client.get(good).status_code == 200
        assert client.get(good, headers={"Range": "bytes=0-9"}).status_code == 206
        assert client.get(good, headers={"Range": "bytes=0-1,4-5"}).status_code == 206
        assert client.get(links.recording_path(S, "nope.mp4")).status_code == 404
        assert client.get(good, headers={"Range": "bytes=900-"}).status_code == 416
    assert _fds() == before
