"""Whether the inbox is on a network filesystem, where events see nothing.

inotify reports only changes made through this machine's own mount: a file an
NFS, SMB or FUSE peer writes raises no event (recordings spec, research). So
``auto`` polls there and uses events everywhere else, including where there is
no ``/proc`` to ask.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

NETWORK = frozenset({"nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "ceph", "glusterfs"})

# The kernel writes space, tab, newline and backslash in a mount point as
# three-digit octal escapes (\040, \011, \012, \134).
_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescaped(point: str) -> str:
    return _ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), point)


def network_filesystem(path, mountinfo: str = "/proc/self/mountinfo") -> bool:
    """True when the mount holding ``path`` is a network or FUSE filesystem."""
    try:
        lines = Path(mountinfo).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    target = os.path.realpath(path)
    best_point, best_type = "", ""
    for line in lines:
        left, sep, right = line.partition(" - ")
        fields = left.split()
        if not sep or len(fields) < 5 or not right.split():
            continue
        point = _unescaped(fields[4])
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and len(point) >= len(best_point):
            best_point, best_type = point, right.split()[0]
    return best_type in NETWORK or best_type.startswith("fuse")


def polling(watch: str, path) -> bool:
    """Whether to poll the inbox, from ``recording.watch``."""
    if watch == "poll":
        return True
    if watch == "events":
        return False
    return network_filesystem(path)
