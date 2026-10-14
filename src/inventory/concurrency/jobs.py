"""Schedulable work unit and its result/status types.

Author: Mohit Purohit
"""

from __future__ import annotations

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
    operation: Callable[[], JobResult | Any]
    lock_manager: LockManager
    status: JobStatus = JobStatus.PENDING
    lock_timeout_ms: int | None = None
    result: JobResult | None = None
    error: str | None = None

    # Aliases matching Class_Diagram.png naming
    @property
    def jobId(self) -> str:
        return self.job_id

    @property
    def type(self) -> str:
        return self.job_type

    @property
    def resourceId(self) -> str:
        return self.resource_id

    @property
    def idempotencyKey(self) -> str:
        return self.idempotency_key

    def run(self) -> JobResult:
        """Acquire the resource lock, run/commit the operation, and release in finally."""
        if self.status == JobStatus.CANCELLED:
            result = JobResult(succeeded=False, error="Job has been cancelled.")
            self.result = result
            return result

        if self.status == JobStatus.RUNNING:
            result = JobResult(succeeded=False, error="Job is already running.")
            return result

        self.status = JobStatus.RUNNING

        token = self.lock_manager.acquire(
            resource_id=self.resource_id,
            owner_id=self.job_id,
            timeout_ms=self.lock_timeout_ms,
        )
        if token is None:
            reason = f"Could not acquire lock for resource '{self.resource_id}'"
            self.mark_failed(reason)
            return self.result  # type: ignore[return-value]

        try:
            if self.status == JobStatus.CANCELLED:
                result = JobResult(succeeded=False, error="Job has been cancelled.")
                self.result = result
                return result

            outcome = self.operation()
            if isinstance(outcome, JobResult):
                result = outcome
            elif isinstance(outcome, bool):
                result = JobResult(succeeded=outcome)
            else:
                result = JobResult(succeeded=True, value=outcome)

            if result.succeeded:
                self.status = JobStatus.SUCCEEDED
                self.error = None
            else:
                self.status = JobStatus.FAILED
                self.error = result.error

            self.result = result
            return result
        except Exception as exc:
            self.mark_failed(str(exc))
            return self.result  # type: ignore[return-value]
        finally:
            self.lock_manager.release(token)

    def retry(self) -> None:
        """Increment attempts and return a retryable job to pending state."""
        if self.status == JobStatus.CANCELLED:
            raise RuntimeError("Cannot retry a cancelled job.")
        self.attempt += 1
        self.status = JobStatus.PENDING
        self.error = None
        self.result = None

    def cancel(self) -> None:
        """Mark a not-yet-running job cancelled and prevent its execution."""
        if self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            return
        self.status = JobStatus.CANCELLED
        self.error = "Job cancelled"
        self.result = JobResult(succeeded=False, error="Job cancelled")

    def mark_failed(self, reason: str) -> None:
        """Record the terminal failure reason and update job status."""
        self.status = JobStatus.FAILED
        self.error = reason
        self.result = JobResult(succeeded=False, error=reason)

    def markFailed(self, reason: str) -> None:
        """CamelCase alias matching Class_Diagram.png."""
        self.mark_failed(reason)
