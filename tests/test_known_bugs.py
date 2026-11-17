"""Likely bugs from issue #162, each pinned by a strict xfail.

A test here asserts the behaviour the code should have. It fails today, and
the day the bug is fixed it passes, which strict xfail reports as a failure:
remove the marker then.
"""

import contextlib
import time
from dataclasses import replace

import pytest

from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .conftest import NAMED, RecordingActions

pytestmark = pytest.mark.unit


class SpyMonitor:
    """Records what the manager tells the monitor."""

    def __init__(self):
        self.told = []

    def __getattr__(self, what):
        def record(*args, **kwargs):
            self.told.append((what, args))

        return record


class StuckActions(RecordingActions):
    """A Grid that answers the quit with an error other than 404."""

    def end_browser(self, session_id):
        raise RuntimeError("grid down")


def bug(reason):
    return pytest.mark.xfail(strict=True, reason=f"#162: {reason}")


def let_go(spy, session_id):
    """Whether the monitor was told to stop watching, or that it ended."""
    return any(
        what in ("release", "ended") and session_id in args for what, args in spy.told
    )


@bug("a failed quit leaves the keep-alive pinging the detached browser")
def test_a_failed_quit_lets_the_monitor_go_of_the_browser():
    spy = SpyMonitor()
    workspaces = Workspaces(StuckActions(), monitor=spy)
    workspaces.remember(NAMED, "one", "https://a.test/")
    workspaces.end_browser(Caller(NAMED))
    assert let_go(spy, "one")


@bug("the racing loser whose quit fails is still watched")
def test_a_losing_open_whose_quit_fails_is_not_left_watched():
    spy = SpyMonitor()
    workspaces = Workspaces(StuckActions(), monitor=spy)
    workspaces.remember(NAMED, "winner", "https://a.test/")
    workspaces.remember(NAMED, "loser", "https://a.test/")
    assert let_go(spy, "loser")


@bug("a reap is called lost when the keep-alive window never ran")
def test_a_reap_with_no_keep_alive_running_is_reaped_not_lost():
    workspaces = Workspaces(RecordingActions(), keep_alive=900)
    workspaces.open_browser(Caller(NAMED), url="https://a.test/")
    now = time.time()
    workspaces.store.update(
        NAMED,
        lambda r: replace(
            r, grid_timeout=300, called_at=now - 500, opened_at=now - 600
        ),
    )
    workspaces.actions.grid.alive.clear()
    workspaces.resolve(NAMED)
    assert workspaces.store.get(NAMED).reopened["cause"] == "reaped"


# ---- auth and live view -------------------------------------------------------


@bug("an OIDC user named admin has the token holder's viewer name")
@pytest.mark.parametrize("name", ["admin", "open", "oidc"])
def test_no_oidc_username_reads_as_the_admin_or_an_open_server(name):
    from kubed.selenium_flow.live.audit import viewer_of
    from kubed.selenium_flow.principal import ADMIN, Principal

    person = Principal("oidc", subject="s1", username=name)
    assert viewer_of(person) not in (viewer_of(ADMIN), viewer_of(None), "oidc")


# ---- site data ---------------------------------------------------------------


@bug(
    "a save drops the storage of an origin that entered the history after the capture read it"
)
def test_a_save_keeps_the_storage_of_an_origin_visited_during_the_capture():
    from kubed.selenium_flow.site_data.snapshot import CAPTURED

    workspaces = Workspaces(RecordingActions())
    workspaces.open_browser(Caller(NAMED), url="https://b.test/")
    session_id = workspaces.store.get(NAMED).session_id
    workspaces.store.update(
        NAMED,
        lambda r: r.with_site_data(
            {
                "cookies": [],
                "origins": {"https://a.test": {"local": {"k": "v"}}},
                "session": {},
                "saved_at": 1.0,
            }
        ),
    )
    # The capture read the history as it was: only b.test. A call landing on
    # a.test before the save is written adds it to the record's history.
    captured = {
        "origin": "https://b.test",
        "local": {"x": "1"},
        "session": {},
        "cookies": [],
        "others": {},
        "failed": [],
    }
    workspaces.store.update(NAMED, lambda r: r.visited("https://a.test/"))
    workspaces.settle(
        NAMED, {CAPTURED: captured}, browser=session_id, url=None, touch=False
    )
    origins = workspaces.store.get(NAMED).site_data["origins"]
    assert origins.get("https://a.test") == {"local": {"k": "v"}}


@bug(
    "save_site_data is not refused from an insecure browser, so a normal one restores it"
)
def test_an_insecure_browser_saves_no_site_data():
    from kubed.selenium_flow.site_data.snapshot import CAPTURED

    workspaces = Workspaces(RecordingActions())
    workspaces.open_browser(Caller(NAMED), insecure=True)
    session_id = workspaces.store.get(NAMED).session_id
    captured = {
        "cookies": [
            {
                "name": "sid",
                "value": "v",
                "domain": "bad.test",
                "path": "/",
                "http_only": True,
                "secure": True,
                "same_site": "lax",
                "expiry": None,
                "value_type": "string",
            }
        ],
        "origin": "https://bad.test",
        "local": {"k": "v"},
        "session": {},
        "others": {},
        "failed": [],
    }
    # A refusal is as good as a drop.
    with contextlib.suppress(Exception):
        workspaces.settle(
            NAMED, {"url": "https://bad.test/", CAPTURED: captured}, browser=session_id
        )
    assert not workspaces.store.get(NAMED).site_data


# ---- surfaces ----------------------------------------------------------------


# ---- browser actions ----------------------------------------------------------


@bug("an unreadable frame index switches to frame 0")
def test_an_unreadable_frame_index_is_refused(actions, scripted):
    driver = scripted()
    with pytest.raises(ValueError):
        actions.frame("abc", "switch", index="abc")
    assert ("switch_to", "frame", 0) not in driver.log


@bug("an empty text is refused as 'the file is required'")
def test_an_empty_text_file_can_be_uploaded(actions, scripted):
    scripted()
    result = actions.upload_file("abc", selector={"css": "input"}, text="")
    assert result["bytes"] == 0


@bug(
    "Actions.write types the string None for text=None, and POST /browser/write passes it"
)
def test_a_null_text_is_never_typed_as_the_word_none(actions, scripted):
    driver = scripted()
    sent = []
    driver.element.send_keys = lambda *keys: sent.append(keys)
    with contextlib.suppress(ValueError):
        actions.write("abc", None, selector={"css": "#q"})
    assert sent == []


# ---- saved flows -------------------------------------------------------------


@bug(
    "an unknown top-level key is neither refused nor warned about, so a typo saves clean"
)
async def test_a_flow_with_an_unknown_top_level_key_is_refused(server):
    from kubed.selenium_flow.core.capabilities import ENDPOINTS
    from kubed.selenium_flow.flows.document import (
        InvalidFlow,
        concerns,
        step_schemas,
        validate,
    )

    tools = {}
    for name in sorted(set(ENDPOINTS.values())):
        tools[name] = (await server.mcp.get_tool(name)).parameters or {}
    document = {
        "description": "x",
        "step": [{"tool": "navigate", "args": {"url": "https://example.test/"}}],
        "steps": [{"tool": "navigate", "args": {"url": "https://example.test/"}}],
    }
    try:
        validate(document, step_schemas(tools))
    except InvalidFlow:
        return  # refused
    assert concerns(document)  # or at least warned about


@bug("a run with no browser and no such flow answers 'no session is open'")
def test_a_missing_flow_is_named_before_a_missing_browser():
    from types import SimpleNamespace

    from kubed.selenium_flow.flows import api as flowapi

    from .conftest import manager

    workspaces = manager()
    store = SimpleNamespace(get=lambda workspace, name: None)
    with pytest.raises(ValueError, match="no flow called"):
        flowapi.run_for(store, workspaces.actions, workspaces, NAMED, "nope")


# ---- config -------------------------------------------------------------------


@bug("the Settings tab shows session.capabilities unmasked, proxy credentials included")
def test_the_settings_tab_does_not_show_credentials_in_capabilities():
    import json

    from kubed.selenium_flow.config import Settings, describe

    proxy = {
        "proxy": {"proxyType": "manual", "httpProxy": "http://u:hunter2@proxy:3128"}
    }
    shown = describe(Settings(session={"capabilities": proxy}), {})
    assert "hunter2" not in json.dumps(shown)


# ---- capture ------------------------------------------------------------------


@bug("the first call after capture.ttl of quiet has no hint: the mark is None")
def test_the_first_call_after_a_quiet_spell_still_counts_what_it_caused():
    from kubed.selenium_flow.capture.store import Capture

    from .capture_events import log
    from .fakes import FakeMonitor, build

    now = [1000.0]
    capture = Capture(ttl=10, clock=lambda: now[0])
    workspaces = build(capture, FakeMonitor())
    now[0] += 60

    def click(session_id):
        capture.ingest(
            "ws",
            session_id,
            *log("Uncaught TypeError", type="javascript", level="error"),
        )
        return {"url": "https://a.test/", "title": "A"}

    result = workspaces.act(
        Caller("ws", "query"), click, tool="interact", surface="mcp"
    )
    assert result["page"]["errors"] == 1


# ---- plugin and protocol -------------------------------------------------------


@bug(
    "prompts._split leaves the carriage return of a CRLF file at the start of the body"
)
def test_a_crlf_prompt_has_no_carriage_return_before_its_body():
    from kubed.selenium_flow.mcp.prompts import _split

    _, body = _split("---\r\ndescription: a\r\n---\r\nBody\r\n")
    assert body.startswith("Body")


@bug(
    "prompts._split opens a header on ---x, where the shared splitter wants exactly ---"
)
def test_a_first_line_that_is_not_exactly_three_dashes_opens_no_header():
    from kubed.selenium_flow.mcp import frontmatter
    from kubed.selenium_flow.mcp.prompts import _split

    text = "---x\ndescription: a\n---\nBody\n"
    assert frontmatter.split(text) is None
    assert _split(text) is None


@bug(
    "log.debug(exc_info=True) in core/ writes frames that can quote a Grid URL's userinfo"
)
def test_a_failure_logged_with_a_traceback_is_scrubbed_of_credentials(actions, caplog):
    import logging
    from types import SimpleNamespace

    def refuse(session_id):
        raise ConnectionError("cannot reach http://drk:hunter2@grid.test:4444/session")

    actions.pointers = SimpleNamespace(get=refuse)
    with caplog.at_level(logging.DEBUG, logger="kubed.selenium_flow.core.actions"):
        actions._pointer("abc")
    assert caplog.records
    formatter = logging.Formatter()
    assert all("hunter2" not in formatter.format(r) for r in caplog.records)


# ---- config -------------------------------------------------------------------


@bug("port has no bound: a negative port loads and fails later")
def test_a_negative_port_is_refused_at_load():
    from kubed.selenium_flow.config import Settings

    with pytest.raises(ValueError):
        Settings(port=-1)
