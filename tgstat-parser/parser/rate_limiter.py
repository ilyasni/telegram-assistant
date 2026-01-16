"""Rate limiter для парсинга TGStat.
Context7: Защита от блокировки через ограничение частоты запросов.
"""

import time
from typing import Optional
import structlog

logger = structlog.get_logger()


class RateLimiter:
    """Rate limiter для ограничения частоты HTTP запросов.
    
    Context7: Реализует простой rate limiting с учётом времени между запросами
    и общего количества запросов за сессию.
    """
    
    def __init__(
        self,
        requests_per_second: float = 1.0,
        max_requests: int = 200
    ):
        """Инициализация rate limiter.
        
        Args:
            requests_per_second: Количество запросов в секунду (Context7: по умолчанию 1.0)
            max_requests: Максимальное количество запросов за сессию (Context7: по умолчанию 200)
        """
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self.max_requests = max_requests
        self.last_request_time: Optional[float] = None
        self.request_count = 0
        self.session_start_time = time.time()
        
        logger.info(
            "Rate limiter initialized",
            requests_per_second=requests_per_second,
            max_requests=max_requests
        )
    
    def wait_if_needed(self) -> None:
        """Ожидание перед следующим запросом, если необходимо.
        
        Context7: Автоматически делает паузу для соблюдения rate limit.
        """
        # Проверка лимита запросов за сессию
        if self.request_count >= self.max_requests:
            elapsed = time.time() - self.session_start_time
            logger.warning(
                "Rate limit max requests reached",
                request_count=self.request_count,
                max_requests=self.max_requests,
                elapsed_seconds=elapsed
            )
            # Context7: Увеличение метрики rate limit hits
            try:
                from parser.tgstat_parser import rate_limit_hits_total
                rate_limit_hits_total.inc()
            except ImportError:
                pass  # Метрика будет доступна после импорта модуля
            
            # Context7: При достижении лимита делаем паузу 60 секунд
            time.sleep(60)
            # Сброс счётчика для новой сессии
            self.request_count = 0
            self.session_start_time = time.time()
        
        # Ожидание минимального интервала между запросами
        if self.last_request_time is not None:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                logger.debug(
                    "Rate limit: waiting before next request",
                    sleep_seconds=sleep_time,
                    elapsed=elapsed
                )
                time.sleep(sleep_time)
        
        self.last_request_time = time.time()
        self.request_count += 1
    
    def reset_session(self) -> None:
        """Сброс счётчика сессии.
        
        Context7: Позволяет начать новую сессию запросов.
        """
        self.request_count = 0
        self.session_start_time = time.time()
        logger.info("Rate limiter session reset")
