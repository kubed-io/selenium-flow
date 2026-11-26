"""The switch from the environment to the config, held in place."""

import asyncio
import json
import logging
import os
import pathlib
import re
import sys

import pytest
from kubed.mcp import Source, TelemetrySettings

import kubed.selenium_flow as package
from kubed.selenium_flow import config
from kubed.selenium_flow import main as main_module
from kubed.selenium_flow import server as server_module
from kubed.selenium_flow.config import ConfigError
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN, over_http

pytestmark = pytest.mark.unit
GRID = {"url": "http://grid.invalid:4444"}


def test_no_setting_of_the_runner_reaches_a_test():
    assert not [leaf.env for leaf in config.leaves() if leaf.env in os.environ]


def test_the_environment_is_read_in_one_module_only():
    root = pathlib.Path(package.__file__).parent
    readers = sorted(
        str(p.relative_to(root)) for p in root.rglob("*.py")
        if re.search(r"os\.environ|os\.getenv|getenv\(", p.read_text(encoding="utf-8"))
    )
    assert set(readers) <= {"secrets.py"}


def test_a_server_built_from_a_mapping_uses_it(tmp_path):
    server = SeleniumMCP({"grid": GRID, "auth": {"token": "t"}, "route_prefix": "/flow",
                          "data": {"dir": str(tmp_path)},
                          "session": {"browser": "firefox", "keep_alive": 600},
                          "workspace": {"ttl": 42}})
    assert server.settings is server.mcp.config
    assert server.sources is server.mcp.config_sources
    assert (server.auth_token, server.prefix) == ("t", "/flow")
    assert server.flows is not None
    assert server.workspaces.defaults == {"browser": "firefox"}
    assert server.monitor.keep_alive == 600
    assert server.monitor.ping == server.grid.ping
    from kubed.selenium_flow.monitor.monitor import KEEPALIVE
    assert KEEPALIVE not in server.monitor.sinks
    assert KEEPALIVE not in server.monitor.events_for
    assert KEEPALIVE not in server.monitor.collectors_for
    assert server.store._ttl == 42
    assert server.sources["workspace.ttl"] == Source("code")


def test_a_server_built_in_code_reads_no_environment(monkeypatch):
    monkeypatch.setenv("PORT", "1")
    monkeypatch.setenv("AUTH_TOKEN", "t0ken")
    server = SeleniumMCP()
    assert server.settings.serving.port == 8000 and server.auth_token is None


def test_a_server_built_from_argv_reads_them_and_the_environ_given():
    server = SeleniumMCP(argv=["--port", "9001"], environ={"AUTH_TOKEN": "t", "GRID_URL": GRID["url"]})
    assert (server.settings.serving.port, server.auth_token) == (9001, "t")
    assert server.sources["serving.port"] == Source("arg", "--port")


def test_the_auth_is_set_after_the_config_loads():
    server = SeleniumMCP({"grid": GRID, "auth": {"token": "t"}})
    assert server.mcp.auth is server.doors.mcp is not None


def test_a_config_kubed_mcp_refuses_is_a_config_error():
    with pytest.raises(ConfigError, match=r"^the config did not load:\n  serving\.port \(code\)"):
        SeleniumMCP({"serving": {"port": 0}})


def test_on_loaded_runs_before_the_rest_is_built(monkeypatch):
    seen = []
    monkeypatch.setattr(server_module.secrets, "from_settings",
                        lambda *a, **k: seen.append("secrets"))
    SeleniumMCP({"grid": GRID}, on_loaded=lambda c: seen.append(c.serving.log_level))
    assert seen == ["INFO", "secrets"]


def test_the_oidc_and_recording_checks_keep_their_messages():
    with pytest.raises(ConfigError, match=r"^oidc needs auth.token: the token stays the admin login$"):
        SeleniumMCP({"grid": GRID, "oidc": {"issuer": "https://id.example.com",
                     "audience": "a", "jwks_uri": "https://id.example.com/jwks"}})
    with pytest.raises(ConfigError, match=r"^recording.enabled needs data.dir"):
        SeleniumMCP({"grid": GRID, "recording": {"enabled": True}})


def test_a_refused_config_stops_before_anything_is_built(tmp_path):
    """The checks run before the store pings Redis and before the data folder
    is touched: the refusal is the one named, and `sessions/` stays put."""
    oidc = {"issuer": "https://id.example.com", "audience": "a",
            "jwks_uri": "https://id.example.com/jwks"}
    with pytest.raises(ConfigError, match=r"^oidc needs auth.token"):
        SeleniumMCP({"grid": GRID, "oidc": oidc, "workspace": {"store": "redis"},
                     "redis": {"host": "127.0.0.1", "port": 1}})
    (tmp_path / "sessions" / "w").mkdir(parents=True)
    with pytest.raises(ConfigError, match=r"must not overlap"):
        SeleniumMCP({"grid": GRID, "data": {"dir": str(tmp_path)},
                     "recording": {"enabled": True, "dir": str(tmp_path / "workspaces")}})
    assert (tmp_path / "sessions" / "w").is_dir()
    assert not (tmp_path / "workspaces").exists()


def test_the_config_capabilities_and_version_reach_every_open():
    """`session.capabilities` is its own source, beside the cascade (spec
    2026-10-26-session-capabilities): the actions hold it for every build.
    `session.version` is the cascade's floor, like `session.browser`."""
    server = SeleniumMCP({"grid": GRID,
                          "session": {"version": "152", "capabilities": {"kubed:pool": "default"}}})
    assert server.actions.config_capabilities == {"kubed:pool": "default"}
    assert server.workspaces.defaults == {"version": "152"}


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


CREDENTIALED = {
    "GRID_URL": "http://gu:hunter1@grid.invalid:4444",
    "GRID_CONSOLE_URL": "https://cu:hunter2@console.example.com/ui",
    "PUBLIC_BASE_URL": "https://pu:hunter3@flow.example.com",
    "SESSION_CAPABILITIES": json.dumps({
        "proxy": {"proxyType": "manual", "httpProxy": "xu:hunter4@proxy.example.com:3128"},
        "goog:chromeOptions": {"args": ["--proxy-server=http://au:hunter5@proxy.example.com:3128"]},
    }),
}


def test_the_startup_log_never_shows_a_urls_credentials(monkeypatch, caplog):
    """A URL setting may carry userinfo (`GRID_URL=http://user:pass@hub:4444`),
    and the log is not a secret store. Both lines a boot writes are read:
    main's own, and the `config:` line kubed-mcp logs as the lifespan enters,
    which `run` here enters for real."""

    async def enter(server):
        app = server.mcp.server.http_app()
        async with app.router.lifespan_context(app):
            pass

    monkeypatch.setattr(SeleniumMCP, "run", lambda self, **kw: asyncio.run(enter(self)))
    for name, value in CREDENTIALED.items():
        monkeypatch.setenv(name, value)
    with caplog.at_level(logging.INFO):
        main_module.main([])
    [boot] = [r.getMessage() for r in caplog.records if r.name == "kubed.mcp.config"]
    urls = dict(re.findall(r'([\w.]+)="([^"]*)"', boot))
    assert urls == {
        "public_base_url": "https://flow.example.com",
        "grid.url": "http://grid.invalid:4444",
        "grid.console_url": "https://console.example.com/ui",
    }
    capabilities = json.loads(re.search(r"session\.capabilities=(\{.*\}) \(env", boot)[1])
    assert capabilities["proxy"]["httpProxy"] == "proxy.example.com:3128"
    assert capabilities["goog:chromeOptions"]["args"] == ["--proxy-server=http://proxy.example.com:3128"]
    [started] = [r.getMessage() for r in caplog.records
                 if r.name == "kubed.selenium_flow.main" and r.getMessage().startswith("config=")]
    assert dict(word.split("=", 1) for word in started.split() if "=" in word)["grid"] == (
        "http://grid.invalid:4444"
    )
    assert not re.search(r"hunter\d|[gcpxa]u:", caplog.text)


def test_the_boot_line_filter_is_installed_once_and_touches_nothing_else():
    SeleniumMCP({"grid": GRID})
    SeleniumMCP({"grid": GRID})
    logger = logging.getLogger("kubed.mcp.config")
    assert logger.filters.count(server_module.BOOT_LINE_FILTER) == 1
    other = logging.LogRecord("kubed.mcp.config", logging.INFO, "", 0,
                              "probe %s", ("http://u:p@grid.invalid",), None)
    broken = logging.LogRecord("kubed.mcp.config", logging.INFO, "", 0, "%s %s", ("one",), None)
    assert server_module.BOOT_LINE_FILTER.filter(other) is True
    assert other.getMessage() == "probe http://u:p@grid.invalid"
    assert server_module.BOOT_LINE_FILTER.filter(broken) is True


def test_the_startup_log_says_telemetry_off_by_default(monkeypatch, caplog):
    monkeypatch.setattr(SeleniumMCP, "run", lambda self, **kw: None)
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main([])
    assert "telemetry=off" in caplog.text


def test_the_startup_log_never_shows_the_profiles_address(monkeypatch, caplog):
    monkeypatch.setattr(SeleniumMCP, "run", lambda self, **kw: None)
    monkeypatch.setenv("TELEMETRY_PROFILES", "http://u:hunter2@pyroscope.example.com:4040")
    monkeypatch.setenv("TELEMETRY_OTEL", "true")
    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.main"):
        main_module.main([])
    assert "telemetry=profiles, otel" in caplog.text
    assert "pyroscope.example.com" not in caplog.text and "hunter2" not in caplog.text


def test_profiles_without_a_sampler_are_off_in_the_log():
    t = TelemetrySettings(profiles="http://pyroscope.example.com:4040",
                                 profile_cpu=False, profile_memory=False)
    assert main_module._telemetry_summary(t) == "off"


async def test_profiles_on_without_the_extra_still_serve(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "pyroscope", None)  # import fails as on a plain install
    server = SeleniumMCP({"grid": GRID, "auth": {"token": TOKEN},
                          "telemetry": {"profiles": "http://pyroscope.example.com:4040"}})
    with caplog.at_level(logging.WARNING):
        async with over_http(server) as client:
            assert await client.list_tools()
    assert "kubed-mcp[telemetry]" in caplog.text
