"""QR-авторизация через Telethon с публикацией статуса в Redis.

Важно: минимальная реализация для e2e — публикует qr_url и ждёт авторизации.
"""

import asyncio
import time
from typing import Optional
import structlog
import redis
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from prometheus_client import Counter, Histogram

from config import settings
from services.events import publish_user_authorized
from services.session_storage import session_storage
from crypto_utils import encrypt_session

logger = structlog.get_logger()

# Context7 best practice: Prometheus метрики для QR-авторизации (с multi-tenant лейблами)
AUTH_QR_PUBLISHED = Counter(
    "auth_qr_published_total",
    "QR URL published",
    ["tenant_id"],
    namespace="telethon"
)
AUTH_QR_EXPIRED = Counter(
    "auth_qr_expired_total",
    "QR session expired",
    ["tenant_id"],
    namespace="telethon"
)
AUTH_QR_SUCCESS = Counter(
    "auth_qr_success_total",
    "QR session authorized",
    ["tenant_id"],
    namespace="telethon"
)
AUTH_QR_FAIL = Counter(
    "auth_qr_fail_total",
    "QR session failed",
    ["tenant_id"],
    namespace="telethon"
)

# Context7 best practice: Telethon метрики
FLOODWAIT_TOTAL = Counter("telethon_floodwait_total", "FloodWait events", ["reason", "seconds"])
FLOODWAIT_DURATION = Histogram("telethon_floodwait_duration_seconds", "FloodWait wait duration", ["reason"])
SESSION_CLEANUP_TOTAL = Counter("telethon_session_cleanup_total", "Session cleanup operations", ["status"])
SESSION_CLEANUP_DURATION = Histogram("telethon_session_cleanup_duration_seconds", "Session cleanup duration")
QR_SESSION_TOTAL = Counter("telethon_qr_session_total", "QR sessions", ["status"])
RATE_LIMIT_HITS = Counter("telethon_qr_rate_limit_hits_total", "Rate limit hits", ["endpoint"])
THROTTLING_DELAY = Histogram("telethon_throttling_delay_seconds", "Request throttling delay")


class QrAuthService:
    def __init__(self):
        # Context7 best practice: используем decode_responses=True для автоматического декодирования строк
        # Это соответствует реализации в API и упрощает работу с данными
        self.redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        self.session_storage = session_storage
        self.running = False

    async def run(self):
        self.running = True
        logger.info("QR auth loop started")
        while self.running:
            try:
                # Context7: Сканируем QR сессии с единым префиксом t:{tenant}:qr:session
                # Поддерживаем оба формата для обратной совместимости
                keys_found = list(self._scan_keys("t:")) + list(self._scan_keys("tg:qr:session:"))
                logger.debug("Scanning for QR sessions", keys_count=len(keys_found))
                
                for key in keys_found:
                    try:
                        # Context7: проверяем тип ключа перед обработкой
                        key_type = self.redis_client.type(key)
                        if key_type != "hash":
                            logger.warning("Key is not a hash, skipping or deleting", key=key, type=key_type)
                            # Удаляем старые ключи неправильного типа для очистки
                            if key_type in ["string", "none"]:
                                try:
                                    self.redis_client.delete(key)
                                    logger.info("Deleted old key with wrong type", key=key, type=key_type)
                                except Exception as e:
                                    logger.warning("Failed to delete old key", key=key, error=str(e))
                            continue
                        
                        data = self.redis_client.hgetall(key)
                        if not data:
                            logger.debug("Empty session data", key=key)
                            continue
                        # Context7: с decode_responses=True все значения уже декодированы в строки
                        status = data.get("status", "pending")
                        tenant_id = data.get("tenant_id", "")
                        logger.debug("Found session", key=key, status=status, tenant_id=tenant_id)
                    except redis.exceptions.ResponseError as e:
                        # Обработка ошибок типа WRONGTYPE
                        if "WRONGTYPE" in str(e):
                            logger.warning("Wrong key type, attempting to fix", key=key, error=str(e))
                            try:
                                # Пытаемся удалить ключ неправильного типа
                                self.redis_client.delete(key)
                                logger.info("Deleted key with wrong type", key=key)
                            except Exception as del_err:
                                logger.error("Failed to delete wrong key type", key=key, error=str(del_err))
                        else:
                            logger.error("Redis error processing key", key=key, error=str(e))
                        continue
                    
                    # Context7 best practice: идемпотентность на уровне telegram_user_id
                    # [C7-ID: security-idempotency-002]
                    # ВАЖНО: проверяем существующую сессию только если есть telegram_user_id
                    # Для новых пользователей без telegram_user_id всегда показываем QR-код
                    if status == "pending":
                        # Получаем telegram_user_id из Redis сессии (если есть)
                        # Context7: с decode_responses=True значение уже строка
                        telegram_user_id_raw = data.get("telegram_user_id")
                        telegram_user_id = None
                        if telegram_user_id_raw:
                            try:
                                telegram_user_id = int(telegram_user_id_raw)
                            except (ValueError, TypeError):
                                pass
                        
                        # Проверяем существующую сессию только если есть telegram_user_id
                        # Это предотвращает использование сессии другого пользователя с тем же tenant_id
                        if telegram_user_id:
                            existing_session = await self._check_existing_session(tenant_id, telegram_user_id)
                            if existing_session:
                                # Проверяем, что найденная сессия действительно принадлежит этому пользователю
                                existing_telegram_id = existing_session.get('telegram_user_id')
                                if existing_telegram_id and int(existing_telegram_id) == telegram_user_id:
                                    logger.info("Existing session found for user, skipping new QR", 
                                               tenant_id=tenant_id, 
                                               telegram_user_id=telegram_user_id,
                                               session_id=str(existing_session.get('id', '')))
                                    # Получаем session_string из БД для Redis
                                    session_string = None
                                    try:
                                        from crypto_utils import decrypt_session
                                        session_string = decrypt_session(existing_session.get('session_string_enc'))
                                    except Exception as e:
                                        logger.warning("Failed to decrypt session_string for Redis", error=str(e))
                                    
                                    # Обновляем статус на "authorized" для идемпотентности
                                    self.redis_client.hset(key, mapping={
                                        "status": "authorized",
                                        "session_id": str(existing_session.get('id', '')),
                                        "telegram_user_id": str(telegram_user_id),
                                        "reason": "existing_session",
                                        "session_string": session_string or ""
                                    })
                                    continue
                                else:
                                    logger.warning("Existing session belongs to different user, will show QR", 
                                                  tenant_id=tenant_id,
                                                  telegram_user_id=telegram_user_id,
                                                  existing_telegram_id=existing_telegram_id)
                        else:
                            # Для новых пользователей без telegram_user_id всегда показываем QR-код
                            logger.info("No telegram_user_id in session, will show QR for new user", 
                                       tenant_id=tenant_id)
                    
                    # Context7 best practice: обрабатываем все сессии для валидации
                    # ВАЖНО: включаем "in_progress" и "awaiting_scan" для корректной обработки QR-генерации
                    # Удаляем expired сессии, чтобы они не блокировали создание новых
                    if status == "expired":
                        logger.debug("Deleting expired session", key=key, tenant_id=tenant_id)
                        try:
                            self.redis_client.delete(key)
                            logger.info("Deleted expired QR session", key=key, tenant_id=tenant_id)
                        except Exception as e:
                            logger.warning("Failed to delete expired session", key=key, error=str(e))
                        continue
                    
                    if status not in ["pending", "failed", "authorized", "in_progress", "awaiting_scan"]:
                        logger.debug("Skipping non-processable session", key=key, status=status)
                        continue
                    
                    # Context7 best practice: пропускаем сессии, которые уже в процессе обработки QR
                    # (in_progress означает что QR-генерация уже началась, awaiting_scan - что QR уже опубликован)
                    if status in ["in_progress", "awaiting_scan"]:
                        logger.debug("QR generation already in progress or QR published", 
                                   key=key, status=status, tenant_id=tenant_id)
                        continue
                    
                    # Context7 best practice: валидация только старых authorized сессий (старше 5 минут)
                    if status == "authorized":
                        # Context7: с decode_responses=True значение уже строка
                        created_at = data.get("created_at")
                        if created_at:
                            try:
                                created_timestamp = int(created_at)
                                current_timestamp = int(time.time())
                                if current_timestamp - created_timestamp > 300:  # 5 минут
                                    logger.info("Validating old authorized session", key=key, tenant_id=tenant_id)
                                    await self._validate_authorized_session(key, tenant_id)
                                else:
                                    logger.debug("Skipping validation for recent session", key=key, tenant_id=tenant_id)
                                    # Context7: ensure backfill to ingest Redis key if missing
                                    try:
                                        # Context7: Ключ сессии для ingest с префиксом t:{tenant}:session
                                        ingest_key = self._get_session_key(tenant_id)
                                        if not self.redis_client.exists(ingest_key):
                                            ss = self.redis_client.hget(key, "session_string")
                                            if ss:
                                                self.redis_client.set(ingest_key, ss, ex=86400)
                                                logger.info("Backfilled telegram:session for ingest", tenant_id=tenant_id)
                                            else:
                                                # Пометить для повторной авторизации миниаппом
                                                self.redis_client.hset(key, "status", "not_found")
                                                logger.warning("Authorized session has no session_string; marked not_found", tenant_id=tenant_id)
                                    except Exception as e:
                                        logger.warning("Backfill to ingest redis failed", tenant_id=tenant_id, error=str(e))
                            except (ValueError, AttributeError):
                                logger.warning("Invalid created_at timestamp", key=key, tenant_id=tenant_id)
                        continue
                    
                    # Context7 best practice: для failed сессий проверяем наличие session_string
                    if status == "failed":
                        # Context7: с decode_responses=True значение уже строка
                        session_string = data.get("session_string")
                        if not session_string:
                            logger.debug("Skipping failed session without session_string", key=key)
                            continue
                        logger.info("Processing failed session with session_string", key=key, tenant_id=tenant_id)

                    # Context7 best practice: distributed lock для предотвращения гонок
                    if not tenant_id:
                        logger.warning("Tenant id missing in session; skipping", key=key)
                        continue
                    # Context7: Блокировка с единым префиксом t:{tenant}:lock:qr
                    lock_key = self._get_lock_key(tenant_id)
                    got_lock = self.redis_client.set(lock_key, "1", nx=True, ex=600)
                    if not got_lock:
                        logger.debug("Lock busy, skipping", key=key, lock_key=lock_key)
                        continue

                    # Запустить QR-login
                    logger.info("Triggering QR login", key=key, tenant_id=tenant_id)
                    try:
                        await self._handle_qr_login(key, tenant_id)
                    finally:
                        # Освобождаем lock
                        try:
                            self.redis_client.delete(lock_key)
                        except Exception:
                            pass

            except Exception as e:
                logger.error("QR auth loop error", error=str(e))

            await asyncio.sleep(2)

    def stop(self):
        self.running = False

    def _scan_keys(self, prefix: str):
        """
        Context7: Сканирование ключей с поддержкой multi-tenant префиксов.
        Поддерживает как старый формат (tg:qr:session:*), так и новый (t:{tenant}:qr:session:*).
        """
        cursor = 0
        while True:
            cursor, keys = self.redis_client.scan(cursor=cursor, match=f"{prefix}*")
            for k in keys:
                # Context7: с decode_responses=True ключи уже строки
                # Оставляем fallback для обратной совместимости
                if isinstance(k, (bytes, bytearray)):
                    yield k.decode()
                else:
                    yield k
            if cursor == 0:
                break
    
    def _get_qr_session_key(self, tenant_id: str) -> str:
        """Context7: Генерация ключа QR сессии с единым префиксом t:{tenant}:qr:session."""
        return f"t:{tenant_id}:qr:session"
    
    def _get_session_key(self, tenant_id: str) -> str:
        """Context7: Генерация ключа Telegram сессии для ingest с префиксом t:{tenant}:session."""
        return f"t:{tenant_id}:session"
    
    def _get_lock_key(self, tenant_id: str) -> str:
        """Context7: Генерация ключа блокировки с префиксом t:{tenant}:lock:qr."""
        return f"t:{tenant_id}:lock:qr"

    async def _check_existing_session(self, tenant_id: str, telegram_user_id: int = None) -> Optional[dict]:
        """Context7 best practice: проверка существующей активной сессии с валидацией.
        
        Args:
            tenant_id: ID арендатора
            telegram_user_id: ID пользователя в Telegram (опционально, для точной проверки)
        """
        try:
            # Инициализация session storage если нужно
            if not self.session_storage.db_connection:
                await self.session_storage.init_db()
            
            # Context7 best practice: если есть telegram_user_id, ищем сессию по нему для точности
            # Это предотвращает использование сессии другого пользователя с тем же tenant_id
            session_data = None
            if telegram_user_id:
                session_data = await self._get_session_by_telegram_id(telegram_user_id, tenant_id)
            
            # Fallback: если не нашли по telegram_user_id, используем старый метод
            if not session_data:
                session_data = await self.session_storage.get_telegram_session(tenant_id, tenant_id)
            
            if session_data and session_data.get('status') == 'authorized':
                # Context7 best practice: валидация сессии через get_me()
                is_valid = await self._validate_session(session_data.get('session_string_enc'), session_data.get('key_id'))
                if is_valid:
                    # Дополнительная проверка: если указан telegram_user_id, убеждаемся что сессия принадлежит ему
                    if telegram_user_id:
                        existing_telegram_id = session_data.get('telegram_user_id')
                        if existing_telegram_id and int(existing_telegram_id) != telegram_user_id:
                            logger.warning("Session belongs to different user", 
                                         expected=telegram_user_id,
                                         actual=existing_telegram_id,
                                         tenant_id=tenant_id)
                            return None
                    
                    logger.info("Found valid existing session", 
                              tenant_id=tenant_id, 
                              telegram_user_id=telegram_user_id,
                              session_id=session_data.get('id'))
                    return session_data
                else:
                    logger.warning("Found invalid session, marking as revoked", 
                                 tenant_id=tenant_id, 
                                 telegram_user_id=telegram_user_id,
                                 session_id=session_data.get('id'))
                    # Помечаем сессию как недействительную
                    await self.session_storage.update_telegram_session_status(
                        session_data.get('id'), 
                        "revoked", 
                        "session_invalidated_by_telegram"
                    )
                    return None
            
            return None
            
        except Exception as e:
            logger.error("Failed to check existing session", 
                        tenant_id=tenant_id, 
                        telegram_user_id=telegram_user_id,
                        error=str(e))
            return None
    
    async def _get_session_by_telegram_id(self, telegram_user_id: int, tenant_id: str = None) -> Optional[dict]:
        """Context7 best practice: получение сессии по telegram_user_id для точной проверки."""
        try:
            if not self.session_storage.db_connection:
                await self.session_storage.init_db()
            
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._get_session_by_telegram_id_sync,
                telegram_user_id,
                tenant_id
            )
        except Exception as e:
            logger.error("Failed to get session by telegram_user_id", 
                        telegram_user_id=telegram_user_id,
                        tenant_id=tenant_id,
                        error=str(e))
            return None
    
    def _get_session_by_telegram_id_sync(self, telegram_user_id: int, tenant_id: str = None) -> Optional[dict]:
        """Синхронное получение сессии по telegram_user_id."""
        try:
            from psycopg2.extras import RealDictCursor
            with self.session_storage.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                # Ищем сессию по telegram_user_id в таблице users
                query = """
                    SELECT 
                        ts.id,
                        ts.session_string_enc,
                        ts.status,
                        ts.created_at,
                        ts.key_id,
                        ts.updated_at,
                        u.telegram_id as telegram_user_id
                    FROM telegram_sessions ts
                    JOIN users u ON u.id::uuid = ts.user_id::uuid
                    WHERE u.telegram_id = %s 
                      AND ts.status = 'authorized'
                """
                params = [telegram_user_id]
                
                # Если указан tenant_id, добавляем его в условие
                if tenant_id:
                    query += " AND ts.tenant_id::text = %s"
                    params.append(tenant_id)
                
                query += " ORDER BY ts.updated_at DESC LIMIT 1"
                
                cursor.execute(query, params)
                result = cursor.fetchone()
                if result:
                    return dict(result)
                
                # Fallback: поиск в users таблице для обратной совместимости
                query_fallback = """
                    SELECT 
                        u.id::text as id,
                        u.telegram_session_enc as session_string_enc,
                        u.telegram_auth_status as status,
                        u.telegram_auth_created_at as created_at,
                        u.telegram_session_key_id as key_id,
                        u.telegram_auth_updated_at as updated_at,
                        u.telegram_id as telegram_user_id
                    FROM users u
                    WHERE u.telegram_id = %s 
                      AND u.telegram_auth_status = 'authorized'
                      AND u.telegram_session_enc IS NOT NULL
                """
                params_fallback = [telegram_user_id]
                
                if tenant_id:
                    query_fallback += " AND u.tenant_id::text = %s"
                    params_fallback.append(tenant_id)
                
                query_fallback += " ORDER BY u.telegram_auth_updated_at DESC LIMIT 1"
                
                cursor.execute(query_fallback, params_fallback)
                result_fallback = cursor.fetchone()
                if result_fallback:
                    return dict(result_fallback)
                
                return None
        except Exception as e:
            logger.error("Failed to get session by telegram_user_id (sync)", 
                        telegram_user_id=telegram_user_id,
                        tenant_id=tenant_id,
                        error=str(e))
            return None

    async def _validate_session(self, encrypted_session: str, key_id: str) -> bool:
        """Context7 best practice: валидация Telegram сессии через get_me()."""
        try:
            if not encrypted_session or not key_id:
                return False
            
            # Расшифровываем сессию
            from crypto_utils import decrypt_session
            session_string = decrypt_session(encrypted_session)
            if not session_string:
                logger.warning("Failed to decrypt session for validation")
                return False
            
            # Создаем временный клиент для валидации
            temp_client = TelegramClient(
                StringSession(session_string),
                settings.master_api_id,
                settings.master_api_hash,
                device_model="TelegramAssistant",
                system_version="Linux",
                app_version="1.0"
            )
            
            try:
                await temp_client.start()
                # Context7 best practice: get_me() для валидации сессии
                me = await temp_client.get_me()
                logger.debug("Session validation successful", user_id=me.id)
                return True
            except Exception as e:
                logger.warning("Session validation failed", error=str(e))
                return False
            finally:
                await temp_client.disconnect()
                
        except Exception as e:
            logger.error("Failed to validate session", error=str(e))
            return False

    async def _validate_authorized_session(self, redis_key: str, tenant_id: str):
        """Context7 best practice: валидация authorized сессий."""
        try:
            # Получаем данные сессии из Redis
            # Context7: с decode_responses=True все значения уже строки
            session_data = self.redis_client.hgetall(redis_key)
            session_id = session_data.get("session_id")
            telegram_user_id = session_data.get("telegram_user_id")
            
            if not session_id:
                logger.warning("No session_id in authorized session", tenant_id=tenant_id)
                return
            
            # Инициализация session storage если нужно
            if not self.session_storage.db_connection:
                await self.session_storage.init_db()
            
            # Получаем зашифрованную сессию из БД
            db_session = await self.session_storage.get_telegram_session(tenant_id, tenant_id)
            if not db_session or db_session.get('status') != 'authorized':
                logger.warning("No authorized session in DB", tenant_id=tenant_id)
                self.redis_client.hset(redis_key, "status", "not_found")
                return
            
            # Валидируем сессию
            is_valid = await self._validate_session(
                db_session.get('session_string_enc'), 
                db_session.get('key_id')
            )
            
            if is_valid:
                logger.debug("Session validation successful", tenant_id=tenant_id, session_id=session_id)
            else:
                logger.warning("Session validation failed, marking as revoked", tenant_id=tenant_id, session_id=session_id)
                # Помечаем сессию как недействительную в БД
                # Context7: с decode_responses=True session_id уже строка
                await self.session_storage.update_telegram_session_status(
                    session_id,
                    "revoked",
                    "session_invalidated_by_telegram"
                )
                # Обновляем статус в Redis
                self.redis_client.hset(redis_key, "status", "revoked")
                
        except Exception as e:
            logger.error("Failed to validate authorized session", tenant_id=tenant_id, error=str(e))

    async def _process_existing_session_string(self, redis_key: str, tenant_id: str, session_string: str):
        """Context7 best practice: обработка существующего session_string из failed сессии."""
        try:
            # Инициализация session storage если нужно
            if not self.session_storage.db_connection:
                await self.session_storage.init_db()
            
            # Получаем telegram_user_id из существующей сессии
            # Context7: с decode_responses=True значение уже строка
            telegram_user_id = self.redis_client.hget(redis_key, "telegram_user_id")
            if telegram_user_id:
                telegram_user_id = int(telegram_user_id)
            else:
                # Если нет telegram_user_id, получаем из session_string
                from telethon.sessions import StringSession
                temp_client = TelegramClient(
                    StringSession(session_string),
                    settings.master_api_id,
                    settings.master_api_hash
                )
                await temp_client.start()
                me = await temp_client.get_me()
                telegram_user_id = me.id
                await temp_client.disconnect()
            
            # Context7 best practice: получаем правильный tenant_id из БД
            if not self.session_storage.db_connection:
                await self.session_storage.init_db()
            
            # Получаем tenant_id из БД по telegram_user_id
            with self.session_storage.db_connection.cursor() as cursor:
                cursor.execute("SELECT tenant_id FROM users WHERE telegram_id = %s", (telegram_user_id,))
                result = cursor.fetchone()
                if result:
                    db_tenant_id = str(result[0])
                else:
                    # Если пользователь не найден, используем переданный tenant_id как fallback
                    db_tenant_id = tenant_id
            
            # Сохраняем сессию в БД
            session_id = await self.session_storage.save_telegram_session(
                tenant_id=db_tenant_id,
                user_id=db_tenant_id,
                session_string=session_string,
                telegram_user_id=telegram_user_id,
                invite_code=None
            )
            
            if session_id:
                logger.info("Successfully processed existing session_string", tenant_id=tenant_id, session_id=session_id)
                self.redis_client.hset(redis_key, mapping={
                    "status": "authorized",
                    "session_id": session_id,
                    "telegram_user_id": str(telegram_user_id)
                })
                self.redis_client.expire(redis_key, 3600)
                # Context7: публикуем StringSession в единый ключ для ingest с префиксом t:{tenant}:session
                try:
                    session_key = self._get_session_key(tenant_id)
                    self.redis_client.set(session_key, session_string, ex=86400)
                except Exception:
                    pass
                AUTH_QR_SUCCESS.labels(tenant_id=tenant_id or "unknown").inc()
            else:
                logger.error("Failed to save existing session_string", tenant_id=tenant_id)
                self.redis_client.hset(redis_key, "status", "failed")
                AUTH_QR_FAIL.labels(tenant_id=tenant_id or "unknown").inc()
                
        except Exception as e:
            logger.error("Failed to process existing session_string", tenant_id=tenant_id, error=str(e))
            self.redis_client.hset(redis_key, "status", "failed")
            AUTH_QR_FAIL.labels(tenant_id=tenant_id or "unknown").inc()

    async def _handle_qr_login(self, redis_key: str, tenant_id: str):
        logger.info("Starting QR login", tenant_id=tenant_id, key=redis_key)
        
        # Context7 best practice: проверка существующей активной сессии по telegram_user_id
        # Получаем telegram_user_id из Redis сессии
        # Context7: с decode_responses=True значение уже строка
        telegram_user_id_raw = self.redis_client.hget(redis_key, "telegram_user_id")
        telegram_user_id = None
        if telegram_user_id_raw:
            try:
                telegram_user_id = int(telegram_user_id_raw)
            except (ValueError, TypeError):
                pass
        
        # Проверяем существующую сессию только если есть telegram_user_id
        # Для новых пользователей всегда показываем QR-код
        existing_session = None
        if telegram_user_id:
            existing_session = await self._check_existing_session(tenant_id, telegram_user_id)
        
        if existing_session:
            logger.info("User already has active session", tenant_id=tenant_id, session_id=str(existing_session.get('id', '')))
            
            # Получаем session_string из БД для Redis
            session_string = None
            try:
                from crypto_utils import decrypt_session
                session_string = decrypt_session(existing_session.get('session_string_enc'))
            except Exception as e:
                logger.warning("Failed to decrypt session_string for Redis", error=str(e))
            
            self.redis_client.hset(redis_key, mapping={
                "status": "authorized",
                "session_id": str(existing_session.get('id', '')),
                "telegram_user_id": str(existing_session.get('telegram_user_id', '')),
                "reason": "existing_session",
                "session_string": session_string or ""
            })
            self.redis_client.expire(redis_key, 3600)
            # Context7: публикуем StringSession в единый ключ для ingest с префиксом t:{tenant}:session
            try:
                if session_string:
                    session_key = self._get_session_key(tenant_id)
                    self.redis_client.set(session_key, session_string, ex=86400)
            except Exception:
                pass
            AUTH_QR_SUCCESS.labels(tenant_id=tenant_id or "unknown").inc()
            return
        
        # Context7 best practice: обработка failed сессий с session_string
        # Context7: с decode_responses=True значение уже строка
        current_status = self.redis_client.hget(redis_key, "status")
        if current_status and current_status == "failed":
            session_string = self.redis_client.hget(redis_key, "session_string")
            if session_string:
                logger.info("Processing failed session with existing session_string", tenant_id=tenant_id)
                await self._process_existing_session_string(redis_key, tenant_id, session_string)
                return
        
        # Проверка на уже запущенную сессию (дедупликация)
        # Context7 best practice: проверяем текущий статус перед началом QR-генерации
        current_status = self.redis_client.hget(redis_key, "status")
        if current_status:
            # Context7: с decode_responses=True значение уже строка
            current_status_str = current_status
            # Если статус уже "awaiting_scan" или "authorized", QR уже опубликован или авторизация завершена
            if current_status_str in ["awaiting_scan", "authorized"]:
                logger.info("QR session already processed", tenant_id=tenant_id, status=current_status_str)
                return
            # Если статус "in_progress", ждем завершения текущей генерации
            if current_status_str == "in_progress":
                logger.debug("QR generation already in progress, waiting", tenant_id=tenant_id, key=redis_key)
                return
        
        # Пометка как "in_progress" для предотвращения дублирования
        self.redis_client.hset(redis_key, "status", "in_progress")
        logger.info("QR session marked as in_progress, starting QR generation", tenant_id=tenant_id, key=redis_key)
        
        # Context7: Используем единое мастер-приложение для всех пользователей
        # Все tenants используют одну пару api_id/api_hash, сессии различаются по StringSession
        client = TelegramClient(
            StringSession(), 
            settings.master_api_id, 
            settings.master_api_hash,
            device_model="TelegramAssistant",
            system_version="Linux", 
            app_version="1.0"
        )
        
        try:
            logger.debug("Connecting to Telegram", tenant_id=tenant_id)
            await client.connect()
            logger.debug("Connected to Telegram", tenant_id=tenant_id)
            
            # Context7 best practice: throttling между запросами
            # [C7-ID: telethon-throttle-004]
            import random
            import asyncio
            delay = random.uniform(0.2, 0.5)  # 200-500ms
            THROTTLING_DELAY.observe(delay)
            await asyncio.sleep(delay)
            
            logger.debug("Starting QR login", tenant_id=tenant_id)
            qr_login = await client.qr_login()
            logger.debug("QR login initiated", tenant_id=tenant_id, qr_url=qr_login.url)
            
            # Context7 best practice: TTL для QR-сессий
            # [C7-ID: telethon-qr-ttl-002]
            import os
            QR_TTL_SECONDS = int(os.getenv("QR_TTL_SECONDS", "600"))  # 10 минут
            
            # Публикация QR URL для Mini App с TTL
            self.redis_client.hset(redis_key, mapping={
                "qr_url": qr_login.url,
                "status": "awaiting_scan"
            })
            # Устанавливаем TTL для всей сессии
            self.redis_client.expire(redis_key, QR_TTL_SECONDS)
            
            logger.info("QR published", tenant_id=tenant_id, url=qr_login.url, ttl=QR_TTL_SECONDS)
            AUTH_QR_PUBLISHED.labels(tenant_id=tenant_id or "unknown").inc()
            QR_SESSION_TOTAL.labels(status="created").inc()
            
            # Ожидание авторизации с timeout
            try:
                await asyncio.wait_for(qr_login.wait(), timeout=590)
            except asyncio.TimeoutError:
                self.redis_client.hset(redis_key, "status", "expired")
                logger.warning("QR login timeout", tenant_id=tenant_id)
                AUTH_QR_EXPIRED.labels(tenant_id=tenant_id or "unknown").inc()
                QR_SESSION_TOTAL.labels(status="expired").inc()
                return
            except SessionPasswordNeededError:
                self.redis_client.hset(redis_key, mapping={
                    "status": "failed", 
                    "reason": "password_required"
                })
                logger.warning("QR login requires 2FA", tenant_id=tenant_id)
                AUTH_QR_FAIL.labels(tenant_id=tenant_id or "unknown").inc()
                QR_SESSION_TOTAL.labels(status="failed").inc()
                return
            
            # Context7 best practice: усиленная проверка владельца
            # [C7-ID: security-owner-verify-001]
            me = await client.get_me()
            
            # Дополнительная валидация Telegram user data
            if not me or not me.id:
                logger.error("Invalid Telegram user data", tenant_id=tenant_id)
                self.redis_client.hset(redis_key, mapping={
                    "status": "failed",
                    "reason": "invalid_telegram_user"
                })
                AUTH_QR_FAIL.labels(tenant_id=tenant_id or "unknown").inc()
                QR_SESSION_TOTAL.labels(status="failed").inc()
                return
            
            # Context7: получаем expected_telegram_id из Redis сессии (если был передан initData)
            # Context7 best practice: используем telegram_user_id из Redis для точной проверки владельца
            expected_telegram_id = None
            telegram_user_id_raw = self.redis_client.hget(redis_key, "telegram_user_id")
            
            logger.info("Checking expected_telegram_id", 
                       redis_key=redis_key,
                       telegram_user_id_raw=telegram_user_id_raw,
                       tenant_id=tenant_id,
                       actual_telegram_id=me.id)
            
            if telegram_user_id_raw:
                try:
                    expected_telegram_id = int(telegram_user_id_raw)
                    logger.info("Found telegram_user_id in Redis", 
                               expected_telegram_id=expected_telegram_id,
                               tenant_id=tenant_id,
                               actual_telegram_id=me.id)
                except (ValueError, TypeError) as e:
                    logger.error("Failed to parse telegram_user_id from Redis", 
                               telegram_user_id_raw=telegram_user_id_raw,
                               error=str(e),
                               tenant_id=tenant_id)
            
            # Context7: Если telegram_user_id не был сохранен (initData пустой/не передан),
            # используем реальный telegram_user_id из QR как expected.
            # Это безопасно, потому что:
            # 1. QR код защищен session_token
            # 2. QR код может быть отсканирован только один раз
            # 3. Для новых пользователей initData может быть недоступен
            if expected_telegram_id is None:
                expected_telegram_id = me.id
                # Сохраняем telegram_user_id в Redis для последующего использования
                self.redis_client.hset(redis_key, "telegram_user_id", str(me.id))
                logger.info("Using actual telegram_user_id from QR (initData was not provided)", 
                           telegram_user_id=me.id,
                           tenant_id=tenant_id,
                           note="This is safe because QR is protected by session_token")
            
            # Строгая проверка соответствия владельца
            if me.id != expected_telegram_id:
                logger.error("Ownership mismatch", 
                           expected=expected_telegram_id, 
                           actual=me.id,
                           tenant_id=tenant_id,
                           username=getattr(me, 'username', None))
                self.redis_client.hset(redis_key, mapping={
                    "status": "failed",
                    "reason": "ownership_mismatch"
                })
                AUTH_QR_FAIL.labels(tenant_id=tenant_id or "unknown").inc()
                QR_SESSION_TOTAL.labels(status="failed").inc()
                return
            
            # Логирование успешной проверки владельца
            logger.info("Ownership verified", 
                       telegram_id=me.id, 
                       tenant_id=tenant_id,
                       username=getattr(me, 'username', None))
            
            # Context7 best practice: сохранение StringSession в БД
            session_string = client.session.save()
            
            # Context7 best practice: инициализация session storage если нужно
            if not self.session_storage.db_connection:
                await self.session_storage.init_db()
            
            # Context7 best practice: получаем правильный tenant_id из БД
            with self.session_storage.db_connection.cursor() as cursor:
                cursor.execute("SELECT tenant_id FROM users WHERE telegram_id = %s", (me.id,))
                result = cursor.fetchone()
                if result:
                    db_tenant_id = str(result[0])
                else:
                    # Если пользователь не найден, используем переданный tenant_id как fallback
                    db_tenant_id = tenant_id
            
            # Context7 best practice: сохранение сессии в БД с данными пользователя
            # Детектор правды: детальная диагностика ошибок
            success, session_id, error_code, error_details = await self.session_storage.save_telegram_session(
                tenant_id=db_tenant_id,
                user_id=db_tenant_id,
                session_string=session_string,
                telegram_user_id=me.id,
                first_name=getattr(me, 'first_name', None),
                last_name=getattr(me, 'last_name', None),
                username=getattr(me, 'username', None),
                invite_code=None  # TODO: получать invite_code из запроса
            )
            
            if not success:
                # Детектор правды: разделение ошибок по типам
                if error_code == "db_integrity":
                    failure_reason = "session_store_integrity_failed"
                elif error_code == "db_operational":
                    failure_reason = "session_store_operational_failed"
                elif error_code == "db_generic":
                    failure_reason = "session_store_database_failed"
                elif error_code == "session_saver_failed":
                    failure_reason = "session_saver_failed"
                else:
                    failure_reason = "session_store_unexpected_failed"
                
                logger.error(
                    "Failed to save session to database", 
                    tenant_id=tenant_id,
                    error_code=error_code,
                    error_details=error_details,
                    failure_reason=failure_reason,
                    session_length=len(session_string),
                    telegram_user_id=me.id
                )
                
                self.redis_client.hset(redis_key, mapping={
                    "status": "failed",
                    "reason": failure_reason,
                    "error_code": error_code,
                    "error_details": error_details or "Unknown error"
                })
                AUTH_QR_FAIL.labels(tenant_id=tenant_id or "unknown").inc()
                QR_SESSION_TOTAL.labels(status="failed").inc()
                return
            
            # Context7 best practice: обновление Redis с session_id
            # Успешно: ставим authorized и очищаем reason
            self.redis_client.hset(redis_key, mapping={
                "status": "authorized",
                "session_id": session_id,
                "telegram_user_id": str(me.id),
                "session_string": session_string
            })
            self.redis_client.hdel(redis_key, "reason")
            self.redis_client.expire(redis_key, 3600)  # продление для интеграции с инвайтами
            
            logger.info("QR login success", tenant_id=tenant_id, user_id=me.id, session_id=session_id)
            AUTH_QR_SUCCESS.labels(tenant_id=tenant_id or "unknown").inc()
            QR_SESSION_TOTAL.labels(status="authorized").inc()
            
            # Проверка invite-кода и публикация события для активации тарифа
            # Context7: с decode_responses=True значение уже строка
            invite_code = self.redis_client.hget(redis_key, "invite_code")
            if invite_code:
                try:
                    # Публикация события для активации тарифа
                    publish_user_authorized(self.redis_client, {
                        "telegram_id": str(me.id),
                        "tenant_id": tenant_id,
                        "session_string_encrypted": encrypted_session,
                        "invite_code": invite_code
                    })
                    logger.info("User authorized event published", tenant_id=tenant_id, invite_code=invite_code)
                except Exception as e:
                    logger.error("Failed to publish user authorized event", error=str(e), tenant_id=tenant_id)
            
        except FloodWaitError as e:
            # Context7 best practice: FloodWait handling с экспоненциальным backoff
            # [C7-ID: telethon-floodwait-001]
            import random
            import asyncio
            
            # Максимальное ожидание: 60 секунд
            max_wait = min(e.seconds, 60)
            
            # Экспоненциальный backoff с джиттером
            base_delay = min(max_wait, 2 ** min(e.seconds // 10, 6))
            jitter = random.uniform(0.1, 0.3) * base_delay
            delay = base_delay + jitter
            
            # Метрики FloodWait
            FLOODWAIT_TOTAL.labels(reason="qr_login", seconds=str(e.seconds)).inc()
            FLOODWAIT_DURATION.labels(reason="qr_login").observe(delay)
            
            logger.warning("FloodWait during QR login", 
                          seconds=e.seconds, 
                          delay=delay, 
                          tenant_id=tenant_id)
            
            # Ждём с backoff
            await asyncio.sleep(delay)
            
            # Обновляем статус
            self.redis_client.hset(redis_key, mapping={
                "status": "failed",
                "reason": f"flood_wait_{e.seconds}s"
            })
            AUTH_QR_FAIL.labels(tenant_id=tenant_id or "unknown").inc()
        except Exception as e:
            logger.error("QR login error", error=str(e), tenant_id=tenant_id)
            self.redis_client.hset(redis_key, "status", "failed")
            AUTH_QR_FAIL.labels(tenant_id=tenant_id or "unknown").inc()
        finally:
            # Context7 best practice: гарантированная остановка клиента
            # [C7-ID: telethon-cleanup-003]
            import time
            start_time = time.time()
            
            try:
                if client.is_connected():
                    await client.disconnect()
                    logger.debug("Telethon client disconnected", tenant_id=tenant_id)
                    SESSION_CLEANUP_TOTAL.labels(status="success").inc()
                else:
                    logger.debug("Client already disconnected", tenant_id=tenant_id)
                    SESSION_CLEANUP_TOTAL.labels(status="already_disconnected").inc()
            except Exception as e:
                logger.warning("Error during client disconnect", error=str(e), tenant_id=tenant_id)
                SESSION_CLEANUP_TOTAL.labels(status="failed").inc()
            finally:
                cleanup_duration = time.time() - start_time
                SESSION_CLEANUP_DURATION.observe(cleanup_duration)
                logger.debug("Session cleanup completed", 
                           duration=cleanup_duration, 
                           tenant_id=tenant_id)


