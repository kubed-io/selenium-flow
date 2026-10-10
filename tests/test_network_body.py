"""`network_body`: one response body, fetched from the browser that made the
request and only from it (spec §7, ruling 11; plan rulings 1, 16, 19)."""

import base64

import pytest

from kubed.selenium_flow import errors
from kubed.selenium_flow.capture import MASK
from kubed.selenium_flow.capture.store import Capture
from kubed.selenium_flow.core.actions import BODY_CAP, Actions
from kubed.selenium_flow.faults import NotFound

from .capture_events import done, nav_started, req
from .fakes import FakeChannel, FakeGrid
from .site_data_fakes import channels_for

pytestmark = pytest.mark.unit


class Fed:
    def __init__(self, capture, channel):
        self.capture, self.channel = capture, channel


@pytest.fixture
def fed():
    capture, channel = Capture(), FakeChannel()
    capture.attached("ws", "s1", "chrome", "top-1", again=False)
    for method, params in (
        nav_started("https://a.test/"),
        req("r1", "https://a.test/api", initiator="fetch"),
        done("r1", 500, mime="application/json"),
    ):
        capture.ingest("ws", "s1", method, params)
    return Fed(capture, channel)


@pytest.fixture
def actions(fed):
    actions = Actions(FakeGrid())
    actions.capture = fed.capture
    actions.channels = channels_for(fed.channel)
    return actions


def body_of(value, kind="string"):
    return {"bytes": {"type": kind, "value": value}}


def test_text_comes_back_whole_under_the_cap(actions, fed):
    fed.channel.bodies["r1"] = body_of('{"error":"insert failed"}')
    assert actions.network_body("s1", "1.1") == {
        "id": "1.1", "uri": "workspace://network/1.1", "mime": "application/json",
        "size": 25, "binary": False, "text": '{"error":"insert failed"}', "truncated": False}
    assert fed.channel.params("network.getData") == {"dataType": "response", "request": "r1"}


def test_text_over_100_kb_is_cut_on_a_character_and_marked(actions, fed):
    fed.channel.bodies["r1"] = body_of("é" * 60_000)            # 120 000 bytes
    got = actions.network_body("s1", "1.1")
    assert got["truncated"] is True and len(got["text"].encode()) <= BODY_CAP
    assert got["text"] == "é" * 50_000
    assert got["size"] == 120_000


def test_binary_returns_its_type_and_size_only(actions, fed):
    fed.channel.bodies["r1"] = body_of(base64.b64encode(b"\x89PNG" * 10).decode(), "base64")
    got = actions.network_body("s1", "1.1")
    assert got["binary"] is True and got["text"] is None and got["size"] == 40


def test_an_unknown_id_is_400(actions, fed):
    with pytest.raises(ValueError, match="workspace://network lists them"):
        actions.network_body("s1", "9.9")
    assert errors.status_for(ValueError()) == 400
    assert fed.channel.log == []


@pytest.mark.parametrize("bad", ["", "x" * 65, 7, None])
def test_an_id_that_is_not_a_short_string_is_400(actions, fed, bad):
    with pytest.raises(ValueError, match=r"workspace://network"):
        actions.network_body("s1", bad)
    assert fed.channel.log == []


def test_without_capture_there_is_nothing_to_find(fed):
    actions = Actions(FakeGrid())
    actions.channels = channels_for(fed.channel)
    with pytest.raises(ValueError, match="workspace://network lists them"):
        actions.network_body("s1", "1.1")


def test_a_body_from_an_ended_browser_is_404(actions, fed):
    fed.capture.attached("ws", "s2", "chrome", "top-1", again=False)     # the reopened browser
    with pytest.raises(NotFound, match=r"request 1\.1 has ended") as raised:
        actions.network_body("s2", "1.1")
    assert errors.status_for(raised.value) == 404
    assert "s1" not in str(raised.value)
    assert fed.channel.log == []                                         # nothing asked of any browser


def test_a_body_the_browser_dropped_is_404(actions, fed):
    with pytest.raises(NotFound, match="no longer holds"):
        actions.network_body("s1", "1.1")                                # FakeChannel: no such network data


@pytest.mark.parametrize(
    ("code", "says"),
    [
        ("unavailable network data", "did not keep this body"),
        ("no such network collector", "no longer collectable"),
    ],
)
def test_the_other_known_browser_answers_are_404_in_our_words(actions, fed, code, says):
    fed.channel.bodies["r1"] = code
    with pytest.raises(NotFound, match=says) as raised:
        actions.network_body("s1", "1.1")
    assert errors.status_for(raised.value) == 404
    assert "/tmp/x" not in str(raised.value)


def test_an_unrecognised_browser_error_never_carries_its_text(actions, fed):
    fed.channel.bodies["r1"] = "unknown error"
    with pytest.raises(RuntimeError) as raised:
        actions.network_body("s1", "1.1")
    assert "/tmp/x" not in str(raised.value) and raised.value.__cause__ is None


# ---- masked like every other capture surface (final review I2) -----------------


def test_a_bound_secret_echoed_in_a_body_comes_back_masked(actions, fed):
    fed.capture.secret("ws", ["hunter2", "hunter%32"])
    sent = '{"echo":"hunter2","form":"pw=hunter%32"}'
    fed.channel.bodies["r1"] = body_of(sent)
    got = actions.network_body("s1", "1.1")
    assert got["text"] == f'{{"echo":"{MASK}","form":"pw={MASK}"}}'
    assert got["size"] == len(sent) and got["truncated"] is False


def test_a_secret_straddling_the_body_cap_is_masked_before_the_cut(actions, fed):
    fed.capture.secret("ws", ["hunter2"])
    fed.channel.bodies["r1"] = body_of("x" * (BODY_CAP - 3) + "hunter2" + "y" * 10)
    got = actions.network_body("s1", "1.1")
    assert "hun" not in got["text"] and got["truncated"] is True
    assert len(got["text"].encode()) <= BODY_CAP


# ---- answered from the row when asking would cost the connection (I4) ----------


def kept(fed, rid, mime, size, n):
    """One more kept request on the page, ``1.<n>``."""
    for method, params in (
        req(rid, f"https://a.test/{rid}", initiator="fetch"),
        done(rid, 200, mime=mime, body_size=size),
    ):
        fed.capture.ingest("ws", "s1", method, params)
    return f"1.{n}"


@pytest.mark.parametrize("mime", ["image/png", "application/octet-stream", "font/woff2", "video/mp4"])
def test_a_binary_body_is_answered_from_the_row_without_asking(actions, fed, mime):
    id = kept(fed, "r2", mime, 8300, 2)
    assert actions.network_body("s1", id) == {
        "id": id, "uri": f"workspace://network/{id}", "mime": mime,
        "size": 8300, "binary": True, "text": None, "truncated": False}
    assert fed.channel.log == []


def test_a_text_body_over_8_mb_is_refused_before_asking(actions, fed):
    id = kept(fed, "r2", "application/json", 9_000_000, 2)
    with pytest.raises(ValueError, match="too large to fetch") as raised:
        actions.network_body("s1", id)
    assert errors.status_for(raised.value) == 400
    assert fed.channel.log == []


@pytest.mark.parametrize("mime", [
    "text/html; charset=utf-8", "application/json", "application/problem+json",
    "application/xml", "image/svg+xml", "text/javascript", "application/javascript",
    "application/x-www-form-urlencoded", "",
])
def test_text_and_unknown_types_are_asked_of_the_browser(actions, fed, mime):
    id = kept(fed, "r2", mime, 8_000_000, 2)
    fed.channel.bodies["r2"] = body_of("ok")
    assert actions.network_body("s1", id)["text"] == "ok"
