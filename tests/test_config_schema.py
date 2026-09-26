"""The schema: one setting, three spellings, and nothing read from the environment."""

import pytest
from pydantic import ValidationError

from kubed.selenium_flow import config
from kubed.selenium_flow.config import Settings

pytestmark = pytest.mark.unit

SECTION_NAMES = [name for name in config.SECTION_ORDER if name != "server"]


def test_every_leaf_follows_the_naming_rule():
    for leaf in config.leaves():
        assert leaf.env == leaf.path.upper().replace(".", "_")
        assert leaf.flag == "--" + leaf.path.replace(".", "-").replace("_", "-")


def test_no_section_name_can_collide_with_an_env_prefix_already_in_use():
    for name in SECTION_NAMES:
        assert "_" not in name, name
        assert name not in {"browser", "selenium"}, name


def test_every_leaf_has_a_one_sentence_description():
    for leaf in config.leaves():
        assert leaf.description, leaf.path
        assert len(leaf.description) <= 60, (leaf.path, leaf.description)
        assert leaf.description.endswith("."), leaf.path


def test_every_section_has_a_description_and_an_order():
    assert config.SECTION_ORDER[0] == "server"
    assert set(config.SECTION_ORDER) == set(config.SECTION_DESCRIPTIONS)


def test_the_secret_definitions_are_not_a_leaf():
    """`secrets.entries` is structure: no env name, no flag, no row."""
    assert "secrets.entries" not in {leaf.path for leaf in config.leaves()}


def test_the_sensitive_settings_are_exactly_the_three():
    assert {leaf.path for leaf in config.leaves() if leaf.sensitive} == {
        "auth.token", "redis.url", "redis.password",
    }


def test_defaults_need_no_environment(monkeypatch):
    monkeypatch.setenv("PORT", "9999")
    monkeypatch.setenv("SESSION_TTL", "1")
    s = Settings()
    assert s.port == 8000
    assert s.session.ttl == 86400
    assert s.session.store == "memory"


def test_an_unknown_key_is_refused():
    with pytest.raises(ValidationError, match="bogus"):
        Settings.model_validate({"redis": {"bogus": 1}})


def test_a_bad_browser_is_refused_and_a_good_one_normalised():
    assert Settings(session={"browser": "Firefox"}).session.browser == "firefox"
    with pytest.raises(ValidationError):
        Settings(session={"browser": "safari"})


def test_secrets_dirs_split_like_path(tmp_path):
    import os
    s = Settings(secrets={"dirs": f"/a{os.pathsep}/b{os.pathsep} "})
    assert s.secrets.dirs == ["/a", "/b"]


def test_a_blank_flow_dir_is_off():
    assert Settings(flow={"data_dir": "   "}).flow.data_dir is None


def test_a_key_reference_is_exactly_one_of_file_env_value():
    entry = {"keys": {"a": {"file": "/f"}, "b": {"env": "X"}, "c": {"value": "v"}}}
    s = Settings(secrets={"entries": {"demo": entry}})
    keys = s.secrets.entries["demo"].keys
    assert isinstance(keys["a"], config.FromFile)
    assert isinstance(keys["b"], config.FromEnv)
    assert isinstance(keys["c"], config.FromValue)
    for bad in ({"file": "/f", "env": "X"}, {}, {"path": "/f"}):
        with pytest.raises(ValidationError):
            Settings(secrets={"entries": {"demo": {"keys": {"k": bad}}}})


def test_allowed_urls_must_be_bare_origins_and_the_error_never_echoes_credentials():
    ok = Settings(secrets={"entries": {"d": {"allowed_urls": ["https://Example.com/"]}}})
    assert ok.secrets.entries["d"].allowed_urls == ["https://example.com"]
    with pytest.raises(ValidationError) as caught:
        Settings(secrets={"entries": {"d": {"allowed_urls": ["https://u:hunter2@x.com"]}}})
    assert "hunter2" not in str(caught.value)


def test_a_secret_name_follows_the_directory_rule():
    with pytest.raises(ValidationError):
        Settings(secrets={"entries": {"../up": {}}})


def test_a_key_name_cannot_be_reserved():
    with pytest.raises(ValidationError):
        Settings(secrets={"entries": {"d": {"keys": {"_allowed_urls": {"env": "X"}}}}})


def test_an_inline_value_never_reaches_a_repr():
    s = Settings(auth={"token": "t0ken"}, secrets={"entries": {"d": {"keys": {"k": {"value": "s3cret"}}}}})
    assert "t0ken" not in repr(s) and "s3cret" not in repr(s)
    assert "s3cret" not in s.model_dump_json()


def test_value_of_reads_a_dotted_path():
    s = Settings(redis={"db": 2})
    assert config.value_of(s, "redis.db") == 2
    assert config.value_of(s, "port") == 8000
