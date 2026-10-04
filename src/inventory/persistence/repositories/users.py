"""Persistence operations for users."""

from inventory.auth.models import User
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class UserRepository(Repository[User, object]):
    """User storage, including lookup by username."""

    def __init__(self, db: DatabaseManager) -> None:
        # TODO: Initialize the base repository with the shared database manager.
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
        # TODO: Find a user by normalized username for authentication.
        raise NotImplementedError
