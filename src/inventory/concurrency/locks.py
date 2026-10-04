"""Per-resource lock management for concurrent stock changes."""

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
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
        # TODO: Initialize the per-resource lock map and its master lock.
        self.locks: dict[str, Any] = {}
        self._master_lock = Lock()
        self.default_timeout_ms = default_timeout_ms
        self.lease_ms = lease_ms

    def acquire(
        self,
        resource_id: str,
        owner_id: str,
        timeout_ms: int | None = None,
    ) -> LockToken | None:
        # TODO: Safely get/create a resource lock and acquire it within the timeout.
        raise NotImplementedError

    def release(self, token: LockToken) -> None:
        # TODO: Validate token ownership and release its resource lock exactly once.
        raise NotImplementedError

    def renew(self, token: LockToken) -> bool:
        # TODO: Extend a still-valid lease and update the supplied token expiry.
        raise NotImplementedError

    def is_held(self, resource_id: str) -> bool:
        # TODO: Report whether a resource currently has an acquired lock.
        raise NotImplementedError

    def expire_stale_locks(self) -> int:
        # TODO: Release expired leases safely and return the number cleaned up.
        raise NotImplementedError
