"""Schedulable work unit and its result/status types."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from inventory.concurrency.locks import LockManager


class JobStatus(str, Enum):
    """Lifecycle state of a scheduled job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobResult:
    """Outcome returned when a job runs."""

    succeeded: bool
    value: Any = None
    error: str | None = None


@dataclass
class Job:
    """One unit of work submitted to the request scheduler."""

    job_id: str
    job_type: str
    resource_id: str
    priority: int
    attempt: int
    idempotency_key: str
    operation: Callable[[], JobResult]
    lock_manager: LockManager
    status: JobStatus = JobStatus.PENDING

    def run(self) -> JobResult:
        # TODO: Acquire the resource lock, run/commit the operation, and release in finally.
        raise NotImplementedError

    def retry(self) -> None:
        # TODO: Increment attempts and return a retryable job to pending state.
        raise NotImplementedError

    def cancel(self) -> None:
        # TODO: Mark a not-yet-running job cancelled and prevent its execution.
        raise NotImplementedError

    def mark_failed(self, reason: str) -> None:
        # TODO: Record the terminal failure reason and update job status.
        raise NotImplementedError
