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
        """Fetch one user profile by user ID."""
        sql = (
            "SELECT user_id, username, email, role_id, created_at "
            "FROM users WHERE user_id = ?"
        )
        rows = self.db.execute_query(sql, (entity_id,))
        if not rows:
            return None
        return self._row_to_user(rows[0])

    def find_all(self, filters: object | None = None) -> list[User]:
        """Return user profiles matching optional search criteria."""
        sql = "SELECT user_id, username, email, role_id, created_at FROM users"
        conditions: list[str] = []
        params: list[Any] = []

        if isinstance(filters, dict):
            if filters.get("username"):
                conditions.append("LOWER(username) LIKE LOWER(?)")
                params.append(f"%{filters['username']}%")
            if filters.get("email"):
                conditions.append("LOWER(email) LIKE LOWER(?)")
                params.append(f"%{filters['email']}%")
            if filters.get("role_id") is not None:
                conditions.append("role_id = ?")
                params.append(filters["role_id"])

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY user_id ASC"
        rows = self.db.execute_query(sql, params)
        return [self._row_to_user(row) for row in rows]

    def save(self, entity: User) -> User:
        """Persist the user profile without writing credential data here."""
        if entity.user_id and self.find_by_id(entity.user_id) is not None:
            self.db.execute_query(
                "UPDATE users SET username = ?, email = ?, role_id = ?, created_at = ? WHERE user_id = ?",
                (
                    entity.username,
                    entity.email,
                    entity.role_id,
                    entity.created_at.isoformat(),
                    entity.user_id,
                ),
            )
            return entity
        if entity.user_id and entity.user_id > 0:
            self.db.execute_query(
                "INSERT INTO users (user_id, username, email, role_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    entity.user_id,
                    entity.username,
                    entity.email,
                    entity.role_id,
                    entity.created_at.isoformat(),
                ),
            )
            return entity

        rows = self.db.execute_query(
            "INSERT INTO users (username, email, role_id, created_at) VALUES (?, ?, ?, ?) RETURNING user_id",
            (
                entity.username,
                entity.email,
                entity.role_id,
                entity.created_at.isoformat(),
            ),
        )
        if rows:
            entity.user_id = int(rows[0]["user_id"])
        elif self.db.last_inserted_id:
            entity.user_id = int(self.db.last_inserted_id)
        return entity

    def delete(self, entity_id: int) -> None:
        """Delete the user row and require an existing record."""
        user = self.find_by_id(entity_id)
        if user is None:
            raise KeyError(f"User with ID {entity_id} does not exist.")
        self.db.execute_query("DELETE FROM users WHERE user_id = ?", (entity_id,))

    def find_by_username(self, username: str) -> User | None:
        """Find a user by username in a case-insensitive lookup."""
        rows = self.db.execute_query(
            "SELECT user_id, username, email, role_id, created_at FROM users WHERE LOWER(username) = LOWER(?)",
            (username,),
        )
        if not rows:
            return None
        return self._row_to_user(rows[0])

    # ------------------------------------------------------------------
    # Credential access — used exclusively by Authentication service
    # ------------------------------------------------------------------

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
