"""Credential verification and login/logout operations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory.auth.models import Session, User
    from inventory.persistence.repositories.users import UserRepository


class Authentication:
    """Own password hashing, credential checks, and session issuance."""

    def __init__(self, user_repository: "UserRepository") -> None:
        # TODO: Retain the user repository and initialize an in-memory session store.
        self._user_repository = user_repository
        self._sessions: dict[str, "Session"] = {}

    def login(self, username: str, password: str) -> "Session":
        # TODO: Verify credentials and issue a session for the user's current roles.
        raise NotImplementedError

    def logout(self, session: "Session") -> None:
        # TODO: Invalidate and remove the supplied session token.
        raise NotImplementedError

    def get_session(self, session_token: str) -> "Session | None":
        # TODO: Resolve a live session by token without accepting expired sessions.
        raise NotImplementedError

    def change_password(self, user: "User", new_password: str) -> None:
        # TODO: Hash the new password and update the authentication record securely.
        raise NotImplementedError

    def verify_credentials(self, username: str, password: str) -> bool:
        # TODO: Load the stored hash and compare it with a hash of the supplied password.
        raise NotImplementedError
