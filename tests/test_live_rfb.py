"""The live view's bytes: VNC auth, both handshakes and the client-message gate."""

import asyncio

import pytest

from kubed.selenium_flow.live.rfb import (
    EXTENDED_MOUSE_BUTTONS,
    NO_PASSWORD,
    RFB_38,
    UNREADABLE,
    Bytes,
    Gate,
    Passed,
    Refused,
    read_password,
    reverse_bits,
    server_handshake,
    viewer_handshake,
    vnc_response,
)

pytestmark = pytest.mark.unit


def script(*chunks: bytes):
    """A `read` fed one chunk per frame, so reads split across frames are exercised."""
    pending, buffer = list(chunks), Bytes()

    async def read(n: int) -> bytes:
        while True:
            try:
                return await asyncio.wait_for(buffer.read(n), 0.01)
            except TimeoutError:
                if not pending:
                    raise EOFError("the script ran out") from None
                buffer.feed(pending.pop(0))

    return read


class Sent:
    """The `write` callable; `.data` is everything written."""

    def __init__(self) -> None:
        self.data = b""

    async def __call__(self, data: bytes) -> None:
        self.data += data


async def test_bytes_waits_for_the_count_and_raises_what_close_was_given():
    buffer = Bytes()
    task = asyncio.ensure_future(buffer.read(4))
    buffer.feed(b"ab")
    await asyncio.sleep(0)
    assert not task.done()
    buffer.feed(b"cdef")
    assert await task == b"abcd" and buffer.take() == b"ef" and buffer.take() == b""
    buffer.close(ConnectionError("gone"))
    with pytest.raises(ConnectionError):
        await buffer.read(1)


def test_reverse_bits_gives_the_known_vnc_fixed_key():
    assert reverse_bits(bytes([23, 82, 107, 6, 35, 78, 88, 7])) == bytes.fromhex("e84ad660c4721ae0")

def test_vnc_auth_against_the_vncpasswd_vector():
    # vncpasswd encrypts "password" with the d3des key above to dbd83cfd727a1458;
    # if this fails, check the vector before the code.
    assert vnc_response(b"password" * 2, bytes([23, 82, 107, 6, 35, 78, 88, 7])) == bytes.fromhex("dbd83cfd727a1458") * 2

def test_a_long_password_uses_its_first_eight_bytes():
    assert vnc_response(bytes(16), b"12345678tail") == vnc_response(bytes(16), b"12345678")

def test_the_password_file_is_stripped_bytes_and_unreadable_is_refused(tmp_path):
    (tmp_path / "p").write_bytes(b"secret\n")
    assert read_password(str(tmp_path / "p")) == b"secret" and read_password(None) is None
    with pytest.raises(Refused, match=UNREADABLE):
        read_password(str(tmp_path / "missing"))

async def test_vnc_auth_upstream_split_across_frames():
    sent = Sent()
    await server_handshake(script(b"RFB 003", b".008\n", b"\x01", b"\x02", bytes(10), bytes(6), b"\x00\x00\x00\x00"),
                           sent, b"secret")
    assert sent.data == RFB_38 + b"\x02" + vnc_response(bytes(16), b"secret")

async def test_none_only_upstream():                                  # SE_VNC_NO_PASSWORD
    sent = Sent()
    await server_handshake(script(RFB_38, b"\x01\x01", bytes(4)), sent, None)
    assert sent.data == RFB_38 + b"\x01"

@pytest.mark.parametrize("server, password, reason", [
    (b"RFB 003.003\n", None, "the Grid's VNC server speaks RFB 003.003; 3.8 is required"),
    (RFB_38 + b"\x01\x02", None, NO_PASSWORD),
    (RFB_38 + b"\x00" + (8).to_bytes(4, "big") + b"too many", None, "too many"),
    (RFB_38 + b"\x01\x10", None, "offers security types [16]"),
    (RFB_38 + b"\x01\x02" + bytes(16) + b"\x00\x00\x00\x01" + (5).to_bytes(4, "big") + b"nope!", b"x", "nope!"),
])
async def test_an_upstream_handshake_failure_is_refused_with_the_node_s_words(server, password, reason):
    with pytest.raises(Refused) as caught:
        await server_handshake(script(server), Sent(), password)
    assert caught.value.code == 4003 and reason in caught.value.reason

async def test_the_viewer_handshake_offers_none_and_reads_client_init():
    sent = Sent()
    await viewer_handshake(script(RFB_38, b"\x01", b"\x00"), sent)
    assert sent.data == RFB_38 + b"\x01\x01" + bytes(4)

@pytest.mark.parametrize("viewer", [b"RFB 003.003\n\x01\x00", RFB_38 + b"\x02\x00"])
async def test_a_viewer_that_is_not_3_8_none_is_refused(viewer):
    with pytest.raises(Refused) as caught:
        await viewer_handshake(script(viewer), Sent())
    assert caught.value.code == 4003

KEY = bytes([4, 1, 0, 0]) + (0x61).to_bytes(4, "big")
POINTER = bytes([5, 1]) + (10).to_bytes(2, "big") + (20).to_bytes(2, "big")
CUT = bytes([6, 0, 0, 0]) + (3).to_bytes(4, "big") + b"abc"
EXT_CUT = bytes([6, 0, 0, 0]) + (-4).to_bytes(4, "big", signed=True) + b"wxyz"
QEMU_KEY = bytes([255, 0]) + bytes(10)
UPDATE = bytes([3, 1]) + bytes(8)
PIXEL = bytes([0]) + bytes(19)
CONTINUOUS = bytes([150, 1]) + bytes(8)
FENCE = bytes([248, 0, 0, 0]) + bytes(4) + bytes([2]) + b"ab"
XVP = bytes([250, 0, 1, 2])
DESKTOP = bytes([251, 0]) + bytes(4) + bytes([1, 0]) + bytes(16)

@pytest.mark.parametrize("msg", [PIXEL, UPDATE, CONTINUOUS, FENCE])
@pytest.mark.parametrize("mode", ["watch", "control"])
def test_picture_messages_pass_in_both_modes(msg, mode):
    assert Gate().feed(msg, mode) == Passed(msg, False)

@pytest.mark.parametrize("msg", [KEY, POINTER, CUT, EXT_CUT, QEMU_KEY])
def test_input_is_dropped_in_watch_and_forwarded_in_control(msg):
    assert Gate().feed(msg, "watch") == Passed(b"", False)
    assert Gate().feed(msg, "control") == Passed(msg, True)

@pytest.mark.parametrize("msg", [XVP, DESKTOP])
@pytest.mark.parametrize("mode", ["watch", "control"])
def test_xvp_and_set_desktop_size_never_pass(msg, mode):
    assert Gate().feed(msg, mode) == Passed(b"", False)

def test_set_encodings_loses_extended_mouse_buttons():
    encodings = [7, EXTENDED_MOUSE_BUTTONS, -223]
    msg = bytes([2, 0]) + len(encodings).to_bytes(2, "big") + b"".join(e.to_bytes(4, "big", signed=True) for e in encodings)
    out = Gate().feed(msg, "watch").forward
    assert out == bytes([2, 0, 0, 2]) + (7).to_bytes(4, "big", signed=True) + (-223).to_bytes(4, "big", signed=True)

def test_a_top_bit_pointer_cannot_smuggle_a_resize():                # Review Focus 1
    crafted = bytes([5, 0x80]) + bytes(4)
    assert Gate().feed(crafted + DESKTOP + KEY, "control") == Passed(crafted + KEY, True)

def test_messages_split_and_coalesced_across_frames():
    stream = UPDATE + KEY + CUT + UPDATE
    gate, out = Gate(), b""
    for i in range(len(stream)):
        out += gate.feed(stream[i:i + 1], "watch").forward
    assert out == UPDATE + UPDATE

def test_a_mode_switch_applies_from_the_next_whole_message():
    gate = Gate()
    assert gate.feed(KEY[:3], "watch").forward == b""
    assert gate.feed(KEY[3:] + KEY, "control").forward == KEY + KEY

@pytest.mark.parametrize("msg", [bytes([7]), bytes([253, 0, 0, 4]), bytes([255, 1, 0, 0])])
def test_anything_else_closes_4005(msg):
    with pytest.raises(Refused) as caught:
        Gate().feed(msg, "control")
    assert (caught.value.code, caught.value.reason) == (4005, "unknown VNC message")

@pytest.mark.parametrize("mode", ["watch", "control"])
def test_a_two_gigabyte_cut_closes_4007_from_its_header(mode):    # Review Focus 3
    with pytest.raises(Refused) as caught:
        Gate().feed(bytes([6, 0, 0, 0]) + (2**31 - 1).to_bytes(4, "big"), mode)
    assert caught.value.code == 4007

def test_watch_forwards_no_input_byte_whatever_the_page_sends():
    noise = (KEY + POINTER + CUT + QEMU_KEY + UPDATE) * 50
    out = Gate().feed(noise, "watch").forward
    assert out == UPDATE * 50
