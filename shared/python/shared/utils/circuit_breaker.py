"""
Circuit Breaker для защиты от каскадных сбоев внешних API.

Context7 best practice: защита от каскадных сбоев через circuit breaker pattern.
Состояния: CLOSED (нормальная работа), OPEN (блокировка), HALF_OPEN (тестирование).

Использование:
    circuit_breaker = CircuitBreaker(
        name="gigachat_vision",
        failure_threshold=5,
        recovery_timeout=60
    )
    
    try:
        result = await circuit_breaker.call_async(api_call, *args, **kwargs)
    except CircuitBreakerOpenError:
        # Circuit breaker открыт, используем fallback
        result = await fallback_call()
"""

import time
from enum import Enum
from typing import Optional, Callable, Any, Dict
from datetime import datetime, timezone
import structlog
from prometheus_client import Counter, Gauge

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

circuit_breaker_state = Gauge(
    'circuit_breaker_state',
    'Current state of circuit breaker',
    ['name', 'state']  # state: closed=0, open=1, half_open=2
)

circuit_breaker_transitions_total = Counter(
    'circuit_breaker_transitions_total',
    'Total circuit breaker state transitions',
    ['name', 'from_state', 'to_state']
)

circuit_breaker_failures_total = Counter(
    'circuit_breaker_failures_total',
    'Total circuit breaker failures',
    ['name']
)

circuit_breaker_calls_total = Counter(
    'circuit_breaker_calls_total',
    'Total circuit breaker calls',
    ['name', 'result']  # result: success, failure, rejected
)


class CircuitBreakerState(Enum):
    """Состояния circuit breaker."""
    CLOSED = "CLOSED"  # Нормальная работа
    OPEN = "OPEN"  # Блокировка вызовов
    HALF_OPEN = "HALF_OPEN"  # Тестирование восстановления


class CircuitBreakerOpenError(Exception):
    """Исключение при открытом circuit breaker."""
    pass


class CircuitBreaker:
    """
    Circuit Breaker для защиты от каскадных сбоев.
    
    Context7 best practice: автоматическое управление состоянием на основе
    количества сбоев и времени восстановления.
    
    Args:
        name: Имя circuit breaker (для метрик и логов)
        failure_threshold: Количество сбоев для открытия (по умолчанию 5)
        recovery_timeout: Время в секундах до попытки восстановления (по умолчанию 60)
        expected_exception: Тип исключения, которое считается сбоем (по умолчанию Exception)
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        # Состояние
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.last_success_time: Optional[float] = None
        
        # Метрики
        self._update_metrics()
        
        logger.info(
            "Circuit breaker initialized",
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout
        )
    
    def _update_metrics(self):
        """Обновление метрик Prometheus."""
        # Сбрасываем все состояния
        for state in CircuitBreakerState:
            circuit_breaker_state.labels(name=self.name, state=state.value).set(0)
        
        # Устанавливаем текущее состояние
        state_value = {
            CircuitBreakerState.CLOSED: 0,
            CircuitBreakerState.OPEN: 1,
            CircuitBreakerState.HALF_OPEN: 2
        }[self.state]
        circuit_breaker_state.labels(name=self.name, state=self.state.value).set(state_value)
    
    def _transition_to(self, new_state: CircuitBreakerState):
        """Переход в новое состояние с логированием и метриками."""
        if self.state != new_state:
            old_state = self.state
            self.state = new_state
            self._update_metrics()
            
            circuit_breaker_transitions_total.labels(
                name=self.name,
                from_state=old_state.value,
                to_state=new_state.value
            ).inc()
            
            logger.info(
                "Circuit breaker state transition",
                name=self.name,
                from_state=old_state.value,
                to_state=new_state.value,
                failure_count=self.failure_count
            )
    
    async def call_async(
        self,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Асинхронный вызов функции через circuit breaker.
        
        Args:
            func: Асинхронная функция для вызова
            *args: Позиционные аргументы
            **kwargs: Именованные аргументы
            
        Returns:
            Результат вызова функции
            
        Raises:
            CircuitBreakerOpenError: Если circuit breaker открыт
            Exception: Исключение из функции
        """
        # Проверка состояния перед вызовом
        if self.state == CircuitBreakerState.OPEN:
            # Проверяем, прошло ли время восстановления
            if self.last_failure_time and \
               (time.time() - self.last_failure_time) >= self.recovery_timeout:
                # Переход в HALF_OPEN для тестирования
                self._transition_to(CircuitBreakerState.HALF_OPEN)
            else:
                # Circuit breaker открыт, отклоняем вызов
                circuit_breaker_calls_total.labels(name=self.name, result='rejected').inc()
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.name} is OPEN. "
                    f"Last failure: {self.last_failure_time}, "
                    f"Recovery timeout: {self.recovery_timeout}s"
                )
        
        # Вызов функции
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            circuit_breaker_calls_total.labels(name=self.name, result='success').inc()
            return result
            
        except self.expected_exception as e:
            self._on_failure()
            circuit_breaker_calls_total.labels(name=self.name, result='failure').inc()
            raise
        
        except Exception as e:
            # Неожиданное исключение - не считаем сбоем для circuit breaker
            logger.warning(
                "Unexpected exception in circuit breaker",
                name=self.name,
                error=str(e),
                error_type=type(e).__name__
            )
            circuit_breaker_calls_total.labels(name=self.name, result='failure').inc()
            raise
    
    def call_sync(
        self,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Синхронный вызов функции через circuit breaker.
        
        Args:
            func: Синхронная функция для вызова
            *args: Позиционные аргументы
            **kwargs: Именованные аргументы
            
        Returns:
            Результат вызова функции
            
        Raises:
            CircuitBreakerOpenError: Если circuit breaker открыт
            Exception: Исключение из функции
        """
        # Проверка состояния перед вызовом
        if self.state == CircuitBreakerState.OPEN:
            # Проверяем, прошло ли время восстановления
            if self.last_failure_time and \
               (time.time() - self.last_failure_time) >= self.recovery_timeout:
                # Переход в HALF_OPEN для тестирования
                self._transition_to(CircuitBreakerState.HALF_OPEN)
            else:
                # Circuit breaker открыт, отклоняем вызов
                circuit_breaker_calls_total.labels(name=self.name, result='rejected').inc()
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.name} is OPEN. "
                    f"Last failure: {self.last_failure_time}, "
                    f"Recovery timeout: {self.recovery_timeout}s"
                )
        
        # Вызов функции
        try:
            result = func(*args, **kwargs)
            self._on_success()
            circuit_breaker_calls_total.labels(name=self.name, result='success').inc()
            return result
            
        except self.expected_exception as e:
            self._on_failure()
            circuit_breaker_calls_total.labels(name=self.name, result='failure').inc()
            raise
        
        except Exception as e:
            # Неожиданное исключение - не считаем сбоем для circuit breaker
            logger.warning(
                "Unexpected exception in circuit breaker",
                name=self.name,
                error=str(e),
                error_type=type(e).__name__
            )
            circuit_breaker_calls_total.labels(name=self.name, result='failure').inc()
            raise
    
    def _on_success(self):
        """Обработка успешного вызова."""
        self.last_success_time = time.time()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            # Успешный вызов в HALF_OPEN - переход в CLOSED
            self._transition_to(CircuitBreakerState.CLOSED)
            self.failure_count = 0
        
        elif self.state == CircuitBreakerState.CLOSED:
            # Успешный вызов в CLOSED - сброс счетчика сбоев
            self.failure_count = 0
    
    def _on_failure(self):
        """Обработка сбоя."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        circuit_breaker_failures_total.labels(name=self.name).inc()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            # Сбой в HALF_OPEN - переход обратно в OPEN
            self._transition_to(CircuitBreakerState.OPEN)
        
        elif self.state == CircuitBreakerState.CLOSED:
            # Проверяем, достигнут ли порог сбоев
            if self.failure_count >= self.failure_threshold:
                # Переход в OPEN
                self._transition_to(CircuitBreakerState.OPEN)
        
        logger.warning(
            "Circuit breaker failure",
            name=self.name,
            failure_count=self.failure_count,
            state=self.state.value,
            threshold=self.failure_threshold
        )
    
    def get_state(self) -> Dict[str, Any]:
        """Получение текущего состояния circuit breaker."""
        return {
            'name': self.name,
            'state': self.state.value,
            'failure_count': self.failure_count,
            'failure_threshold': self.failure_threshold,
            'recovery_timeout': self.recovery_timeout,
            'last_failure_time': self.last_failure_time,
            'last_success_time': self.last_success_time
        }
    
    def reset(self):
        """Сброс circuit breaker в CLOSED состояние."""
        self._transition_to(CircuitBreakerState.CLOSED)
        self.failure_count = 0
        self.last_failure_time = None
        self.last_success_time = None
        
        logger.info("Circuit breaker reset", name=self.name)

