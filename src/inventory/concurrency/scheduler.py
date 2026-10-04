"""Worker-thread scheduler for queued jobs."""

from inventory.concurrency.jobs import Job
from inventory.concurrency.queue import PriorityJobQueue
from inventory.concurrency.retry import RetryPolicy
from threading import Thread


class RequestScheduler:
    """Submit, execute, retry, cancel, and shut down jobs."""

    def __init__(
        self,
        queue: PriorityJobQueue,
        retry_policy: RetryPolicy,
        worker_count: int = 3,
        max_queue_depth: int = 100,
    ) -> None:
        # TODO: Store dependencies and initialize worker/active-job tracking state.
        self.queue = queue
        self.retry_policy = retry_policy
        self.worker_count = worker_count
        self.max_queue_depth = max_queue_depth
        self.active_jobs = 0
        self._workers: list[Thread] = []
        self._accepting = True

    def submit(self, job: Job) -> str:
        # TODO: Enqueue the job or reject it when the queue is overloaded.
        raise NotImplementedError

    def cancel(self, job_id: str) -> bool:
        # TODO: Cancel queued work or signal cancellation to a running job.
        raise NotImplementedError

    def schedule_retry(self, job: Job, delay: float) -> None:
        # TODO: Resubmit a retryable job after the requested delay.
        raise NotImplementedError

    def reject_when_overloaded(self, job: Job) -> bool:
        # TODO: Reject work once active plus queued jobs exceed configured capacity.
        raise NotImplementedError

    def wait_for_idle(self, timeout_seconds: float | None = None) -> bool:
        # TODO: Wait until both queued and active job counts reach zero or timeout.
        raise NotImplementedError

    def shutdown_gracefully(self) -> None:
        # TODO: Stop accepting work and join workers after queued jobs finish.
        raise NotImplementedError
