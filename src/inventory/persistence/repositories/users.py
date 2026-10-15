"""Persistence operations for users."""

from __future__ import annotations

from inventory.auth.models import User, UserCredentials
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class UserRepository(Repository[User, object]):
    """User storage, including lookup by username and credential retrieval."""

    def __init__(self, db: DatabaseManager) -> None:
        super().__init__(db)

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

    def find_credentials_by_username(self, username: str) -> UserCredentials | None:
        """Fetch the stored password hash for a given username."""
        rows = self.db.execute_query(
            "SELECT user_id, password_hash FROM users WHERE LOWER(username) = LOWER(?)",
            (username,),
        )
        if not rows:
            return None
        row = rows[0]
        password_hash = row["password_hash"]
        if password_hash is None:
            return None
        return UserCredentials(
            user_id=int(row["user_id"]),
            password_hash=str(password_hash),
        )
