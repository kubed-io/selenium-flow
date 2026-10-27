"""The bytes of the live view: VNC auth, both RFB handshakes, and the gate.

WebSocket frames are not aligned with RFB messages on either side, so the
handshakes read from `Bytes`, a buffer that hands back exactly the count asked
for. The proxy answers both handshakes itself (spec 2026-10-27, rulings 11, 23
and 25): upstream as a 3.8 client of the Grid's VNC server, downstream as a 3.8
server offering security type None to the viewer.

**The gate is an allowlist** (rulings 16, 21, 22 and 24). The viewer's stream is
parsed into whole client messages, because that is the only way to tell input
from the framebuffer requests noVNC sends continuously. Watch forwards only the
messages that ask for pictures; an unknown type closes the tunnel, since the
parser could no longer find message boundaries and guessing would forward input
in Watch. This module imports no selenium and no protocol library.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

Mode = Literal["watch", "control"]

RFB_38 = b"RFB 003.008\n"
NONE, VNC_AUTH = 1, 2
# noVNC 1.7.0, core/encodings.js: pseudoEncodingExtendedMouseButtons: -316
# (confirmed against the v1.7.0 tag).
EXTENDED_MOUSE_BUTTONS = -316
MAX_CLIENT_MESSAGE = 2**20

# What the tunnel sends upstream for the viewer's ClientInit (ruling 23): share.
SHARED = b"\x01"

NO_PASSWORD = "this Grid asks for a VNC password and live.password_file is not set"
UNREADABLE = "the VNC password file cannot be read"


class Refused(Exception):
    """The tunnel closes with this code and reason."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def read_password(path: str | None) -> bytes | None:
    """The password file's bytes, stripped; None when no file is configured."""
    if path is None:
        return None
    try:
        return Path(path).read_bytes().strip()
    except OSError:
        raise Refused(4003, UNREADABLE) from None


def reverse_bits(key: bytes) -> bytes:
    """Each byte's bits reversed: VNC's quirk, applied to the DES key."""
    return bytes(int(f"{byte:08b}"[::-1], 2) for byte in key)


def vnc_response(challenge: bytes, password: bytes) -> bytes:
    """The 16-byte challenge encrypted DES-ECB under the password's first 8 bytes."""
    key = reverse_bits(password.ljust(8, b"\0")[:8])
    # TripleDES with the key repeated three times is single DES.
    encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return encryptor.update(challenge) + encryptor.finalize()


class Bytes:
    """Frames in, exact reads out."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._exc: BaseException | None = None
        self._changed = asyncio.Event()

    def feed(self, data: bytes) -> None:
        self._buffer += data
        self._changed.set()

    async def read(self, n: int) -> bytes:
        """Wait for n bytes; raises what `close` was given once the buffer is short."""
        while len(self._buffer) < n:
            if self._exc is not None:
                raise self._exc
            self._changed.clear()
            await self._changed.wait()
        out = bytes(self._buffer[:n])
        del self._buffer[:n]
        return out

    def close(self, exc: BaseException) -> None:
        self._exc = exc
        self._changed.set()

    def take(self) -> bytes:
        """What is buffered, emptied."""
        out = bytes(self._buffer)
        self._buffer.clear()
        return out


Read = Callable[[int], Awaitable[bytes]]
Write = Callable[[bytes], Awaitable[None]]


def _version(raw: bytes) -> str:
    return raw.decode("ascii", "replace").strip()


async def server_handshake(read: Read, write: Write, password: bytes | None) -> None:
    """Be a 3.8 client of the Grid's VNC server, up to and including SecurityResult."""
    version = await read(12)
    if version != RFB_38:
        raise Refused(
            4003, f"the Grid's VNC server speaks {_version(version)}; 3.8 is required"
        )
    await write(RFB_38)
    count = (await read(1))[0]
    if count == 0:
        size = struct.unpack(">I", await read(4))[0]
        reason = (await read(size)).decode("utf-8", "replace")
        raise Refused(4003, reason)
    types = set(await read(count))
    if VNC_AUTH in types and password is not None:
        await write(bytes([VNC_AUTH]))
        await write(vnc_response(await read(16), password))
    elif NONE in types:
        await write(bytes([NONE]))
    elif VNC_AUTH in types:
        raise Refused(4003, NO_PASSWORD)
    else:
        raise Refused(
            4003,
            f"the Grid's VNC server offers security types {sorted(types)}; "
            "selenium-flow speaks 1 (None) and 2 (VNC auth)",
        )
    if struct.unpack(">I", await read(4))[0] != 0:
        size = struct.unpack(">I", await read(4))[0]
        reason = (await read(size)).decode("utf-8", "replace")
        raise Refused(4003, reason or "the Grid's VNC server refused the password")


async def viewer_handshake(read: Read, write: Write) -> None:
    """Be a 3.8 server to the viewer, offering None; ends having read ClientInit."""
    await write(RFB_38)
    version = await read(12)
    if version != RFB_38:
        raise Refused(4003, f"the viewer speaks {_version(version)}; 3.8 is required")
    await write(bytes([1, NONE]))
    chosen = (await read(1))[0]
    if chosen != NONE:
        raise Refused(
            4003, f"the viewer chose security type {chosen}; only 1 (None) is offered"
        )
    await write(bytes(4))
    await read(1)


@dataclass(frozen=True)
class Passed:
    forward: bytes  # whole messages to send upstream, in order
    input: bool  # an input message was among them


PICTURE, INPUT, NEVER = "picture", "input", "never"


class Gate:
    """Parses the viewer's stream into whole messages and decides each one."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes, mode: Mode) -> Passed:
        self._buffer += data
        out = bytearray()
        saw_input = False
        while self._buffer:
            kind, size = self._classify()
            if size is None or len(self._buffer) < size:
                break
            message = bytes(self._buffer[:size])
            del self._buffer[:size]
            if kind == PICTURE:
                out += self._without_extended_mouse(message)
            elif kind == INPUT and mode == "control":
                out += message
                saw_input = True
        return Passed(bytes(out), saw_input)

    @staticmethod
    def _without_extended_mouse(message: bytes) -> bytes:
        if message[0] != 2:
            return message
        count = struct.unpack(">H", message[2:4])[0]
        kept = [
            message[4 + 4 * i : 8 + 4 * i]
            for i in range(count)
            if struct.unpack(">i", message[4 + 4 * i : 8 + 4 * i])[0]
            != EXTENDED_MOUSE_BUTTONS
        ]
        return message[:2] + struct.pack(">H", len(kept)) + b"".join(kept)

    def _classify(self) -> tuple[str, int | None]:
        """The kind and whole length of the message at the front of the buffer.

        The length is None while its header is not yet whole. A declared length
        is checked the moment it can be read, before any body is buffered.
        """
        buf = self._buffer
        kind = buf[0]
        if kind == 0:
            return PICTURE, 20
        if kind in (3, 150):
            return PICTURE, 10
        if kind == 4:
            return INPUT, 8
        if kind == 5:
            return INPUT, 6
        if kind == 250:
            return NEVER, 4
        if kind == 2:
            if len(buf) < 4:
                return PICTURE, None
            return PICTURE, 4 + 4 * struct.unpack(">H", buf[2:4])[0]
        if kind == 248:
            if len(buf) < 9:
                return PICTURE, None
            return PICTURE, 9 + buf[8]
        if kind == 6:
            if len(buf) < 8:
                return INPUT, None
            size = 8 + abs(struct.unpack(">i", buf[4:8])[0])
            if size > MAX_CLIENT_MESSAGE:
                raise Refused(4007, "VNC message too large")
            return INPUT, size
        if kind == 251:
            if len(buf) < 7:
                return NEVER, None
            return NEVER, 8 + 16 * buf[6]
        if kind == 255:
            if len(buf) < 2:
                return INPUT, None
            if buf[1] == 0:
                return INPUT, 12
        raise Refused(4005, "unknown VNC message")
