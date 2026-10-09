"""A finished recording ends in an mfro box (ffmpeg movenc.c, mov_write_mfra_tag)."""

import pytest

from kubed.selenium_flow.recordings import mp4

pytestmark = pytest.mark.unit

BODY = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16 + b"\x00\x00\x00\x10moof" + b"\x00" * 200


def write(tmp_path, data):
    path = tmp_path / "v.mp4"
    path.write_bytes(data)
    return path


def test_a_file_that_ends_in_a_valid_mfra_is_complete(tmp_path):
    assert mp4.is_complete(write(tmp_path, BODY + mp4.trailer()))


def test_a_trailer_with_tfra_entries_is_complete(tmp_path):
    assert mp4.is_complete(write(tmp_path, BODY + mp4.trailer(b"\x00" * 40)))


def test_a_file_still_growing_is_not(tmp_path):
    assert not mp4.is_complete(write(tmp_path, BODY))


def test_a_copy_cut_inside_the_trailer_is_not(tmp_path):
    assert not mp4.is_complete(write(tmp_path, (BODY + mp4.trailer())[:-5]))


def test_an_mfro_naming_the_wrong_size_is_not(tmp_path):
    bad = bytearray(BODY + mp4.trailer())
    bad[-1] ^= 0x01
    assert not mp4.is_complete(write(tmp_path, bytes(bad)))


def test_a_tiny_or_missing_file_is_not(tmp_path):
    assert not mp4.is_complete(write(tmp_path, b"\x00" * 10))
    assert not mp4.is_complete(tmp_path / "absent.mp4")


def test_a_read_fault_is_not_an_unfinished_file(tmp_path, monkeypatch):
    import errno
    from pathlib import Path

    path = write(tmp_path, BODY + mp4.trailer())

    def eio(self, *a, **k):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(Path, "open", eio)
    with pytest.raises(OSError):
        mp4.is_complete(path)


def test_a_file_that_vanished_is_not_complete(tmp_path):
    assert mp4.is_complete(tmp_path / "gone.mp4") is False
