import pytest

from kubed.selenium_flow.recordings import mounts

pytestmark = pytest.mark.unit

INFO = """\
22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw
40 22 0:50 / /data/flows rw,relatime - nfs4 nas:/volume1/flows rw
41 22 0:51 / /mnt/my\\040share rw - cifs //nas/share rw
42 22 0:52 / /data/local rw - xfs /dev/sdb1 rw
"""


@pytest.fixture
def info(tmp_path):
    path = tmp_path / "mountinfo"
    path.write_text(INFO)
    return str(path)


def test_the_longest_mount_decides(info):
    assert mounts.network_filesystem("/data/flows/recordings", info)
    assert not mounts.network_filesystem("/data/local/recordings", info)
    assert not mounts.network_filesystem("/tmp/x", info)
    assert mounts.network_filesystem("/mnt/my share/v", info)


def test_no_proc_means_events(tmp_path):
    assert not mounts.network_filesystem("/x", str(tmp_path / "absent"))


def test_watch_overrides_auto(info, monkeypatch):
    monkeypatch.setattr(mounts, "network_filesystem", lambda p, *_: True)
    assert mounts.polling("auto", "/x") is True
    assert mounts.polling("events", "/x") is False
    assert mounts.polling("poll", "/x") is True
