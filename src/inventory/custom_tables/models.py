"""Stretch-only custom table schema and record models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CustomFieldDefinition:
    """Field metadata belonging to a custom table."""

    field_id: int
    table_id: int
    field_name: str
    data_type: str
    required: bool = False
    display_order: int = 0

    def validate(self, value: Any) -> bool:
        # TODO: Validate nullability and value type against data_type and required.
        raise NotImplementedError


@dataclass
class CustomTableDefinition:
    """Admin-defined custom table and its field schema."""

    table_id: int
    table_name: str
    owner_id: int
    description: str
    created_at: datetime
    fields: list[CustomFieldDefinition] = field(default_factory=list)

    def add_field(self, field_definition: CustomFieldDefinition) -> None:
        # TODO: Validate field name/type uniqueness before adding it to the schema.
        raise NotImplementedError

    def remove_field(self, field_id: int) -> None:
        # TODO: Remove the requested field and define behavior for existing records.
        raise NotImplementedError

    def get_schema(self) -> list[CustomFieldDefinition]:
        # TODO: Return fields in their configured display order.
        raise NotImplementedError


@dataclass
class CustomRecord:
    """JSON-like data stored against a custom table definition."""

    record_id: int
    table_id: int
    data: dict[str, Any]
    created_at: datetime

    def get_field(self, field_name: str) -> Any:
        # TODO: Return a field value and define behavior when the field is absent.
        raise NotImplementedError

    def set_field(self, field_name: str, value: Any) -> None:
        # TODO: Update a field value after validating it against the table schema.
        raise NotImplementedError

    def validate_against_schema(self, schema: list[CustomFieldDefinition]) -> bool:
        # TODO: Validate required fields, types, and unexpected keys against schema.
        raise NotImplementedError
