"""The load, by kubed-mcp: defaults < config < env < args, and where every value came from."""

import os
import re

import pytest
from kubed.mcp import Source

from kubed.selenium_flow import config

from .conftest import configured, loaded

pytestmark = pytest.mark.unit


def _file(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_nothing_set_is_every_default():
    cfg, sources = loaded()
    assert cfg.serving.port == 8000
    assert set(sources.values()) == {Source("default")}


def test_precedence_per_leaf(tmp_path):
    path = _file(tmp_path, "serving:\n  port: 1\nworkspace:\n  ttl: 1\nredis:\n  port: 1\n")
    s, src = loaded(["--port", "3", "--config-file", path],
                    {"PORT": "2", "WORKSPACE_TTL": "2", "REDIS_HOST": "h"})
    assert (s.serving.port, src["serving.port"]) == (3, Source("arg", "--port"))
    assert (s.workspace.ttl, src["workspace.ttl"]) == (2, Source("env", "WORKSPACE_TTL"))
    # A partial section survives another layer setting a sibling.
    assert (s.redis.port, src["redis.port"]) == (1, Source("file", path))
    assert (s.redis.host, src["redis.host"]) == ("h", Source("env", "REDIS_HOST"))
    assert config.config_file(src) == path


def test_a_malformed_section_in_the_file_is_refused_whatever_env_sets(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("redis: broken\n")
    with pytest.raises(ValueError, match=re.escape(f"redis (file {path})")):
        loaded(["--config-file", str(path)], {"REDIS_HOST": "r.example.com"})


def test_a_malformed_section_in_the_file_stops_the_boot_even_when_args_set_its_keys(tmp_path):
    path = _file(tmp_path, "workspace: 5\n")
    with pytest.raises(ValueError, match="workspace"):
        loaded(["--workspace-ttl", "3"], {"CONFIG_FILE": path})


def test_the_config_file_comes_from_env_when_no_flag(tmp_path):
    path = _file(tmp_path, "serving:\n  log_level: DEBUG\n")
    cfg, sources = loaded(environ={"CONFIG_FILE": path})
    assert cfg.serving.log_level == "DEBUG"
    assert sources["serving.log_level"] == Source("file", path)
    assert config.config_file(sources) == path


def test_session_store_is_derived_from_a_redis_setting():
    assert loaded()[0].workspace.store == "memory"
    assert loaded(environ={"REDIS_HOST": "r"})[0].workspace.store == "redis"
    assert loaded(["--redis-url", "redis://r:6379"])[0].workspace.store == "redis"
    assert loaded(environ={"REDIS_HOST": "r", "WORKSPACE_STORE": "memory"})[0].workspace.store == "memory"
    # A port alone configures nothing: it is not host or url, so naming only
    # it must not flip the store to redis.
    assert loaded(environ={"REDIS_PORT": "6380"})[0].workspace.store == "memory"


def test_an_explicit_store_beside_redis_reports_its_own_source():
    # No redis setting at all: memory IS the default, so the source stays default.
    cfg, sources = loaded()
    assert cfg.workspace.store == "memory"
    assert sources["workspace.store"] == Source("default")

    # Explicit WORKSPACE_STORE=memory alongside REDIS_HOST: the explicit env
    # value wins, and its own source is reported, not derived.
    cfg, sources = loaded(environ={"REDIS_HOST": "r", "WORKSPACE_STORE": "memory"})
    assert cfg.workspace.store == "memory"
    assert sources["workspace.store"] == Source("env", "WORKSPACE_STORE")


@pytest.mark.parametrize("name", ["FLOW_UI_PORT", "MCP_KB_PORT", "SESSION_FOO_PORT", "BROWSER", "SELENIUM_FLOW_PORT", "SECRETS_ENTRIES"])
def test_unknown_env_names_are_ignored(name):
    assert loaded(environ={name: "tcp://10.0.0.1:80"})[0].model_dump() == configured().model_dump()


def test_browser_is_not_read_from_the_unix_convention_env_var():
    """`BROWSER` is a Unix convention for the user's preferred browser command,
    and plenty of environments — code-server among them — set it to a shell
    script. `session.browser`'s env name is `SESSION_BROWSER`, so this is just
    an unknown name being ignored, but it is worth pinning by itself: reading
    `BROWSER` would break the server for reasons that have nothing to do with
    it."""
    assert loaded(environ={"BROWSER": "/usr/bin/xdg-open"})[0].session.browser is None


@pytest.mark.parametrize(
    ("refusal", "env"),
    [
        # Unlike the two default sources `workspace/settings.py` merges on top of
        # this, the config itself is strict: a typo here is the operator's, not a
        # client's, and refusing the boot says so with the reason.
        pytest.param(
            r"session\.browser",
            {"SESSION_BROWSER": "nonsense"},
            id="an-unusable-session-browser-stops-the-boot",
        ),
        pytest.param(
            "WORKSPACE_TTL", {"WORKSPACE_TTL": "soon"}, id="a-bad-env-value-names-the-variable"
        ),
        pytest.param("log_level", {"LOG_LEVEL": "loud"}, id="a-bad-log-level-stops-the-boot"),
        pytest.param(
            r"live\.grace", {"LIVE_GRACE": "10"}, id="a-live-grace-under-fifteen-is-refused"
        ),
    ],
)
def test_an_unusable_value_stops_the_boot_naming_it(refusal, env):
    with pytest.raises(ValueError, match=refusal):
        loaded(environ=env)


@pytest.mark.parametrize("name", [n for n in config.SECTION_ORDER if n != "server"])
def test_a_bare_section_name_in_env_is_ignored_not_a_crash(name):
    assert loaded(environ={name.upper(): "x"})[0].model_dump() == configured().model_dump()


def test_env_names_match_exactly():
    assert loaded(environ={"redis_host": "r.example.com"})[0].redis.host == "localhost"


def test_secrets_dirs_from_env_and_args():
    assert loaded(environ={"SECRETS_DIRS": f"/a{os.pathsep}/b"})[0].secrets.dirs == ["/a", "/b"]
    assert loaded(["--secrets-dirs", "/c"])[0].secrets.dirs == ["/c"]


def test_a_list_from_env_is_split_not_decoded():
    cfg, _ = loaded(environ={"SECRETS_DIRS": f"/a{os.pathsep}/b", "OIDC_ROLES": "a, b,"})
    assert (cfg.secrets.dirs, cfg.oidc.roles) == (["/a", "/b"], ["a", "b"])


def test_a_blank_secrets_dir_in_the_config_file_is_dropped(tmp_path):
    # The YAML list form: a lone blank entry must mean "no dirs", the same as
    # an unset SECRETS_DIRS, not a FilesystemSource("") over the cwd.
    path = _file(tmp_path, 'secrets:\n  dirs:\n  - ""\n')
    assert loaded(environ={"CONFIG_FILE": path})[0].secrets.dirs == []


@pytest.mark.parametrize("body", [
    "secrets:\n  dirs: 5\n",
    "secrets:\n  dirs: [5]\n",
])
def test_a_malformed_secrets_dirs_is_a_config_error_not_a_crash(tmp_path, body):
    path = _file(tmp_path, body)
    with pytest.raises(ValueError, match=r"secrets\.dirs"):
        loaded(environ={"CONFIG_FILE": path})


@pytest.mark.parametrize("body, field", [
    ("data:\n  dir: 1\n", r"data\.dir"),
    ("serving:\n  log_level: 5\n", r"serving\.log_level"),
    ("session:\n  browser: 5\n", r"session\.browser"),
])
def test_a_wrong_typed_before_validated_setting_is_a_config_error_not_a_crash(tmp_path, body, field):
    """Every `mode="before"` validator in config.py must tolerate non-string
    input and hand it to pydantic's own type check, the same as `_split`
    (`dirs`) was fixed to. `dir`'s validator is `mode="after"`, so
    pydantic's own str|None check already rejects a non-string before the
    validator ever sees it — this locks that in alongside the two that do
    run `mode="before"` (`browser`, `log_level`)."""
    path = _file(tmp_path, body)
    with pytest.raises(ValueError, match=field):
        loaded(environ={"CONFIG_FILE": path})


def test_booleans_take_a_value_on_the_command_line():
    assert loaded(environ={"MCP_APPS": "false"})[0].mcp.apps is False
    assert loaded(["--mcp-apps", "false"])[0].mcp.apps is False


def test_there_is_no_skill_toggle(tmp_path):
    """The plugin is always served (kubed-mcp ruling 28). `mcp.skill` is an
    unknown key now: the file refuses it, the command line has no flag for it,
    and the env drops it as it drops every name it does not know."""
    with pytest.raises(ValueError, match="skill"):
        loaded(environ={"CONFIG_FILE": _file(tmp_path, "mcp:\n  skill: false\n")})
    with pytest.raises(ValueError, match="--mcp-skill"):
        loaded(["--mcp-skill", "false"])
    assert "skill" not in loaded(environ={"MCP_SKILL": "false"})[0].mcp.model_dump()


def test_mcp_kb_env_vars_do_not_collide_with_the_mcp_section():
    """Kubernetes injects `MCP_KB_PORT` and `MCP_KB_SERVICE_HOST` for the
    sibling `mcp-kb` service. `mcp` is now a real section, but `kb_port` and
    `kb_service_host` are not leaves under it, so both are dropped."""
    cfg, _ = loaded(environ={
        "MCP_KB_PORT": "tcp://10.0.0.1:8000",
        "MCP_KB_SERVICE_HOST": "10.0.0.1",
    })
    assert cfg.model_dump() == configured().model_dump()


@pytest.mark.parametrize("text,needle", [
    ("redis:\n  bogus: 1\n", "redis.bogus"),
    ("- a\n- b\n", "mapping"),
    ("session: [\n", "line"),
    ("config_file: /x\n", "config_file"),
    ("secrets:\n  entries:\n    d:\n      allowed_urls: [staging.internal]\n", "bare origin"),
])
def test_a_bad_file_stops_the_boot_and_says_why(tmp_path, text, needle):
    with pytest.raises(ValueError, match=needle):
        loaded(["--config-file", _file(tmp_path, text)])


def test_a_named_file_that_is_missing_stops_the_boot(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        loaded(["--config-file", str(tmp_path / "nope.yaml")])


def test_a_named_directory_is_reported_as_not_a_file(tmp_path):
    """A directory exists, so "does not exist" would be a lie; say what is
    actually wrong with it."""
    with pytest.raises(ValueError, match="is not a file"):
        loaded(["--config-file", str(tmp_path)])


def test_a_validation_error_never_echoes_the_rejected_input():
    """Renamed from `..._a_sensitive_value`: no sensitive leaf can actually fail
    validation (`SecretStr` accepts any string), so `REDIS_PORT` was never
    testing sensitivity — it was testing `hide_input_in_errors` on any leaf,
    which is what this now says."""
    with pytest.raises(ValueError) as caught:
        loaded(environ={"REDIS_PORT": "hunter2"})
    assert "hunter2" not in str(caught.value)


def test_a_load_error_names_the_path_and_the_layer_and_never_the_value():
    with pytest.raises(ValueError) as caught:
        loaded(environ={"PORT": "hunter2", "SESSION_CAPABILITIES": "{hunter2",
                        "REDIS_PASSWORD": "hunter2"})
    text = str(caught.value)
    assert text.startswith("the config did not load:\n")
    assert "  serving.port (env PORT): " in text
    assert "  session.capabilities (env SESSION_CAPABILITIES): must be a JSON object" in text
    assert "hunter2" not in text


def test_an_empty_redis_env_value_counts_as_unset():
    """`REDIS_URL=""` and `REDIS_HOST=""` are what an unset docker-compose
    interpolation (`${REDIS_URL:-}`) actually sends — not absence."""
    cfg, sources = loaded(environ={"REDIS_URL": "", "REDIS_HOST": ""})
    assert cfg.workspace.store == "memory"
    assert sources["redis.url"] == Source("default")
    assert sources["redis.host"] == Source("default")


def test_an_empty_auth_token_reads_as_not_set(tmp_path):
    cfg, sources = loaded(environ={"AUTH_TOKEN": ""})
    assert cfg.auth.token is None
    body = config.describe(cfg, sources)
    row = next(r for s in body["sections"] for r in s["settings"] if r["key"] == "auth.token")
    assert row["set"] is False


def test_an_empty_auth_token_env_leaves_auth_off():
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(argv=[], environ={"AUTH_TOKEN": ""})
    assert server.auth_token is None


def test_an_empty_arg_value_also_counts_as_unset():
    cfg, sources = loaded(["--auth-token", ""])
    assert cfg.auth.token is None
    assert sources["auth.token"] == Source("default")


@pytest.mark.parametrize(
    ("text", "key"),
    [
        pytest.param(
            'redis:\n  host: ""\n',
            "redis.host",
            id="a-blank-redis-host-in-the-config-file-counts-as-unset",
        ),
        pytest.param(
            'redis:\n  url: "  "\n',
            "redis.url",
            id="a-whitespace-only-redis-url-in-the-config-file-counts-as-unset",
        ),
    ],
)
def test_a_blank_redis_address_in_the_config_file_counts_as_unset(tmp_path, text, key):
    path = _file(tmp_path, text)
    cfg, sources = loaded(environ={"CONFIG_FILE": path})
    assert cfg.workspace.store == "memory"
    assert sources[key] == Source("default")


def test_a_blank_log_level_in_the_config_file_is_the_default(tmp_path):
    path = _file(tmp_path, 'serving:\n  log_level: ""\n')
    cfg, sources = loaded(environ={"CONFIG_FILE": path})
    assert cfg.serving.log_level == "INFO"
    assert sources["serving.log_level"] == Source("default")


def test_the_file_cannot_define_entries_through_env(tmp_path):
    path = _file(tmp_path, "secrets:\n  entries:\n    d:\n      keys:\n        k: {env: X}\n")
    assert "d" in loaded(["--config-file", path])[0].secrets.entries


def test_env_merges_into_the_file_s_object(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("telemetry:\n  tags:\n    team: browsers\n")
    cfg, _ = loaded(["--config-file", str(path)], {"TELEMETRY_TAGS": '{"env": "prod"}'})
    assert cfg.telemetry.tags == {"team": "browsers", "env": "prod"}


def test_a_retired_name_is_ignored_in_env_and_unknown_in_the_file(tmp_path):
    cfg, _ = loaded(environ={"FLOW_DATA_DIR": "/d", "SESSION_STORE": "redis"})
    assert cfg.data.dir is None and cfg.workspace.store == "memory"
    path = tmp_path / "c.yaml"
    path.write_text("session:\n  ttl: 5\n")
    with pytest.raises(ValueError, match=re.escape(f"session.ttl (file {path}): Extra inputs")):
        loaded(["--config-file", str(path)])


@pytest.mark.parametrize("argv, says", [
    (["--bogus", "1"], "the arguments (arg --bogus): no item takes this flag"),
    (["--redis-h", "r"], "no item takes this flag"),
])
def test_an_unknown_or_abbreviated_flag_is_refused(argv, says):
    with pytest.raises(ValueError, match=re.escape(says)):
        loaded(argv)


def test_a_config_file_key_inside_the_file_is_unknown(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("config_file: /x\n")
    with pytest.raises(ValueError, match=re.escape(f"config_file (file {path})")):
        loaded(["--config-file", str(path)])


@pytest.mark.parametrize("value", ["debug", "Debug", "DEBUG"])
def test_log_level_is_case_insensitive(value):
    assert loaded(environ={"LOG_LEVEL": value})[0].serving.log_level == "DEBUG"


def test_describe_is_the_payload_the_tab_renders(tmp_path):
    path = _file(tmp_path, "redis:\n  db: 2\n")
    cfg, sources = loaded(["--config-file", path], {"AUTH_TOKEN": "t0ken"})
    body = config.describe(cfg, sources)
    assert [s["name"] for s in body["sections"]] == config.SECTION_ORDER
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert rows["redis.db"] == {"key": "redis.db", "name": "db", "description": "Redis database number.", "value": 2, "source": "config"}
    assert rows["auth.token"] == {"key": "auth.token", "name": "token", "description": "Bearer token for every request. Unset is open.", "value": None, "source": "env", "sensitive": True, "set": True}
    assert rows["redis.password"]["set"] is False
    assert "secrets.entries" not in rows
    assert "t0ken" not in str(body)


def test_describe_never_shows_a_grid_urls_credentials():
    """`GRID_URL` may carry userinfo, and `/admin/settings` answers to anyone
    with the token — the same reason `urls.without_userinfo` strips it from
    a failure message (Copilot)."""
    cfg, sources = loaded(environ={"GRID_URL": "http://u:hunter2@hub:4444"})
    body = config.describe(cfg, sources)
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert "hunter2" not in str(body)
    assert rows["grid.url"]["value"] == "http://hub:4444"


def test_describe_keeps_the_default_console_urls_path():
    """`grid.console_url` defaults to `/`, and the live server serves it there
    — `without_userinfo` (unlike `urls.public_url`) never rewrites a path,
    so the Settings tab must not show `""` for it (Copilot)."""
    body = config.describe(*loaded())
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert rows["grid.console_url"]["value"] == "/"


def test_security_frame_ancestors_from_env_and_args():
    env = {"SECURITY_FRAME_ANCESTORS": "https://a.example,https://b.example"}
    assert loaded(environ=env)[0].security.frame_ancestors == [
        "https://a.example",
        "https://b.example",
    ]
    args = ["--security-frame-ancestors", "https://c.example"]
    assert loaded(args)[0].security.frame_ancestors == ["https://c.example"]


def test_data_dir_is_the_setting_and_reads_from_env():
    cfg, sources = loaded(environ={"DATA_DIR": "/srv/data"})
    assert cfg.data.dir == "/srv/data"
    assert sources["data.dir"] == Source("env", "DATA_DIR")


def test_recording_section_defaults_and_env():
    s = loaded(environ={"DATA_DIR": "/d", "RECORDING_ENABLED": "true"})[0]
    assert s.recording.enabled is True
    assert (s.recording.wait, s.recording.watch, s.recording.poll) == (600, "auto", 1000)
    assert s.recording.settle == 10


def test_recording_settle_reads_from_env_and_flag_and_zero_is_allowed():
    assert loaded(environ={"RECORDING_SETTLE": "0"})[0].recording.settle == 0
    assert loaded(["--recording-settle", "25"])[0].recording.settle == 25
    with pytest.raises(ValueError, match=r"recording\.settle"):
        loaded(environ={"RECORDING_SETTLE": "-1"})


def test_the_workspace_section_reads_from_env():
    assert loaded(environ={"WORKSPACE_TTL": "60"})[0].workspace.ttl == 60


def test_session_browser_is_still_a_session_setting():
    assert loaded(environ={"SESSION_BROWSER": "firefox"})[0].session.browser == "firefox"


def test_a_session_file_section_with_only_browser_settings_loads(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("session:\n  browser: firefox\n")
    assert loaded(["--config-file", str(path)])[0].session.browser == "firefox"


# ---- session.version and session.capabilities (spec 2026-10-26) -------------


def test_session_capabilities_and_version_come_from_env_as_json():
    cfg, sources = loaded(environ={
        "SESSION_CAPABILITIES": '{"kubed:pool": "default", "x:y": [1, {"z": null}]}',
        "SESSION_VERSION": " 152 ",
    })
    session = cfg.session
    assert session.capabilities == {"kubed:pool": "default", "x:y": [1, {"z": None}]}
    assert session.version == "152"
    assert sources["session.capabilities"] == Source("env", "SESSION_CAPABILITIES")


def test_session_capabilities_default_to_none_and_come_from_the_file(tmp_path):
    assert loaded()[0].session.capabilities == {}
    assert loaded()[0].session.version is None
    path = _file(tmp_path, "session:\n  capabilities:\n    kubed:pool: default\n")
    cfg, sources = loaded(["--config-file", path])
    assert cfg.session.capabilities == {"kubed:pool": "default"}
    assert sources["session.capabilities"] == Source("file", path)


def test_a_session_capabilities_string_in_the_file_is_parsed(tmp_path):
    path = _file(tmp_path, "session:\n  capabilities: '{\"kubed:pool\": \"default\"}'\n")
    assert loaded(["--config-file", path])[0].session.capabilities == {"kubed:pool": "default"}


def test_session_capabilities_come_from_the_command_line():
    cfg, _ = loaded(["--session-capabilities", '{"kubed:pool": "a"}'])
    assert cfg.session.capabilities == {"kubed:pool": "a"}


@pytest.mark.parametrize(
    ("env", "refusal"),
    [
        pytest.param(
            {"SESSION_CAPABILITIES": '{"browserName": "x"}'},
            "browserName cannot be sent in session.capabilities: use browser= instead",
            id="an-owned-key-in-session-capabilities-stops-the-boot",
        ),
        pytest.param(
            {"SESSION_CAPABILITIES": '{"moz:firefoxOptions": {"args": ["-headless"]}}'},
            "moz:firefoxOptions.args -headless cannot be sent in session.capabilities: "
            "use headless=true or headless=false instead",
            id="a-headless-flag-in-session-capabilities-stops-the-boot",
        ),
    ],
)
def test_a_key_the_server_owns_in_session_capabilities_stops_the_boot(env, refusal):
    with pytest.raises(ValueError) as refused:
        loaded(environ=env)
    assert refusal in str(refused.value)


@pytest.mark.parametrize("value", ["not json", "[1, 2]", '"a string"'])
def test_session_capabilities_that_are_not_an_object_stop_the_boot(value):
    with pytest.raises(ValueError, match=r"session\.capabilities"):
        loaded(environ={"SESSION_CAPABILITIES": value})


def test_a_blank_session_version_is_unset():
    assert loaded(environ={"SESSION_VERSION": "   "})[0].session.version is None


# ---- the live view (spec 2026-10-27-live-view) -------------------------------


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        pytest.param(
            {
                "LIVE_ENABLED": "false",
                "LIVE_PASSWORD_FILE": "/run/vnc/password",
                "LIVE_GRACE": "45",
            },
            (False, "/run/vnc/password", 45),
            id="the-live-section-loads-from-env",
        ),
        pytest.param({}, (True, None, 30), id="the-live-defaults"),
    ],
)
def test_the_live_section_loads(env, expected):
    s = loaded(environ=env)[0]
    assert (s.live.enabled, s.live.password_file, s.live.grace) == expected


def test_a_blank_live_password_file_is_none():
    assert loaded(environ={"LIVE_PASSWORD_FILE": "  "})[0].live.password_file is None


def test_the_live_section_reads_from_the_file(tmp_path):
    path = _file(tmp_path, "live:\n  password_file: /run/vnc/password\n  grace: 20\n")
    s = loaded(environ={"CONFIG_FILE": path})[0]
    assert (s.live.password_file, s.live.grace) == ("/run/vnc/password", 20)


# ---- telemetry ----

TELEMETRY = "http://pyroscope.example.com:4040"


def test_telemetry_comes_from_env():
    cfg, sources = loaded(environ={
        "TELEMETRY_PROFILES": TELEMETRY,
        "TELEMETRY_PROFILE_MEMORY": "false",
        "TELEMETRY_OTEL": "true",
        "TELEMETRY_TAGS": '{"cluster": "example"}',
    })
    t = cfg.telemetry
    assert (t.profiles.get_secret_value(), t.profile_cpu, t.profile_memory, t.otel) == (
        TELEMETRY, True, False, True)
    assert t.tags == {"cluster": "example"}
    assert sources["telemetry.tags"] == Source("env", "TELEMETRY_TAGS")


def test_telemetry_comes_from_flags_and_the_file(tmp_path):
    cfg, _ = loaded(["--telemetry-profiles", TELEMETRY, "--telemetry-tags", '{"a": "b"}'])
    assert cfg.telemetry.profiles.get_secret_value() == TELEMETRY
    assert cfg.telemetry.tags == {"a": "b"}
    path = _file(tmp_path, f"telemetry:\n  profiles: {TELEMETRY}\n  tags:\n    cluster: example\n")
    t = loaded(["--config-file", path])[0].telemetry
    assert (t.profiles.get_secret_value(), t.tags) == (TELEMETRY, {"cluster": "example"})


def test_a_blank_telemetry_profiles_is_off():
    assert loaded(environ={"TELEMETRY_PROFILES": "  "})[0].telemetry.profiles is None


@pytest.mark.parametrize("value", ["not json", "[1, 2]", '"a string"'])
def test_telemetry_tags_that_are_not_an_object_stop_the_boot(value):
    with pytest.raises(ValueError) as refused:
        loaded(environ={"TELEMETRY_TAGS": value})
    message = str(refused.value)
    assert "telemetry.tags" in message and "must be a JSON object" in message
    assert "TELEMETRY_TAGS" in message


def test_telemetry_tags_that_are_not_strings_stop_the_boot(tmp_path):
    with pytest.raises(ValueError, match=r"telemetry\.tags"):
        loaded(environ={"TELEMETRY_TAGS": '{"tier": 1}'})
    path = _file(tmp_path, "telemetry:\n  tags:\n    tier: 1\n")
    with pytest.raises(ValueError, match=r"telemetry\.tags"):
        loaded(["--config-file", path])


def test_a_typo_under_telemetry_in_the_file_stops_the_boot(tmp_path):
    path = _file(tmp_path, f"telemetry:\n  profile: {TELEMETRY}\n")
    with pytest.raises(ValueError, match=r"telemetry\.profile\b"):
        loaded(["--config-file", path])
