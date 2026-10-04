"""Authentication-facing identity and session models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from inventory.access_control.models import Role


@dataclass
class User:
    """User identity and profile, without password or login mechanics."""

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
class Session:
    """Short-lived authenticated session."""

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
