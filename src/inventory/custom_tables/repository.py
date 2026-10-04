"""Stretch-only persistence for custom records."""

from inventory.custom_tables.models import CustomRecord
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class CustomRecordRepository(Repository[CustomRecord, int]):
    """Store and query custom table records."""

    def __init__(self, db: DatabaseManager) -> None:
        # TODO: Initialize the shared repository base with the database manager.
        super().__init__(db)

    def find_by_id(self, entity_id: int) -> CustomRecord | None:
        # TODO: Fetch and decode one JSON-like custom record by record ID.
        raise NotImplementedError

    def find_all(self, filters: int | None = None) -> list[CustomRecord]:
        # TODO: Treat the optional filter as a table ID and fetch its records.
        raise NotImplementedError

    def save(self, entity: CustomRecord) -> CustomRecord:
        # TODO: Persist the record JSON after validating it against its table schema.
        raise NotImplementedError

    def delete(self, entity_id: int) -> None:
        # TODO: Delete a custom record by ID.
        raise NotImplementedError

    def find_by_table(self, table_id: int) -> list[CustomRecord]:
        # TODO: Return all records belonging to one custom table.
        raise NotImplementedError
