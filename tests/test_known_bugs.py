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


def bug(reason):
    return pytest.mark.xfail(strict=True, reason=f"#162: {reason}")


@bug("a reap after every keep-alive ping failed reads lost")
async def test_a_reap_after_every_keep_alive_ping_failed_is_reaped():
    """The keep-alive was owed but never reached the browser, so the node's own
    timeout is the window that applied."""
    from kubed.selenium_flow.monitor.events import LocalBus
    from kubed.selenium_flow.monitor.monitor import LISTING_TICK, Monitor

    from .fakes import Clock
    from .test_monitor import Listing

    pinged = []

    def ping(session_id):
        pinged.append(session_id)
        raise ConnectionError("grid down")

    clock, listing = Clock(), Listing()
    monitor = Monitor(
        LocalBus(), listing=listing, ping=ping, keep_alive=900,
        clock=clock, tick=LISTING_TICK,
    )
    workspaces = Workspaces(RecordingActions(), monitor=monitor, keep_alive=900)
    workspaces.open_browser(Caller(NAMED), url="https://a.test/")
    session_id = workspaces.store.get(NAMED).session_id
    listing.running[session_id] = 300
    for _ in range(2):
        clock.now += 240
        await monitor.look()
    assert pinged == [session_id, session_id]
    now = time.time()
    workspaces.booted = now - 1000
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


# ---- site data ---------------------------------------------------------------


# ---- surfaces ----------------------------------------------------------------


@bug("an unparseable JSON body is read as empty, so POST /browser opens a browser")
def test_a_malformed_json_body_does_not_open_a_browser(server, monkeypatch):
    from starlette.testclient import TestClient

    from .conftest import TOKEN

    opened = []
    monkeypatch.setattr(
        server.workspaces, "open_browser", lambda *a, **k: opened.append(k) or {}
    )
    client = TestClient(server.mcp.http_app())
    response = client.post(
        "/browser",
        content=b"{not json",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Sf-Workspace": NAMED,
            "Content-Type": "application/json",
        },
    )
    assert opened == []
    assert response.status_code == 400


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


@bug("an unreachable Redis at boot is a traceback, not 'selenium-flow: <reason>'")
def test_an_unreachable_redis_stops_the_boot_with_the_reason(tmp_path, monkeypatch):
    import sys
    import types

    from kubed.selenium_flow import main as main_module

    module = types.ModuleType("redis")
    module.Redis = type(
        "Redis",
        (),
        {
            "__init__": lambda self, **kw: None,
            "ping": lambda self: (_ for _ in ()).throw(ConnectionError("refused")),
            "from_url": classmethod(lambda cls, url, **kw: cls()),
        },
    )
    monkeypatch.setitem(sys.modules, "redis", module)
    path = tmp_path / "config.yaml"
    path.write_text("workspace:\n  store: redis\nredis:\n  url: redis://nope:6379\n")
    with pytest.raises(SystemExit) as caught:
        main_module.main(["--config-file", str(path)])
    assert str(caught.value.code).startswith("selenium-flow: ")


@bug("the Settings tab shows session.capabilities unmasked, proxy credentials included")
def test_the_settings_tab_does_not_show_credentials_in_capabilities():
    import json

    from kubed.selenium_flow.config import Settings, describe

    proxy = {
        "proxy": {"proxyType": "manual", "httpProxy": "http://u:hunter2@proxy:3128"}
    }
    shown = describe(Settings(session={"capabilities": proxy}), {})
    assert "hunter2" not in json.dumps(shown)


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
    "MCP logs an unreachable Grid (503) as an ERROR with a traceback; HTTP logs one WARNING"
)
def test_a_503_over_mcp_is_one_warning_line_as_it_is_over_http():
    import logging
    import sys

    import requests

    from kubed.selenium_flow import errors
    from kubed.selenium_flow.mcp.failures import FAILED_CALL, QuietCallerMistakes

    try:
        raise requests.ConnectionError("grid down")
    except requests.ConnectionError:
        exc_info = sys.exc_info()
    assert errors.status_for(exc_info[1]) == 503
    record = logging.LogRecord(
        "fastmcp.server.server",
        logging.ERROR,
        __file__,
        1,
        f"{FAILED_CALL} 'navigate'",
        (),
        exc_info,
    )
    QuietCallerMistakes().filter(record)
    assert record.levelno == logging.WARNING
    assert "Traceback" not in record.getMessage()


# ---- config -------------------------------------------------------------------


@bug("port has no bound: a negative port loads and fails later")
def test_a_negative_port_is_refused_at_load():
    from kubed.selenium_flow.config import Settings

    with pytest.raises(ValueError):
        Settings(port=-1)
