"""Worker-thread scheduler for queued jobs.

Author: Mohit Purohit
"""

from __future__ import annotations

from threading import Condition, RLock, Thread, Timer
import time
from typing import Callable

from inventory.concurrency.jobs import Job, JobStatus
from inventory.concurrency.queue import PriorityJobQueue
from inventory.concurrency.retry import RetryPolicy


class RequestScheduler:
    """Submit, execute, retry, cancel, and shut down jobs."""

    def __init__(
        self,
        queue: PriorityJobQueue,
        retry_policy: RetryPolicy,
        worker_count: int = 3,
        max_queue_depth: int = 100,
    ) -> None:
        """Store dependencies and initialize worker/active-job tracking state."""
        if worker_count <= 0:
            raise ValueError("worker_count must be positive")
        if max_queue_depth <= 0:
            raise ValueError("max_queue_depth must be positive")

        self.queue = queue
        self.retry_policy = retry_policy
        self.worker_count = worker_count
        self.max_queue_depth = max_queue_depth
        self.active_jobs = 0
        self._pending_retries = 0

        self._workers: list[Thread] = []
        self._running_jobs: dict[str, Job] = {}
        self._timers: list[Timer] = []
        self._state_lock = RLock()
        self._idle_condition = Condition(self._state_lock)
        self._accepting = True
        self._stopped = False
        self._started = False

    # Property aliases matching Class_Diagram.png camelCase
    @property
    def workerCount(self) -> int:
        return self.worker_count

    @property
    def activeJobs(self) -> int:
        return self.active_jobs

    @property
    def maxQueueDepth(self) -> int:
        return self.max_queue_depth

    def _ensure_workers_started(self) -> None:
        """Start worker threads on demand when first work is submitted."""
        with self._state_lock:
            if not self._started and not self._stopped:
                self._started = True
                for i in range(self.worker_count):
                    worker = Thread(
                        target=self._worker_loop,
                        name=f"RequestSchedulerWorker-{i}",
                        daemon=True,
                    )
                    self._workers.append(worker)
                    worker.start()

    def _worker_loop(self) -> None:
        """Continuous execution loop running on worker threads."""
        while True:
            with self._state_lock:
                if self._stopped and self.queue.size() == 0 and self._pending_retries == 0:
                    break

            job = self.queue.dequeue()
            if job is None:
                with self._state_lock:
                    if self._stopped or (
                        not self._accepting
                        and self.queue.size() == 0
                        and self._pending_retries == 0
                    ):
                        break
                time.sleep(0.01)
                continue

            if job.status == JobStatus.CANCELLED:
                continue

            with self._state_lock:
                self.active_jobs += 1
                self._running_jobs[job.job_id] = job

            try:
                res = job.run()
                if not res.succeeded and self.retry_policy:
                    err = Exception(res.error or "Job execution failed")
                    if self.retry_policy.should_retry(err, job.attempt):
                        delay_sec = self.retry_policy.next_delay(job.attempt) / 1000.0
                        self.schedule_retry(job, delay_sec)
            finally:
                with self._state_lock:
                    self._running_jobs.pop(job.job_id, None)
                    self.active_jobs -= 1
                    if (
                        self.active_jobs == 0
                        and self.queue.size() == 0
                        and self._pending_retries == 0
                    ):
                        self._idle_condition.notify_all()

    def submit(self, job: Job) -> str:
        """Enqueue the job or reject it when the queue is overloaded."""
        with self._state_lock:
            if not self._accepting or self._stopped:
                job.mark_failed("Scheduler is shut down or not accepting work.")
                raise RuntimeError("Scheduler is not accepting work.")

            if self.reject_when_overloaded(job):
                job.mark_failed("Scheduler capacity exceeded; job rejected.")
                raise RuntimeError("Scheduler queue capacity overloaded.")

            if not self.queue.enqueue(job):
                job.mark_failed("Queue full or paused.")
                raise RuntimeError("Queue rejected job.")

            self._ensure_workers_started()
            return job.job_id

    def cancel(self, job_id: str) -> bool:
        """Cancel queued work or signal cancellation to a running job."""
        with self._state_lock:
            if self.queue.remove(job_id):
                return True

            if job_id in self._running_jobs:
                self._running_jobs[job_id].cancel()
                return True

            return False

    def schedule_retry(self, job: Job, delay: float) -> None:
        """Resubmit a retryable job after the requested delay."""
        with self._state_lock:
            if not self._accepting or self._stopped:
                return

            try:
                job.retry()
            except RuntimeError:
                return

            self._pending_retries += 1

            def resubmit() -> None:
                with self._state_lock:
                    self._pending_retries -= 1
                    if not self._accepting or self._stopped:
                        if (
                            self.active_jobs == 0
                            and self.queue.size() == 0
                            and self._pending_retries == 0
                        ):
                            self._idle_condition.notify_all()
                        return
                    self.queue.enqueue(job)
                    self._idle_condition.notify_all()

            timer = Timer(delay, resubmit)
            timer.daemon = True
            self._timers.append(timer)
            timer.start()

    def scheduleRetry(self, job: Job, delay: float) -> None:
        """CamelCase alias matching Class_Diagram.png."""
        self.schedule_retry(job, delay)

    def reject_when_overloaded(self, job: Job | None = None) -> bool:
        """Reject work once active plus queued jobs exceed configured capacity."""
        with self._state_lock:
            current_depth = self.active_jobs + self.queue.size() + self._pending_retries
            return current_depth >= self.max_queue_depth

    def rejectWhenOverloaded(self, job: Job | None = None) -> bool:
        """CamelCase alias matching Class_Diagram.png."""
        return self.reject_when_overloaded(job)

    def wait_for_idle(self, timeout_seconds: float | None = None) -> bool:
        """Wait until both queued, active, and pending retry counts reach zero or timeout."""
        deadline = None
        if timeout_seconds is not None:
            deadline = time.monotonic() + timeout_seconds

        with self._state_lock:
            while (
                self.active_jobs > 0
                or self.queue.size() > 0
                or self._pending_retries > 0
            ):
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._idle_condition.wait(remaining)
                else:
                    self._idle_condition.wait(0.05)
            return True

    def shutdown_gracefully(self) -> None:
        """Stop accepting work and join workers after queued jobs finish."""
        with self._state_lock:
            self._accepting = False

        self.wait_for_idle(timeout_seconds=5.0)

        with self._state_lock:
            self._stopped = True
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()

        for worker in self._workers:
            if worker.is_alive():
                worker.join(timeout=1.0)

    def shutdownGracefully(self) -> None:
        """CamelCase alias matching Class_Diagram.png."""
        self.shutdown_gracefully()
