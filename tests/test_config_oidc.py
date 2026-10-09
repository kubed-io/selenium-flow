"""The `oidc` section: a JWT issuer beside the token, never instead of it."""

import pytest

from kubed.selenium_flow import config
from kubed.selenium_flow.config import ConfigError, Settings, load

pytestmark = pytest.mark.unit

ISSUER = "https://auth.example.com/realms/example"
JWKS = ISSUER + "/protocol/openid-connect/certs"
ON = {
    "OIDC_ISSUER": ISSUER,
    "OIDC_AUDIENCE": "https://mcp.example.com",
    "OIDC_JWKS_URI": JWKS,
    "AUTH_TOKEN": "t0ken",
}
ADMIN_UI = {"OIDC_CLIENT_ID": "selenium-flow-admin", "OIDC_ADMIN_ROLES": "admin, ops,"}


def test_off_by_default():
    oidc = Settings().oidc
    assert (oidc.issuer, oidc.audience, oidc.jwks_uri, oidc.roles) == (
        None,
        None,
        None,
        [],
    )
    assert oidc.roles_claim == "roles"
    assert (oidc.client_id, oidc.admin_roles) == (None, [])
    assert config.oidc_problem(Settings()) is None


def test_the_env_names_follow_the_rule():
    envs = {leaf.env for leaf in config.leaves() if leaf.section == "oidc"}
    assert envs == {
        "OIDC_ISSUER",
        "OIDC_AUDIENCE",
        "OIDC_JWKS_URI",
        "OIDC_ROLES",
        "OIDC_ROLES_CLAIM",
        "OIDC_CLIENT_ID",
        "OIDC_ADMIN_ROLES",
    }


def test_roles_are_a_comma_list_from_env():
    loaded = load([], ON | {"OIDC_ROLES": "mcp, admin,"})
    assert loaded.settings.oidc.roles == ["mcp", "admin"]


def test_all_three_load_with_a_token():
    oidc = load([], ON).settings.oidc
    assert (oidc.issuer, oidc.jwks_uri) == (ISSUER, JWKS)


@pytest.mark.parametrize("missing", ["OIDC_AUDIENCE", "OIDC_JWKS_URI", "OIDC_ISSUER"])
def test_issuer_audience_and_jwks_are_all_or_nothing(missing):
    env = {k: v for k, v in ON.items() if k != missing}
    with pytest.raises(ConfigError, match="together"):
        load([], env)


def test_oidc_needs_the_token():
    env = {k: v for k, v in ON.items() if k != "AUTH_TOKEN"}
    with pytest.raises(ConfigError, match=r"auth\.token"):
        load([], env)


def test_oidc_in_the_file_and_the_token_in_env_loads(tmp_path):
    """The cluster's shape: `load()` validates the file alone first."""
    path = tmp_path / "config.yaml"
    path.write_text(
        f"oidc:\n  issuer: {ISSUER}\n  audience: https://mcp.example.com\n"
        f"  jwks_uri: {JWKS}\n  roles: [mcp]\n"
    )
    loaded = load(["--config-file", str(path)], {"AUTH_TOKEN": "t0ken"})
    assert loaded.settings.oidc.roles == ["mcp"]
    assert loaded.sources["oidc.issuer"] == "config"


@pytest.mark.parametrize("key", ["OIDC_ISSUER", "OIDC_JWKS_URI"])
def test_the_urls_are_http(key):
    with pytest.raises(ConfigError, match="http"):
        load([], ON | {key: "ftp://auth.example.com/x"})


def test_oidc_problem_is_the_same_rule_for_settings_built_in_code():
    built = Settings(oidc={"issuer": ISSUER, "audience": "a", "jwks_uri": JWKS})
    assert "auth.token" in config.oidc_problem(built)


def test_the_admin_ui_settings_load_beside_the_issuer():
    oidc = load([], ON | ADMIN_UI).settings.oidc
    assert oidc.client_id == "selenium-flow-admin"
    assert oidc.admin_roles == ["admin", "ops"]


@pytest.mark.parametrize("missing", ["OIDC_CLIENT_ID", "OIDC_ADMIN_ROLES"])
def test_client_id_and_admin_roles_come_together(missing):
    env = {k: v for k, v in (ON | ADMIN_UI).items() if k != missing}
    with pytest.raises(
        ConfigError,
        match=r"oidc\.client_id and oidc\.admin_roles are set together or not at all",
    ):
        load([], env)


def test_the_admin_ui_needs_the_issuer():
    with pytest.raises(ConfigError, match=r"oidc\.client_id needs oidc\.issuer"):
        load([], ADMIN_UI | {"AUTH_TOKEN": "t0ken"})


def test_the_admin_ui_settings_show_on_the_settings_tab():
    loaded = load([], ON | ADMIN_UI)
    rows = {
        row["key"]: row
        for section in config.describe(loaded.settings, loaded.sources)["sections"]
        for row in section["settings"]
    }
    assert rows["oidc.client_id"]["value"] == "selenium-flow-admin"
    assert rows["oidc.admin_roles"]["value"] == ["admin", "ops"]
