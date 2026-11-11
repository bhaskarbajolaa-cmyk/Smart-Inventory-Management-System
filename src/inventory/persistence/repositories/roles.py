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
        """Return all roles with their permission grants loaded."""
        rows = self.db.execute_query(
            "SELECT role_id, role_name, created_by, is_system_default, default_job_priority FROM roles ORDER BY role_id ASC"
        )
        return [self._row_to_role(row) for row in rows]

    def save(self, entity: Role) -> Role:
        """Persist the role and its grant relationships."""
        if entity.role_id > 0:
            self.db.execute_query(
                """UPDATE roles
                   SET role_name = ?, default_job_priority = ?
                   WHERE role_id = ?""",
                (entity.role_name, entity.default_job_priority, entity.role_id),
            )
        else:
            rows = self.db.execute_query(
                """INSERT INTO roles (role_name, created_by, is_system_default, default_job_priority)
                   VALUES (?, ?, ?, ?) RETURNING role_id""",
                (
                    entity.role_name,
                    entity.created_by,
                    1 if entity.is_system_default else 0,
                    entity.default_job_priority,
                ),
            )
            if rows:
                entity.role_id = int(rows[0]["role_id"])

        if entity.grants:
            now_iso = datetime.now().isoformat()
            for grant in entity.grants:
                if grant.grant_id == 0:
                    self.db.execute_query(
                        """INSERT INTO table_access_grants
                           (role_id, permission_id, table_name, granted_by, granted_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            entity.role_id,
                            grant.permission_id,
                            grant.table_name,
                            grant.granted_by or entity.created_by,
                            now_iso,
                        ),
                    )
        return entity

    def delete(self, entity_id: int) -> None:
        """Delete an eligible non-system role not currently assigned to users."""
        role = self.find_by_id(entity_id)
        if role is None:
            raise KeyError(f"Role {entity_id} does not exist.")
        if role.is_system_default:
            raise ValueError(f"System default role '{role.role_name}' cannot be deleted.")

        user_rows = self.db.execute_query(
            "SELECT COUNT(*) AS count FROM users WHERE role_id = ?", (entity_id,)
        )
        if user_rows and int(user_rows[0]["count"]) > 0:
            raise ValueError(
                f"Cannot delete role '{role.role_name}': {user_rows[0]['count']} active user(s) are assigned to it."
            )

        self.db.execute_query("DELETE FROM table_access_grants WHERE role_id = ?", (entity_id,))
        self.db.execute_query("DELETE FROM roles WHERE role_id = ?", (entity_id,))
