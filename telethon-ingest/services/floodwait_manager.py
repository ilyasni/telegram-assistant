"""Централизованное управление FloodWait с учётом per-account и per-method лимитов (Context7 P0.2).

Управление лимитами Telegram API через Redis с автоматическим retry/backoff.
"""

import asyncio
import time
from typing import Optional, Dict
from datetime import datetime
import structlog

from telethon.errors import FloodWaitError
from telethon import TelegramClient
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# Context7: Метрики Prometheus для FloodWait
telethon_floodwait_total = Counter(
    'telethon_floodwait_total',
    'Total FloodWait errors',
    ['account_id', 'method']
)

telethon_floodwait_duration_seconds = Histogram(
    'telethon_floodwait_duration_seconds',
    'FloodWait wait duration',
    ['account_id', 'method'],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600]
)


class FloodWaitManager:
    """Централизованное управление FloodWait с учётом per-account и per-method лимитов."""
    
    def __init__(self, redis_client, prometheus_client=None):
        """
        Инициализация FloodWaitManager.
        
        Args:
            redis_client: Async Redis клиент
            prometheus_client: Опциональный Prometheus клиент (для метрик)
        """
        self.redis_client = redis_client
        self.prometheus = prometheus_client
    
    async def handle_floodwait(
        self,
        error: FloodWaitError,
        account_id: str,
        method: str = "unknown"
    ):
        """
        Обработка FloodWait с сохранением состояния в Redis.
        
        Args:
            error: FloodWaitError из Telethon
            account_id: Идентификатор аккаунта (telegram_id или identity_id)
            method: Название метода API (для per-method лимитов)
        """
        wait_seconds = error.seconds
        key = f"floodwait:{account_id}:{method}"
        
        # Сохранение времени разблокировки
        unlock_time = time.time() + wait_seconds
        await self.redis_client.setex(
            key,
            wait_seconds + 60,  # Запас 1 минута
            str(unlock_time)
        )
        
        # Метрика
        telethon_floodwait_total.labels(account_id=account_id, method=method).inc()
        if self.prometheus:
            telethon_floodwait_duration_seconds.labels(
                account_id=account_id,
                method=method
            ).observe(wait_seconds)
        
        logger.warning("FloodWait detected", 
                      seconds=wait_seconds,
                      account_id=account_id,
                      method=method)
        
        await asyncio.sleep(wait_seconds)
    
    async def is_rate_limited(self, account_id: str, method: str = "unknown") -> bool:
        """
        Проверка, не заблокирован ли account/method.
        
        Args:
            account_id: Идентификатор аккаунта
            method: Название метода API
        
        Returns:
            True если rate limited, False иначе
        """
        key = f"floodwait:{account_id}:{method}"
        try:
            unlock_time_str = await self.redis_client.get(key)
            if unlock_time_str:
                unlock_time = float(unlock_time_str)
                if time.time() < unlock_time:
                    return True
        except Exception as e:
            logger.debug("Failed to check rate limit", 
                        account_id=account_id,
                        method=method,
                        error=str(e))
        return False
    
    async def get_wait_time(self, account_id: str, method: str = "unknown") -> float:
        """
        Получение оставшегося времени ожидания для account/method.
        
        Args:
            account_id: Идентификатор аккаунта
            method: Название метода API
        
        Returns:
            Оставшееся время в секундах (0 если не заблокирован)
        """
        key = f"floodwait:{account_id}:{method}"
        try:
            unlock_time_str = await self.redis_client.get(key)
            if unlock_time_str:
                unlock_time = float(unlock_time_str)
                wait_time = unlock_time - time.time()
                return max(0.0, wait_time)
        except Exception as e:
            logger.debug("Failed to get wait time", 
                        account_id=account_id,
                        method=method,
                        error=str(e))
        return 0.0
    
    async def get_adaptive_batch_size(self, account_id: str, hour: Optional[int] = None) -> int:
        """
        Адаптивный размер батча в зависимости от времени суток и текущих лимитов.
        
        Args:
            account_id: Идентификатор аккаунта
            hour: Текущий час (0-23), если None - определяется автоматически
        
        Returns:
            Рекомендуемый размер батча
        """
        if hour is None:
            hour = datetime.now().hour
        
        base_batch_size = 50  # Базовый размер батча
        
        # Ночью (2-6) - большие батчи
        if 2 <= hour < 6:
            multiplier = 2.0
        # Днём (10-18) - малые батчи (высокая активность)
        elif 10 <= hour < 18:
            multiplier = 0.5
        # Вечером (18-22) - средние батчи
        elif 18 <= hour < 22:
            multiplier = 0.75
        # Остальное время - нормальные батчи
        else:
            multiplier = 1.0
        
        # Проверка текущих лимитов
        wait_time = await self.get_wait_time(account_id, "get_messages")
        if wait_time > 30:
            # Если большой FloodWait - уменьшаем батч
            multiplier *= 0.5
        
        return int(base_batch_size * multiplier)


class TelethonClientWrapper:
    """Wrapper над TelegramClient с автоматическим FloodWait handling."""
    
    def __init__(
        self,
        client: TelegramClient,
        account_id: str,
        floodwait_manager: FloodWaitManager
    ):
        """
        Инициализация wrapper.
        
        Args:
            client: TelegramClient для обёртки
            account_id: Идентификатор аккаунта
            floodwait_manager: Экземпляр FloodWaitManager
        """
        self.client = client
        self.account_id = account_id
        self.fw_manager = floodwait_manager
    
    async def call(
        self,
        method_name: str,
        func,
        *args,
        **kwargs
    ):
        """
        Вызов функции Telegram API с обработкой FloodWait.
        
        Args:
            method_name: Название метода (для логирования и метрик)
            func: Функция для вызова (awaitable)
            *args, **kwargs: Аргументы функции
        
        Returns:
            Результат вызова функции
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Проверка rate limit перед вызовом
                if await self.fw_manager.is_rate_limited(self.account_id, method_name):
                    wait_time = await self.fw_manager.get_wait_time(self.account_id, method_name)
                    if wait_time > 0:
                        logger.debug("Waiting for rate limit", 
                                   account_id=self.account_id,
                                   method=method_name,
                                   wait_seconds=wait_time)
                        await asyncio.sleep(wait_time)
                
                # Вызов функции
                result = await func(*args, **kwargs)
                return result
            except FloodWaitError as e:
                await self.fw_manager.handle_floodwait(e, self.account_id, method_name)
                if attempt == max_retries - 1:
                    raise
                # Exponential backoff
                await asyncio.sleep(2 ** attempt)
        
        raise RuntimeError(f"Failed to call {method_name} after {max_retries} retries")

