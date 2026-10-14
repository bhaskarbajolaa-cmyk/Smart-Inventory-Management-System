"""Tests for RequestScheduler worker execution, retries, and cancellation."""

import threading
import time
import pytest

from inventory.concurrency.jobs import Job, JobResult, JobStatus
from inventory.concurrency.locks import LockManager
from inventory.concurrency.queue import PriorityJobQueue
from inventory.concurrency.retry import RetryPolicy
from inventory.concurrency.scheduler import RequestScheduler


def test_scheduler_init_and_aliases() -> None:
    queue = PriorityJobQueue(capacity=50)
    retry_policy = RetryPolicy()
    scheduler = RequestScheduler(
        queue=queue,
        retry_policy=retry_policy,
        worker_count=2,
        max_queue_depth=20,
    )

    assert scheduler.worker_count == 2
    assert scheduler.max_queue_depth == 20
    assert scheduler.active_jobs == 0

    # Class_Diagram.png property aliases
    assert scheduler.workerCount == 2
    assert scheduler.maxQueueDepth == 20
    assert scheduler.activeJobs == 0

    with pytest.raises(ValueError):
        RequestScheduler(queue, retry_policy, worker_count=0)

    with pytest.raises(ValueError):
        RequestScheduler(queue, retry_policy, max_queue_depth=0)


def test_scheduler_submit_and_execute_jobs() -> None:
    queue = PriorityJobQueue(capacity=50)
    retry_policy = RetryPolicy()
    scheduler = RequestScheduler(queue=queue, retry_policy=retry_policy, worker_count=2)
    lock_mgr = LockManager()

    results = []
    lock = threading.Lock()

    def work(val: int) -> JobResult:
        with lock:
            results.append(val)
        return JobResult(succeeded=True, value=val)

    job1 = Job(
        job_id="job-1",
        job_type="test",
        resource_id="res-1",
        priority=1,
        attempt=0,
        idempotency_key="k1",
        operation=lambda: work(1),
        lock_manager=lock_mgr,
    )
    job2 = Job(
        job_id="job-2",
        job_type="test",
        resource_id="res-2",
        priority=2,
        attempt=0,
        idempotency_key="k2",
        operation=lambda: work(2),
        lock_manager=lock_mgr,
    )

    sub1 = scheduler.submit(job1)
    sub2 = scheduler.submit(job2)

    assert sub1 == "job-1"
    assert sub2 == "job-2"

    idle = scheduler.wait_for_idle(timeout_seconds=3.0)
    assert idle is True
    assert set(results) == {1, 2}
    assert job1.status == JobStatus.SUCCEEDED
    assert job2.status == JobStatus.SUCCEEDED

    scheduler.shutdown_gracefully()


def test_scheduler_cancel_queued_job() -> None:
    # Use paused queue to keep job queued
    queue = PriorityJobQueue(capacity=50)
    queue.pause()
    retry_policy = RetryPolicy()
    scheduler = RequestScheduler(queue=queue, retry_policy=retry_policy, worker_count=1)
    lock_mgr = LockManager()

    job = Job(
        job_id="to-cancel",
        job_type="test",
        resource_id="res-cancel",
        priority=1,
        attempt=0,
        idempotency_key="kc",
        operation=lambda: JobResult(succeeded=True),
        lock_manager=lock_mgr,
    )

    # Queue is paused so submit won't enqueue or we can enqueue manually
    queue.accepting = True
    queue.enqueue(job)

    cancelled = scheduler.cancel("to-cancel")
    assert cancelled is True
    assert queue.size() == 0

    scheduler.shutdown_gracefully()


def test_scheduler_overload_rejection() -> None:
    queue = PriorityJobQueue(capacity=50)
    queue.pause()  # pause so jobs don't get processed immediately
    retry_policy = RetryPolicy()
    scheduler = RequestScheduler(
        queue=queue,
        retry_policy=retry_policy,
        worker_count=1,
        max_queue_depth=2,
    )
    lock_mgr = LockManager()

    job1 = Job("j1", "t", "r1", 1, 0, "k1", lambda: JobResult(True), lock_mgr)
    job2 = Job("j2", "t", "r2", 1, 0, "k2", lambda: JobResult(True), lock_mgr)
    job3 = Job("j3", "t", "r3", 1, 0, "k3", lambda: JobResult(True), lock_mgr)

    queue.accepting = True
    scheduler.submit(job1)
    scheduler.submit(job2)

    # 3rd job should be rejected due to max_queue_depth=2
    with pytest.raises(RuntimeError):
        scheduler.submit(job3)

    assert job3.status == JobStatus.FAILED
    assert "capacity exceeded" in job3.error.lower() or "overloaded" in job3.error.lower()

    scheduler.shutdown_gracefully()


def test_scheduler_retry_flow() -> None:
    queue = PriorityJobQueue(capacity=50)
    retry_policy = RetryPolicy(max_attempts=3, initial_delay_ms=20, jitter=False)
    scheduler = RequestScheduler(queue=queue, retry_policy=retry_policy, worker_count=2)
    lock_mgr = LockManager()

    attempts_seen = []

    def flaky_operation() -> JobResult:
        attempts_seen.append(len(attempts_seen))
        if len(attempts_seen) < 3:
            return JobResult(succeeded=False, error="Transient failure")
        return JobResult(succeeded=True, value="Done")

    job = Job(
        job_id="flaky-job",
        job_type="test",
        resource_id="res-flaky",
        priority=1,
        attempt=0,
        idempotency_key="kf",
        operation=flaky_operation,
        lock_manager=lock_mgr,
    )

    scheduler.submit(job)

    # Wait for retry loop to finish
    idle = scheduler.wait_for_idle(timeout_seconds=5.0)
    assert idle is True
    assert len(attempts_seen) == 3
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempt == 2

    scheduler.shutdown_gracefully()
