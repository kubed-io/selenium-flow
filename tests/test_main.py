"""The boot: what `main()` does between a config and a listening server.

Run in-process. The one thing stubbed is the socket: FastMCP 4 serves through
`uvicorn.Server(config).serve()` rather than `uvicorn.run`, so that is what is
replaced, and it records where the server was told to listen. Everything
before it — the loader, the logging setup, the server's own construction — is
the real thing.
"""

import io
import logging
import subprocess
import sys

import pytest
import uvicorn

from kubed.selenium_flow import config as config_module
from kubed.selenium_flow import main as main_module
from kubed.selenium_flow import server as server_module
from kubed.selenium_flow.http import access_log
from kubed.selenium_flow.main import WIRE_LOGGERS

from .fakes import patch_os

pytestmark = pytest.mark.unit


@pytest.fixture
def listened(monkeypatch, fastmcp_logger, levels):
    """Boot without listening: ``listened`` is a list of (host, port) the server
    was told to serve on, empty if the boot never got that far. It also puts the
    ``fastmcp`` logger back, and the root's level, which ``QuickMCP.run()`` sets
    to ``serving.log_level``."""
    served = []

    async def serve(self, sockets=None):
        served.append((self.config.host, self.config.port))

    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    return served


@pytest.fixture
def levels():
    """Put every logger level main() may touch back where it was."""
    names = ["", *WIRE_LOGGERS]
    saved = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture
def fastmcp_logger():
    """Put the ``fastmcp`` logger's handlers, propagation and level back."""
    logger = logging.getLogger("fastmcp")
    handlers, propagate, level = list(logger.handlers), logger.propagate, logger.level
    yield logger
    logger.handlers[:] = handlers
    logger.propagate = propagate
    logger.setLevel(level)


def config(tmp_path, text=""):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return ["--config-file", str(path)]


def test_a_boot_leaves_the_fastmcp_logger_as_it_found_it(request):
    """Under random order a boot can run before the nbf refusal check in
    ``test_auth.py``, which attaches a handler to ``fastmcp``. A boot that left
    that logger propagating logged the record twice there. The child pins the
    order: a booting test, then the check."""
    done = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider", "-q",
            "tests/test_main.py::test_oidc_without_roles_warns",
            "tests/test_auth.py::test_our_refusals_are_logged_with_the_subject[not-yet-valid]",
        ],
        cwd=request.config.rootpath,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, (done.returncode, done.stdout[-2000:])


def test_a_config_key_the_server_does_not_know_stops_the_boot(tmp_path, listened):
    """Restarted until fixed beats started on something misread: the key is in
    the exit message, the exit is not a clean one, and nothing listened."""
    with pytest.raises(SystemExit) as caught:
        main_module.main(config(tmp_path, "grid:\n  url: http://g:4444\nbanana: 1\n"))
    assert caught.value.code not in (0, None)
    assert str(caught.value.code).startswith("selenium-flow: ")
    assert "banana" in str(caught.value.code)
    assert listened == []


def test_logging_is_configured_before_the_server_is_built(tmp_path, listened, monkeypatch):
    # By order, not by records: under pytest the root already has handlers, so
    # basicConfig sets no level and a caplog check could not tell the two apart.
    seen = []
    monkeypatch.setattr(main_module, "configure_logging", lambda level: seen.append(("logging", level)))
    real = server_module.secrets.from_settings
    monkeypatch.setattr(server_module.secrets, "from_settings",
                        lambda *a, **k: seen.append(("secrets",)) or real(*a, **k))
    main_module.main(config(tmp_path, "serving:\n  log_level: debug\n"))
    assert seen[:2] == [("logging", "DEBUG"), ("secrets",)]


def test_a_load_error_exits_naming_it_and_never_the_value(tmp_path, listened, monkeypatch):
    monkeypatch.setenv("PORT", "hunter2")
    monkeypatch.setenv("REDIS_PASSWORD", "hunter2")
    with pytest.raises(SystemExit) as caught:
        main_module.main(config(tmp_path))
    text = str(caught.value.code)
    assert text.startswith("selenium-flow: the config did not load:\n")
    assert "serving.port (env PORT)" in text and "hunter2" not in text
    assert listened == []


def test_an_unknown_flag_stops_the_boot(listened):
    with pytest.raises(SystemExit) as caught:
        main_module.main(["--bogus", "1"])
    assert "no item takes this flag" in str(caught.value.code)
    assert listened == []


def test_a_top_level_transport_stops_the_boot(tmp_path, listened):
    with pytest.raises(SystemExit) as caught:
        main_module.main(config(tmp_path, "transport: stdio\n"))
    assert "transport (file " in str(caught.value.code)


@pytest.mark.parametrize("text, shown", [("grid:\n  url: http://g:4444\n", "path"), (None, "none")])
def test_the_boot_line_names_the_file_or_none(tmp_path, listened, caplog, text, shown):
    caplog.set_level(logging.INFO, logger="kubed.selenium_flow.main")
    main_module.main(config(tmp_path, text) if text is not None else [])
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("config=")]
    expected = str(tmp_path / "config.yaml") if shown == "path" else "none"
    assert line.startswith(f"config={expected} ")


def test_selenium_wire_loggers_are_held_at_info_whatever_log_level_says(
    tmp_path, listened, levels
):
    """DEBUG there logs every BiDi frame, and site data's frames carry cookie
    values."""
    for name in WIRE_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    main_module.main(config(tmp_path, "serving:\n  log_level: DEBUG\n"))
    for name in WIRE_LOGGERS:
        assert logging.getLogger(name).level == logging.INFO, name


def test_fastmcp_logs_through_the_root_handler_at_info_or_the_root_level(
    tmp_path, listened, fastmcp_logger, levels
):
    """FastMCP's own Rich handlers would wrap every line at 80 columns, and
    LOG_LEVEL=WARNING still quiets it. The handlers go in ``QuickMCP.run()``,
    which ``main`` reaches here."""
    logging.getLogger().setLevel(logging.WARNING)
    main_module.main(config(tmp_path, "serving:\n  log_level: WARNING\n"))
    assert fastmcp_logger.handlers == []
    assert fastmcp_logger.propagate is True
    assert fastmcp_logger.level == logging.WARNING


def test_fastmcp_is_never_opened_below_info_whatever_log_level_says(
    tmp_path, listened, fastmcp_logger, levels
):
    """FastMCP logs every tool's arguments at DEBUG: scripts, typed text."""
    logging.getLogger().setLevel(logging.DEBUG)
    main_module.main(config(tmp_path, "serving:\n  log_level: DEBUG\n"))
    ops = logging.getLogger("fastmcp.server.mixins.mcp_operations")
    assert not ops.isEnabledFor(logging.DEBUG)


def test_a_long_tool_error_is_one_line_in_the_log(tmp_path, listened, fastmcp_logger):
    """Loki stores each physical line as its own entry, so a wrapped error
    cannot be found by its text."""
    main_module.main(config(tmp_path))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        message = "Error calling tool 'x': " + "y" * 200
        logging.getLogger("fastmcp.server.server").warning(message)
    finally:
        root.removeHandler(handler)
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert message in lines[0]


def test_a_server_booted_without_a_token_says_auth_is_off(
    tmp_path, listened, caplog
):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path))
    assert "auth=off" in caplog.text
    assert "auth=on" not in caplog.text


def test_a_server_booted_with_a_token_says_auth_is_on(tmp_path, listened, caplog):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, "auth:\n  token: s3cret\n"))
    assert "auth=on" in caplog.text
    assert "s3cret" not in caplog.text, "the log line names the state, not the token"


OIDC = """auth:
  token: s3cret
oidc:
  issuer: https://auth.example.com/realms/example
  audience: https://mcp.example.com
  jwks_uri: https://auth.example.com/realms/example/certs
"""


def test_the_boot_line_says_oidc_is_off(tmp_path, listened, caplog):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path))
    assert "oidc=off" in caplog.text


def test_the_boot_line_names_the_oidc_issuer(tmp_path, listened, caplog):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, OIDC + "  roles: [mcp]\n"))
    assert "oidc=https://auth.example.com/realms/example" in caplog.text
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_oidc_without_roles_warns(tmp_path, listened, caplog):
    """No roles: every token from the issuer for the audience gets in."""
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, OIDC))
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "oidc.roles" in warned[0]


def test_the_server_listens_where_the_config_says(tmp_path, listened):
    main_module.main(config(tmp_path, "serving:\n  host: 127.0.0.1\n  port: 8123\n"))
    assert listened == [("127.0.0.1", 8123)]


def test_a_tokenless_server_on_a_reachable_address_warns_loudly(
    tmp_path, listened, caplog
):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, "serving:\n  host: 10.1.2.3\n  port: 8123\n"))
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warned] == [
        "AUTH_TOKEN is not set: anyone who can reach 10.1.2.3:8123 can drive every browser"
    ]
    assert "auth=off" in caplog.text


@pytest.mark.parametrize("host", ["127.example.com", "127.0.0.2.nip.io"])
def test_a_host_that_only_starts_with_127_is_not_loopback(tmp_path, listened, caplog, host):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, f"serving:\n  host: {host}\n"))
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_a_tokenless_loopback_server_does_not_warn(tmp_path, listened, caplog, host):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, f"serving:\n  host: '{host}'\n"))
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_a_token_silences_the_warning(tmp_path, listened, caplog):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, "auth:\n  token: s3cret\n"))
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def access_line(path):
    """What uvicorn's access log says for a GET of ``path``, once filtered."""
    access = logging.getLogger("uvicorn.access")
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", path, "1.1", 200),
        None,
    )
    assert all(f.filter(record) for f in access.filters)
    return record.getMessage()


def test_the_access_log_drops_the_query_string_and_keeps_the_rest(tmp_path, listened):
    main_module.main(config(tmp_path))
    main_module.main(config(tmp_path))  # once, however often it boots
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, access_log.RouteTemplates) for f in access.filters) == 1
    line = access_line("/files/abc/x.png?workspace=s3cr3t&exp=1&sig=abc")
    assert line == '1.2.3.4:5 - "GET /files/{session_id}/{name} HTTP/1.1" 200'
    assert "workspace=" not in line and "sig=" not in line


SOCKET_LINK = "/flow/admin/workspaces/s3cr3t/live/socket?exp=1&sig=abc&by=drk"
SOCKET_ROUTE = "/flow/admin/workspaces/{key}/live/socket"
WEBSOCKET_LINES = [
    ('%s - "WebSocket %s" [accepted]', ("10.0.0.1:5000", SOCKET_LINK), "[accepted]"),
    ('%s - "WebSocket %s" 403', ("10.0.0.1:5000", SOCKET_LINK), "403"),
    ('%s - "WebSocket %s" %d', ("10.0.0.1:5000", SOCKET_LINK, 404), "404"),
]


def error_line(msg, args, logger="uvicorn.error"):
    record = logging.LogRecord(logger, logging.INFO, __file__, 1, msg, args, None)
    assert all(f.filter(record) for f in logging.getLogger(logger).filters)
    return record.getMessage()


@pytest.mark.parametrize(("msg", "args", "tail"), WEBSOCKET_LINES)
def test_uvicorn_s_websocket_line_names_the_route_not_the_link(
    tmp_path, listened, msg, args, tail
):
    """The live socket's path carries the workspace and its query a signature."""
    main_module.main(config(tmp_path, "route_prefix: /flow\n"))
    line = error_line(msg, args)
    assert line == f'10.0.0.1:5000 - "WebSocket {SOCKET_ROUTE}" {tail}'
    for leak in ("?", "sig", "exp", "by=", "s3cr3t"):
        assert leak not in line


def test_no_other_uvicorn_error_record_is_altered(tmp_path, listened):
    main_module.main(config(tmp_path))
    assert error_line("Started server process [%d]", (7,)) == "Started server process [7]"
    assert error_line("a %s b %s c %s", ("x", "/y?z=1", "w")) == "a x b /y?z=1 c w"


def test_the_one_filter_serves_both_loggers_however_often_it_boots(tmp_path, listened):
    main_module.main(config(tmp_path))
    main_module.main(config(tmp_path))
    mine = [
        [f for f in logging.getLogger(name).filters if isinstance(f, access_log.RouteTemplates)]
        for name in ("uvicorn.access", "uvicorn.error")
    ]
    assert [len(m) for m in mine] == [1, 1] and mine[0][0] is mine[1][0]


def test_quiet_returns_the_filter_it_installs(tmp_path):
    from starlette.applications import Starlette

    app = Starlette()
    first = access_log.quiet(app)
    on_access = [f for f in logging.getLogger("uvicorn.access").filters if f is first]
    assert isinstance(first, access_log.RouteTemplates) and on_access == [first]
    assert access_log.quiet(app) is first


SESSION_PATHS = [
    ("/admin/workspaces/s3cr3t/history", "/admin/workspaces/{key}/history"),
    ("/kept/s3cr3t/x.pdf", "/kept/{workspace}/{name}"),
    ("/screenshots/s3cr3t/shot.png", "/screenshots/{workspace}/{name}"),
    ("/files/s3cr3t/report.pdf", "/files/{session_id}/{name}"),
]


@pytest.mark.parametrize("prefix", ["", "/flow"])
@pytest.mark.parametrize(("path", "template"), SESSION_PATHS)
def test_the_access_log_names_the_route_never_the_workspace(
    tmp_path, listened, prefix, path, template
):
    """Copilot, review 2: a workspace name is the address of a browser anyone
    with the token can drive, and dropping the query still left it in the path. The line names the route."""
    main_module.main(config(tmp_path, f"route_prefix: {prefix or '/'}\n"))
    line = access_line(f"{prefix}{path}")
    assert line == f'1.2.3.4:5 - "GET {prefix}{template} HTTP/1.1" 200'
    assert "s3cr3t" not in line


@pytest.mark.parametrize("prefix", ["", "/flow"])
def test_the_probes_log_as_themselves(tmp_path, listened, prefix):
    main_module.main(config(tmp_path, f"route_prefix: {prefix or '/'}\n"))
    assert access_line("/health") == '1.2.3.4:5 - "GET /health HTTP/1.1" 200'


def test_a_path_no_route_serves_keeps_only_its_first_segment(tmp_path, listened):
    main_module.main(config(tmp_path))
    line = access_line("/kept/s3cr3t?x=1")
    assert line == '1.2.3.4:5 - "GET /kept/… HTTP/1.1" 200'
    assert access_line("/") == '1.2.3.4:5 - "GET / HTTP/1.1" 200'


def test_a_config_error_while_building_the_server_stops_the_boot(tmp_path, listened):
    (tmp_path / "old" / "flows").mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:
        main_module.main(["--data-dir", str(tmp_path)])
    assert "move" in str(exc.value) and "workspaces/" in str(exc.value)


def test_a_missing_plugin_folder_stops_the_boot_naming_it(tmp_path, listened, monkeypatch):
    from kubed.selenium_flow.mcp import skill

    missing = tmp_path / "no-plugin"
    monkeypatch.setattr(skill, "plugin_path", lambda: missing)
    with pytest.raises(SystemExit) as exc:
        main_module.main(config(tmp_path))
    assert str(exc.value).startswith("selenium-flow: ")
    assert str(missing) in str(exc.value)
    assert listened == []


def test_an_unreadable_data_dir_exits_naming_it_not_a_traceback(tmp_path, listened, levels, monkeypatch):
    import errno
    import os

    data = tmp_path / "data"
    data.mkdir()
    real = os.stat

    def faulty(path, *a, **k):
        if str(path) == str(data):
            raise OSError(errno.EIO, "EIO")
        return real(path, *a, **k)

    patch_os(monkeypatch, "stat", faulty)
    monkeypatch.setenv("DATA_DIR", str(data))
    with pytest.raises(SystemExit) as exc:
        main_module.main(config(tmp_path))
    assert str(data) in str(exc.value) and "OSError" in str(exc.value)
    assert listened == []


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


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_every_setting_and_builds_nothing(flag, capsys, listened, monkeypatch):
    monkeypatch.setattr(main_module, "create_app", lambda **kw: pytest.fail("built"))
    with pytest.raises(SystemExit) as caught:
        main_module.main(["--port", "1", flag])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: selenium-flow")
    for leaf in config_module.leaves():
        assert leaf.flag in out and leaf.env in out, leaf.path
    assert listened == []
