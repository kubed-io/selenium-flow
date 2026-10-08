"""The boot: what `main()` does between a config and a listening server.

Run in-process. The one thing stubbed is the socket: FastMCP 4 serves through
`uvicorn.Server(config).serve()` rather than `uvicorn.run`, so that is what is
replaced, and it records where the server was told to listen. Everything
before it — the loader, the logging setup, the server's own construction — is
the real thing.
"""

import logging

import pytest
import uvicorn

from kubed.selenium_flow import main as main_module
from kubed.selenium_flow.http import access_log
from kubed.selenium_flow.main import WIRE_LOGGERS

pytestmark = pytest.mark.unit


@pytest.fixture
def listened(monkeypatch):
    """Boot without listening: ``listened`` is a list of (host, port) the server
    was told to serve on, empty if the boot never got that far."""
    served = []

    async def serve(self, sockets=None):
        served.append((self.config.host, self.config.port))

    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    for name in ("AUTH_TOKEN", "CONFIG_FILE", "PORT", "HOST", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    return served


@pytest.fixture
def levels():
    """Put every logger level main() may touch back where it was."""
    names = ["", *WIRE_LOGGERS]
    saved = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def config(tmp_path, text=""):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return ["--config-file", str(path)]


def test_a_config_key_the_server_does_not_know_stops_the_boot(tmp_path, listened):
    """Restarted until fixed beats started on something misread: the key is in
    the exit message, the exit is not a clean one, and nothing listened."""
    with pytest.raises(SystemExit) as caught:
        main_module.main(config(tmp_path, "grid:\n  url: http://g:4444\nbanana: 1\n"))
    assert caught.value.code not in (0, None)
    assert str(caught.value.code).startswith("selenium-flow: ")
    assert "banana" in str(caught.value.code)
    assert listened == []


def test_selenium_wire_loggers_are_held_at_info_whatever_log_level_says(
    tmp_path, listened, levels
):
    """DEBUG there logs every BiDi frame, and site data's frames carry cookie
    values."""
    for name in WIRE_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    main_module.main(config(tmp_path, "log_level: DEBUG\n"))
    for name in WIRE_LOGGERS:
        assert logging.getLogger(name).level == logging.INFO, name


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
    main_module.main(config(tmp_path, "host: 127.0.0.1\nport: 8123\n"))
    assert listened == [("127.0.0.1", 8123)]


def test_a_tokenless_server_on_a_reachable_address_warns_loudly(
    tmp_path, listened, caplog
):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, "host: 10.1.2.3\nport: 8123\n"))
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warned] == [
        "AUTH_TOKEN is not set: anyone who can reach 10.1.2.3:8123 can drive every browser"
    ]
    assert "auth=off" in caplog.text


@pytest.mark.parametrize("host", ["127.example.com", "127.0.0.2.nip.io"])
def test_a_host_that_only_starts_with_127_is_not_loopback(tmp_path, listened, caplog, host):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, f"host: {host}\n"))
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_a_tokenless_loopback_server_does_not_warn(tmp_path, listened, caplog, host):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, f"host: '{host}'\n"))
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
    line = access_line("/files/abc/x.png?session=s3cr3t&exp=1&sig=abc")
    assert line == '1.2.3.4:5 - "GET /files/{session_id}/{name} HTTP/1.1" 200'
    assert "session=" not in line and "sig=" not in line


SESSION_PATHS = [
    ("/admin/sessions/s3cr3t/history", "/admin/sessions/{key}/history"),
    ("/kept/s3cr3t/x.pdf", "/kept/{session}/{name}"),
    ("/screenshots/s3cr3t/shot.png", "/screenshots/{session}/{name}"),
    ("/files/s3cr3t/report.pdf", "/files/{session_id}/{name}"),
]


@pytest.mark.parametrize("prefix", ["", "/flow"])
@pytest.mark.parametrize(("path", "template"), SESSION_PATHS)
def test_the_access_log_names_the_route_never_the_session(
    tmp_path, listened, prefix, path, template
):
    """Copilot, review 2: a session name is the credential past the token, and
    dropping the query still left it in the path. The line names the route."""
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
    assert "move" in str(exc.value) and "sessions/" in str(exc.value)
