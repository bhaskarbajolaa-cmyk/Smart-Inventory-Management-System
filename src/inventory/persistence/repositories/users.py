"""Persistence operations for users."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from inventory.auth.models import User, UserCredentials
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class UserRepository(Repository[User, object]):
    """User storage, including lookup by username and credential retrieval."""

    def __init__(self, db: DatabaseManager) -> None:
        super().__init__(db)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_user(self, row: Any) -> User:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        return User(
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            email=str(row["email"]),
            role_id=int(row["role_id"]) if row["role_id"] is not None else 0,
            created_at=created_at,
        )

    # ------------------------------------------------------------------
    # Repository contract
    # ------------------------------------------------------------------

    def find_by_id(self, entity_id: int) -> User | None:
        # TODO: Fetch and map one user profile by user ID.
        raise NotImplementedError

    def find_all(self, filters: object | None = None) -> list[User]:
        # TODO: Return user profiles matching optional query criteria.
        raise NotImplementedError

    def save(self, entity: User) -> User:
        # TODO: Persist the profile without storing credentials on the User entity.
        raise NotImplementedError

    def delete(self, entity_id: int) -> None:
        # TODO: Delete or deactivate a user according to session/audit requirements.
        raise NotImplementedError

    def find_by_username(self, username: str) -> User | None:
        # TODO: Find a user by normalised username for authentication.
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Credential access — used exclusively by Authentication service
    # ------------------------------------------------------------------

    def find_credentials_by_username(self, username: str) -> UserCredentials | None:
        """Fetch the stored password hash for a given username.

        Returns a ``UserCredentials`` object (user_id + password_hash) or
        ``None`` when the username is unknown.  This method is intentionally
        separate from ``find_by_username`` so that the password hash is only
        loaded when explicitly needed by ``Authentication``.
        """
        # TODO: SELECT user_id, password_hash FROM users WHERE LOWER(username) = LOWER(?)
        raise NotImplementedError
