"""Recordings and notes in the session store."""

import os

import pytest

from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.names import (
    FILES_DIR,
    RECORDINGS_DIR,
    InvalidName,
    candidates,
    valid_grid_id,
)

pytestmark = pytest.mark.unit

GID = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path / "sessions")


def test_recordings_is_a_folder(store):
    store.write_file("bot", "a.mp4", b"x", RECORDINGS_DIR)
    assert [f["name"] for f in store.files("bot", RECORDINGS_DIR)] == ["a.mp4"]


def test_move_in_takes_a_file_from_outside_and_never_overwrites(store, tmp_path):
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    (inbox / f"bot_{GID}.mp4").write_bytes(b"one")
    first = store.move_in("bot", inbox / f"bot_{GID}.mp4", "rec-20261008-1403.mp4", RECORDINGS_DIR)
    (inbox / "x.mp4").write_bytes(b"two")
    second = store.move_in("bot", inbox / "x.mp4", "rec-20261008-1403.mp4", RECORDINGS_DIR)
    assert first["name"] == "rec-20261008-1403.mp4"
    assert second["name"] == "rec-20261008-1403 (1).mp4"
    assert not (inbox / f"bot_{GID}.mp4").exists() and not (inbox / "x.mp4").exists()
    assert store.read_file("bot", second["name"], RECORDINGS_DIR) == b"two"


def test_move_in_copies_when_a_link_is_impossible(store, tmp_path, monkeypatch):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"data")
    monkeypatch.setattr(os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError(18, "EXDEV")))
    landed = store.move_in("bot", src, "rec.mp4", RECORDINGS_DIR)
    assert store.read_file("bot", landed["name"], RECORDINGS_DIR) == b"data"
    assert not src.exists()


def test_moving_a_recording_into_files_skips_the_reserved_names(store, tmp_path):
    src = tmp_path / "recordings"
    src.write_bytes(b"v")
    landed = store.move_in("bot", src, "recordings", FILES_DIR)
    assert landed["name"] == "recordings (1)"


def test_notes_are_written_listed_and_deleted_and_never_listed_as_files(store):
    store.write_note("bot", GID, {"opened": 1, "ended": None, "browser": "chrome"})
    assert store.notes() == [("bot", GID, {"opened": 1, "ended": None, "browser": "chrome"})]
    assert store.files("bot", RECORDINGS_DIR) == []
    assert store.delete_note("bot", GID) is True
    assert store.notes() == []
    assert store.delete_note("bot", GID) is False


def test_a_broken_note_is_skipped_not_fatal(store):
    store.write_note("bot", GID, {"opened": 1})
    path = store.root / "bot" / RECORDINGS_DIR / ".pending" / f"{GID}.json"
    path.write_text("{not json")
    assert store.notes() == []


def test_grid_ids_are_validated_before_they_become_paths():
    assert valid_grid_id(GID) == GID
    assert valid_grid_id("bf532044-b55e-4c1a-9f0e-1234567890ab")
    for bad in ("../x", "a/b", "", "x" * 80, ".hidden"):
        with pytest.raises(InvalidName):
            valid_grid_id(bad)


def test_candidates_are_the_browser_naming_rule():
    it = candidates("a.mp4")
    assert [next(it) for _ in range(3)] == ["a.mp4", "a (1).mp4", "a (2).mp4"]
