"""The schema: one setting, three spellings, and nothing read from the environment."""

import json
import os

import kubed.mcp
import pytest
from kubed.mcp import Source
from pydantic import ValidationError

from kubed.selenium_flow import config
from kubed.selenium_flow.config import (
    RedisSettings,
    SecretsSettings,
    SecuritySettings,
    ServingSettings,
    SessionSettings,
    describe,
    leaves,
)

from .conftest import configured, loaded, quick

pytestmark = pytest.mark.unit

SECTION_NAMES = [name for name in config.SECTION_ORDER if name != "server"]


def test_every_leaf_follows_the_naming_rule():
    for leaf in leaves():
        path = leaf.path.removeprefix("serving.")
        assert leaf.env == path.replace(".", "_").upper(), leaf.path
        assert leaf.flag == "--" + path.replace(".", "-").replace("_", "-"), leaf.path


def test_every_leaf_s_flag_is_one_kubed_mcp_reads():
    for leaf in leaves():
        loaded([leaf.flag, ""])  # an unknown flag is refused; a blank one is unset


def test_every_leaf_s_env_name_is_one_kubed_mcp_reads():
    for leaf in leaves():
        if leaf.path == "config_file" or leaf.default in (None, "", []):
            continue
        value = json.dumps(leaf.default) if isinstance(leaf.default, dict) else str(leaf.default)
        _, sources = loaded(environ={leaf.env: value})
        assert sources[leaf.path] == Source("env", leaf.env), leaf.path


def test_no_section_name_can_collide_with_an_env_prefix_already_in_use():
    for name in SECTION_NAMES:
        assert "_" not in name, name
        assert name not in {"browser", "selenium"}, name


def test_every_leaf_has_a_one_sentence_description():
    for leaf in leaves():
        assert leaf.description, leaf.path
        assert len(leaf.description) <= 100, (leaf.path, leaf.description)
        assert leaf.description.endswith("."), leaf.path


def test_every_section_has_a_description_and_an_order():
    assert config.SECTION_ORDER[0] == "server"
    assert set(config.SECTION_ORDER) == set(config.SECTION_DESCRIPTIONS)


def test_the_secret_definitions_are_not_a_leaf():
    """`secrets.entries` is structure: no env name, no flag, no row."""
    assert "secrets.entries" not in {leaf.path for leaf in leaves()}


def test_the_sensitive_settings_are_exactly_the_four():
    # The schema's set (config_schema's `sensitive: true`): a drift in kubed-mcp shows here.
    assert {leaf.path for leaf in leaves() if leaf.sensitive} == {
        "auth.token", "redis.url", "redis.password", "telemetry.profiles"}


def test_leaves_read_the_schema():
    by_path = {leaf.path: leaf for leaf in leaves()}
    assert (by_path["serving.port"].default, by_path["serving.transport"].default) == (8000, "http")
    assert by_path["serving.port"].description  # the doc=, through the schema
    assert by_path["session.capabilities"].default is None  # an object default the schema scrubs
    assert by_path["telemetry.profiles"].default is None
    assert "secrets.entries" not in by_path
    assert list(by_path)[:4] == ["config_file", "route_prefix", "public_base_url", "link_ttl"]


def test_defaults_need_no_environment(monkeypatch):
    monkeypatch.setenv("PORT", "9999")
    monkeypatch.setenv("WORKSPACE_TTL", "1")
    s = configured()
    assert s.serving.port == 8000
    assert s.workspace.ttl == 86400
    assert s.workspace.store == "memory"


def test_an_unknown_key_is_refused():
    with pytest.raises(ValidationError, match="bogus"):
        RedisSettings.model_validate({"bogus": 1})


def test_a_bad_browser_is_refused_and_a_good_one_normalised():
    assert SessionSettings(browser="Firefox").browser == "firefox"
    with pytest.raises(ValidationError):
        SessionSettings(browser="safari")


def test_secrets_dirs_split_like_path(tmp_path):
    s = SecretsSettings(dirs=f"/a{os.pathsep}/b{os.pathsep} ")
    assert s.dirs == ["/a", "/b"]


def test_secrets_dirs_as_a_list_drops_blanks_too():
    # The YAML list form must not bypass the same blank-dropping the string
    # (env/CLI) form gets — a blank/whitespace entry used to sail through and
    # FilesystemSource("") would resolve to the process's working directory.
    s = SecretsSettings(dirs=["", "  ", "/a"])
    assert s.dirs == ["/a"]


def test_a_blank_flow_dir_is_off():
    assert config.DataSettings(dir="   ").dir is None


def test_a_key_reference_is_exactly_one_of_file_env_value():
    entry = {"keys": {"a": {"file": "/f"}, "b": {"env": "X"}, "c": {"value": "v"}}}
    s = SecretsSettings(entries={"demo": entry})
    keys = s.entries["demo"].keys
    assert isinstance(keys["a"], config.FromFile)
    assert isinstance(keys["b"], config.FromEnv)
    assert isinstance(keys["c"], config.FromValue)
    for bad in ({"file": "/f", "env": "X"}, {}, {"path": "/f"}):
        with pytest.raises(ValidationError):
            SecretsSettings(entries={"demo": {"keys": {"k": bad}}})


def test_a_key_sets_masked_beside_its_value_or_alone():
    entry = {"keys": {"a": {"file": "/f", "masked": False}, "b": {"masked": False},
                      "c": {"env": "X"}}}
    keys = SecretsSettings(entries={"demo": entry}).entries["demo"].keys
    assert isinstance(keys["a"], config.FromFile) and keys["a"].masked is False
    assert isinstance(keys["b"], config.KeySettings) and keys["b"].masked is False
    assert keys["c"].masked is True
    bad_keys = ({"masked": False, "path": "/f"}, {"masked": "sometimes"},
                {"masked": "false"}, {"masked": 0}, {"file": "/f", "masked": "no"})
    for bad in bad_keys:
        with pytest.raises(ValidationError):
            SecretsSettings(entries={"demo": {"keys": {"k": bad}}})


def test_allowed_urls_must_be_bare_origins_and_the_error_never_echoes_credentials():
    ok = SecretsSettings(entries={"d": {"allowed_urls": ["https://Example.com/"]}})
    assert ok.entries["d"].allowed_urls == ["https://example.com"]
    with pytest.raises(ValidationError) as caught:
        SecretsSettings(entries={"d": {"allowed_urls": ["https://u:hunter2@x.com"]}})
    assert "hunter2" not in str(caught.value)


@pytest.mark.parametrize(
    "secrets",
    [
        pytest.param({"entries": {"../up": {}}}, id="a-secret-name-follows-the-directory-rule"),
        pytest.param(
            {"entries": {"d": {"keys": {"_allowed_urls": {"env": "X"}}}}},
            id="a-key-name-cannot-be-reserved",
        ),
    ],
)
def test_a_secret_entry_with_an_unusable_name_is_refused(secrets):
    with pytest.raises(ValidationError):
        SecretsSettings(**secrets)


def test_an_inline_value_never_reaches_a_repr():
    s = configured(auth={"token": "t0ken"}, secrets={"entries": {"d": {"keys": {"k": {"value": "s3cret"}}}}})
    assert "t0ken" not in repr(s) and "s3cret" not in repr(s)
    assert "s3cret" not in s.model_dump_json()


def test_value_of_reads_a_dotted_path():
    s = configured(redis={"db": 2})
    assert config.value_of(s, "redis.db") == 2
    assert config.value_of(s, "serving.port") == 8000


def test_security_frame_ancestors_is_a_comma_list_with_all_three_spellings():
    leaf = next(x for x in leaves() if x.path == "security.frame_ancestors")
    assert (leaf.env, leaf.flag) == (
        "SECURITY_FRAME_ANCESTORS",
        "--security-frame-ancestors",
    )
    assert SecuritySettings().frame_ancestors == []
    s = SecuritySettings(frame_ancestors="https://a.example, https://b:8443,")
    assert s.frame_ancestors == ["https://a.example", "https://b:8443"]
    assert SecuritySettings(
        frame_ancestors="https://*.example.com"
    ).frame_ancestors == ["https://*.example.com"]


@pytest.mark.parametrize(
    ("bad", "says"),
    [
        ("https://a.example; script-src *", "not an origin"),
        ("'self'", "not an origin"),
        ("https://\u20ac.example", "printable ASCII"),
        ("https://a.example\x00", "printable ASCII"),
        ("https://a.exa\tmple", "printable ASCII"),
        ("example.com", "scheme://host"),
        ("*", "empty by default"),
        ("none", "empty by default"),
        ("'none'", "empty by default"),
    ],
)
def test_frame_ancestors_refuses_what_is_not_an_origin(bad, says):
    with pytest.raises(ValidationError, match=says):
        SecuritySettings(frame_ancestors=[bad])


def test_the_redis_prefix_names_workspaces():
    assert RedisSettings().prefix == "selenium-flow:workspace:"


def test_keep_alive_is_fifteen_minutes_capped_at_an_hour_and_zero_is_off():  # rulings 3, 12
    assert SessionSettings().keep_alive == 900
    assert SessionSettings(keep_alive=0).keep_alive == 0
    assert SessionSettings(keep_alive=3600).keep_alive == 3600
    for bad in (-1, 3601):
        with pytest.raises(ValidationError):
            SessionSettings(keep_alive=bad)


def test_the_settings_tab_does_not_show_credentials_in_capabilities():
    proxy = {
        "proxy": {"proxyType": "manual", "httpProxy": "http://u:hunter2@proxy:3128"}
    }
    shown = describe(configured(session={"capabilities": proxy}), {})
    assert "hunter2" not in json.dumps(shown)


def test_a_negative_port_is_refused_at_load():
    with pytest.raises(ValueError):
        ServingSettings(port=-1)


def test_the_settings_tab_masks_a_proxy_with_no_scheme_and_keeps_an_email():
    capabilities = {
        "proxy": {"proxyType": "manual", "httpProxy": "u:hunter2@proxy:3128"},
        "goog:note": "mail drk@example.com",
    }
    shown = json.dumps(describe(configured(session={"capabilities": capabilities}), {}))
    assert "hunter2" not in shown
    assert "drk@example.com" in shown


def test_the_settings_tab_masks_a_socks_password_and_keeps_an_empty_one():
    def shown(password):
        proxy = {"proxyType": "manual", "socksProxy": "proxy:1080", "socksVersion": 5,
                 "socksUsername": "svc", "socksPassword": password}
        payload = describe(configured(session={"capabilities": {"proxy": proxy}}), {})
        [row] = [r for s in payload["sections"] for r in s["settings"]
                 if r["key"] == "session.capabilities"]
        return row["value"]["proxy"]

    assert shown("hunter2") == {"proxyType": "manual", "socksProxy": "proxy:1080",
                                "socksVersion": 5, "socksUsername": "svc", "socksPassword": "***"}
    assert shown("")["socksPassword"] == ""


def test_the_telemetry_section_is_kubed_mcps_and_strict_like_every_section():
    assert isinstance(configured().telemetry, kubed.mcp.TelemetrySettings)
    with pytest.raises(ValueError, match=r"telemetry\.bogus"):
        configured(telemetry={"bogus": 1})


def test_telemetry_defaults_are_all_off():
    t = configured().telemetry
    assert (t.profiles, t.otel, t.tags) == (None, False, {})
    assert (t.profile_cpu, t.profile_memory) == (True, True)


def test_the_telemetry_settings_and_their_spellings():
    found = {(x.path, x.env, x.flag) for x in leaves() if x.section == "telemetry"}
    assert found == {
        ("telemetry.profiles", "TELEMETRY_PROFILES", "--telemetry-profiles"),
        ("telemetry.profile_cpu", "TELEMETRY_PROFILE_CPU", "--telemetry-profile-cpu"),
        ("telemetry.profile_memory", "TELEMETRY_PROFILE_MEMORY", "--telemetry-profile-memory"),
        ("telemetry.otel", "TELEMETRY_OTEL", "--telemetry-otel"),
        ("telemetry.tags", "TELEMETRY_TAGS", "--telemetry-tags"),
    }


def test_telemetry_tags_keeps_kubed_mcps_description():
    upstream = kubed.mcp.TelemetrySettings.model_fields["tags"].description
    assert next(x for x in leaves() if x.path == "telemetry.tags").description == upstream


def test_the_tab_labels_kubed_mcp_s_sources(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("grid:\n  url: http://grid.example.com:4444\n")
    cfg, sources = loaded(["--config-file", str(path), "--link-ttl", "120"], {"REDIS_DB": "2"})
    payload = describe(cfg, sources)
    rows = {r["key"]: r for s in payload["sections"] for r in s["settings"]}
    assert [s["name"] for s in payload["sections"]] == config.SECTION_ORDER
    assert [r["key"] for r in payload["sections"][0]["settings"]] == [
        "config_file", "route_prefix", "public_base_url", "link_ttl"]
    assert (rows["config_file"]["value"], rows["config_file"]["source"]) == (str(path), "config")
    assert rows["grid.url"]["source"] == "config"
    assert rows["redis.db"]["source"] == "env"
    assert rows["link_ttl"]["source"] == "args"
    assert rows["auth.token"]["source"] == "default"


def test_code_reads_as_args_and_no_file_as_a_default():
    mcp = quick(config={"link_ttl": 120})
    rows = {r["key"]: r for s in describe(mcp.config, mcp.config_sources)["sections"]
            for r in s["settings"]}
    assert rows["link_ttl"]["source"] == "args"
    assert (rows["config_file"]["value"], rows["config_file"]["source"]) == (None, "default")


def test_telemetry_profiles_is_a_secret_on_the_tab():
    mcp = quick(config={"telemetry": {"profiles": "http://u:hunter2@pyroscope.example.com:4040"}})
    payload = describe(mcp.config, mcp.config_sources)
    rows = {r["key"]: r for s in payload["sections"] for r in s["settings"]}
    assert rows["telemetry.profiles"]["value"] is None
    assert rows["telemetry.profiles"]["sensitive"] is True and rows["telemetry.profiles"]["set"] is True
    assert "hunter2" not in json.dumps(payload) and "pyroscope.example.com" not in json.dumps(payload)
