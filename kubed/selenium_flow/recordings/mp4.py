"""Whether a recording the Grid made is finished.

The recorder's ffmpeg writes a fragmented MP4 (``-movflags
frag_keyframe+empty_moov+default_base_moof``) and closes it with an ``mfra``
box whose last 16 bytes are an ``mfro`` box: size 16, ``mfro``, version and
flags 0, then the size of the whole ``mfra`` (FFmpeg ``movenc.c``,
``mov_write_trailer`` → ``mov_write_mfra_tag``). A file still being recorded,
or still being copied by whatever delivers it, cannot end that way. Only a
recording whose ffmpeg was killed has none — the collector handles that case.
"""

from __future__ import annotations

import os
from pathlib import Path

TRAILER = 16


def trailer(tfra: bytes = b"") -> bytes:
    """A valid ``mfra`` box around ``tfra``: what a finished recording ends with."""
    size = 8 + len(tfra) + TRAILER
    mfro = (16).to_bytes(4, "big") + b"mfro" + b"\0\0\0\0" + size.to_bytes(4, "big")
    return size.to_bytes(4, "big") + b"mfra" + tfra + mfro


def is_complete(path) -> bool:
    """True when ``path`` ends in an ``mfro`` that names a real ``mfra``."""
    try:
        with Path(path).open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size < 8 + TRAILER:
                return False
            f.seek(size - TRAILER)
            tail = f.read(TRAILER)
            if tail[:12] != (16).to_bytes(4, "big") + b"mfro" + b"\0\0\0\0":
                return False
            mfra = int.from_bytes(tail[12:], "big")
            if mfra < 8 + TRAILER or mfra > size:
                return False
            f.seek(size - mfra)
            head = f.read(8)
            return head[4:] == b"mfra" and int.from_bytes(head[:4], "big") == mfra
    except OSError:
        return False
