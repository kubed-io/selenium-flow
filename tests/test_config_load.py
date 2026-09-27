"""The loader: defaults < config < env < args, and where every value came from."""

import pytest

from kubed.selenium_flow import config
from kubed.selenium_flow.config import ConfigError, load

pytestmark = pytest.mark.unit


def _file(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_nothing_set_is_every_default():
    loaded = load([], {})
    assert loaded.settings.port == 8000
    assert set(loaded.sources.values()) == {"default"}


def test_precedence_per_leaf(tmp_path):
    path = _file(tmp_path, "port: 1\nsession:\n  ttl: 1\nredis:\n  port: 1\n")
    loaded = load(["--port", "3", "--config-file", path], {"PORT": "2", "SESSION_TTL": "2", "REDIS_HOST": "h"})
    s, src = loaded.settings, loaded.sources
    assert (s.port, src["port"]) == (3, "args")
    assert (s.session.ttl, src["session.ttl"]) == (2, "env")
    # A partial section survives another layer setting a sibling.
    assert (s.redis.port, src["redis.port"]) == (1, "config")
    assert (s.redis.host, src["redis.host"]) == ("h", "env")
    assert src["config_file"] == "args"


def test_the_config_file_comes_from_env_when_no_flag(tmp_path):
    path = _file(tmp_path, "log_level: DEBUG\n")
    loaded = load([], {"CONFIG_FILE": path})
    assert loaded.settings.log_level == "DEBUG"
    assert loaded.sources["log_level"] == "config"
    assert loaded.settings.config_file == path


def test_session_store_is_derived_from_a_redis_setting():
    assert load([], {}).settings.session.store == "memory"
    assert load([], {"REDIS_HOST": "r"}).settings.session.store == "redis"
    assert load(["--redis-url", "redis://r:6379"], {}).settings.session.store == "redis"
    assert load([], {"REDIS_HOST": "r", "SESSION_STORE": "memory"}).settings.session.store == "memory"
    # A port alone configures nothing: it is not host or url, so naming only
    # it must not flip the store to redis.
    assert load([], {"REDIS_PORT": "6380"}).settings.session.store == "memory"


def test_a_derived_redis_store_reports_where_redis_was_set(tmp_path):
    # A store derived from Redis must say where Redis was set, not "default".
    assert load([], {"REDIS_HOST": "r"}).sources["session.store"] == "env"
    assert load(["--redis-url", "redis://r:6379"], {}).sources["session.store"] == "args"

    path = _file(tmp_path, "redis:\n  host: h\n")
    assert load([], {"CONFIG_FILE": path}).sources["session.store"] == "config"

    # host from config, url from env: env outranks config.
    loaded = load([], {"CONFIG_FILE": path, "REDIS_URL": "redis://r:6379"})
    assert loaded.sources["session.store"] == "env"

    # No redis setting at all: memory IS the default, so the source stays default.
    loaded = load([], {})
    assert loaded.settings.session.store == "memory"
    assert loaded.sources["session.store"] == "default"

    # Explicit SESSION_STORE=memory alongside REDIS_HOST: the explicit env
    # value wins, and its own source is reported, not derived.
    loaded = load([], {"REDIS_HOST": "r", "SESSION_STORE": "memory"})
    assert loaded.settings.session.store == "memory"
    assert loaded.sources["session.store"] == "env"


@pytest.mark.parametrize("name", ["FLOW_UI_PORT", "MCP_KB_PORT", "SESSION_FOO_PORT", "BROWSER", "SELENIUM_FLOW_PORT", "SECRETS_ENTRIES"])
def test_unknown_env_names_are_ignored(name):
    assert load([], {name: "tcp://10.0.0.1:80"}).settings == config.Settings()


def test_browser_is_not_read_from_the_unix_convention_env_var():
    """`BROWSER` is a Unix convention for the user's preferred browser command,
    and plenty of environments — code-server among them — set it to a shell
    script. `session.browser`'s env name is `SESSION_BROWSER`, so this is just
    an unknown name being ignored, but it is worth pinning by itself: reading
    `BROWSER` would break the server for reasons that have nothing to do with
    it."""
    assert load([], {"BROWSER": "/usr/bin/xdg-open"}).settings.session.browser is None


def test_an_unusable_session_browser_stops_the_boot():
    """Unlike the two default sources `session/settings.py` merges on top of
    this, the config itself is strict: a typo here is the operator's, not a
    client's, and refusing the boot says so with the reason (§F4.12)."""
    with pytest.raises(ConfigError, match=r"session\.browser"):
        load([], {"SESSION_BROWSER": "nonsense"})


@pytest.mark.parametrize("name", [n for n in config.SECTION_ORDER if n != "server"])
def test_a_bare_section_name_in_env_is_ignored_not_a_crash(name):
    assert load([], {name.upper(): "x"}).settings == config.Settings()


def test_env_names_are_case_insensitive():
    assert load([], {"session_ttl": "5"}).settings.session.ttl == 5


def test_secrets_dirs_from_env_and_args():
    import os
    assert load([], {"SECRETS_DIRS": f"/a{os.pathsep}/b"}).settings.secrets.dirs == ["/a", "/b"]
    assert load(["--secrets-dirs", "/c"], {}).settings.secrets.dirs == ["/c"]


def test_a_blank_secrets_dir_in_the_config_file_is_dropped(tmp_path):
    # The YAML list form: a lone blank entry must mean "no dirs", the same as
    # an unset SECRETS_DIRS, not a FilesystemSource("") over the cwd.
    path = _file(tmp_path, 'secrets:\n  dirs:\n  - ""\n')
    assert load([], {"CONFIG_FILE": path}).settings.secrets.dirs == []


@pytest.mark.parametrize("body", [
    "secrets:\n  dirs:\n",
    "secrets:\n  dirs: 5\n",
    "secrets:\n  dirs: [5]\n",
])
def test_a_malformed_secrets_dirs_is_a_config_error_not_a_crash(tmp_path, body):
    path = _file(tmp_path, body)
    with pytest.raises(ConfigError, match=r"secrets\.dirs"):
        load([], {"CONFIG_FILE": path})


@pytest.mark.parametrize("body, field", [
    ("flow:\n  data_dir: 1\n", r"flow\.data_dir"),
    ("log_level: 5\n", "log_level"),
    ("session:\n  browser: 5\n", r"session\.browser"),
])
def test_a_wrong_typed_before_validated_setting_is_a_config_error_not_a_crash(tmp_path, body, field):
    """Every `mode="before"` validator in config.py must tolerate non-string
    input and hand it to pydantic's own type check, the same as `_split`
    (`dirs`) was fixed to. `data_dir`'s validator is `mode="after"`, so
    pydantic's own str|None check already rejects a non-string before the
    validator ever sees it — this locks that in alongside the two that do
    run `mode="before"` (`browser`, `log_level`)."""
    path = _file(tmp_path, body)
    with pytest.raises(ConfigError, match=field):
        load([], {"CONFIG_FILE": path})


def test_booleans_take_a_value_on_the_command_line():
    assert load([], {"MCP_SKILL": "false"}).settings.mcp.skill is False
    assert load(["--mcp-apps", "false"], {}).settings.mcp.apps is False


def test_mcp_kb_env_vars_do_not_collide_with_the_mcp_section():
    """Kubernetes injects `MCP_KB_PORT` and `MCP_KB_SERVICE_HOST` for the
    sibling `mcp-kb` service. `mcp` is now a real section, but `kb_port` and
    `kb_service_host` are not leaves under it, so both are dropped."""
    loaded = load([], {
        "MCP_KB_PORT": "tcp://10.0.0.1:8000",
        "MCP_KB_SERVICE_HOST": "10.0.0.1",
    })
    assert loaded.settings == config.Settings()


@pytest.mark.parametrize("text,needle", [
    ("redis:\n  bogus: 1\n", "redis.bogus"),
    ("- a\n- b\n", "mapping"),
    ("session: [\n", "line"),
    ("config_file: /x\n", "config_file"),
    ("secrets:\n  entries:\n    d:\n      allowed_urls: [staging.internal]\n", "bare origin"),
])
def test_a_bad_file_stops_the_boot_and_says_why(tmp_path, text, needle):
    with pytest.raises(ConfigError, match=needle):
        load(["--config-file", _file(tmp_path, text)], {})


def test_a_named_file_that_is_missing_stops_the_boot(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load(["--config-file", str(tmp_path / "nope.yaml")], {})


def test_a_named_directory_is_reported_as_not_a_file(tmp_path):
    """A directory exists, so "does not exist" would be a lie; say what is
    actually wrong with it."""
    with pytest.raises(ConfigError, match="is not a file"):
        load(["--config-file", str(tmp_path)], {})


def test_a_bad_env_value_names_the_variable():
    with pytest.raises(ConfigError, match="SESSION_TTL"):
        load([], {"SESSION_TTL": "soon"})


def test_a_validation_error_never_echoes_the_rejected_input():
    """Renamed from `..._a_sensitive_value`: no sensitive leaf can actually fail
    validation (`SecretStr` accepts any string), so `REDIS_PORT` was never
    testing sensitivity — it was testing `hide_input_in_errors` on any leaf,
    which is what this now says."""
    with pytest.raises(ConfigError) as caught:
        load([], {"REDIS_PORT": "hunter2"})
    assert "hunter2" not in str(caught.value)


def test_an_empty_redis_env_value_counts_as_unset():
    """`REDIS_URL=""` and `REDIS_HOST=""` are what an unset docker-compose
    interpolation (`${REDIS_URL:-}`) actually sends — not absence."""
    loaded = load([], {"REDIS_URL": "", "REDIS_HOST": ""})
    assert loaded.settings.session.store == "memory"
    assert loaded.sources["redis.url"] == "default"
    assert loaded.sources["redis.host"] == "default"


def test_an_empty_auth_token_reads_as_not_set(tmp_path):
    loaded = load([], {"AUTH_TOKEN": ""})
    assert loaded.settings.auth.token is None
    body = config.describe(loaded.settings, loaded.sources)
    row = next(r for s in body["sections"] for r in s["settings"] if r["key"] == "auth.token")
    assert row["set"] is False


def test_an_empty_auth_token_env_leaves_auth_off():
    from kubed.selenium_flow.server import SeleniumMCP

    loaded = load([], {"AUTH_TOKEN": ""})
    server = SeleniumMCP(loaded.settings, sources=loaded.sources)
    assert server.auth_token is None


def test_an_empty_arg_value_also_counts_as_unset():
    loaded = load(["--auth-token", ""], {})
    assert loaded.settings.auth.token is None
    assert loaded.sources["auth.token"] == "default"


def test_a_blank_redis_host_in_the_config_file_counts_as_unset(tmp_path):
    # Env and args already dropped a blank value before it ever reached
    # `_has`/`_merge`; the file layer did not, so this used to make
    # `redis.host`'s source "config" and drive the store to redis anyway.
    path = _file(tmp_path, 'redis:\n  host: ""\n')
    loaded = load([], {"CONFIG_FILE": path})
    assert loaded.settings.session.store == "memory"
    assert loaded.sources["redis.host"] == "default"


def test_a_blank_log_level_in_the_config_file_is_the_default(tmp_path):
    path = _file(tmp_path, 'log_level: ""\n')
    loaded = load([], {"CONFIG_FILE": path})
    assert loaded.settings.log_level == "INFO"
    assert loaded.sources["log_level"] == "default"


def test_a_whitespace_only_redis_url_in_the_config_file_counts_as_unset(tmp_path):
    path = _file(tmp_path, 'redis:\n  url: "  "\n')
    loaded = load([], {"CONFIG_FILE": path})
    assert loaded.settings.session.store == "memory"
    assert loaded.sources["redis.url"] == "default"


def test_the_file_cannot_define_entries_through_env(tmp_path):
    path = _file(tmp_path, "secrets:\n  entries:\n    d:\n      keys:\n        k: {env: X}\n")
    loaded = load(["--config-file", path], {})
    assert "d" in loaded.settings.secrets.entries


def test_an_abbreviated_flag_is_refused():
    """`allow_abbrev=False`: `--redis-h` must not silently match `--redis-host`,
    the way argparse's default prefix-matching would let it."""
    with pytest.raises(SystemExit):
        load(["--redis-h", "x"], {})


@pytest.mark.parametrize("value", ["debug", "Debug", "DEBUG"])
def test_log_level_is_case_insensitive(value):
    assert load([], {"LOG_LEVEL": value}).settings.log_level == "DEBUG"


def test_a_bad_log_level_stops_the_boot():
    with pytest.raises(ConfigError, match="log_level"):
        load([], {"LOG_LEVEL": "loud"})


def test_help_names_all_three_spellings(capsys):
    with pytest.raises(SystemExit):
        load(["--help"], {})
    out = capsys.readouterr().out
    assert "--session-ttl" in out and "SESSION_TTL" in out and "session.ttl" in out
    assert "--secrets-entries" not in out


def test_sources_for_code_built_settings():
    s = config.Settings(port=9)
    src = config.sources_for(s)
    assert src["port"] == "args" and src["host"] == "default"


def test_describe_is_the_payload_the_tab_renders(tmp_path):
    path = _file(tmp_path, "redis:\n  db: 2\n")
    loaded = load(["--config-file", path], {"AUTH_TOKEN": "t0ken"})
    body = config.describe(loaded.settings, loaded.sources)
    assert [s["name"] for s in body["sections"]] == config.SECTION_ORDER
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert rows["redis.db"] == {"key": "redis.db", "name": "db", "description": "Redis database number.", "value": 2, "source": "config"}
    assert rows["auth.token"] == {"key": "auth.token", "name": "token", "description": "Bearer token for every request. Unset is open.", "value": None, "source": "env", "sensitive": True, "set": True}
    assert rows["redis.password"]["set"] is False
    assert "secrets.entries" not in rows
    assert "t0ken" not in str(body)


def test_describe_never_shows_a_grid_urls_credentials():
    """`GRID_URL` may carry userinfo, and `/admin/settings` answers to anyone
    with the token — the same reason `errors.without_userinfo` strips it from
    a failure message (Copilot)."""
    loaded = load([], {"GRID_URL": "http://u:hunter2@hub:4444"})
    body = config.describe(loaded.settings, loaded.sources)
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert "hunter2" not in str(body)
    assert rows["grid.url"]["value"] == "http://hub:4444"


def test_describe_keeps_the_default_console_urls_path():
    """`grid.console_url` defaults to `/`, and the live server serves it there
    — `without_userinfo` (unlike `browser.public_url`) never rewrites a path,
    so the Settings tab must not show `""` for it (Copilot)."""
    loaded = load([], {})
    body = config.describe(loaded.settings, loaded.sources)
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert rows["grid.console_url"]["value"] == "/"
