"""Authentication-facing identity and session models."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from inventory.access_control.models import Permission, Role, TableAccessGrant

if TYPE_CHECKING:
    from inventory.access_control.store import RoleStore


@dataclass
class User:
    """User identity and profile, with optional password attribute."""

    user_id: int
    username: str
    email: str
    role_id: int
    created_at: datetime
    password: str = ""

    def has_permission(self, code: str, role: Role) -> bool:
        """Delegate the permission check to the user's assigned role."""
        if self.role_id != role.role_id:
            return False
        return role.has_permission(code, None)

    def create_role(
        self,
        name: str,
        permissions: list[str],
        role_id: int = 0,
        job_priority: int = 3,
        grant_id: int = 0,
        store: RoleStore | None = None,
    ) -> Role:
        """Verify caller authority, create the role and grants, save it, and return it."""
        if self.role_id != 1:
            raise PermissionError("Only administrators can create roles.")

        new_role = Role(
            role_id=role_id,
            role_name=name,
            created_by=self.user_id,
            is_system_default=False,
            default_job_priority=job_priority,
            grants=[],
        )
        for i, code in enumerate(dict.fromkeys(permissions), start=1):
            perm = Permission(permission_id=i, code=code, description="")
            grant = TableAccessGrant(
                grant_id=grant_id,
                role_id=role_id,
                permission_id=i,
                table_name=None,
                granted_by=self.user_id,
                granted_at=datetime.now(),
                permission=perm,
                permission_code=code,
            )
            new_role.add_permission(grant)

        if store is None:
            from inventory.access_control.store import RoleStore
            store = RoleStore("roles.json")
        store.save(new_role)
        return new_role

    def assign_role(self, user: "User", role: Role) -> None:
        """Verify caller authority and update the target user's assigned role."""
        if self.role_id != 1:
            raise PermissionError("Only administrators can assign roles.")
        user.role_id = role.role_id

    def delete_role(self, role: Role, store: RoleStore | None = None) -> None:
        """Verify caller authority, prevent deletion of protected roles, and remove from storage."""
        if self.role_id != 1:
            raise PermissionError("Only administrators can delete roles.")
        if role.is_system_default:
            raise ValueError("Cannot delete a system default role.")

        if store is None:
            from inventory.access_control.store import RoleStore
            store = RoleStore("roles.json")
        store.delete(role.role_id)


@dataclass
class UserCredentials:
    """Stores the hashed password for a user.

    This object is never exposed outside ``Authentication`` and
    ``UserRepository``. Keeping credentials separate from ``User`` ensures
    that the user identity object is safe to pass to any layer without
    accidentally leaking sensitive data.
    """

    user_id: int
    password_hash: str


@dataclass
class Session:
    """Short-lived authenticated session."""

    session_token: str
    user_id: int
    roles: list[str]
    issued_at: datetime
    expires_at: datetime
    password: str = ""

    def is_expired(self) -> bool:
        """Compare the expiry timestamp with the current UTC time."""
        now = datetime.now(timezone.utc) if self.expires_at.tzinfo else datetime.now()
        return now >= self.expires_at

    def decode_token(self) -> dict[str, Any]:
        """Validate and decode the session token into its claims."""
        if not self.session_token:
            return {}
        try:
            padded = self.session_token + "=" * (-len(self.session_token) % 4)
            payload = base64.urlsafe_b64decode(padded.encode("ascii"))
            claims = json.loads(payload.decode("utf-8"))
            if isinstance(claims, dict):
                return claims
        except Exception:
            pass
        return {
            "session_token": self.session_token,
            "user_id": self.user_id,
            "roles": self.roles,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

