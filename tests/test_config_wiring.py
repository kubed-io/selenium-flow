"""The switch from the environment to the config, held in place."""

import logging
import pathlib
import re

import pytest

import kubed.selenium_flow as package
from kubed.selenium_flow import main as main_module
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP

pytestmark = pytest.mark.unit
GRID = {"url": "http://grid.invalid:4444"}


def test_the_environment_is_read_in_two_modules_only():
    root = pathlib.Path(package.__file__).parent
    readers = sorted(
        str(p.relative_to(root)) for p in root.rglob("*.py")
        if re.search(r"os\.environ|os\.getenv|getenv\(", p.read_text(encoding="utf-8"))
    )
    assert set(readers) <= {"config.py", "secrets.py"}
    assert "config.py" in readers


def test_a_server_built_from_settings_uses_them(tmp_path):
    s = Settings(grid=GRID, auth={"token": "t"}, route_prefix="/flow",
                 data={"dir": str(tmp_path)}, mcp={"skill": False},
                 session={"browser": "firefox", "ttl": 42})
    server = SeleniumMCP(s)
    assert server.settings is s
    assert server.auth_token == "t"
    assert server.prefix == "/flow"
    assert server.flows is not None
    assert server.skill is None
    assert server.sessions.defaults == {"browser": "firefox"}
    assert server.store._ttl == 42
    assert server.sources["session.ttl"] == "args"


def test_no_settings_is_every_default(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "/nowhere")
    server = SeleniumMCP()
    assert server.flows is None and server.secrets is None and server.auth_token is None


def test_main_refuses_a_bad_config_with_the_reason(tmp_path, capsys):
    bad = tmp_path / "c.yaml"
    bad.write_text("redis:\n  bogus: 1\n")
    with pytest.raises(SystemExit) as caught:
        main_module.main(["--config-file", str(bad)])
    assert "redis.bogus" in str(caught.value)


def test_the_startup_log_says_secrets_off_when_none_are_configured(monkeypatch, caplog):
    monkeypatch.setattr(SeleniumMCP, "run", lambda self, **kw: None)
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main([])
    assert "secrets=off" in caplog.text


def test_the_startup_log_counts_dirs_and_config_entries_separately(monkeypatch, caplog, tmp_path):
    """`secrets=0` used to mean either off or config-entries-only. Now it
    names both counts, which is the only way the two stop looking the same."""
    monkeypatch.setattr(SeleniumMCP, "run", lambda self, **kw: None)
    d = tmp_path / "onedir"
    d.mkdir()
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"secrets:\n  dirs: [{d}]\n  entries:\n    a: {{}}\n    b: {{}}\n    c: {{}}\n"
    )
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main(["--config-file", str(cfg)])
    assert "secrets=1 dir, 3 from config" in caplog.text


def test_the_startup_log_never_shows_the_grid_urls_credentials(monkeypatch, caplog):
    """`GRID_URL` may carry userinfo, and the startup log is not a secret
    store — same reason `urls.without_userinfo` strips it from the probes."""
    monkeypatch.setattr(SeleniumMCP, "run", lambda self, **kw: None)
    monkeypatch.setenv("GRID_URL", "http://u:hunter2@hub:4444")
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main([])
    assert "hunter2" not in caplog.text
    assert "http://hub:4444" in caplog.text
