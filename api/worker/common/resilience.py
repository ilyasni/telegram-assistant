"""
Resilience utilities for group digest pipeline.

Context7 best practices:
- RetryPolicy (exponential backoff + jitter) inspired by LangGraph / Kafka executor patterns.
- Circuit breaker with half-open probing (Context Engineering resilience patterns).
- DLQ publisher schema aligned with `worker/events/schemas/dlq_v1.py`.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Sequence, Tuple, Type, Union

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Retry Policy
# ---------------------------------------------------------------------------


DEFAULT_RETRY_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
)


@dataclass(frozen=True)
class RetryPolicy:
    """Retry configuration (Context7: langgraph RetryPolicy analogue)."""

    initial_interval: float = 0.5
    backoff_factor: float = 2.0
    max_interval: float = 30.0
    max_attempts: int = 3
    jitter: bool = True
    retry_on: Union[
        Type[BaseException],
        Sequence[Type[BaseException]],
        Callable[[BaseException], bool],
    ] = DEFAULT_RETRY_EXCEPTIONS

    def should_retry(self, error: BaseException) -> bool:
        handler = self.retry_on
        if callable(handler):
            return bool(handler(error))
        if isinstance(handler, Sequence):
            return any(isinstance(error, exc) for exc in handler)
        return isinstance(error, handler)


def retry_sync(
    func: Callable[..., Any],
    *,
    policy: RetryPolicy,
    args: Optional[Sequence[Any]] = None,
    kwargs: Optional[dict[str, Any]] = None,
) -> Any:
    """Синхронный retry с экспоненциальным backoff и джиттером."""
    args = tuple(args or ())
    kwargs = dict(kwargs or {})

    attempt = 0
    interval = policy.initial_interval
    last_error: Optional[BaseException] = None

    while attempt < policy.max_attempts:
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # pylint: disable=broad-except
            last_error = exc
            attempt += 1
            if attempt >= policy.max_attempts or not policy.should_retry(exc):
                break
            sleep_for = interval
            if policy.jitter:
                sleep_for += random.uniform(0, interval)  # noqa: S311 (non-crypto jitter)
            logger.warning(
                "retry_sync",
                attempt=attempt,
                max_attempts=policy.max_attempts,
                sleep=round(sleep_for, 2),
                error=str(exc),
            )
            time.sleep(sleep_for)
            interval = min(policy.max_interval, interval * policy.backoff_factor)
    if last_error:
        raise last_error
    raise RuntimeError("retry_sync failed without exception")  # pragma: no cover


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreakerState:
    failure_count: int = 0
    last_failure_at: Optional[datetime] = None
    state: str = "closed"  # closed | open | half_open
    next_attempt_at: Optional[datetime] = None


class CircuitBreaker:
    """Простой circuit breaker с half-open пробой."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._state = CircuitBreakerState()
        self._half_open_calls = 0

    def before_call(self) -> None:
        if self._state.state == "open":
            if self._state.next_attempt_at and datetime.utcnow() >= self._state.next_attempt_at:
                self._state.state = "half_open"
                self._half_open_calls = 0
            else:
                raise CircuitOpenError("Circuit open; skipping call")
        if self._state.state == "half_open":
            if self._half_open_calls >= self.half_open_max_calls:
                raise CircuitOpenError("Circuit half-open throttle")
            self._half_open_calls += 1

    def record_success(self) -> None:
        self._state = CircuitBreakerState()
        self._half_open_calls = 0

    def record_failure(self) -> None:
        self._state.failure_count += 1
        self._state.last_failure_at = datetime.utcnow()
        if self._state.failure_count >= self.failure_threshold:
            self._state.state = "open"
            self._state.next_attempt_at = datetime.utcnow() + timedelta(seconds=self.recovery_timeout)


class CircuitOpenError(RuntimeError):
    """Raised when circuit is open and calls are blocked."""


def guarded_call(
    func: Callable[..., Any],
    *,
    breaker: CircuitBreaker,
    retry_policy: Optional[RetryPolicy] = None,
    args: Optional[Sequence[Any]] = None,
    kwargs: Optional[dict[str, Any]] = None,
) -> Any:
    """Обёртка для вызова функции с circuit breaker и опциональным retry."""

    breaker.before_call()

    def _call() -> Any:
        return func(*(args or ()), **(kwargs or {}))

    try:
        if retry_policy:
            result = retry_sync(_call, policy=retry_policy)
        else:
            result = _call()
    except Exception as exc:  # pylint: disable=broad-except
        breaker.record_failure()
        raise
    else:
        breaker.record_success()
        return result


# ---------------------------------------------------------------------------
# DLQ Payload Helper
# ---------------------------------------------------------------------------


def build_dlq_payload(
    *,
    base_event_type: str,
    payload_snippet: dict[str, Any],
    error_code: str,
    error_details: str,
    retry_count: int,
    tenant_id: Optional[str],
    stack_trace: Optional[str] = None,
    next_retry_at: Optional[datetime] = None,
    first_seen_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Формирует payload для `DLQEventV1`."""
    now = datetime.utcnow()
    if first_seen_at is None:
        first_seen_at = now
    return {
        "event_type": "dlq.message",
        "schema_version": "1.0",
        "base_event_type": base_event_type,
        "payload_snippet": payload_snippet,
        "error_code": error_code,
        "error_details": error_details[:500],
        "error_stack_trace": stack_trace[:1024] if stack_trace else None,
        "retry_count": retry_count,
        "first_seen_at": first_seen_at.isoformat(),
        "last_seen_at": now.isoformat(),
        "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
        "tenant_id": tenant_id,
    }

