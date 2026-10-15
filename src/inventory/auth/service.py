"""Credential verification and login/logout operations."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from inventory.auth.models import Session

if TYPE_CHECKING:
    from inventory.auth.models import User
    from inventory.persistence.repositories.users import UserRepository


class Authentication:
    """Own password hashing, credential checks, and session issuance."""

    def __init__(self, user_repository: "UserRepository") -> None:
        self._user_repository = user_repository
        self._sessions: dict[str, Session] = {}

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
        """Verify credentials and issue a session for the user's current role."""
        user = self._user_repository.find_by_username(username)
        if user is None:
            raise PermissionError("Invalid username or password.")

        credentials = self._user_repository.find_credentials_by_username(username)
        if credentials is None or not self.verify_credentials(username, password):
            raise PermissionError("Invalid username or password.")

        roles = [str(user.role_id)] if user.role_id else []
        issued_at = datetime.now()
        expires_at = issued_at + timedelta(hours=1)
        token = self._make_session_token(user.user_id, roles)
        session = Session(
            session_token=token,
            user_id=user.user_id,
            roles=roles,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self._sessions[token] = session
        return session

    def logout(self, session: Session) -> None:
        """Invalidate and remove the supplied session token."""
        self._sessions.pop(session.session_token, None)

    def get_session(self, session_token: str) -> Session | None:
        """Resolve a live session by token without accepting expired sessions."""
        session = self._sessions.get(session_token)
        if session is None:
            return None
        if session.is_expired():
            self._sessions.pop(session_token, None)
            return None
        return session

    def change_password(self, user: User, new_password: str) -> None:
        """Hash the new password and update the credential record securely."""
        digest = self._hash_password(new_password)
        self._user_repository.db.execute_query(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (digest, user.user_id),
        )

    def verify_credentials(self, username: str, password: str) -> bool:
        """Load the stored hash and compare it with a hash of the supplied password."""
        credentials = self._user_repository.find_credentials_by_username(username)
        if credentials is None:
            return False
        return credentials.password_hash == self._hash_password(password)
