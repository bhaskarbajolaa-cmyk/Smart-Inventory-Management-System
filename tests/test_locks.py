"""Tests for thread-safe per-resource LockManager."""

from datetime import datetime, timedelta, timezone
from threading import Thread
import time
import pytest

from inventory.concurrency.locks import LockManager, LockToken


def test_lock_manager_init_validation() -> None:
    mgr = LockManager(default_timeout_ms=500, lease_ms=2000)
    assert mgr.default_timeout_ms == 500
    assert mgr.lease_ms == 2000

    with pytest.raises(ValueError):
        LockManager(default_timeout_ms=-1)

    with pytest.raises(ValueError):
        LockManager(lease_ms=0)


def test_acquire_and_release() -> None:
    mgr = LockManager(default_timeout_ms=500, lease_ms=3000)
    assert not mgr.is_held("PROD-1")

    token = mgr.acquire("PROD-1", "owner-1")
    assert token is not None
    assert token.resource_id == "PROD-1"
    assert token.owner_id == "owner-1"
    assert token.expires_at > token.issued_at
    assert mgr.is_held("PROD-1")

    # Release and verify it is freed
    mgr.release(token)
    assert not mgr.is_held("PROD-1")


def test_mutual_exclusion() -> None:
    mgr = LockManager(default_timeout_ms=50, lease_ms=2000)
    token1 = mgr.acquire("PROD-1", "owner-1")
    assert token1 is not None

    # Second acquire should time out and return None
    start = time.monotonic()
    token2 = mgr.acquire("PROD-1", "owner-2", timeout_ms=50)
    elapsed = time.monotonic() - start
    assert token2 is None
    assert elapsed >= 0.04

    # Once owner-1 releases, owner-2 can acquire
    mgr.release(token1)
    token3 = mgr.acquire("PROD-1", "owner-2", timeout_ms=100)
    assert token3 is not None
    assert token3.owner_id == "owner-2"
    mgr.release(token3)


def test_concurrent_different_resources() -> None:
    mgr = LockManager()
    token_a = mgr.acquire("PROD-A", "owner-1")
    token_b = mgr.acquire("PROD-B", "owner-2")
    assert token_a is not None
    assert token_b is not None
    assert mgr.is_held("PROD-A")
    assert mgr.is_held("PROD-B")

    mgr.release(token_a)
    assert not mgr.is_held("PROD-A")
    assert mgr.is_held("PROD-B")
    mgr.release(token_b)
    assert not mgr.is_held("PROD-B")


def test_non_blocking_acquire() -> None:
    mgr = LockManager()
    token1 = mgr.acquire("PROD-1", "owner-1", timeout_ms=0)
    assert token1 is not None

    token2 = mgr.acquire("PROD-1", "owner-2", timeout_ms=0)
    assert token2 is None
    mgr.release(token1)


def test_release_ownership_validation_and_idempotence() -> None:
    mgr = LockManager()
    token = mgr.acquire("PROD-1", "owner-1")
    assert token is not None

    # Imposter token with different owner cannot release
    fake_token = LockToken(
        resource_id="PROD-1",
        owner_id="imposter",
        issued_at=token.issued_at,
        expires_at=token.expires_at,
    )
    mgr.release(fake_token)
    assert mgr.is_held("PROD-1")

    # Legitimate owner releases
    mgr.release(token)
    assert not mgr.is_held("PROD-1")

    # Releasing again is a safe no-op
    mgr.release(token)
    assert not mgr.is_held("PROD-1")


def test_renew_lease() -> None:
    mgr = LockManager(lease_ms=500)
    token = mgr.acquire("PROD-1", "owner-1")
    assert token is not None
    original_expiry = token.expires_at

    time.sleep(0.05)
    success = mgr.renew(token)
    assert success is True
    assert token.expires_at > original_expiry

    # Cannot renew non-held or fake token
    fake_token = LockToken(
        resource_id="PROD-99",
        owner_id="nobody",
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    assert mgr.renew(fake_token) is False

    mgr.release(token)
    assert mgr.renew(token) is False


def test_expire_stale_locks() -> None:
    # Short lease of 50ms
    mgr = LockManager(lease_ms=50)
    token = mgr.acquire("PROD-1", "owner-1")
    assert token is not None

    time.sleep(0.07)
    # Stale locks cleaned up
    cleaned = mgr.expire_stale_locks()
    assert cleaned == 1
    assert not mgr.is_held("PROD-1")

    # Another worker can acquire immediately without waiting
    token2 = mgr.acquire("PROD-1", "owner-2", timeout_ms=50)
    assert token2 is not None
    mgr.release(token2)


def test_multithreaded_sequential_access() -> None:
    mgr = LockManager()
    counter = 0

    def worker(worker_id: str) -> None:
        nonlocal counter
        tok = mgr.acquire("SHARED-RESOURCE", worker_id, timeout_ms=2000)
        assert tok is not None
        current = counter
        time.sleep(0.01)
        counter = current + 1
        mgr.release(tok)

    threads = [Thread(target=worker, args=(f"worker-{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert counter == 5
    assert not mgr.is_held("SHARED-RESOURCE")
