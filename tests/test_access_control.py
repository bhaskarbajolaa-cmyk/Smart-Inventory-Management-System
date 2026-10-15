"""Unit tests for access control models."""

from datetime import datetime
import pytest

from inventory.access_control.models import Permission, Role, TableAccessGrant


def test_table_access_grant_applies_to():
    global_grant = TableAccessGrant(
        grant_id=1,
        role_id=1,
        permission_id=1,
        table_name=None,
        granted_by=1,
        granted_at=datetime.now(),
        permission_code="VIEW",
    )
    assert global_grant.applies_to("products") is True
    assert global_grant.applies_to("users") is True

    table_grant = TableAccessGrant(
        grant_id=2,
        role_id=2,
        permission_id=2,
        table_name="products",
        granted_by=1,
        granted_at=datetime.now(),
        permission_code="EDIT",
    )
    assert table_grant.applies_to("products") is True
    assert table_grant.applies_to("suppliers") is False


def test_role_has_permission():
    grant_global = TableAccessGrant(
        grant_id=1,
        role_id=1,
        permission_id=1,
        table_name=None,
        granted_by=1,
        granted_at=datetime.now(),
        permission_code="VIEW",
    )
    grant_table = TableAccessGrant(
        grant_id=2,
        role_id=1,
        permission_id=2,
        table_name="products",
        granted_by=1,
        granted_at=datetime.now(),
        permission_code="EDIT",
    )
    role = Role(
        role_id=1,
        role_name="Manager",
        created_by=1,
        is_system_default=False,
        default_job_priority=3,
        grants=[grant_global, grant_table],
    )

    # Global permission applies everywhere
    assert role.has_permission("VIEW") is True
    assert role.has_permission("VIEW", "products") is True
    assert role.has_permission("VIEW", "suppliers") is True

    # Scoped permission applies only to target table
    assert role.has_permission("EDIT", "products") is True
    assert role.has_permission("EDIT", "suppliers") is False

    # Unassigned permission
    assert role.has_permission("DELETE") is False


def test_role_add_and_remove_permission():
    role = Role(
        role_id=1,
        role_name="Clerk",
        created_by=1,
        is_system_default=False,
        default_job_priority=2,
        grants=[],
    )

    grant = TableAccessGrant(
        grant_id=10,
        role_id=1,
        permission_id=1,
        table_name="products",
        granted_by=1,
        granted_at=datetime.now(),
        permission_code="VIEW",
    )
    role.add_permission(grant)
    assert len(role.grants) == 1

    # Adding duplicate grant is a no-op
    duplicate_grant = TableAccessGrant(
        grant_id=11,
        role_id=1,
        permission_id=1,
        table_name="products",
        granted_by=1,
        granted_at=datetime.now(),
        permission_code="VIEW",
    )
    role.add_permission(duplicate_grant)
    assert len(role.grants) == 1

    # Removing existing grant
    role.remove_permission(10)
    assert len(role.grants) == 0

    # Removing non-existent grant raises KeyError
    with pytest.raises(KeyError):
        role.remove_permission(999)
