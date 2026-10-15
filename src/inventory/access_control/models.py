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
    """Permission grant scoped to one table or all tables.

    ``permission`` is a fully-hydrated ``Permission`` object loaded via a JOIN
    from the ``permissions`` table when the grant is fetched from the database.
    The ``permission_code`` TEXT column has been removed from the
    ``table_access_grants`` schema to eliminate the redundant denormalised copy
    that could go stale when a permission code was renamed.
    """

    grant_id: int
    role_id: int
    permission_id: int
    table_name: str | None
    granted_by: int
    granted_at: datetime
    permission: Permission  # hydrated via JOIN — never store a bare code string

    def applies_to(self, table_name: str | None) -> bool:
        """Return true when the grant applies to the requested table.

        A grant with ``table_name is None`` is global and therefore applies to
        every table unless the caller did not specify a table at all.
        """
        if table_name is None:
            return self.table_name is None or self.table_name == ""
        if self.table_name is None or self.table_name == "":
            return True
        return self.table_name.lower() == table_name.lower()


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
        """Return true when this role grants the requested permission."""
        for grant in self.grants:
            if grant.permission.code == code and grant.applies_to(table_name):
                return True
        return False

    def add_permission(self, grant: TableAccessGrant) -> None:
        """Add a grant only when an equivalent one is not already present."""
        for existing in self.grants:
            if (
                existing.role_id == grant.role_id
                and existing.permission_id == grant.permission_id
                and existing.table_name == grant.table_name
            ):
                return
        self.grants.append(grant)

    def remove_permission(self, grant_id: int) -> None:
        """Remove the matching grant; raise KeyError when no grant matches."""
        for index, grant in enumerate(self.grants):
            if grant.grant_id == grant_id:
                del self.grants[index]
                return
        raise KeyError(f"Grant with ID {grant_id} does not exist.")
