"""Priority queue for scheduled jobs."""

import heapq
from threading import Condition, RLock

from inventory.concurrency.jobs import Job


class PriorityJobQueue:
    """Capacity-limited priority heap for jobs."""

    def __init__(self, capacity: int = 100) -> None:
        """Create a queue that can hold at most ``capacity`` pending jobs."""
        if capacity < 0:
            raise ValueError("capacity must be non-negative")

        self.capacity = capacity
        self.heap: list[tuple[int, int, Job]] = []
        self.accepting = True
        self._insertion_order = 0
        self._lock = RLock()
        self._condition = Condition(self._lock)

    def enqueue(self, job: Job) -> bool:
        """Add ``job`` when the queue is accepting work and has capacity."""
        with self._condition:
            if not self.accepting or len(self.heap) >= self.capacity:
                return False

            heapq.heappush(
                self.heap,
                (job.priority, self._insertion_order, job),
            )
            self._insertion_order += 1
            self._condition.notify()
            return True

    def dequeue(self) -> Job | None:
        """Remove the next job, returning ``None`` if paused or empty."""
        with self._lock:
            if not self.accepting or not self.heap:
                return None
            return heapq.heappop(self.heap)[2]

    def peek(self) -> Job | None:
        """Return the next pending job without removing it."""
        with self._lock:
            return self.heap[0][2] if self.heap else None

    def remove(self, job_id: str) -> bool:
        """Remove the first pending job whose ID is ``job_id``."""
        with self._lock:
            for index, (_, _, job) in enumerate(self.heap):
                if job.job_id == job_id:
                    last_entry = self.heap.pop()
                    if index < len(self.heap):
                        self.heap[index] = last_entry
                        heapq.heapify(self.heap)
                    return True
            return False

    def size(self) -> int:
        """Return the number of pending jobs."""
        with self._lock:
            return len(self.heap)

    def pause(self) -> None:
        """Pause submissions and dequeue operations."""
        with self._condition:
            self.accepting = False

    def resume(self) -> None:
        """Resume submissions and dequeue operations."""
        with self._condition:
            self.accepting = True
            self._condition.notify_all()
