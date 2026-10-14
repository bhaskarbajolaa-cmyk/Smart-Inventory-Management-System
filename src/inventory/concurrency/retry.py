"""Retry decisions and delay calculation for failed jobs.

Author: Mohit Purohit
"""

from __future__ import annotations

from dataclasses import dataclass
import random
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass
class RetryPolicy:
    """Determine whether a failed operation should be retried."""

    max_attempts: int = 3
    initial_delay_ms: int = 200
    max_delay_ms: int = 5000
    multiplier: float = 2.0
    jitter: bool = True

    # Property aliases matching Class_Diagram.png camelCase
    @property
    def maxAttempts(self) -> int:
        return self.max_attempts

    @property
    def initialDelayMs(self) -> int:
        return self.initial_delay_ms

    @property
    def maxDelayMs(self) -> int:
        return self.max_delay_ms

    def should_retry(self, error: Exception | None, attempt: int) -> bool:
        """Apply retryable-error rules and stop after max_attempts."""
        if attempt >= self.max_attempts:
            return False

        # Non-retryable domain/validation errors
        if isinstance(error, (ValueError, LookupError, KeyError, PermissionError)):
            return False

        return True

    def shouldRetry(self, error: Exception | None, attempt: int) -> bool:
        """CamelCase alias matching Class_Diagram.png."""
        return self.should_retry(error, attempt)

    def next_delay(self, attempt: int) -> int:
        """Calculate a bounded backoff delay in ms with optional random jitter."""
        if attempt < 0:
            attempt = 0

        calculated = self.initial_delay_ms * (self.multiplier**attempt)
        bounded = min(calculated, float(self.max_delay_ms))

        if self.jitter and bounded > 0:
            delay = random.uniform(bounded * 0.5, bounded)
        else:
            delay = bounded

        return int(delay)

    def nextDelay(self, attempt: int) -> int:
        """CamelCase alias matching Class_Diagram.png."""
        return self.next_delay(attempt)

    def calculate_delay(self, attempt: int) -> int:
        """Alias for next_delay matching issue tracker naming."""
        return self.next_delay(attempt)

    def reset(self) -> None:
        """Reset any per-operation retry state if the policy tracks it."""
        pass

    def execute_with_retry(
        self,
        operation: Callable[[], T],
        on_retry: Callable[[Exception, int, int], None] | None = None,
    ) -> T:
        """Execute a callable with automatic retries according to this policy."""
        attempt = 1
        while True:
            try:
                return operation()
            except Exception as exc:
                if not self.should_retry(exc, attempt):
                    raise exc

                delay_ms = self.next_delay(attempt - 1)
                if on_retry:
                    on_retry(exc, attempt, delay_ms)

                time.sleep(delay_ms / 1000.0)
                attempt += 1
