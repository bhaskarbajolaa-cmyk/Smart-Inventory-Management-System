"""Per-resource lock management for concurrent stock changes."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
import time
from typing import Any


@dataclass
class LockToken:
    """Token representing ownership of an acquired resource lock."""

    resource_id: str
    owner_id: str
    issued_at: datetime
    expires_at: datetime


class LockManager:
    """Manage real thread locks keyed by resource ID."""

    def __init__(self, default_timeout_ms: int = 1000, lease_ms: int = 5000) -> None:
        """Initialize the per-resource lock map and its master lock."""
        if default_timeout_ms < 0:
            raise ValueError("default_timeout_ms must be non-negative")
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")

        self.locks: dict[str, Any] = {}
        self._tokens: dict[str, LockToken] = {}
        self._master_lock = Lock()
        self.default_timeout_ms = default_timeout_ms
        self.lease_ms = lease_ms

    def acquire(
        self,
        resource_id: str,
        owner_id: str,
        timeout_ms: int | None = None,
    ) -> LockToken | None:
        """Safely get or create a resource lock and acquire it within the timeout."""
        if timeout_ms is not None:
            effective_timeout_ms = timeout_ms
        else:
            effective_timeout_ms = self.default_timeout_ms

        with self._master_lock:
            if resource_id not in self.locks:
                self.locks[resource_id] = Lock()
            lock = self.locks[resource_id]

        deadline = None
        if effective_timeout_ms >= 0:
            deadline = time.monotonic() + (effective_timeout_ms / 1000.0)

        while True:
            with self._master_lock:
                current_token = self._tokens.get(resource_id)
                if current_token is not None and self._is_expired(current_token):
                    if lock.locked():
                        try:
                            lock.release()
                        except RuntimeError:
                            pass
                    self._tokens.pop(resource_id, None)
                    current_token = None

                if deadline is None:
                    wait_time = -1.0
                else:
                    remaining_total = deadline - time.monotonic()
                    if remaining_total <= 0:
                        wait_time = 0.0
                    elif current_token is not None:
                        now_dt = datetime.now(timezone.utc) if current_token.expires_at.tzinfo else datetime.now()
                        lease_left = (current_token.expires_at - now_dt).total_seconds()
                        if lease_left > 0:
                            wait_time = min(remaining_total, lease_left)
                        else:
                            wait_time = min(remaining_total, 0.05)
                    else:
                        wait_time = remaining_total

            if wait_time < 0:
                acquired = lock.acquire()
            elif wait_time == 0.0:
                acquired = lock.acquire(blocking=False)
            else:
                acquired = lock.acquire(timeout=wait_time)

            if acquired:
                with self._master_lock:
                    now = datetime.now(timezone.utc)
                    expires_at = now + timedelta(milliseconds=self.lease_ms)
                    token = LockToken(
                        resource_id=resource_id,
                        owner_id=owner_id,
                        issued_at=now,
                        expires_at=expires_at,
                    )
                    self._tokens[resource_id] = token
                    return token

            if deadline is not None and time.monotonic() >= deadline:
                return None

    def release(self, token: LockToken) -> None:
        """Validate token ownership and release its resource lock exactly once."""
        if token is None or not isinstance(token, LockToken):
            return

        with self._master_lock:
            current_token = self._tokens.get(token.resource_id)
            if current_token is None:
                return
            if current_token.owner_id != token.owner_id or current_token.issued_at != token.issued_at:
                return

            self._tokens.pop(token.resource_id, None)
            lock = self.locks.get(token.resource_id)
            if lock is not None and lock.locked():
                try:
                    lock.release()
                except RuntimeError:
                    pass

    def renew(self, token: LockToken) -> bool:
        """Extend a still-valid lease and update the supplied token expiry."""
        if token is None or not isinstance(token, LockToken):
            return False

        with self._master_lock:
            current_token = self._tokens.get(token.resource_id)
            if current_token is None:
                return False
            if current_token.owner_id != token.owner_id or current_token.issued_at != token.issued_at:
                return False
            if self._is_expired(current_token):
                return False

            now = datetime.now(timezone.utc) if current_token.expires_at.tzinfo else datetime.now()
            new_expires_at = now + timedelta(milliseconds=self.lease_ms)
            current_token.expires_at = new_expires_at
            token.expires_at = new_expires_at
            return True

    def is_held(self, resource_id: str) -> bool:
        """Report whether a resource currently has an acquired lock."""
        with self._master_lock:
            current_token = self._tokens.get(resource_id)
            if current_token is None:
                return False
            if self._is_expired(current_token):
                lock = self.locks.get(resource_id)
                if lock is not None and lock.locked():
                    try:
                        lock.release()
                    except RuntimeError:
                        pass
                self._tokens.pop(resource_id, None)
                return False

            lock = self.locks.get(resource_id)
            return lock is not None and lock.locked()

    def expire_stale_locks(self) -> int:
        """Release expired leases safely and return the number cleaned up."""
        with self._master_lock:
            expired_resources = [
                res_id
                for res_id, token in self._tokens.items()
                if self._is_expired(token)
            ]
            for res_id in expired_resources:
                self._tokens.pop(res_id, None)
                lock = self.locks.get(res_id)
                if lock is not None and lock.locked():
                    try:
                        lock.release()
                    except RuntimeError:
                        pass
            return len(expired_resources)

    def _is_expired(self, token: LockToken) -> bool:
        """Report whether a lock token's lease has expired."""
        if token.expires_at.tzinfo is not None:
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()
        return now >= token.expires_at
