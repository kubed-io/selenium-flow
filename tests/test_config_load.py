"""The loader: defaults < file < env < args, and where every value came from."""

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
    assert (s.redis.port, src["redis.port"]) == (1, "file")
    assert (s.redis.host, src["redis.host"]) == ("h", "env")
    assert src["config_file"] == "args"


def test_the_config_file_comes_from_env_when_no_flag(tmp_path):
    path = _file(tmp_path, "log_level: DEBUG\n")
    loaded = load([], {"CONFIG_FILE": path})
    assert loaded.settings.log_level == "DEBUG"
    assert loaded.sources["log_level"] == "file"
    assert loaded.settings.config_file == path


def test_session_store_is_derived_from_a_redis_setting():
    assert load([], {}).settings.session.store == "memory"
    assert load([], {"REDIS_HOST": "r"}).settings.session.store == "redis"
    assert load(["--redis-url", "redis://r:6379"], {}).settings.session.store == "redis"
    assert load([], {"REDIS_HOST": "r", "SESSION_STORE": "memory"}).settings.session.store == "memory"
    # A port alone configures nothing: it is not host or url, so naming only
    # it must not flip the store to redis.
    assert load([], {"REDIS_PORT": "6380"}).settings.session.store == "memory"


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


def test_booleans_take_a_value_on_the_command_line():
    assert load(["--skill-enabled", "false"], {}).settings.skill.enabled is False
    assert load([], {"APPS_ENABLED": "off"}).settings.apps.enabled is False


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
    assert body["config_file"] == path
    assert [s["name"] for s in body["sections"]] == config.SECTION_ORDER
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert rows["redis.db"] == {"key": "redis.db", "name": "db", "description": "Redis database number.", "value": 2, "source": "file", "file": path}
    assert rows["auth.token"] == {"key": "auth.token", "name": "token", "description": "Bearer token for every request. Unset is open.", "value": None, "source": "env", "sensitive": True, "set": True}
    assert rows["redis.password"]["set"] is False
    assert "secrets.entries" not in rows
    assert "t0ken" not in str(body)
