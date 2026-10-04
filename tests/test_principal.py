"""Who a verified caller is: the server token's admin, or an OIDC subject."""

import pytest

from kubed.selenium_flow.principal import ADMIN, Principal, roles_in

pytestmark = pytest.mark.unit


def test_the_server_token_is_the_admin():
    assert ADMIN.admin and ADMIN.kind == "admin"
    assert ADMIN.status() == {"kind": "admin"}


def test_a_jwt_is_an_oidc_principal_with_its_subject_and_username():
    claims = {"sub": "6b0f", "preferred_username": "drk", "roles": ["mcp", "admin"]}
    who = Principal.from_claims(claims, "roles")
    assert (who.kind, who.subject, who.username, who.roles) == (
        "oidc",
        "6b0f",
        "drk",
        ("mcp", "admin"),
    )
    # A JWT role named admin is not the admin this round (spec, The principal).
    assert not who.admin
    assert who.status() == {"kind": "oidc", "subject": "6b0f", "username": "drk"}


def test_a_jwt_without_a_username_says_none():
    assert Principal.from_claims({"sub": "x"}, "roles").username is None


@pytest.mark.parametrize(
    ("claims", "path", "held"),
    [
        ({"roles": ["mcp"]}, "roles", ("mcp",)),
        ({"realm_access": {"roles": ["a", "b"]}}, "realm_access.roles", ("a", "b")),
        ({}, "roles", ()),
        ({"roles": "mcp"}, "roles", ()),  # a string is not a list
        ({"roles": ["mcp", 3, None]}, "roles", ("mcp",)),  # non-strings dropped
        ({"realm_access": ["x"]}, "realm_access.roles", ()),  # walk hits a list
    ],
)
def test_roles_in_reads_a_dotted_path_and_nothing_else(claims, path, held):
    assert roles_in(claims, path) == held
