"""Tests for thread-safe Job execution, lifecycle, and LockManager integration."""

import threading
import time
import pytest

from inventory.concurrency.jobs import Job, JobResult, JobStatus
from inventory.concurrency.locks import LockManager


def test_job_initial_state() -> None:
    lock_mgr = LockManager()
    job = Job(
        job_id="job-1",
        job_type="sale",
        resource_id="prod-100",
        priority=10,
        attempt=0,
        idempotency_key="key-1",
        operation=lambda: JobResult(succeeded=True),
        lock_manager=lock_mgr,
    )
    assert job.job_id == "job-1"
    assert job.job_type == "sale"
    assert job.resource_id == "prod-100"
    assert job.priority == 10
    assert job.attempt == 0
    assert job.idempotency_key == "key-1"
    assert job.status == JobStatus.PENDING
    assert job.result is None
    assert job.error is None

    # Test Class_Diagram.png property aliases
    assert job.jobId == "job-1"
    assert job.type == "sale"
    assert job.resourceId == "prod-100"
    assert job.idempotencyKey == "key-1"


def test_job_successful_run() -> None:
    lock_mgr = LockManager()
    executed = False

    def op() -> JobResult:
        nonlocal executed
        executed = True
        # Lock should be held during execution
        assert lock_mgr.is_held("prod-1")
        return JobResult(succeeded=True, value={"order_id": 42})

    job = Job(
        job_id="job-success",
        job_type="sale",
        resource_id="prod-1",
        priority=5,
        attempt=0,
        idempotency_key="idem-1",
        operation=op,
        lock_manager=lock_mgr,
    )

    result = job.run()
    assert executed is True
    assert result.succeeded is True
    assert result.value == {"order_id": 42}
    assert job.status == JobStatus.SUCCEEDED
    # Lock must be released after run completes
    assert not lock_mgr.is_held("prod-1")


def test_job_run_handles_boolean_and_value_returns() -> None:
    lock_mgr = LockManager()
    job = Job(
        job_id="job-bool",
        job_type="inventory_check",
        resource_id="prod-2",
        priority=1,
        attempt=0,
        idempotency_key="idem-2",
        operation=lambda: True,
        lock_manager=lock_mgr,
    )
    res = job.run()
    assert res.succeeded is True
    assert job.status == JobStatus.SUCCEEDED


def test_job_failed_operation() -> None:
    lock_mgr = LockManager()

    def op() -> JobResult:
        return JobResult(succeeded=False, error="Insufficient stock")

    job = Job(
        job_id="job-fail",
        job_type="sale",
        resource_id="prod-3",
        priority=1,
        attempt=0,
        idempotency_key="idem-3",
        operation=op,
        lock_manager=lock_mgr,
    )

    result = job.run()
    assert result.succeeded is False
    assert result.error == "Insufficient stock"
    assert job.status == JobStatus.FAILED
    assert job.error == "Insufficient stock"
    # Lock must be released even on failure
    assert not lock_mgr.is_held("prod-3")


def test_job_operation_raises_exception() -> None:
    lock_mgr = LockManager()

    def broken_op() -> JobResult:
        raise ValueError("Database connection dropped")

    job = Job(
        job_id="job-error",
        job_type="purchase",
        resource_id="prod-4",
        priority=1,
        attempt=0,
        idempotency_key="idem-4",
        operation=broken_op,
        lock_manager=lock_mgr,
    )

    result = job.run()
    assert result.succeeded is False
    assert "Database connection dropped" in result.error
    assert job.status == JobStatus.FAILED
    assert not lock_mgr.is_held("prod-4")


def test_job_lock_acquisition_failure() -> None:
    lock_mgr = LockManager(default_timeout_ms=50)
    # Manually hold the lock from outside
    token = lock_mgr.acquire(resource_id="prod-locked", owner_id="other-owner")
    assert token is not None

    job = Job(
        job_id="job-timeout",
        job_type="sale",
        resource_id="prod-locked",
        priority=1,
        attempt=0,
        idempotency_key="idem-5",
        operation=lambda: JobResult(succeeded=True),
        lock_manager=lock_mgr,
        lock_timeout_ms=50,
    )

    result = job.run()
    assert result.succeeded is False
    assert "Could not acquire lock" in result.error
    assert job.status == JobStatus.FAILED

    lock_mgr.release(token)


def test_job_cancellation() -> None:
    lock_mgr = LockManager()
    job = Job(
        job_id="job-cancel",
        job_type="sale",
        resource_id="prod-5",
        priority=1,
        attempt=0,
        idempotency_key="idem-6",
        operation=lambda: JobResult(succeeded=True),
        lock_manager=lock_mgr,
    )

    assert job.status == JobStatus.PENDING
    job.cancel()
    assert job.status == JobStatus.CANCELLED

    # Running a cancelled job must not execute the operation
    result = job.run()
    assert result.succeeded is False
    assert "cancelled" in result.error.lower()
    assert not lock_mgr.is_held("prod-5")


def test_job_retry() -> None:
    lock_mgr = LockManager()
    job = Job(
        job_id="job-retry",
        job_type="sale",
        resource_id="prod-6",
        priority=1,
        attempt=0,
        idempotency_key="idem-7",
        operation=lambda: JobResult(succeeded=False, error="Temporary glitch"),
        lock_manager=lock_mgr,
    )

    job.run()
    assert job.status == JobStatus.FAILED
    assert job.attempt == 0

    job.retry()
    assert job.attempt == 1
    assert job.status == JobStatus.PENDING
    assert job.error is None
    assert job.result is None


def test_job_retry_on_cancelled_raises() -> None:
    lock_mgr = LockManager()
    job = Job(
        job_id="job-cancel-retry",
        job_type="sale",
        resource_id="prod-7",
        priority=1,
        attempt=0,
        idempotency_key="idem-8",
        operation=lambda: JobResult(succeeded=True),
        lock_manager=lock_mgr,
    )
    job.cancel()
    with pytest.raises(RuntimeError):
        job.retry()


def test_job_mark_failed_and_alias() -> None:
    lock_mgr = LockManager()
    job = Job(
        job_id="job-mf",
        job_type="sale",
        resource_id="prod-8",
        priority=1,
        attempt=0,
        idempotency_key="idem-9",
        operation=lambda: JobResult(succeeded=True),
        lock_manager=lock_mgr,
    )

    job.mark_failed("Critical abort")
    assert job.status == JobStatus.FAILED
    assert job.error == "Critical abort"
    assert job.result.succeeded is False

    job.markFailed("Second abort")
    assert job.error == "Second abort"


def test_job_concurrent_resource_locking() -> None:
    """Ensure two jobs on the same resource execute sequentially via lock exclusivity."""
    lock_mgr = LockManager(default_timeout_ms=3000)
    execution_order = []
    lock = threading.Lock()

    def work(job_name: str) -> JobResult:
        with lock:
            execution_order.append(f"{job_name}_start")
        time.sleep(0.05)
        with lock:
            execution_order.append(f"{job_name}_end")
        return JobResult(succeeded=True)

    job_a = Job(
        job_id="job-A",
        job_type="sale",
        resource_id="shared-resource",
        priority=1,
        attempt=0,
        idempotency_key="key-A",
        operation=lambda: work("jobA"),
        lock_manager=lock_mgr,
    )

    job_b = Job(
        job_id="job-B",
        job_type="sale",
        resource_id="shared-resource",
        priority=1,
        attempt=0,
        idempotency_key="key-B",
        operation=lambda: work("jobB"),
        lock_manager=lock_mgr,
    )

    thread_a = threading.Thread(target=job_a.run)
    thread_b = threading.Thread(target=job_b.run)

    thread_a.start()
    thread_b.start()

    thread_a.join()
    thread_b.join()

    assert job_a.status == JobStatus.SUCCEEDED
    assert job_b.status == JobStatus.SUCCEEDED
    # Exclusivity: one must completely finish before the other starts
    assert (
        execution_order == ["jobA_start", "jobA_end", "jobB_start", "jobB_end"]
        or execution_order == ["jobB_start", "jobB_end", "jobA_start", "jobA_end"]
    )
