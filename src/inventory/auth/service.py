"""Credential verification and login/logout operations."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from inventory.auth.models import Session

if TYPE_CHECKING:
    from inventory.persistence.repositories.users import UserRepository


class Authentication:
    """Own password hashing, credential checks, and session issuance."""

    def __init__(self, user_repository: "UserRepository") -> None:
        self._user_repository = user_repository

    @staticmethod
    def _hash_password(password: str) -> str:
        """Return a stable SHA-256 digest for a user password."""
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def _make_session_token(self, user_id: int, roles: list[str]) -> str:
        """Create a JSON-encoded, URL-safe token carrying the session claims."""
        payload: dict[str, Any] = {
            "user_id": user_id,
            "roles": roles,
            "issued_at": datetime.now().isoformat(),
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return encoded.rstrip("=")

    def login(self, username: str, password: str) -> Session:
        """Verify credentials and issue a session."""
        credentials = self._user_repository.find_credentials_by_username(username)
        if (
            credentials is None
            or credentials.password_hash != self._hash_password(password)
        ):
            raise PermissionError("Invalid username or password.")

        roles: list[str] = []
        issued_at = datetime.now()
        expires_at = issued_at + timedelta(hours=1)
        token = self._make_session_token(credentials.user_id, roles)
        session = Session(
            session_token=token,
            user_id=credentials.user_id,
            roles=roles,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return session

    def logout(self, session: Session) -> None:
        # TODO: Invalidate and remove the supplied session token.
        raise NotImplementedError

    def get_session(self, session_token: str) -> Session | None:
        # TODO: Resolve a live session by token without accepting expired sessions.
        raise NotImplementedError

    def change_password(self, user: "User", new_password: str) -> None:
        # TODO: Hash the new password and update the authentication record securely.
        raise NotImplementedError

    def verify_credentials(self, username: str, password: str) -> bool:
        """Load the stored hash and compare it with a hash of the supplied password."""
        credentials = self._user_repository.find_credentials_by_username(username)
        if credentials is None:
            return False
        return credentials.password_hash == self._hash_password(password)
