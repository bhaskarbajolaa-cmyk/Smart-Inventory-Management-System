"""Retry decisions and delay calculation for failed jobs."""

from dataclasses import dataclass


@dataclass
class RetryPolicy:
    """Determine whether a failed operation should be retried."""

    max_attempts: int = 3
    initial_delay_ms: int = 200
    max_delay_ms: int = 5000
    multiplier: float = 2.0
    jitter: bool = True

    def should_retry(self, error: Exception, attempt: int) -> bool:
        # TODO: Apply retryable-error rules and stop after max_attempts.
        raise NotImplementedError

    def next_delay(self, attempt: int) -> int:
        # TODO: Calculate a bounded backoff delay and optional random jitter.
        raise NotImplementedError

    def reset(self) -> None:
        # TODO: Reset any per-operation retry state if the policy tracks it.
        raise NotImplementedError
