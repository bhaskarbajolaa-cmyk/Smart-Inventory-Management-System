"""Authentication-facing identity and session models."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from inventory.access_control.models import Permission, Role, TableAccessGrant


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
        """Return whether the supplied role grants the requested permission."""
        return role is not None and role.has_permission(code)

    def create_role(self, name: str, permissions: list[str]) -> Role:
        """Create a role value object for a user-driven role definition."""
        role = Role(
            role_id=0,
            role_name=name,
            created_by=self.user_id,
            is_system_default=False,
            default_job_priority=1,
        )
        for permission_code in permissions:
            role.grants.append(
                TableAccessGrant(
                    grant_id=0,
                    role_id=0,
                    permission_id=0,
                    table_name=None,
                    granted_by=self.user_id,
                    granted_at=datetime.now(),
                    permission=Permission(
                        permission_id=0,
                        code=permission_code,
                        description="",
                    ),
                )
            )
        return role

    def assign_role(self, user: "User", role: Role) -> None:
        """Update the target user to the supplied role."""
        user.role_id = role.role_id

    def delete_role(self, role: Role) -> None:
        """Prevent deletion of protected default roles."""
        if role.is_system_default:
            raise PermissionError("System default roles cannot be deleted.")


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
        """Check whether the session has expired relative to the current time."""
        now = datetime.now(self.expires_at.tzinfo or None)
        return now >= self.expires_at

    def decode_token(self) -> dict[str, Any]:
        """Decode a JSON-encoded, URL-safe session token payload."""
        if not self.session_token:
            return {}

        try:
            padded = self.session_token + "=" * (-len(self.session_token) % 4)
            payload = base64.urlsafe_b64decode(padded.encode("ascii"))
            claims = json.loads(payload.decode("utf-8"))
            return claims if isinstance(claims, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
