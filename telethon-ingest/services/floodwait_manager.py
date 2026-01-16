"""Централизованное управление FloodWait с учётом per-account и per-method лимитов (Context7 P0.2).

Управление лимитами Telegram API через Redis с автоматическим retry/backoff.
"""

import asyncio
import os
import time
from typing import Optional, Dict
from datetime import datetime
import structlog

from telethon.errors import FloodWaitError
from telethon import TelegramClient
from prometheus_client import Counter, Histogram, Gauge, REGISTRY

logger = structlog.get_logger()

# Context7: Используем проверку на существование метрики для предотвращения дублирования
def _get_or_create_counter(name, description, labels):
    """Получить существующую метрику или создать новую."""
    try:
        # Пытаемся найти существующую метрику
        existing = REGISTRY._names_to_collectors.get(name)
        if existing:
            return existing
    except (AttributeError, KeyError, TypeError):
        pass
    
    # Если метрика не найдена, создаем новую
    try:
        return Counter(name, description, labels)
    except ValueError as e:
        # Метрика уже существует - пытаемся найти её
        if "Duplicated timeseries" in str(e):
            try:
                # Ищем метрику в реестре
                for collector_name, collector in REGISTRY._names_to_collectors.items():
                    if collector_name == name or collector_name.startswith(name):
                        logger.debug(f"Found existing metric {collector_name} for {name}")
                        return collector
            except (AttributeError, KeyError, TypeError):
                pass
        # Если не удалось найти, логируем и пробуем создать с другим подходом
        logger.warning(f"Metric {name} already exists, trying to reuse", error=str(e))
        raise

def _get_or_create_histogram(name, description, labels, buckets):
    """Получить существующую метрику Histogram или создать новую."""
    try:
        # Пытаемся найти существующую метрику
        existing = REGISTRY._names_to_collectors.get(name)
        if existing:
            return existing
    except (AttributeError, KeyError, TypeError):
        pass
    
    # Если метрика не найдена, создаем новую
    try:
        # Context7: labels не может быть None для Histogram
        if labels is None:
            labels = []
        return Histogram(name, description, labels, buckets=buckets)
    except ValueError as e:
        # Метрика уже существует - пытаемся найти её
        if "Duplicated timeseries" in str(e):
            try:
                # Ищем метрику в реестре
                for collector_name, collector in REGISTRY._names_to_collectors.items():
                    if collector_name == name or collector_name.startswith(name):
                        logger.debug(f"Found existing metric {collector_name} for {name}")
                        return collector
            except (AttributeError, KeyError, TypeError):
                pass
        logger.warning(f"Metric {name} already exists, trying to reuse", error=str(e))
        raise

def _get_or_create_gauge(name, description, labels):
    """Получить существующую метрику Gauge или создать новую."""
    try:
        # Пытаемся найти существующую метрику
        existing = REGISTRY._names_to_collectors.get(name)
        if existing:
            return existing
    except (AttributeError, KeyError, TypeError):
        pass
    
    # Если метрика не найдена, создаем новую
    try:
        # Context7: labels может быть None для Gauge (метрика без labels)
        if labels is None:
            return Gauge(name, description)
        return Gauge(name, description, labels)
    except ValueError as e:
        # Метрика уже существует - пытаемся найти её
        if "Duplicated timeseries" in str(e):
            try:
                # Ищем метрику в реестре
                for collector_name, collector in REGISTRY._names_to_collectors.items():
                    if collector_name == name or collector_name.startswith(name):
                        logger.debug(f"Found existing metric {collector_name} for {name}")
                        return collector
            except (AttributeError, KeyError, TypeError):
                pass
        logger.warning(f"Metric {name} already exists, trying to reuse", error=str(e))
        raise

# Context7: Метрики Prometheus для FloodWait
# Проверяем существование метрик перед созданием
_telethon_floodwait_total = None
_telethon_floodwait_duration_seconds = None
_tg_floodwait_seconds_gauge = None

# Пытаемся найти существующие метрики
try:
    _telethon_floodwait_total = REGISTRY._names_to_collectors.get('telethon_floodwait_total')
except (AttributeError, KeyError, TypeError):
    pass

try:
    _telethon_floodwait_duration_seconds = REGISTRY._names_to_collectors.get('telethon_floodwait_duration_seconds')
except (AttributeError, KeyError, TypeError):
    pass

try:
    _tg_floodwait_seconds_gauge = REGISTRY._names_to_collectors.get('tg_floodwait_seconds_gauge')
except (AttributeError, KeyError, TypeError):
    pass

# Создаем метрики только если они не существуют
if _telethon_floodwait_total is None:
    try:
        # Пытаемся найти существующую метрику по всем возможным именам
        for key in ['telethon_floodwait_total', 'telethon_floodwait', 'telethon_floodwait_created']:
            try:
                collector = REGISTRY._names_to_collectors.get(key)
                if collector:
                    _telethon_floodwait_total = collector
                    logger.debug(f"Found existing metric {key} for telethon_floodwait_total")
                    break
            except (AttributeError, KeyError, TypeError):
                continue
        
        # Если не нашли, создаем новую
        if _telethon_floodwait_total is None:
            _telethon_floodwait_total = Counter('telethon_floodwait_total', 'Total FloodWait errors', ['account_id', 'method'])
    except ValueError as e:
        # Метрика уже существует, используем существующую
        if "Duplicated timeseries" in str(e):
            try:
                # Ищем метрику по частичному совпадению
                for key, collector in REGISTRY._names_to_collectors.items():
                    if 'telethon_floodwait' in key.lower():
                        _telethon_floodwait_total = collector
                        logger.debug(f"Found existing metric {key} for telethon_floodwait_total after ValueError")
                        break
            except (AttributeError, KeyError, TypeError):
                pass
        if _telethon_floodwait_total is None:
            logger.warning("Failed to get existing telethon_floodwait_total, will use None", error=str(e))
            # Не падаем, просто используем None - метрика будет недоступна

if _telethon_floodwait_duration_seconds is None:
    try:
        # Пытаемся найти существующую метрику
        for key in ['telethon_floodwait_duration_seconds', 'telethon_floodwait_duration']:
            try:
                collector = REGISTRY._names_to_collectors.get(key)
                if collector:
                    _telethon_floodwait_duration_seconds = collector
                    logger.debug(f"Found existing metric {key} for telethon_floodwait_duration_seconds")
                    break
            except (AttributeError, KeyError, TypeError):
                continue
        
        # Если не нашли, создаем новую
        if _telethon_floodwait_duration_seconds is None:
            _telethon_floodwait_duration_seconds = Histogram('telethon_floodwait_duration_seconds', 'FloodWait wait duration', ['account_id', 'method'], buckets=[1, 5, 10, 30, 60, 120, 300, 600])
    except ValueError as e:
        # Метрика уже существует, используем существующую
        if "Duplicated timeseries" in str(e):
            try:
                # Ищем метрику по частичному совпадению
                for key, collector in REGISTRY._names_to_collectors.items():
                    if 'telethon_floodwait_duration' in key.lower():
                        _telethon_floodwait_duration_seconds = collector
                        logger.debug(f"Found existing metric {key} for telethon_floodwait_duration_seconds after ValueError")
                        break
            except (AttributeError, KeyError, TypeError):
                pass
        if _telethon_floodwait_duration_seconds is None:
            logger.warning("Failed to get existing telethon_floodwait_duration_seconds, will use None", error=str(e))
            # Не падаем, просто используем None - метрика будет недоступна

if _tg_floodwait_seconds_gauge is None:
    try:
        # Пытаемся найти существующую метрику
        for key in ['tg_floodwait_seconds_gauge', 'tg_floodwait_seconds']:
            try:
                collector = REGISTRY._names_to_collectors.get(key)
                if collector:
                    _tg_floodwait_seconds_gauge = collector
                    logger.debug(f"Found existing metric {key} for tg_floodwait_seconds_gauge")
                    break
            except (AttributeError, KeyError, TypeError):
                continue
        
        # Если не нашли, создаем новую
        if _tg_floodwait_seconds_gauge is None:
            _tg_floodwait_seconds_gauge = Gauge('tg_floodwait_seconds_gauge', 'Current global FloodWait duration for session', ['session_id'])
    except ValueError as e:
        # Метрика уже существует, используем существующую
        if "Duplicated timeseries" in str(e):
            try:
                # Ищем метрику по частичному совпадению
                for key, collector in REGISTRY._names_to_collectors.items():
                    if 'tg_floodwait_seconds' in key.lower():
                        _tg_floodwait_seconds_gauge = collector
                        logger.debug(f"Found existing metric {key} for tg_floodwait_seconds_gauge after ValueError")
                        break
            except (AttributeError, KeyError, TypeError):
                pass
        if _tg_floodwait_seconds_gauge is None:
            logger.warning("Failed to get existing tg_floodwait_seconds_gauge, will use None", error=str(e))
            # Не падаем, просто используем None - метрика будет недоступна

telethon_floodwait_total = _telethon_floodwait_total
telethon_floodwait_duration_seconds = _telethon_floodwait_duration_seconds
tg_floodwait_seconds_gauge = _tg_floodwait_seconds_gauge


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
        method: str = "unknown",
        session_id: Optional[str] = None
    ):
        """
        Обработка FloodWait с сохранением состояния в Redis.
        
        Args:
            error: FloodWaitError из Telethon
            account_id: Идентификатор аккаунта (telegram_id или identity_id)
            method: Название метода API (для per-method лимитов)
            session_id: Идентификатор сессии для глобального circuit breaker (опционально)
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
        
        # Context7: При большом FloodWait (>60 сек) устанавливаем глобальный circuit breaker
        if wait_seconds > 60 and session_id:
            await self.set_global_floodwait(session_id, wait_seconds)
            logger.error("Global FloodWait circuit breaker activated",
                        seconds=wait_seconds,
                        session_id=session_id,
                        account_id=account_id,
                        method=method)
        
        # Метрика (только если метрика доступна)
        if telethon_floodwait_total:
            try:
                telethon_floodwait_total.labels(account_id=account_id, method=method).inc()
            except Exception as e:
                logger.debug("Failed to update telethon_floodwait_total metric", error=str(e))
        
        if self.prometheus and telethon_floodwait_duration_seconds:
            try:
                telethon_floodwait_duration_seconds.labels(
                    account_id=account_id,
                    method=method
                ).observe(wait_seconds)
            except Exception as e:
                logger.debug("Failed to update telethon_floodwait_duration_seconds metric", error=str(e))
        
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
        
        base_batch_size = int(os.getenv("FLOODWAIT_BASE_BATCH_SIZE", "50"))  # Базовый размер батча
        
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
    
    async def check_global_floodwait(self, session_id: str) -> Optional[float]:
        """
        Проверка глобального FloodWait для сессии.
        
        Context7: Глобальный circuit breaker предотвращает массовые запросы
        при большом FloodWait (например, 14000+ секунд).
        
        Args:
            session_id: Идентификатор сессии Telegram
        
        Returns:
            Оставшееся время в секундах или None если нет блокировки
        """
        key = f"tg:floodwait_until:{session_id}"
        try:
            unlock_time_str = await self.redis_client.get(key)
            if unlock_time_str:
                unlock_time = float(unlock_time_str)
                wait_time = unlock_time - time.time()
                if wait_time > 0:
                    # Обновляем метрику (только если метрика доступна)
                    if tg_floodwait_seconds_gauge:
                        try:
                            tg_floodwait_seconds_gauge.labels(session_id=session_id).set(wait_time)
                        except Exception as e:
                            logger.debug("Failed to update tg_floodwait_seconds_gauge metric", error=str(e))
                    return wait_time
                else:
                    # Время истекло - удаляем ключ
                    await self.redis_client.delete(key)
                    if tg_floodwait_seconds_gauge:
                        try:
                            tg_floodwait_seconds_gauge.labels(session_id=session_id).set(0)
                        except Exception as e:
                            logger.debug("Failed to update tg_floodwait_seconds_gauge metric", error=str(e))
                    return None
        except Exception as e:
            logger.debug("Failed to check global FloodWait",
                        session_id=session_id,
                        error=str(e))
        return None
    
    async def set_global_floodwait(self, session_id: str, seconds: int):
        """
        Установка глобального FloodWait circuit breaker для сессии.
        
        Context7: При большом FloodWait (>60 сек) устанавливается глобальная блокировка,
        которая предотвращает любые попытки резолва до истечения времени.
        
        Args:
            session_id: Идентификатор сессии Telegram
            seconds: Время блокировки в секундах
        """
        key = f"tg:floodwait_until:{session_id}"
        unlock_time = time.time() + seconds
        
        try:
            # Устанавливаем ключ с TTL = seconds + 60 (запас 1 минута)
            await self.redis_client.setex(
                key,
                seconds + 60,
                str(unlock_time)
            )
            
            # Обновляем метрику (только если метрика доступна)
            if tg_floodwait_seconds_gauge:
                try:
                    tg_floodwait_seconds_gauge.labels(session_id=session_id).set(seconds)
                except Exception as e:
                    logger.debug("Failed to update tg_floodwait_seconds_gauge metric", error=str(e))
            
            logger.warning("Global FloodWait circuit breaker set",
                          session_id=session_id,
                          seconds=seconds,
                          unlock_time=unlock_time)
        except Exception as e:
            logger.error("Failed to set global FloodWait",
                        session_id=session_id,
                        seconds=seconds,
                        error=str(e))
    
    async def get_healthy_sessions(
        self,
        session_pool: list[int],
        max_floodwait_seconds: int = 60
    ) -> list[int]:
        """
        Фильтрует сессии из пула, которые не в FloodWait или FloodWait < max_floodwait_seconds.
        
        Context7: Используется для выбора доступных сессий из пула перед резолвом канала.
        
        Args:
            session_pool: Список telegram_id сессий для проверки
            max_floodwait_seconds: Максимально допустимый FloodWait в секундах
        
        Returns:
            Список доступных сессий (telegram_id)
        """
        healthy_sessions = []
        
        for account_id in session_pool:
            session_id = str(account_id)
            wait_time = await self.check_global_floodwait(session_id)
            
            if wait_time is None:
                # Нет FloodWait - сессия доступна
                healthy_sessions.append(account_id)
            elif wait_time <= max_floodwait_seconds:
                # Малый FloodWait - сессия доступна
                healthy_sessions.append(account_id)
            else:
                # Большой FloodWait - сессия недоступна
                logger.debug("Session skipped due to FloodWait",
                           account_id=account_id,
                           wait_seconds=wait_time)
        
        return healthy_sessions
    
    async def should_abort_resolution(
        self,
        floodwait_seconds: int,
        context: str
    ) -> bool:
        """
        Определяет, нужно ли прервать проход резолва при FloodWait.
        
        Context7: Политика переключения сессий с защитой от каскадного FloodWait.
        Для repair скриптов - строгие ограничения, для operational парсинга - более мягкие.
        
        Args:
            floodwait_seconds: Длительность FloodWait в секундах
            context: 'operational' (парсинг) или 'repair' (скрипт валидации)
        
        Returns:
            True если нужно прервать проход резолва
        """
        if context == 'repair':
            # Для repair скриптов: abort при FloodWait > 60-120 сек
            # Строгие ограничения, чтобы не выжигать все сессии по цепочке
            return floodwait_seconds > 120
        elif context == 'operational':
            # Для operational парсинга: abort при FloodWait > 5 минут
            # Более мягкие ограничения, но все равно защита от каскадного FloodWait
            return floodwait_seconds > 300
        else:
            # По умолчанию - консервативный подход
            return floodwait_seconds > 120


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

