"""
Утилита для кэширования результатов health checks.

Context7 best practice: Кэширование health checks для снижения нагрузки
и предотвращения периодических отказов System Overview.
"""

import asyncio
import time
from typing import Optional, Dict, Any, Callable, Awaitable
from datetime import datetime, timezone, timedelta
import structlog

logger = structlog.get_logger()


class HealthCheckCache:
    """
    Кэш для результатов health checks с TTL.
    
    Context7: Async кэширование с автоматической инвалидацией при ошибках.
    """
    
    def __init__(self, ttl_seconds: int = 60):
        """
        Инициализация кэша.
        
        Args:
            ttl_seconds: Время жизни кэша в секундах (по умолчанию 60)
        """
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
    
    async def get(
        self,
        key: str,
        check_func: Callable[[], Awaitable[Any]],
        invalidate_on_error: bool = True
    ) -> Any:
        """
        Получить результат health check из кэша или выполнить проверку.
        
        Context7: Автоматическая инвалидация кэша при ошибках для
        предотвращения кэширования неверных результатов.
        
        Args:
            key: Ключ кэша
            check_func: Асинхронная функция для выполнения проверки
            invalidate_on_error: Инвалидировать кэш при ошибке (по умолчанию True)
        
        Returns:
            Результат health check
        """
        async with self._lock:
            # Проверяем, есть ли актуальный результат в кэше
            cached = self._cache.get(key)
            if cached:
                age = time.time() - cached['timestamp']
                if age < self.ttl_seconds:
                    logger.debug(
                        "Health check cache hit",
                        key=key,
                        age_seconds=round(age, 2),
                        ttl_seconds=self.ttl_seconds
                    )
                    return cached['result']
                else:
                    logger.debug(
                        "Health check cache expired",
                        key=key,
                        age_seconds=round(age, 2),
                        ttl_seconds=self.ttl_seconds
                    )
            
            # Выполняем проверку
            try:
                result = await check_func()
                
                # Сохраняем в кэш
                self._cache[key] = {
                    'result': result,
                    'timestamp': time.time(),
                    'error': False
                }
                
                logger.debug(
                    "Health check executed and cached",
                    key=key,
                    cached=True
                )
                
                return result
                
            except Exception as e:
                logger.error(
                    "Health check failed",
                    key=key,
                    error=str(e),
                    error_type=type(e).__name__
                )
                
                # Инвалидируем кэш при ошибке
                if invalidate_on_error and cached:
                    logger.debug(
                        "Invalidating cache due to error",
                        key=key
                    )
                    del self._cache[key]
                
                # Пробрасываем исключение
                raise
    
    async def invalidate(self, key: str):
        """
        Инвалидировать кэш для конкретного ключа.
        
        Args:
            key: Ключ кэша
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug("Cache invalidated", key=key)
    
    async def clear(self):
        """Очистить весь кэш."""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info("Cache cleared", entries_removed=count)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Получить статистику кэша.
        
        Returns:
            Словарь со статистикой
        """
        now = time.time()
        active_entries = 0
        expired_entries = 0
        
        for key, cached in self._cache.items():
            age = now - cached['timestamp']
            if age < self.ttl_seconds:
                active_entries += 1
            else:
                expired_entries += 1
        
        return {
            'total_entries': len(self._cache),
            'active_entries': active_entries,
            'expired_entries': expired_entries,
            'ttl_seconds': self.ttl_seconds
        }


# Глобальный экземпляр кэша
_health_cache: Optional[HealthCheckCache] = None


def get_health_cache() -> HealthCheckCache:
    """
    Получить глобальный экземпляр кэша health checks.
    
    Returns:
        Экземпляр HealthCheckCache
    """
    global _health_cache
    if _health_cache is None:
        _health_cache = HealthCheckCache(ttl_seconds=60)
        logger.info("Health check cache initialized", ttl_seconds=60)
    return _health_cache

