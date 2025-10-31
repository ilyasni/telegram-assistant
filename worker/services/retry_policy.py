"""
Retry Policy и DLQ Contract
Context7 best practice: exponential backoff + jitter, DLQ для dead letters
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Callable, TypeVar, Optional, Any, Dict

import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
    retry_if_exception_type,
    retry_if_result,
    RetryCallState,
    before_sleep_log,
    after_log,
)

from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

retry_attempts_total = Counter(
    'retry_attempts_total',
    'Total retry attempts',
    ['operation', 'error_type']
)

retry_success_total = Counter(
    'retry_success_total',
    'Successful retries after retries',
    ['operation']
)

retry_failures_total = Counter(
    'retry_failures_total',
    'Failed operations after all retries',
    ['operation', 'error_type']
)

retry_duration_seconds = Histogram(
    'retry_duration_seconds',
    'Total duration of retry operations',
    ['operation', 'result']
)


# ============================================================================
# ERROR CLASSIFICATION
# ============================================================================

class ErrorCategory(Enum):
    """Категория ошибки для определения retry стратегии."""
    RETRYABLE_NETWORK = "retryable_network"  # Network timeout, connection errors
    RETRYABLE_RATE_LIMIT = "retryable_rate_limit"  # HTTP 429
    RETRYABLE_SERVER_ERROR = "retryable_server_error"  # HTTP 5xx
    NON_RETRYABLE_VALIDATION = "non_retryable_validation"  # 4xx (except 429)
    NON_RETRYABLE_BUSINESS = "non_retryable_business"  # Business logic errors
    NON_RETRYABLE_QUOTA = "non_retryable_quota"  # Quota exceeded


@dataclass
class RetryConfig:
    """Конфигурация retry политики."""
    max_attempts: int = 5
    initial_delay_sec: float = 1.0
    max_delay_sec: float = 60.0
    jitter_enabled: bool = True
    exponential_base: float = 2.0
    
    # Retryable exceptions
    retryable_exceptions: tuple = (
        ConnectionError,
        TimeoutError,
        asyncio.TimeoutError,
    )
    
    # Non-retryable exceptions
    non_retryable_exceptions: tuple = (
        ValueError,
        TypeError,
        KeyError,
    )


# Default config
DEFAULT_RETRY_CONFIG = RetryConfig()


def classify_error(error: Exception) -> ErrorCategory:
    """
    Классификация ошибки для определения retry стратегии.
    
    Returns:
        ErrorCategory для ошибки
    """
    error_type = type(error).__name__
    error_str = str(error).lower()
    
    # Network errors
    if isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return ErrorCategory.RETRYABLE_NETWORK
    
    # HTTP errors
    if hasattr(error, 'status_code'):
        status = getattr(error, 'status_code', 0)
        if status == 429:
            return ErrorCategory.RETRYABLE_RATE_LIMIT
        elif 500 <= status < 600:
            return ErrorCategory.RETRYABLE_SERVER_ERROR
        elif 400 <= status < 500:
            return ErrorCategory.NON_RETRYABLE_VALIDATION
    
    # Quota errors
    if 'quota' in error_str or 'limit' in error_str or 'exceeded' in error_str:
        return ErrorCategory.NON_RETRYABLE_QUOTA
    
    # Validation errors
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return ErrorCategory.NON_RETRYABLE_VALIDATION
    
    # Default: retryable (network-like)
    return ErrorCategory.RETRYABLE_NETWORK


def should_retry(category: ErrorCategory) -> bool:
    """Определяет, нужно ли retry для категории ошибки."""
    return category in (
        ErrorCategory.RETRYABLE_NETWORK,
        ErrorCategory.RETRYABLE_RATE_LIMIT,
        ErrorCategory.RETRYABLE_SERVER_ERROR,
    )


# ============================================================================
# RETRY DECORATORS
# ============================================================================

T = TypeVar('T')


def create_retry_decorator(
    config: RetryConfig = DEFAULT_RETRY_CONFIG,
    operation_name: str = "operation"
) -> Callable:
    """
    Создание retry декоратора с конфигурируемой политикой.
    
    Args:
        config: Конфигурация retry
        operation_name: Название операции для метрик
        
    Returns:
        Декоратор для функции
    """
    # Wait strategy с jitter
    if config.jitter_enabled:
        wait_strategy = wait_random_exponential(
            multiplier=config.initial_delay_sec,
            max=config.max_delay_sec
        )
    else:
        wait_strategy = wait_exponential(
            multiplier=config.initial_delay_sec,
            min=config.initial_delay_sec,
            max=config.max_delay_sec
        )
    
    # Retry condition: retryable exceptions
    retry_condition = retry_if_exception_type(*config.retryable_exceptions)
    
    def log_before_sleep(retry_state: RetryCallState):
        """Логирование перед ожиданием retry."""
        if retry_state.outcome:
            exception = retry_state.outcome.exception()
            category = classify_error(exception) if exception else ErrorCategory.RETRYABLE_NETWORK
            error_type = type(exception).__name__ if exception else "Unknown"
            
            retry_attempts_total.labels(
                operation=operation_name,
                error_type=error_type
            ).inc()
            
            logger.warning(
                "Retrying operation",
                operation=operation_name,
                attempt=retry_state.attempt_number,
                max_attempts=config.max_attempts,
                error_type=error_type,
                category=category.value,
                wait_time=retry_state.next_action.sleep
            )
    
    def log_after(retry_state: RetryCallState):
        """Логирование после retry."""
        if retry_state.outcome and retry_state.outcome.successful():
            retry_success_total.labels(operation=operation_name).inc()
    
    return retry(
        stop=stop_after_attempt(config.max_attempts),
        wait=wait_strategy,
        retry=retry_condition,
        before_sleep=log_before_sleep,
        after=log_after,
        reraise=True
    )


# ============================================================================
# DLQ CONTRACT
# ============================================================================

@dataclass
class DLQEntry:
    """Запись в Dead Letter Queue."""
    base_event_type: str
    payload_snippet: Dict[str, Any]  # Первые 1KB
    error_code: str
    error_details: str
    error_stack_trace: Optional[str]
    retry_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    next_retry_at: Optional[datetime]
    trace_id: str
    tenant_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "base_event_type": self.base_event_type,
            "payload_snippet": self.payload_snippet,
            "error_code": self.error_code,
            "error_details": self.error_details[:500],  # Максимум 500 символов
            "error_stack_trace": self.error_stack_trace[:1000] if self.error_stack_trace else None,
            "retry_count": self.retry_count,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
            "trace_id": self.trace_id,
            "tenant_id": self.tenant_id,
        }


class DLQService:
    """
    Dead Letter Queue Service для обработки failed events.
    
    Features:
    - Хранение failed events в Redis Stream
    - Retry scheduling с exponential backoff
    - Manual requeue через API
    - Metrics и monitoring
    """
    
    def __init__(self, redis_client, stream_name: str = "stream:dlq"):
        self.redis = redis_client
        self.stream_name = stream_name
    
    async def send_to_dlq(
        self,
        base_event_type: str,
        payload: Dict[str, Any],
        error: Exception,
        retry_count: int,
        trace_id: str,
        tenant_id: Optional[str] = None
    ) -> str:
        """
        Отправка события в DLQ.
        
        Args:
            base_event_type: Тип оригинального события
            payload: Payload события (будет обрезан до 1KB)
            error: Ошибка, вызвавшая попадание в DLQ
            retry_count: Количество попыток retry
            trace_id: Trace ID для корреляции
            tenant_id: ID tenant (optional)
            
        Returns:
            Message ID в DLQ stream
        """
        category = classify_error(error)
        error_code = category.value
        
        # Обрезаем payload до 1KB
        import json
        payload_json = json.dumps(payload, default=str)
        if len(payload_json) > 1024:
            payload_snippet = json.loads(payload_json[:1024] + "...")
        else:
            payload_snippet = payload
        
        # Вычисляем next_retry_at (если retryable)
        next_retry_at = None
        if should_retry(category) and retry_count < DEFAULT_RETRY_CONFIG.max_attempts:
            delay = min(
                DEFAULT_RETRY_CONFIG.initial_delay_sec * (DEFAULT_RETRY_CONFIG.exponential_base ** retry_count),
                DEFAULT_RETRY_CONFIG.max_delay_sec
            )
            next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        
        entry = DLQEntry(
            base_event_type=base_event_type,
            payload_snippet=payload_snippet,
            error_code=error_code,
            error_details=str(error)[:500],
            error_stack_trace=self._format_exception(error),
            retry_count=retry_count,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            next_retry_at=next_retry_at,
            trace_id=trace_id,
            tenant_id=tenant_id,
        )
        
        # Отправка в Redis Stream
        message_id = await self.redis.xadd(
            self.stream_name,
            entry.to_dict()
        )
        
        logger.error(
            "Event sent to DLQ",
            base_event_type=base_event_type,
            error_code=error_code,
            retry_count=retry_count,
            trace_id=trace_id,
            message_id=message_id
        )
        
        return message_id
    
    def _format_exception(self, error: Exception) -> str:
        """Форматирование exception в строку."""
        import traceback
        return ''.join(traceback.format_exception(type(error), error, error.__traceback__))

