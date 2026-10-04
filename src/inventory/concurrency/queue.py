"""Priority queue for scheduled jobs."""

from inventory.concurrency.jobs import Job


class PriorityJobQueue:
    """Capacity-limited priority heap for jobs."""

    def __init__(self, capacity: int = 100) -> None:
        # TODO: Initialize a thread-safe heap, stable insertion counter, and queue state.
        self.capacity = capacity
        self.heap: list[tuple[int, int, Job]] = []
        self.accepting = True
        self._insertion_order = 0

    def enqueue(self, job: Job) -> bool:
        # TODO: Reject a full or paused queue; enqueue by priority and stable order.
        raise NotImplementedError

    def dequeue(self) -> Job | None:
        # TODO: Remove and return the highest-priority job, or None when unavailable.
        raise NotImplementedError

    def peek(self) -> Job | None:
        # TODO: Return the next job without removing it.
        raise NotImplementedError

    def remove(self, job_id: str) -> bool:
        # TODO: Remove a pending job by ID and restore heap ordering.
        raise NotImplementedError

    def size(self) -> int:
        # TODO: Return the current number of queued jobs.
        raise NotImplementedError

    def pause(self) -> None:
        # TODO: Stop new jobs from being dequeued until resumed.
        raise NotImplementedError

    def resume(self) -> None:
        # TODO: Resume queue processing and notify waiting workers.
        raise NotImplementedError
