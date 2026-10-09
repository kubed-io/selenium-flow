"""Who a verified caller is.

Two kinds, from the two credentials `/mcp` accepts: the server token, which is
the operator and so the admin, and a JWT from the configured OIDC issuer, which
is a subject. Built by the verifiers in ``http/auth.py`` and carried on
``Caller``; nothing decides anything from it yet (spec 2026-10-04, groundwork).
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

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any], roles_claim: str) -> Principal:
        sub = claims.get("sub")
        name = claims.get("preferred_username")
        return cls(
            "oidc",
            subject=sub if isinstance(sub, str) else None,
            username=name if isinstance(name, str) else None,
            roles=roles_in(claims, roles_claim),
        )


ADMIN = Principal("admin")
