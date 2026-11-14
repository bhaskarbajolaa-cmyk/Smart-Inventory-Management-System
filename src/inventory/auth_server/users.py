"""Demo identity directory for the standalone OpenID Connect provider.

The provider deliberately does not read the inventory database. It is an
identity provider: it authenticates *its own* accounts and asserts who the
user is. Mapping that assertion onto an inventory user (and its role) is the
resource server's job, which is exactly what ``Session.google_token`` with a
``user_resolver`` does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DemoUser:
    """An account hosted by the demo identity provider."""

    subject: str
    email: str
    name: str
    picture: str = ""
    email_verified: bool = True

    @property
    def initial(self) -> str:
        return (self.name or self.email or "?")[:1].upper()


DEFAULT_USERS: tuple[DemoUser, ...] = (
    DemoUser(
        subject="google-oauth2|1001",
        email="demo@smobhar.local",
        name="Demo User",
        picture="",
    ),
    DemoUser(
        subject="google-oauth2|1002",
        email="manager@smobhar.local",
        name="Inventory Manager",
        picture="",
    ),
)


class DemoUserDirectory:
    """In-memory account lookup by email or subject."""

    def __init__(self, users: Iterable[DemoUser] | None = None) -> None:
        selected = tuple(users) if users is not None else DEFAULT_USERS
        if not selected:
            raise ValueError("The identity directory needs at least one user.")
        self._users = selected
        self._by_email = {user.email.lower(): user for user in selected}
        self._by_subject = {user.subject: user for user in selected}
        if len(self._by_email) != len(selected) or len(self._by_subject) != len(selected):
            raise ValueError("Demo users must have unique emails and subjects.")

    def find_by_email(self, email: str | None) -> DemoUser | None:
        if not email:
            return None
        return self._by_email.get(email.strip().lower())

    def find_by_subject(self, subject: str | None) -> DemoUser | None:
        if not subject:
            return None
        return self._by_subject.get(subject)

    def all(self) -> tuple[DemoUser, ...]:
        return self._users
