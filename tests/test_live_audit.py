import logging

import pytest

from kubed.selenium_flow.live.audit import (
    clean,
    duration,
    failed,
    left,
    opened,
    refused,
    session_label,
    switched,
    viewer_of,
)
from kubed.selenium_flow.principal import ADMIN, Principal

pytestmark = pytest.mark.unit


def test_the_session_is_named_by_its_start_time():
    assert session_label(1_793_109_731.0) == "session started 2026-10-27T14:02:11Z"
    assert session_label(1_793_109_731.9) == "session started 2026-10-27T14:02:11Z"
    assert session_label(0) == "an unknown session"


@pytest.mark.parametrize(
    "seconds, said", [(45, "45s"), (724, "12m04s"), (3720, "1h02m")]
)
def test_durations(seconds, said):
    assert duration(seconds) == said


def test_who_the_viewer_is():
    assert viewer_of(None) == "open" and viewer_of(ADMIN) == "admin"
    assert viewer_of(Principal("oidc", subject="s1", username="drk")) == "drk"
    assert viewer_of(Principal("oidc", subject="s1")) == "s1"
    assert viewer_of(Principal("oidc")) == "oidc"
    assert (
        viewer_of(Principal("oidc", username="dr\nk" + "x" * 80))
        == ("dr?k" + "x" * 80)[:64]
    )


def test_clean_turns_control_characters_into_question_marks():
    assert clean("a\x00b\x1b[31mc\x7f") == "a?b?[31mc?"


def test_the_three_lines(caplog):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.live"):
        opened("drk", "w1", "session started 2026-10-27T14:02:11Z", "watch")
        left(
            "drk",
            "w1",
            "session started 2026-10-27T14:02:11Z",
            724,
            "watch",
            "nobody was watching",
        )
    assert caplog.messages == [
        "live: drk opened the live view of w1 (session started 2026-10-27T14:02:11Z) in watch",
        "live: drk left the live view of w1 (session started 2026-10-27T14:02:11Z) after 12m04s in watch: nobody was watching",
    ]


def test_failed_names_the_type_and_nothing_else(caplog):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.live"):
        failed("w1", ConnectionError("http://grid/?sig=abc"))
    assert caplog.messages == ["live: the live view of w1 failed: ConnectionError"]


def test_a_switch_and_a_refusal(caplog):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.live"):
        switched("drk", "w1", "an unknown session", "control")
        refused(None, "w1", "the browser ended")
    assert caplog.messages == [
        "live: drk switched to control in w1 (an unknown session)",
        "live: refused someone the live view of w1: the browser ended",
    ]
    assert [r.levelno for r in caplog.records] == [logging.INFO, logging.WARNING]
