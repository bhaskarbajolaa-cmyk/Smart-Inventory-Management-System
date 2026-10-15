"""Authentication-facing identity and session models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from inventory.access_control.models import Role


@dataclass
class User:
    """User identity and profile, without password or login mechanics.

    Credentials (password hash) are kept in ``UserCredentials`` and managed
    exclusively by ``Authentication``; they never appear on this object.
    """

    user_id: int
    username: str
    email: str
    role_id: int
    created_at: datetime

    def has_permission(self, code: str, role: Role) -> bool:
        # TODO: Delegate the permission check to the user's assigned role.
        raise NotImplementedError

    def create_role(self, name: str, permissions: list[str]) -> Role:
        # TODO: Verify caller authority, create the role and grants, then persist them.
        raise NotImplementedError

    def assign_role(self, user: "User", role: Role) -> None:
        # TODO: Verify caller authority and update the target user's assigned role.
        raise NotImplementedError

    def delete_role(self, role: Role) -> None:
        # TODO: Verify caller authority and prevent deletion of protected default roles.
        raise NotImplementedError


@dataclass
class UserCredentials:
    """Stores the hashed password for a user.

    This object is never exposed outside ``Authentication`` and
    ``UserRepository``.  Keeping credentials separate from ``User`` ensures
    that the user identity object is safe to pass to any layer without
    accidentally leaking sensitive data.
    """

    user_id: int
    password_hash: str


@dataclass
class Session:
    """Short-lived authenticated session.

    ``roles`` is a ``list[str]`` in Python.  When persisted to the
    ``sessions`` table the list is JSON-encoded into the ``roles TEXT`` column
    by ``SessionRepository``; it is decoded back on retrieval.
    """

    session_token: str
    user_id: int
    roles: list[str]
    issued_at: datetime
    expires_at: datetime

    def is_expired(self) -> bool:
        # TODO: Compare the expiry timestamp with the current UTC time.
        raise NotImplementedError

    def decode_token(self) -> dict[str, Any]:
        # TODO: Validate and decode the session token into its claims.
        raise NotImplementedError
