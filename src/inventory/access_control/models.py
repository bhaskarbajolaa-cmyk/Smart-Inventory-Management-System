"""Data-driven role-based access control models."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Permission:
    """A named action that may be granted to a role."""

    permission_id: int
    code: str
    description: str


@dataclass
class TableAccessGrant:
    """Permission grant scoped to one table or all tables."""

    grant_id: int
    role_id: int
    permission_id: int
    table_name: str | None
    granted_by: int
    granted_at: datetime
    permission_code: str

    def applies_to(self, table_name: str) -> bool:
        # TODO: Match the requested table, treating a null grant table as global.
        raise NotImplementedError


@dataclass
class Role:
    """Named, editable collection of table access grants."""

    role_id: int
    role_name: str
    created_by: int
    is_system_default: bool
    default_job_priority: int
    grants: list[TableAccessGrant] = field(default_factory=list)

    def has_permission(self, code: str, table_name: str | None = None) -> bool:
        # TODO: Return true when an applicable grant contains the requested code.
        raise NotImplementedError

    def add_permission(self, grant: TableAccessGrant) -> None:
        # TODO: Add a grant without duplicating an existing equivalent grant.
        raise NotImplementedError

    def remove_permission(self, grant_id: int) -> None:
        # TODO: Remove the matching grant and report missing IDs consistently.
        raise NotImplementedError
