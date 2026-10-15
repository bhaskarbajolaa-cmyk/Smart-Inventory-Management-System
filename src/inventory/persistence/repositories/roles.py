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
        """Return roles matching optional criteria with grants applied."""
        sql = "SELECT role_id, role_name, created_by, is_system_default, default_job_priority FROM roles"
        params: list[Any] = []
        conditions: list[str] = []

        if isinstance(filters, dict):
            if filters.get("role_name"):
                conditions.append("LOWER(role_name) LIKE LOWER(?)")
                params.append(f"%{filters['role_name']}%")
            if filters.get("is_system_default") is not None:
                conditions.append("is_system_default = ?")
                params.append(int(bool(filters["is_system_default"])))

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY role_id ASC"
        rows = self.db.execute_query(sql, params)
        return [self._row_to_role(row) for row in rows]

    def save(self, entity: Role) -> Role:
        """Persist the role and its grant relationships atomically."""
        if entity.role_id and self.find_by_id(entity.role_id) is not None:
            self.db.execute_query(
                "UPDATE roles SET role_name = ?, created_by = ?, is_system_default = ?, default_job_priority = ? WHERE role_id = ?",
                (
                    entity.role_name,
                    entity.created_by,
                    int(entity.is_system_default),
                    entity.default_job_priority,
                    entity.role_id,
                ),
            )
        else:
            if entity.role_id and entity.role_id > 0:
                self.db.execute_query(
                    "INSERT INTO roles (role_id, role_name, created_by, is_system_default, default_job_priority) VALUES (?, ?, ?, ?, ?)",
                    (
                        entity.role_id,
                        entity.role_name,
                        entity.created_by,
                        int(entity.is_system_default),
                        entity.default_job_priority,
                    ),
                )
            else:
                rows = self.db.execute_query(
                    "INSERT INTO roles (role_name, created_by, is_system_default, default_job_priority) VALUES (?, ?, ?, ?) RETURNING role_id",
                    (
                        entity.role_name,
                        entity.created_by,
                        int(entity.is_system_default),
                        entity.default_job_priority,
                    ),
                )
                if rows:
                    entity.role_id = int(rows[0]["role_id"])
                elif self.db.last_inserted_id:
                    entity.role_id = int(self.db.last_inserted_id)

        self.db.execute_query("DELETE FROM table_access_grants WHERE role_id = ?", (entity.role_id,))
        for grant in entity.grants:
            permission_id = grant.permission_id
            if permission_id == 0 and grant.permission is not None:
                permission_rows = self.db.execute_query(
                    "SELECT permission_id FROM permissions WHERE code = ?",
                    (grant.permission.code,),
                )
                if permission_rows:
                    permission_id = int(permission_rows[0]["permission_id"])
                else:
                    permission_rows = self.db.execute_query(
                        "INSERT INTO permissions (code, description) VALUES (?, ?) RETURNING permission_id",
                        (grant.permission.code, grant.permission.description),
                    )
                    if permission_rows:
                        permission_id = int(permission_rows[0]["permission_id"])
            self.db.execute_query(
                "INSERT INTO table_access_grants (role_id, permission_id, table_name, granted_by, granted_at) VALUES (?, ?, ?, ?, ?)",
                (
                    entity.role_id,
                    permission_id,
                    grant.table_name,
                    grant.granted_by,
                    grant.granted_at.isoformat() if hasattr(grant.granted_at, "isoformat") else str(grant.granted_at),
                ),
            )
        return entity

    def delete(self, entity_id: int) -> None:
        """Delete a role and its generated grants when the role exists."""
        role = self.find_by_id(entity_id)
        if role is None:
            raise KeyError(f"Role with ID {entity_id} does not exist.")
        self.db.execute_query("DELETE FROM table_access_grants WHERE role_id = ?", (entity_id,))
        self.db.execute_query("DELETE FROM roles WHERE role_id = ?", (entity_id,))
