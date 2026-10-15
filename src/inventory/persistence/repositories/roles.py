"""Persistence operations for access-control roles."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from inventory.access_control.models import Permission, Role, TableAccessGrant
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class RoleRepository(Repository[Role, object]):
    """Role storage and lookup operations."""

    def __init__(self, db: DatabaseManager) -> None:
        super().__init__(db)

    def _row_to_role(self, row: Any) -> Role:
        role = Role(
            role_id=int(row["role_id"]),
            role_name=str(row["role_name"]),
            created_by=int(row["created_by"]) if row["created_by"] is not None else 0,
            is_system_default=bool(row["is_system_default"]),
            default_job_priority=int(row["default_job_priority"]),
        )
        grant_rows = self.db.execute_query(
            """
            SELECT g.grant_id, g.role_id, g.permission_id, g.table_name, g.granted_by, g.granted_at,
                   p.code AS permission_code, p.description AS permission_description
            FROM table_access_grants g
            JOIN permissions p ON p.permission_id = g.permission_id
            WHERE g.role_id = ?
            ORDER BY g.grant_id ASC
            """,
            (role.role_id,),
        )
        for grant_row in grant_rows:
            permission = Permission(
                permission_id=int(grant_row["permission_id"]),
                code=str(grant_row["permission_code"]),
                description=str(grant_row["permission_description"] or ""),
            )
            grant = TableAccessGrant(
                grant_id=int(grant_row["grant_id"]),
                role_id=int(grant_row["role_id"]),
                permission_id=int(grant_row["permission_id"]),
                table_name=grant_row["table_name"],
                granted_by=int(grant_row["granted_by"]) if grant_row["granted_by"] is not None else 0,
                granted_at=(
                    datetime.fromisoformat(grant_row["granted_at"])
                    if isinstance(grant_row["granted_at"], str)
                    else grant_row["granted_at"]
                ),
                permission=permission,
            )
            role.grants.append(grant)
        return role

    def find_by_id(self, entity_id: int) -> Role | None:
        """Fetch a role and hydrate its permission grants."""
        rows = self.db.execute_query(
            "SELECT role_id, role_name, created_by, is_system_default, default_job_priority FROM roles WHERE role_id = ?",
            (entity_id,),
        )
        if not rows:
            return None
        return self._row_to_role(rows[0])

    def find_all(self, filters: object | None = None) -> list[Role]:
        # TODO: Return roles matching optional query criteria with grants loaded.
        raise NotImplementedError

    def save(self, entity: Role) -> Role:
        # TODO: Persist the role and its grant relationships atomically.
        raise NotImplementedError

    def delete(self, entity_id: int) -> None:
        # TODO: Delete an eligible role and define behavior for assigned users.
        raise NotImplementedError
