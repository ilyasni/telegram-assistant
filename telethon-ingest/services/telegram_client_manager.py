"""
Context7 best practice: TelegramClientManager с auto-reconnect и watchdog.

Управление Telegram клиентами с:
- auto_reconnect=True с экспоненциальным backoff
- watchdog для мониторинга подключения
- keep-alive пинги
- метрики Prometheus без высокой кардинальности
"""

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List
import structlog
import redis.asyncio as redis
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.functions.help import GetConfigRequest
# from telethon.connection import ConnectionTcpFull  # Временно отключено
from prometheus_client import Counter, Histogram, Gauge

logger = structlog.get_logger()

# Context7: Метрики без высокой кардинальности (БЕЗ telegram_id в labels)
telethon_disconnects_total = Counter(
    'telethon_disconnects_total',
    'Total disconnects',
    ['reason']  # network, auth_error, timeout
)

telethon_reconnect_attempts_total = Counter(
    'telethon_reconnect_attempts_total',
    'Reconnection attempts',
    ['result']  # success, fail
)

telethon_reconnect_duration_seconds = Histogram(
    'telethon_reconnect_duration_seconds',
    'Reconnection duration',
    buckets=[0.5, 1, 2, 5, 10, 30, 60]
)

telethon_connected_clients = Gauge(
    'telethon_connected_clients',
    'Currently connected clients'
)

# Context7: отдельная метрика авторизованных клиентов
telethon_authorized_clients = Gauge(
    'telethon_authorized_clients',
    'Currently authorized clients'
)

# telegram_floodwait_seconds определен в main.py

# cooldown_channels_total определен в telethon_retry.py


class TelegramClientManager:
    """
    Context7: Управление Telegram клиентами с auto-reconnect.
    
    Features:
    - auto_reconnect с экспоненциальным backoff + джиттер
    - watchdog для мониторинга подключения
    - keep-alive пинги
    - обработка длительных отвалов (on_hold)
    """
    
    def __init__(self, redis_client: redis.Redis, db_connection):
        self._clients: Dict[int, TelegramClient] = {}
        self._reconnect_attempts: Dict[int, int] = {}  # счетчик попыток
        self._reconnect_backoffs: Dict[int, float] = {}
        self._last_success: Dict[int, float] = {}
        self._last_disconnect: Dict[int, float] = {}
        self._redis = redis_client
        self._db = db_connection
        self._watchdog_task: Optional[asyncio.Task] = None
        self._shutdown = False
        
        # Context7: Стабильные параметры клиента
        self._device_model = "Telegram Assistant"
        self._system_version = "Linux 6.8.12"
        self._app_version = "1.0.0"
        
    async def get_client(self, telegram_id: int) -> Optional[TelegramClient]:
        """
        Получить клиент с auto-reconnect.
        
        Returns:
            TelegramClient или None если не удалось подключиться
        """
        client = self._clients.get(telegram_id)
        
        if client and client.is_connected():
            return client
            
        # Попытка переподключения
        return await self._connect_or_reconnect(telegram_id)
    
    async def _connect_or_reconnect(self, telegram_id: int) -> Optional[TelegramClient]:
        """
        Подключение или переподключение клиента.
        """
        try:
            # Получаем сессию из БД
            session_string = await self._get_session_from_db(telegram_id)
            if not session_string:
                logger.warning("No session found for user", telegram_id=telegram_id)
                return None
                
            # Создаем клиент
            client = await self._create_client(telegram_id, session_string)
            
            # Подключаемся
            await client.connect()
            
            # Проверяем авторизацию
            if not await client.is_user_authorized():
                logger.error("Client not authorized", telegram_id=telegram_id)
                # [C7-ID: INGEST-AUTH-STATUS-001] Запишем статус в Redis для наблюдаемости
                try:
                    await self._redis.setex(f"ingest:auth:{telegram_id}", 600, "unauthorized")
                except Exception:
                    pass
                await client.disconnect()
                telethon_authorized_clients.set(max(0, telethon_authorized_clients._value.get() - 1))
                return None
                
            # Сохраняем клиент
            self._clients[telegram_id] = client
            self._last_success[telegram_id] = time.time()
            self._reconnect_attempts[telegram_id] = 0
            self._reconnect_backoffs[telegram_id] = 1.0
            
            logger.info("Client connected successfully", telegram_id=telegram_id)
            # Отметим авторизацию
            try:
                await self._redis.setex(f"ingest:auth:{telegram_id}", 600, "authorized")
            except Exception:
                pass
            telethon_authorized_clients.set(1)
            return client
            
        except Exception as e:
            logger.error("Failed to connect client", 
                        telegram_id=telegram_id, 
                        error=str(e))
            return None
    
    async def _create_client(self, telegram_id: int, session_string: str) -> TelegramClient:
        """
        Context7: Создание клиента с правильными параметрами.
        """
        session = StringSession(session_string)
        
        # Context7: Используем только мастер-приложение (единая пара api_id/api_hash)
        api_id, api_hash = await self._get_api_credentials()
        
        return TelegramClient(
            session=session,
            api_id=api_id,
            api_hash=api_hash,
            # connection=ConnectionTcpFull,  # Временно отключено
            flood_sleep_threshold=90,  # Увеличено для стабильности
            request_retries=0,  # Контролируем сами
            device_model=self._device_model,
            system_version=self._system_version,
            app_version=self._app_version,
            auto_reconnect=True,
            connection_retries=0  # Контролируем сами
        )
    
    async def _get_api_credentials(self) -> tuple:
        """
        Получение API credentials мастер-приложения (Context7).
        
        ВАЖНО: Все пользователи используют единое Telegram приложение (master_api_id/api_hash).
        Это позволяет масштабировать систему без создания отдельных приложений для каждого tenant.
        Сессии Telegram различаются по StringSession, но используют одну пару api_id/api_hash.
        """
        from config import settings
        return (settings.master_api_id, settings.master_api_hash)
    
    async def _get_session_from_db(self, telegram_id: int) -> Optional[str]:
        """
        Context7: Получение сессии: Redis -> fallback БД -> запись в Redis.
        """
        # 1) Redis fast-path
        try:
            session_key = f"telegram:session:{telegram_id}"
            session_string = await self._redis.get(session_key)
            if session_string:
                # Context7: безопасное декодирование (bytes или str)
                if isinstance(session_string, bytes):
                    return session_string.decode('utf-8')
                return session_string
        except Exception as e:
            logger.error("Failed to get session from Redis", telegram_id=telegram_id, error=str(e))
        
        # 2) Fallback A: если miniapp записал session_id в tg:qr:session:<id>
        try:
            qr_key = f"tg:qr:session:{telegram_id}"
            qr_data = await self._redis.hgetall(qr_key)
            if qr_data and (qr_data.get('session_id') or qr_data.get(b'session_id')):
                session_id = qr_data.get('session_id') or (qr_data.get(b'session_id').decode() if isinstance(qr_data.get(b'session_id'), (bytes, bytearray)) else None)
                if session_id:
                    from crypto_utils import decrypt_session
                    import psycopg2
                    from psycopg2.extras import RealDictCursor
                    with self._db.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT session_string_enc FROM telegram_sessions WHERE id = %s AND status IN ('authorized','active')", (session_id,))
                        row = cur.fetchone()
                        if row:
                            session_dec = decrypt_session(row['session_string_enc'])
                            try:
                                await self._redis.set(f"telegram:session:{telegram_id}", session_dec, ex=86400)
                            except Exception:
                                pass
                            return session_dec
        except Exception as e:
            logger.warning("QR session fallback failed", telegram_id=telegram_id, error=str(e))

        # 2) Fallback B: читать из БД авторизованную сессию и расшифровать
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            from crypto_utils import decrypt_session
            with self._db.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT session_string_enc
                    FROM telegram_sessions
                    WHERE status IN ('authorized','active')
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if not row:
                    return None
                session_dec = decrypt_session(row['session_string_enc'])
                # Запишем в Redis для последующих обращений
                try:
                    await self._redis.set(f"telegram:session:{telegram_id}", session_dec, ex=86400)
                except Exception:
                    pass
                return session_dec
        except Exception as e:
            logger.error("Failed to load session from DB", telegram_id=telegram_id, error=str(e))
            return None
    
    async def _reconnect_with_backoff(self, telegram_id: int) -> bool:
        """
        Переподключение с экспоненциальным backoff + джиттер.
        
        Backoff: min(backoff * 2 * rand(0.8..1.2), 60)
        Сброс после успешного запроса.
        """
        if telegram_id not in self._reconnect_attempts:
            self._reconnect_attempts[telegram_id] = 0
            self._reconnect_backoffs[telegram_id] = 1.0
            
        self._reconnect_attempts[telegram_id] += 1
        backoff = self._reconnect_backoffs[telegram_id]
        
        # Экспоненциальный backoff с джиттером
        delay = min(backoff * (0.8 + random.random() * 0.4), 60)
        
        logger.warning("Reconnecting with backoff", 
                      telegram_id=telegram_id,
                      attempt=self._reconnect_attempts[telegram_id],
                      delay=delay)
        
        await asyncio.sleep(delay)
        
        # Увеличиваем backoff для следующей попытки
        self._reconnect_backoffs[telegram_id] = min(backoff * 2, 60)
        
        # Попытка переподключения
        start_time = time.time()
        try:
            client = await self._connect_or_reconnect(telegram_id)
            if client and client.is_connected():
                duration = time.time() - start_time
                telethon_reconnect_duration_seconds.observe(duration)
                telethon_reconnect_attempts_total.labels(result='success').inc()
                
                # Сброс backoff после успеха
                self._reconnect_backoffs[telegram_id] = 1.0
                return True
            else:
                telethon_reconnect_attempts_total.labels(result='fail').inc()
                return False
                
        except Exception as e:
            telethon_reconnect_attempts_total.labels(result='fail').inc()
            logger.error("Reconnect failed", 
                        telegram_id=telegram_id, 
                        error=str(e))
            return False
    
    async def start_watchdog(self):
        """
        Запуск watchdog в фоне.
        """
        if self._watchdog_task and not self._watchdog_task.done():
            return
            
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("Watchdog started")
    
    async def stop_watchdog(self):
        """
        Остановка watchdog.
        """
        self._shutdown = True
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("Watchdog stopped")
    
    async def _watchdog_loop(self):
        """
        Фоновый watchdog:
        - Проверка is_connected() каждые 20 сек
        - Keep-alive GetConfigRequest каждые 150 сек
        - Graceful shutdown по self._shutdown
        """
        last_keepalive = {}
        
        while not self._shutdown:
            try:
                current_time = time.time()
                
                for telegram_id, client in list(self._clients.items()):
                    try:
                        # Проверка подключения
                        if not client.is_connected():
                            logger.warning("Client disconnected", telegram_id=telegram_id)
                            telethon_disconnects_total.labels(reason='network').inc()
                            
                            # Попытка переподключения
                            success = await self._reconnect_with_backoff(telegram_id)
                            if not success:
                                # Проверяем на длительный отвал
                                await self._check_persistent_disconnect(telegram_id)
                            continue
                        
                        # Keep-alive пинг каждые 150 сек
                        last_ping = last_keepalive.get(telegram_id, 0)
                        if current_time - last_ping > 150:
                            try:
                                await asyncio.wait_for(
                                    client(GetConfigRequest()),
                                    timeout=10
                                )
                                last_keepalive[telegram_id] = current_time
                                logger.debug("Keep-alive ping successful", telegram_id=telegram_id)
                            except asyncio.TimeoutError:
                                logger.warning("Keep-alive timeout", telegram_id=telegram_id)
                                telethon_disconnects_total.labels(reason='timeout').inc()
                            except Exception as e:
                                logger.warning("Keep-alive failed", 
                                            telegram_id=telegram_id, 
                                            error=str(e))
                                
                    except Exception as e:
                        logger.error("Watchdog error for client", 
                                   telegram_id=telegram_id, 
                                   error=str(e))
                
                # Обновляем метрику подключенных клиентов
                connected_count = sum(1 for c in self._clients.values() if c.is_connected())
                telethon_connected_clients.set(connected_count)
                
                # Пауза между проверками
                await asyncio.sleep(20)
                
            except Exception as e:
                logger.error("Watchdog loop error", error=str(e))
                await asyncio.sleep(5)
    
    async def _check_persistent_disconnect(self, telegram_id: int):
        """
        Проверка на длительный отвал (>10 попыток за 15 минут).
        """
        attempts = self._reconnect_attempts.get(telegram_id, 0)
        last_success = self._last_success.get(telegram_id, 0)
        current_time = time.time()
        
        # 10 попыток за 15 минут
        if attempts >= 10 and (current_time - last_success) > 900:
            await self._handle_persistent_disconnect(telegram_id)
    
    async def _handle_persistent_disconnect(self, telegram_id: int):
        """
        Обработка длительного отвала:
        - is_authenticated=false в БД
        - on_hold=true для каналов
        - Очистка клиента
        """
        logger.error("Persistent disconnect, marking user on_hold", 
                    telegram_id=telegram_id)
        
        try:
            # Помечаем пользователя как неавторизованного
            await self._mark_user_unauthenticated(telegram_id)
            
            # Помечаем каналы как on_hold
            await self._mark_user_channels_on_hold(telegram_id, True)
            
            # Очищаем клиент
            if telegram_id in self._clients:
                client = self._clients[telegram_id]
                try:
                    await client.disconnect()
                except:
                    pass
                del self._clients[telegram_id]
            
            # Сбрасываем счетчики
            self._reconnect_attempts.pop(telegram_id, None)
            self._reconnect_backoffs.pop(telegram_id, None)
            self._last_success.pop(telegram_id, None)
            
            telethon_disconnects_total.labels(reason='auth_error').inc()
            
        except Exception as e:
            logger.error("Failed to handle persistent disconnect", 
                        telegram_id=telegram_id, 
                        error=str(e))
    
    async def _mark_user_unauthenticated(self, telegram_id: int):
        """Пометить пользователя как неавторизованного в БД."""
        # TODO: Обновить статус в БД
        pass
    
    async def _mark_user_channels_on_hold(self, telegram_id: int, on_hold: bool):
        """Пометить каналы пользователя как on_hold."""
        # TODO: Обновить статус каналов в БД
        pass
    
    def health(self) -> Dict[str, Any]:
        """
        Простой метод для health endpoint.
        БЕЗ приватных ID для безопасности.
        """
        current_time = time.time()
        
        # Подсчет недавних переподключений за 5 минут
        recent_reconnects = 0
        for last_disconnect in self._last_disconnect.values():
            if current_time - last_disconnect < 300:  # 5 минут
                recent_reconnects += 1
        
        return {
            "connected": sum(1 for c in self._clients.values() if c.is_connected()),
            "total": len(self._clients),
            "reconnects_5m": recent_reconnects,
            "last_disconnect_ts": max(self._last_disconnect.values()) if self._last_disconnect else None
        }
    
    async def close_all(self):
        """
        Закрытие всех клиентов.
        """
        for client in self._clients.values():
            try:
                await client.disconnect()
            except:
                pass
        self._clients.clear()
        logger.info("All clients closed")
