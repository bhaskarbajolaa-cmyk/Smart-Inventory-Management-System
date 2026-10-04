"""Shared abstract contract for entity-specific repositories."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from inventory.persistence.database import DatabaseManager

EntityT = TypeVar("EntityT")
FilterT = TypeVar("FilterT")


class Repository(ABC, Generic[EntityT, FilterT]):
    """Base contract; concrete repositories are the only DB access layer."""

    def __init__(self, db: DatabaseManager) -> None:
        # TODO: Retain the shared DatabaseManager instance.
        self.db = db

    @abstractmethod
    def find_by_id(self, entity_id: int) -> EntityT | None:
        # TODO: Fetch one entity by its primary key.
        raise NotImplementedError

    @abstractmethod
    def find_all(self, filters: FilterT | None = None) -> list[EntityT]:
        # TODO: Return entities matching the supplied filter object.
        raise NotImplementedError

    @abstractmethod
    def save(self, entity: EntityT) -> EntityT:
        # TODO: Insert or update the entity and return the persisted representation.
        raise NotImplementedError

    @abstractmethod
    def delete(self, entity_id: int) -> None:
        # TODO: Delete the entity by primary key and define missing-ID behavior.
        raise NotImplementedError
