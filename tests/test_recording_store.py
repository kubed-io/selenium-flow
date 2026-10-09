"""Recordings and notes in a workspace's folder of the data directory."""

import errno
import logging
import os
import shutil
from pathlib import Path

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
    return flows.LocalFlowStore(tmp_path / "workspaces")


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


def test_a_broken_note_is_skipped_not_fatal(store, caplog):
    store.write_note("bot", GID, {"opened": 1})
    path = store.root / "bot" / RECORDINGS_DIR / ".pending" / f"{GID}.json"
    path.write_text("{not json")
    with caplog.at_level(logging.WARNING):
        assert store.notes() == []
    # The operator's log names the workspace, never the Grid's id.
    assert "bot" in caplog.text and GID not in caplog.text


def test_grid_ids_are_validated_before_they_become_paths():
    assert valid_grid_id(GID) == GID
    assert valid_grid_id("bf532044-b55e-4c1a-9f0e-1234567890ab")
    for bad in ("../x", "a/b", "", "x" * 80, ".hidden"):
        with pytest.raises(InvalidName):
            valid_grid_id(bad)


def test_candidates_are_the_browser_naming_rule():
    it = candidates("a.mp4")
    assert [next(it) for _ in range(3)] == ["a.mp4", "a (1).mp4", "a (2).mp4"]


def test_a_copy_that_fails_midway_leaves_no_partial_file(store, tmp_path, monkeypatch):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"data")
    monkeypatch.setattr(os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError(18, "EXDEV")))
    monkeypatch.setattr(shutil, "copyfileobj", lambda *_a, **_k: (_ for _ in ()).throw(OSError(28, "ENOSPC")))
    with pytest.raises(OSError):
        store.move_in("bot", src, "rec.mp4", RECORDINGS_DIR)
    folder = store.root / "bot" / RECORDINGS_DIR
    assert [p for p in folder.iterdir() if p.name != ".pending"] == []
    assert src.read_bytes() == b"data"


def test_a_link_failure_that_is_not_about_links_raises(store, tmp_path, monkeypatch):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"data")
    monkeypatch.setattr(os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError(errno.EIO, "EIO")))
    with pytest.raises(OSError):
        store.move_in("bot", src, "rec.mp4", RECORDINGS_DIR)
    assert src.read_bytes() == b"data"


def test_a_symlinked_note_is_skipped(store, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": 1}')
    pending = store.root / "bot" / RECORDINGS_DIR / ".pending"
    pending.mkdir(parents=True)
    (pending / f"{GID}.json").symlink_to(outside)
    assert store.notes() == []


def test_the_copy_path_keeps_the_sources_mtime(store, tmp_path, monkeypatch):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"data")
    os.utime(src, (1_000_000_000, 1_000_000_000))
    real = os.link
    calls = []

    def link(a, b, *args, **kw):
        if not calls:
            calls.append(1)
            raise OSError(errno.EXDEV, "EXDEV")
        return real(a, b, *args, **kw)

    monkeypatch.setattr(os, "link", link)
    landed = store.move_in("bot", src, "rec.mp4", RECORDINGS_DIR)
    path = store.file_path("bot", landed["name"], RECORDINGS_DIR)
    assert int(path.stat().st_mtime) == 1_000_000_000


def test_a_racing_keep_that_loses_the_source_leaves_no_copy(store, tmp_path, monkeypatch):
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    src = inbox / "a.mp4"
    src.write_bytes(b"v")
    real_link = os.link

    def link_then_lose_the_source(a, b):
        real_link(a, b)
        Path(a).unlink()  # the winning keep removes it between our claim and unlink

    monkeypatch.setattr(os, "link", link_then_lose_the_source)
    with pytest.raises(FileNotFoundError):
        store.move_in("bot", src, "a.mp4", FILES_DIR)
    assert store.files("bot", FILES_DIR) == []


def test_a_stranded_staging_file_is_skipped_without_a_warning(store, caplog):
    """A copy cut off by a crash: listed on every broadcast tick, so a warning
    for it would repeat for ever."""
    folder = store.root / "bot" / RECORDINGS_DIR
    folder.mkdir(parents=True)
    (folder / f".rec.mp4.{'a' * 32}.tmp").write_bytes(b"part")
    (folder / "rec.mp4").write_bytes(b"whole")
    with caplog.at_level(logging.WARNING):
        names = [f["name"] for f in store.files("bot", RECORDINGS_DIR)]
    assert names == ["rec.mp4"]
    assert ".tmp" not in caplog.text


def test_a_note_that_cannot_be_read_is_a_fault_not_a_broken_note(store, monkeypatch):
    """A storage error is not a malformed note: it reaches the caller, which
    retries, instead of the note being skipped until the next restart."""
    from pathlib import Path

    store.write_note("bot", GID, {"opened": 1})
    real = Path.read_text

    def failing(self, *a, **kw):
        if self.suffix == ".json":
            raise OSError(5, "Input/output error")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", failing)
    with pytest.raises(OSError):
        store.notes()


def test_a_workspace_that_cannot_be_read_is_reported_and_the_rest_are_listed(store, monkeypatch):
    """A stat that fails on one workspace folder is that workspace's fault, never
    an empty workspace (Python 3.14's `is_dir` reads any OSError as False)."""
    other = "0123456789abcdef0123456789abcdef"
    store.write_note("good", GID, {"opened": 1})
    store.write_note("bad", other, {"opened": 2})
    bad = str(store.root / "bad")

    def failing(real):
        def stat(path, *a, **kw):
            if os.fspath(path) == bad:
                raise OSError(errno.EIO, "Input/output error", bad)
            return real(path, *a, **kw)
        return stat

    # Both: Python 3.14's `Path.lstat` is `os.lstat`, 3.13's is `os.stat`.
    for name in ("stat", "lstat"):
        monkeypatch.setattr(os, name, failing(getattr(os, name)))
    failed = []
    assert store.notes(on_error=lambda s, e: failed.append((s, type(e)))) == [
        ("good", GID, {"opened": 1})
    ]
    assert failed == [("bad", OSError)]
    with pytest.raises(OSError):
        store.notes()


def test_a_tolerant_move_leaves_an_original_that_cannot_be_removed(store, tmp_path, monkeypatch):
    """The collector's filing: the copy stands, the inbox original stays."""
    src = tmp_path / "inbox.mp4"
    src.write_bytes(b"v")
    real = Path.unlink
    monkeypatch.setattr(
        Path, "unlink",
        lambda self, *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied"))
        if self == src else real(self, *a, **k),
    )
    store.move_in("bot", src, "a.mp4", RECORDINGS_DIR)
    assert src.exists() and len(store.files("bot", RECORDINGS_DIR)) == 1


def test_a_strict_move_unclaims_the_copy_when_the_original_cannot_be_removed(store, tmp_path, monkeypatch):
    src = tmp_path / "inbox.mp4"
    src.write_bytes(b"v")
    real = Path.unlink
    monkeypatch.setattr(
        Path, "unlink",
        lambda self, *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied"))
        if self == src else real(self, *a, **k),
    )
    with pytest.raises(PermissionError):
        store.move_in("bot", src, "a.mp4", FILES_DIR, strict=True)
    assert src.exists() and store.files("bot", FILES_DIR) == []


def test_move_in_refuses_a_symlink_and_a_non_file(store, tmp_path):
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"private")
    link = tmp_path / "link.mp4"
    link.symlink_to(secret)
    with pytest.raises(OSError):
        store.move_in("bot", link, "rec.mp4", RECORDINGS_DIR)
    with pytest.raises(OSError):
        store.move_in("bot", tmp_path, "rec.mp4", RECORDINGS_DIR)
    assert secret.read_bytes() == b"private" and link.is_symlink()
    assert store.files("bot", RECORDINGS_DIR) == []


@pytest.mark.parametrize("copy_path", [True, False])
def test_move_in_files_the_inode_it_checked_not_a_link_swapped_in(
    store, tmp_path, monkeypatch, copy_path
):
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    outside = tmp_path / "token"
    outside.write_bytes(b"AUTH_TOKEN")
    src = inbox / f"bot_{GID}.mp4"
    src.write_bytes(b"real")
    real_link = os.link

    def swap_then(*a, **k):
        src.unlink()
        src.symlink_to(outside)
        if copy_path:
            raise OSError(18, "EXDEV")
        return real_link(*a, **k)

    monkeypatch.setattr(os, "link", swap_then)
    if copy_path:
        landed = store.move_in("bot", src, "rec.mp4", RECORDINGS_DIR)
        assert store.read_file("bot", landed["name"], RECORDINGS_DIR) == b"real"
    else:
        with pytest.raises(OSError):
            store.move_in("bot", src, "rec.mp4", RECORDINGS_DIR)
        assert store.files("bot", RECORDINGS_DIR) == []
    assert outside.read_bytes() == b"AUTH_TOKEN"
    assert all(b"AUTH_TOKEN" not in store.read_file("bot", f["name"], RECORDINGS_DIR)
               for f in store.files("bot", RECORDINGS_DIR))


@pytest.mark.parametrize("copy_path", [True, False])
@pytest.mark.parametrize("strict", [True, False])
def test_a_source_replaced_after_the_pin_is_filed_but_never_unlinked(
    store, tmp_path, monkeypatch, copy_path, strict
):
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    src = inbox / f"bot_{GID}.mp4"
    src.write_bytes(b"original")
    real_link = os.link

    def replace_then(a, b, *args, **k):
        if copy_path:  # no link: the copy is taken from the pinned fd
            src.unlink()
            src.write_bytes(b"newer")
            raise OSError(18, "EXDEV")
        real_link(a, b, *args, **k)  # filed the original inode, then it is replaced
        src.unlink()
        src.write_bytes(b"newer")

    monkeypatch.setattr(os, "link", replace_then)
    landed = store.move_in("bot", src, "rec.mp4", RECORDINGS_DIR, strict=strict)
    assert store.read_file("bot", landed["name"], RECORDINGS_DIR) == b"original"
    assert src.read_bytes() == b"newer"
