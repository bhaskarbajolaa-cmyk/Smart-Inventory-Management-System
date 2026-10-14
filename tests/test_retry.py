"""Tests for RetryPolicy exponential backoff and retry rules."""

import pytest

from inventory.concurrency.retry import RetryPolicy


def test_retry_policy_initial_state() -> None:
    policy = RetryPolicy(
        max_attempts=4,
        initial_delay_ms=100,
        max_delay_ms=2000,
        multiplier=1.5,
        jitter=False,
    )
    assert policy.max_attempts == 4
    assert policy.initial_delay_ms == 100
    assert policy.max_delay_ms == 2000
    assert policy.multiplier == 1.5
    assert policy.jitter is False

    # Diagram aliases
    assert policy.maxAttempts == 4
    assert policy.initialDelayMs == 100
    assert policy.maxDelayMs == 2000


def test_should_retry_rules() -> None:
    policy = RetryPolicy(max_attempts=3)

    # Transient errors are retryable within max_attempts
    assert policy.should_retry(TimeoutError("timed out"), attempt=0) is True
    assert policy.should_retry(RuntimeError("lock busy"), attempt=1) is True
    assert policy.should_retry(ConnectionError("socket closed"), attempt=2) is True

    # At or above max_attempts, should not retry
    assert policy.should_retry(TimeoutError("timed out"), attempt=3) is False
    assert policy.should_retry(TimeoutError("timed out"), attempt=4) is False

    # Domain / validation errors are not retryable
    assert policy.should_retry(ValueError("invalid price"), attempt=0) is False
    assert policy.should_retry(LookupError("product not found"), attempt=0) is False
    assert policy.should_retry(KeyError("missing key"), attempt=0) is False

    # Alias
    assert policy.shouldRetry(TimeoutError("timed out"), attempt=0) is True


def test_next_delay_backoff_and_capping() -> None:
    policy = RetryPolicy(
        initial_delay_ms=100,
        max_delay_ms=500,
        multiplier=2.0,
        jitter=False,
    )

    # attempt 0: 100 * (2^0) = 100
    assert policy.next_delay(0) == 100
    # attempt 1: 100 * (2^1) = 200
    assert policy.next_delay(1) == 200
    # attempt 2: 100 * (2^2) = 400
    assert policy.next_delay(2) == 400
    # attempt 3: 100 * (2^3) = 800 -> capped at 500
    assert policy.next_delay(3) == 500

    # Aliases
    assert policy.nextDelay(0) == 100
    assert policy.calculate_delay(1) == 200


def test_next_delay_with_jitter() -> None:
    policy = RetryPolicy(
        initial_delay_ms=200,
        max_delay_ms=1000,
        multiplier=2.0,
        jitter=True,
    )

    for attempt in range(4):
        delay = policy.next_delay(attempt)
        upper = min(200 * (2**attempt), 1000)
        assert 0 <= delay <= upper


def test_execute_with_retry_success() -> None:
    policy = RetryPolicy(max_attempts=3, initial_delay_ms=10, jitter=False)
    calls = 0

    def work() -> str:
        nonlocal calls
        calls += 1
        return "success"

    result = policy.execute_with_retry(work)
    assert result == "success"
    assert calls == 1


def test_execute_with_retry_after_transient_failures() -> None:
    policy = RetryPolicy(max_attempts=4, initial_delay_ms=5, jitter=False)
    calls = 0

    def flaky_work() -> int:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("Temporary timeout")
        return 42

    result = policy.execute_with_retry(flaky_work)
    assert result == 42
    assert calls == 3


def test_execute_with_retry_raises_when_exhausted() -> None:
    policy = RetryPolicy(max_attempts=2, initial_delay_ms=5, jitter=False)
    calls = 0

    def persistently_failing() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("Always times out")

    with pytest.raises(TimeoutError):
        policy.execute_with_retry(persistently_failing)

    assert calls == 2


def test_execute_with_retry_raises_immediately_on_non_retryable() -> None:
    policy = RetryPolicy(max_attempts=5, initial_delay_ms=5)
    calls = 0

    def bad_arg() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("Invalid argument")

    with pytest.raises(ValueError):
        policy.execute_with_retry(bad_arg)

    # Should NOT retry on ValueError
    assert calls == 1
