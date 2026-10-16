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

    def _row_to_user(self, row: Any) -> User:
        """Convert a DB row into a User object."""
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

    def find_by_id(self, entity_id: int) -> User | None:
        """Fetch and map one user profile by user ID."""
        rows = self.db.execute_query(
            "SELECT user_id, username, email, role_id, created_at "
            "FROM users WHERE user_id = ?",
            (entity_id,),
        )
        if not rows:
            return None
        return self._row_to_user(rows[0])

    def find_all(self, filters: object | None = None) -> list[User]:
        """Return all user profiles ordered by user ID."""
        rows = self.db.execute_query(
            "SELECT user_id, username, email, role_id, created_at "
            "FROM users ORDER BY user_id ASC",
        )
        return [self._row_to_user(row) for row in rows]

    def save(self, entity: User) -> User:
        """Insert or update a user profile.

        Never stores a password — credentials are managed separately
        via find_credentials_by_username and the users.password_hash column.
        """
        created_at_str = (
            entity.created_at.isoformat()
            if isinstance(entity.created_at, datetime)
            else str(entity.created_at)
        )

        if entity.user_id and self.find_by_id(entity.user_id) is not None:
            # Row already exists — update profile columns only.
            self.db.execute_query(
                "UPDATE users "
                "SET username = ?, email = ?, role_id = ?, created_at = ? "
                "WHERE user_id = ?",
                (
                    entity.username,
                    entity.email,
                    entity.role_id,
                    created_at_str,
                    entity.user_id,
                ),
            )
            return entity

        elif entity.user_id and entity.user_id > 0:
            # Known ID but row does not exist yet — insert with explicit ID.
            self.db.execute_query(
                "INSERT INTO users (user_id, username, email, role_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    entity.user_id,
                    entity.username,
                    entity.email,
                    entity.role_id,
                    created_at_str,
                ),
            )
            return entity

        else:
            # No ID — let the DB auto-assign one.
            rows = self.db.execute_query(
                "INSERT INTO users (username, email, role_id, created_at) "
                "VALUES (?, ?, ?, ?) RETURNING user_id",
                (
                    entity.username,
                    entity.email,
                    entity.role_id,
                    created_at_str,
                ),
            )
            if rows:
                entity.user_id = int(rows[0]["user_id"])
            elif self.db.last_inserted_id:
                entity.user_id = self.db.last_inserted_id
            return entity

    def delete(self, entity_id: int) -> None:
        """Delete a user by ID. Raises KeyError if the user does not exist."""
        if self.find_by_id(entity_id) is None:
            raise KeyError(f"User with ID {entity_id} does not exist.")
        self.db.execute_query(
            "DELETE FROM users WHERE user_id = ?",
            (entity_id,),
        )

    def find_by_username(self, username: str) -> User | None:
        """Find a user by username (case-insensitive) for authentication."""
        rows = self.db.execute_query(
            "SELECT user_id, username, email, role_id, created_at "
            "FROM users WHERE LOWER(username) = LOWER(?)",
            (username,),
        )
        if not rows:
            return None
        return self._row_to_user(rows[0])

    def find_credentials_by_username(self, username: str) -> UserCredentials | None:
        """Fetch the stored password hash for a given username.

        Returns None if the user does not exist or has no password set.
        This is the only method that reads password_hash — the hash is
        never exposed on the User object itself.
        """
        rows = self.db.execute_query(
            "SELECT user_id, password_hash "
            "FROM users WHERE LOWER(username) = LOWER(?)",
            (username,),
        )
        if not rows:
            return None
        row = rows[0]
        if row["password_hash"] is None:
            return None
        return UserCredentials(
            user_id=int(row["user_id"]),
            password_hash=str(row["password_hash"]),
        )

    def save_password_hash(self, user_id: int, password_hash: str) -> None:
        """Write a hashed password for an existing user.

        Called by Authentication.register_credentials and
        Authentication.change_password — never called with a plain-text password.
        """
        if self.find_by_id(user_id) is None:
            raise KeyError(f"User with ID {user_id} does not exist.")
        self.db.execute_query(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (password_hash, user_id),
        )
