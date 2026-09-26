"""secrets.entries: merged over the collected secrets, or defined whole."""

import pytest

from kubed.selenium_flow import secrets
from kubed.selenium_flow.config import SecretsSettings

pytestmark = pytest.mark.unit
CFG = "/etc/selenium-flow/config.yaml"


@pytest.fixture
def dirs(tmp_path):
    grafana = tmp_path / "grafana"
    grafana.mkdir()
    (grafana / "username").write_text("viewer")
    (grafana / "password").write_text("pw")
    (grafana / "_allowed_urls").write_text("staging.internal\n")  # broken on disk
    return tmp_path


def _catalogue(dirs, entries, environ=None):
    conf = SecretsSettings(dirs=[str(dirs)] if dirs else [], entries=entries)
    cat = secrets.from_settings(conf, CFG)
    if cat is not None and cat.config is not None and environ is not None:
        cat.config._environ = environ
    return cat


def test_config_merges_description_and_leash_over_a_directory(dirs):
    cat = _catalogue(dirs, {"grafana": {"description": "Viewer", "allowed_urls": ["https://grafana.example.com"]}})
    e = cat.entry("grafana")
    assert e["description"] == "Viewer"
    assert e["allowed_urls"] == ["https://grafana.example.com"] and e["restricted"]
    assert "allowed_urls_rejected" not in e  # the config replaced the broken file
    assert e["keys"] == ["password", "username"]
    assert e["origins"] == [{"source": "filesystem", "location": str(dirs)}, {"source": "config", "location": CFG}]
    assert cat.allows("grafana", "https://grafana.example.com/login")


def test_a_config_key_replaces_a_file_key_of_the_same_name(dirs):
    cat = _catalogue(dirs, {"grafana": {"keys": {"password": {"env": "GF_PW"}}}}, {"GF_PW": "from-env"})
    assert cat.value("grafana", "password") == "from-env"
    assert cat.value("grafana", "username") == "viewer"
    assert cat.entry("grafana")["key_sources"]["password"] == {"from": "env", "name": "GF_PW"}
    assert cat.entry("grafana")["key_sources"]["username"] == {"from": "filesystem"}


def test_a_secret_defined_whole_in_config(tmp_path):
    token = tmp_path / "token"
    token.write_text("abc\n")
    cat = _catalogue(None, {"admin": {"allowed_urls": ["https://s.example.com"],
                                      "keys": {"token": {"file": str(token)}, "user": {"env": "U"}}}}, {"U": "me"})
    assert cat is not None  # secrets are on with entries alone
    assert cat.value("admin", "token") == "abc" and cat.value("admin", "user") == "me"
    e = cat.entry("admin")
    assert e["origins"] == [{"source": "config", "location": CFG}]
    assert e["key_sources"]["token"] == {"from": "file", "path": str(token)}


def test_an_unresolved_key_is_listed_with_its_reason_and_refused_with_it():
    cat = _catalogue(None, {"github": {"keys": {"token": {"env": "GITHUB_TOKEN"}}}}, {})
    assert cat.entry("github")["keys_unresolved"] == [{"key": "token", "reason": "env GITHUB_TOKEN is not set"}]
    assert cat.unresolved("github", "token") == "env GITHUB_TOKEN is not set"
    with pytest.raises(secrets.Refused, match="GITHUB_TOKEN is not set"):
        secrets.bind(cat, {"name": "github", "key": "token"}, "https://github.com", tool="write")


def test_an_inline_value_is_marked_and_never_listed():
    cat = _catalogue(None, {"demo": {"keys": {"password": {"value": "s3cret"}}}})
    assert cat.entry("demo")["inline_keys"] == ["password"]
    assert "s3cret" not in str(cat.listing())
    assert cat.value("demo", "password") == "s3cret"


def test_an_env_key_is_read_at_bind_time_not_at_listing():
    environ = {"U": "one"}
    cat = _catalogue(None, {"d": {"keys": {"u": {"env": "U"}}}}, environ)
    cat.listing()
    environ["U"] = "two"
    assert secrets.bind(cat, {"name": "d", "key": "u"}, "https://x.example", tool="write") == "two"


def test_no_dirs_and_no_entries_is_off():
    assert secrets.from_settings(SecretsSettings()) is None


def test_a_declared_empty_leash_is_usable_nowhere():
    """`allowed_urls: []` is a leash declared and immediately exhausted — not
    the same as never declaring one, which allows any site."""
    cat = _catalogue(None, {"d": {"allowed_urls": [], "keys": {"k": {"value": "v"}}}})
    assert cat.allows("d", "https://x.example") is False
    with pytest.raises(secrets.Refused, match="empty or does not parse"):
        secrets.bind(cat, {"name": "d", "key": "k"}, "https://x.example", tool="write")
