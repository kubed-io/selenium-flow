"""Who a verified caller is.

Two kinds, from the two credentials `/mcp` accepts: the server token, which is
the operator and so the admin, and a JWT from the configured OIDC issuer, which
is a subject. Built by the verifiers in ``http/auth.py`` and carried on
``Caller``. A principal decides two things only: the caller's own session,
which a call that names none acts in (`sessions.own_name`), and who holds a
session, which no other subject may use (`subject_of`, `Workspaces._holds`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


def roles_in(claims: Mapping[str, Any], roles_claim: str) -> tuple[str, ...]:
    """The roles a token holds: ``roles_claim`` walked by dots, strings only.

    The one reading of the claim, shared by the role check and the principal so
    they cannot disagree. Anything that is not a list of strings holds none.
    """
    value: Any = claims
    for part in roles_claim.split("."):
        if not isinstance(value, Mapping):
            return ()
        value = value.get(part)
    if not isinstance(value, list):
        return ()
    return tuple(role for role in value if isinstance(role, str))


# The subject of the server token, and of a call that has no principal (a
# server with no token).
ADMIN_SUBJECT = "admin"
NO_SUBJECT = "default"
# What a JWT with no `sub` has in front of its `preferred_username` as a subject.
FROM_USERNAME = "preferred_username:"


@dataclass(frozen=True)
class Principal:
    kind: Literal["admin", "oidc"]
    subject: str | None = None
    username: str | None = None
    roles: tuple[str, ...] = ()

    @property
    def admin(self) -> bool:
        return self.kind == "admin"

    def status(self) -> dict:
        """What `workspace://current` shows. Roles are an input, not news."""
        if self.admin:
            return {"kind": "admin"}
        return {"kind": "oidc", "subject": self.subject, "username": self.username}

    def opener(self) -> dict:
        """What a workspace keeps about who opened its browser. Shown, never
        consulted."""
        return {"kind": self.kind, "username": self.username}

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any], roles_claim: str) -> Principal:
        sub = claims.get("sub")
        name = claims.get("preferred_username")
        if isinstance(sub, str) and sub:
            subject = sub
        elif isinstance(name, str) and name:
            subject = FROM_USERNAME + name
        else:
            subject = None
        return cls(
            "oidc",
            subject=subject,
            username=name if isinstance(name, str) else None,
            roles=roles_in(claims, roles_claim),
        )


ADMIN = Principal("admin")


def subject_of(principal: Principal | None) -> str:
    """Who a call is, for deciding which session is whose.

    `NO_SUBJECT` for no principal, `ADMIN_SUBJECT` for the token, else the
    JWT's subject. The door admits no JWT without one, so a principal that has
    none was built by hand: it is refused, never read as `NO_SUBJECT`.
    """
    if principal is None:
        return NO_SUBJECT
    if principal.admin:
        return ADMIN_SUBJECT
    if not principal.subject:
        raise ValueError("a JWT principal with no subject")
    return principal.subject
