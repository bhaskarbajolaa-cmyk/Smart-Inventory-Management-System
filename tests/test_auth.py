"""Unit tests for User, Session, and Authentication service."""

from datetime import datetime, timedelta, timezone
import pytest

from inventory.access_control.models import Permission, Role, TableAccessGrant
from inventory.auth.models import Session, User, UserCredentials
from inventory.auth.service import Authentication


class MockUserRepository:
    """In-memory mock for UserRepository to test Authentication without database."""

    def __init__(self):
        self.users: dict[str, User] = {}
        self.credentials: dict[str, UserCredentials] = {}
        self.db = None

    def find_by_username(self, username: str) -> User | None:
        return self.users.get(username.lower())

    def find_credentials_by_username(self, username: str) -> UserCredentials | None:
        return self.credentials.get(username.lower())


def test_user_password_attribute():
    user = User(
        user_id=1,
        username="admin",
        email="admin@example.com",
        role_id=1,
        created_at=datetime.now(),
        password="secretpassword",
    )
    assert user.password == "secretpassword"


def test_user_role_management():
    admin = User(
        user_id=1,
        username="admin",
        email="admin@example.com",
        role_id=1,
        created_at=datetime.now(),
    )
    regular_user = User(
        user_id=2,
        username="john",
        email="john@example.com",
        role_id=2,
        created_at=datetime.now(),
    )

    # Admin creates a role with role_id=2 (non-admin)
    role = admin.create_role("Supervisor", ["VIEW", "EDIT"], role_id=2)

    assert role.role_name == "Supervisor"
    assert len(role.grants) == 2
    assert role.has_permission("VIEW") is True
    assert role.has_permission("EDIT") is True

    # Non-admin cannot create roles
    with pytest.raises(PermissionError):
        regular_user.create_role("Hacker", ["ALL"])

    # Admin assigns role
    admin.assign_role(regular_user, role)
    assert regular_user.role_id == role.role_id

    # Non-admin cannot assign roles
    with pytest.raises(PermissionError):
        regular_user.assign_role(admin, role)

    # Deleting system default role fails
    system_role = Role(role_id=1, role_name="Admin", created_by=1, is_system_default=True, default_job_priority=1)
    with pytest.raises(ValueError):
        admin.delete_role(system_role)


def test_session_expiration_and_token():
    now = datetime.now(timezone.utc)
    active_session = Session(
        session_token="token_active",
        user_id=1,
        roles=["1"],
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        password="test",
    )
    assert active_session.is_expired() is False
    assert active_session.password == "test"

    expired_session = Session(
        session_token="token_expired",
        user_id=1,
        roles=["1"],
        issued_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )
    assert expired_session.is_expired() is True

    claims = active_session.decode_token()
    assert claims["user_id"] == 1
    assert claims["roles"] == ["1"]


def test_authentication_service_flow():
    repo = MockUserRepository()
    auth = Authentication(repo)

    # Setup a user with credentials
    password_hash = Authentication._hash_password("mypassword")
    repo.credentials["testuser"] = UserCredentials(user_id=42, password_hash=password_hash)
    repo.users["testuser"] = User(
        user_id=42,
        username="testuser",
        email="test@example.com",
        role_id=2,
        created_at=datetime.now(),
    )

    # 1. Verify credentials
    assert auth.verify_credentials("testuser", "mypassword") is True
    assert auth.verify_credentials("testuser", "wrongpassword") is False
    assert auth.verify_credentials("nonexistent", "mypassword") is False

    # 2. Login succeeds
    session = auth.login("testuser", "mypassword")
    assert session.user_id == 42
    assert session.roles == ["2"]
    assert session.session_token is not None

    # Login with bad password fails
    with pytest.raises(PermissionError):
        auth.login("testuser", "wrongpassword")

    # 3. get_session
    retrieved = auth.get_session(session.session_token)
    assert retrieved is not None
    assert retrieved.user_id == 42

    # Unknown token returns None
    assert auth.get_session("invalid_token") is None

    # 4. Logout invalidates session
    auth.logout(session)
    assert auth.get_session(session.session_token) is None
