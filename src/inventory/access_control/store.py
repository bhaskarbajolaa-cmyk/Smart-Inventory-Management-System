"""File-based storage for Role objects. Independent of DatabaseManager."""

import json
import os
from datetime import datetime

from inventory.access_control.models import Role, TableAccessGrant


class RoleStore:
    """Persist and retrieve Role objects using a local JSON file.

    Usage:
        store = RoleStore("roles.json")
        store.save(role)
        role = store.find_by_id(1)
        all_roles = store.load_all()
        store.delete(1)
    """

    def __init__(self, filepath: str = "roles.json") -> None:
        """Set the path to the JSON file. File is created on first save."""
        self._filepath = filepath

    def save(self, role: Role) -> Role:
        """Insert or overwrite a role entry. Returns the saved role."""
        roles = self._read_file()

        # Auto-assign an ID if the role is new (id == 0)
        if role.role_id == 0:
            existing_ids = [int(k) for k in roles.keys()] if roles else [0]
            role.role_id = max(existing_ids) + 1

        roles[str(role.role_id)] = self._role_to_dict(role)
        self._write_file(roles)
        return role

    def load_all(self) -> list[Role]:
        """Return all stored roles."""
        roles = self._read_file()
        return [self._dict_to_role(data) for data in roles.values()]

    def find_by_id(self, role_id: int) -> Role | None:
        """Return a single role by ID, or None if not found."""
        roles = self._read_file()
        data = roles.get(str(role_id))
        if data is None:
            return None
        return self._dict_to_role(data)

    def delete(self, role_id: int) -> None:
        """Remove a role by ID. Silently does nothing if ID is not found."""
        roles = self._read_file()
        roles.pop(str(role_id), None)
        self._write_file(roles)

    def _read_file(self) -> dict:
        """Load raw JSON from disk. Returns empty dict if file doesn't exist."""
        if not os.path.exists(self._filepath):
            return {}
        with open(self._filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_file(self, data: dict) -> None:
        """Write raw dict as JSON to disk."""
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _role_to_dict(self, role: Role) -> dict:
        """Serialize a Role (and its grants) to a plain dict."""
        return {
            "role_id": role.role_id,
            "role_name": role.role_name,
            "created_by": role.created_by,
            "is_system_default": role.is_system_default,
            "default_job_priority": role.default_job_priority,
            "grants": [self._grant_to_dict(g) for g in role.grants],
        }

    def _grant_to_dict(self, grant: TableAccessGrant) -> dict:
        """Serialize a TableAccessGrant to a plain dict."""
        return {
            "grant_id": grant.grant_id,
            "role_id": grant.role_id,
            "permission_id": grant.permission_id,
            "table_name": grant.table_name,
            "granted_by": grant.granted_by,
            "granted_at": grant.granted_at.isoformat(),
            "permission_code": grant.permission_code,
        }

    def _dict_to_role(self, data: dict) -> Role:
        """Deserialize a dict back into a Role with its grants."""
        grants = [self._dict_to_grant(g) for g in data.get("grants", [])]
        return Role(
            role_id=data["role_id"],
            role_name=data["role_name"],
            created_by=data["created_by"],
            is_system_default=data["is_system_default"],
            default_job_priority=data["default_job_priority"],
            grants=grants,
        )

    def _dict_to_grant(self, data: dict) -> TableAccessGrant:
        """Deserialize a dict back into a TableAccessGrant."""
        return TableAccessGrant(
            grant_id=data["grant_id"],
            role_id=data["role_id"],
            permission_id=data["permission_id"],
            table_name=data["table_name"],
            granted_by=data["granted_by"],
            granted_at=datetime.fromisoformat(data["granted_at"]),
            permission_code=data["permission_code"],
        )
