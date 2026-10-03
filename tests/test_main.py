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


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_a_tokenless_loopback_server_does_not_warn(tmp_path, listened, caplog, host):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, f"host: '{host}'\n"))
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_a_token_silences_the_warning(tmp_path, listened, caplog):
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(config(tmp_path, "auth:\n  token: s3cret\n"))
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_the_access_log_drops_the_query_string_and_keeps_the_rest(tmp_path, listened):
    main_module.main(config(tmp_path))
    main_module.main(config(tmp_path))  # once, however often it boots
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, main_module.DropQuery) for f in access.filters) == 1
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/files/x?session=s3cr3t&exp=1&sig=abc", "1.1", 200),
        None,
    )
    assert all(f.filter(record) for f in access.filters)
    line = record.getMessage()
    assert line == '1.2.3.4:5 - "GET /files/x HTTP/1.1" 200'
    assert "session=" not in line and "sig=" not in line
