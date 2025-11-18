"""Context7 best practice: сервис для сохранения Telegram сессий в БД."""

import asyncio
import uuid
from datetime import datetime
from typing import Optional, Tuple
import structlog
import psycopg2
from psycopg2.extras import RealDictCursor, Json

from config import settings
from crypto_utils import encrypt_session

logger = structlog.get_logger()


class SessionStorageService:
    """Сервис для управления Telegram сессиями в БД."""
    
    def __init__(self):
        self.db_connection = None
    
    async def init_db(self):
        """Инициализация подключения к БД."""
        try:
            loop = asyncio.get_event_loop()
            self.db_connection = await loop.run_in_executor(
                None,
                lambda: psycopg2.connect(
                    settings.database_url,
                    connect_timeout=10
                )
            )
            logger.info("Session storage DB connected")
            return True
        except Exception as e:
            logger.error("Failed to connect to session storage DB", error=str(e))
            return False
    
    async def save_telegram_session(
        self,
        tenant_id: str,
        user_id: str,
        session_string: str,
        telegram_user_id: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        username: Optional[str] = None,
        invite_code: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Context7 best practice: сохранение Telegram сессии в БД с улучшенной диагностикой.
        
        Args:
            tenant_id: ID арендатора
            user_id: ID пользователя в системе
            session_string: StringSession от Telethon
            telegram_user_id: ID пользователя в Telegram
            first_name: Имя пользователя
            last_name: Фамилия пользователя
            username: Username пользователя
            invite_code: Код приглашения (опционально)
            
        Returns:
            (success, session_id, error_code, error_details)
        """
        if not self.db_connection:
            logger.error("DB connection not initialized")
            return False, None, "db_not_initialized", "Database connection not initialized"
        
        try:
            # Context7 best practice: шифрование сессии
            encrypted_session = encrypt_session(session_string)
            
            # Context7 best practice: получение активного ключа шифрования
            key_id = await self._get_active_encryption_key()
            if not key_id:
                logger.error("No active encryption key found")
                return False, None, "no_encryption_key", "No active encryption key found"
            
            session_id = str(uuid.uuid4())
            
            # Context7 best practice: сохранение в БД с проверкой уникальности
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._save_session_sync,
                session_id,
                tenant_id,
                user_id,
                encrypted_session,
                key_id,
                telegram_user_id,
                first_name,
                last_name,
                username,
                invite_code
            )
            
            logger.info(
                "Telegram session saved",
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                telegram_user_id=telegram_user_id
            )
            
            return True, session_id, None, None
            
        except Exception as e:
            error_code = self._classify_error(e)
            error_details = str(e)
            
            logger.error(
                "Failed to save Telegram session", 
                error=str(e),
                error_code=error_code,
                tenant_id=tenant_id,
                user_id=user_id
            )
            
            return False, None, error_code, error_details
    
    def _classify_error(self, error: Exception) -> str:
        """Классификация ошибок для детальной диагностики."""
        error_str = str(error).lower()
        
        if 'value too long' in error_str or 'character varying' in error_str:
            return "session_too_long"
        elif 'column does not exist' in error_str:
            return "missing_column"
        elif 'unique constraint' in error_str or 'duplicate key' in error_str:
            return "duplicate_session"
        elif 'foreign key constraint' in error_str:
            return "invalid_tenant_or_user"
        elif 'not null constraint' in error_str:
            return "missing_required_field"
        elif 'permission denied' in error_str or 'insufficient privilege' in error_str:
            return "permission_denied"
        elif 'connection' in error_str or 'timeout' in error_str:
            return "connection_error"
        else:
            return "database_error"
    
    def _save_session_sync(
        self,
        session_id: str,
        tenant_id: str,
        user_id: str,
        encrypted_session: str,
        key_id: str,
        telegram_user_id: int,
        first_name: Optional[str],
        last_name: Optional[str],
        username: Optional[str],
        invite_code: Optional[str]
    ):
        """
        Context7 best practice: сохранение сессии в telegram_sessions (single source of truth).
        
        Сохраняет в таблицу telegram_sessions и также обновляет users для обратной совместимости.
        """
        with self.db_connection.cursor() as cursor:
            # 1) Если передан invite_code — валидируем и получаем tenant и роль
            resolved_tenant_id = tenant_id
            resolved_role = None
            if invite_code:
                cursor.execute(
                    """
                    SELECT tenant_id, role, uses_limit, uses_count, active, expires_at
                    FROM invite_codes
                    WHERE code = %s
                    """,
                    (invite_code,)
                )
                row = cursor.fetchone()
                if row:
                    (inv_tenant_id, inv_role, uses_limit, uses_count, active, expires_at) = row
                    import datetime as _dt
                    if active and (expires_at is None or expires_at > _dt.datetime.utcnow()) and (uses_limit == 0 or uses_count < uses_limit):
                        resolved_tenant_id = str(inv_tenant_id)
                        resolved_role = inv_role
                        # отметим использование
                        cursor.execute(
                            """
                            UPDATE invite_codes
                            SET uses_count = uses_count + 1, last_used_at = NOW()
                            WHERE code = %s
                            """,
                            (invite_code,)
                        )
            # Context7 best practice: сохранение в telegram_sessions (основная таблица)
            # Сначала отзываем старые сессии для этого tenant+user
            cursor.execute("""
                UPDATE telegram_sessions 
                SET status = 'revoked', updated_at = NOW()
                WHERE tenant_id::text = %s 
                  AND (user_id::text = %s OR user_id IS NULL)
                  AND status = 'authorized'
            """, (resolved_tenant_id, user_id))
            
            # Context7: Получаем user_id из БД (если существует)
            # Context7 best practice: tenant_id в telegram_sessions - это character varying, не UUID
            # Используем строковое значение resolved_tenant_id напрямую
            db_user_id = None
            cursor.execute("""
                SELECT id::text
                FROM users
                WHERE telegram_id = %s
                LIMIT 1
            """, (telegram_user_id,))
            user_result = cursor.fetchone()
            if user_result:
                db_user_id = user_result[0]
            
            # Context7: INSERT в telegram_sessions (после отзыва старых сессий)
            # Context7 best practice: используем простой INSERT, так как старые сессии уже отозваны
            # Context7: tenant_id хранится как character varying (строка), не UUID
            cursor.execute("""
                INSERT INTO telegram_sessions (
                    id, tenant_id, user_id, session_string_enc, key_id, status, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'authorized', NOW(), NOW()
                )
            """, (session_id, resolved_tenant_id, db_user_id, encrypted_session, key_id))
            
            # Context7 best practice: получаем или создаем tenant_id UUID для users
            db_tenant_uuid_for_users = None
            if resolved_tenant_id:
                # Пробуем найти существующий tenant
                cursor.execute("""
                    SELECT id FROM tenants WHERE id::text = %s LIMIT 1
                """, (resolved_tenant_id,))
                tenant_result = cursor.fetchone()
                if tenant_result:
                    db_tenant_uuid_for_users = tenant_result[0]
                else:
                    # Context7: если tenant не найден, создаем его (для новых пользователей)
                    # Используем resolved_tenant_id как UUID или создаем новый
                    try:
                        import uuid as _uuid
                        # Пробуем использовать resolved_tenant_id как UUID
                        db_tenant_uuid_for_users = _uuid.UUID(resolved_tenant_id)
                    except (ValueError, AttributeError):
                        # Если не UUID, создаем новый tenant
                        new_tenant_id = _uuid.uuid4()
                        cursor.execute("""
                            INSERT INTO tenants (id, name, created_at, updated_at)
                            VALUES (%s, %s, NOW(), NOW())
                            ON CONFLICT (id) DO NOTHING
                        """, (new_tenant_id, f"Tenant {telegram_user_id}"))
                        db_tenant_uuid_for_users = new_tenant_id
            
            # Context7 best practice: создаем или обновляем пользователя
            # Context7: сначала проверяем, существует ли пользователь
            cursor.execute("""
                SELECT id FROM users WHERE telegram_id = %s LIMIT 1
            """, (telegram_user_id,))
            existing_user = cursor.fetchone()
            
            if existing_user:
                # Обновляем существующего пользователя
                cursor.execute("""
                    UPDATE users 
                    SET 
                        telegram_session_enc = %s,
                        telegram_session_key_id = %s,
                        telegram_auth_status = 'authorized',
                        telegram_auth_created_at = NOW(),
                        telegram_auth_updated_at = NOW(),
                        telegram_auth_error = NULL,
                        first_name = COALESCE(%s, first_name),
                        last_name = COALESCE(%s, last_name),
                        username = COALESCE(%s, username),
                        tenant_id = COALESCE(%s, tenant_id),
                        role = COALESCE(%s, role)
                    WHERE telegram_id = %s
                """, (encrypted_session, key_id, first_name, last_name, username, db_tenant_uuid_for_users, resolved_role, telegram_user_id))
            else:
                # Context7: создаем нового пользователя, если его нет
                import uuid as _uuid
                new_user_id = _uuid.uuid4()
                cursor.execute("""
                    INSERT INTO users (
                        id, telegram_id, username, first_name, last_name,
                        telegram_session_enc, telegram_session_key_id,
                        telegram_auth_status, telegram_auth_created_at, telegram_auth_updated_at,
                        tenant_id, role, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        'authorized', NOW(), NOW(),
                        %s, %s, NOW(), NOW()
                    )
                """, (
                    new_user_id, telegram_user_id, username, first_name, last_name,
                    encrypted_session, key_id,
                    db_tenant_uuid_for_users, resolved_role or 'user'
                ))
                logger.info(
                    "Created new user during QR auth",
                    user_id=str(new_user_id),
                    telegram_id=telegram_user_id,
                    tenant_id=str(db_tenant_uuid_for_users) if db_tenant_uuid_for_users else None
                )
            
            # Context7 best practice: логирование события авторизации
            # Context7: получаем user_id для события (теперь пользователь точно создан)
            event_user_id = None
            cursor.execute("""
                SELECT id::text FROM users WHERE telegram_id = %s LIMIT 1
            """, (telegram_user_id,))
            user_result = cursor.fetchone()
            if user_result:
                event_user_id = user_result[0]
            
            # Context7: вставляем событие только если user_id найден
            # Для новых пользователей событие может быть пропущено, но это не критично
            if event_user_id:
                cursor.execute("""
                    INSERT INTO telegram_auth_events (
                        id, user_id, event, reason, at, meta
                    ) VALUES (
                        %s, %s, 'qr_authorized', %s, NOW(), %s
                    )
                """, (
                    str(uuid.uuid4()),
                    event_user_id,
                    f"telegram_user_id={telegram_user_id}",
                    Json({"invite_code": invite_code} if invite_code else {})
                ))
            else:
                # Context7: логируем, что событие не было создано (не критично)
                logger.warning(
                    "Cannot create auth event - user not found",
                    telegram_user_id=telegram_user_id,
                    tenant_id=tenant_id
                )
            
            self.db_connection.commit()
    
    async def _get_active_encryption_key(self) -> Optional[str]:
        """Получение активного ключа шифрования с retry логикой."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None,
                    self._get_active_key_sync
                )
            except Exception as e:
                logger.warning(f"Failed to get active encryption key (attempt {attempt + 1}/{max_retries})", error=str(e))
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
                else:
                    logger.error("Failed to get active encryption key after all retries", error=str(e))
                    return None
    
    def _get_active_key_sync(self) -> Optional[str]:
        """Синхронное получение активного ключа с очисткой транзакции."""
        try:
            with self.db_connection.cursor() as cursor:
                cursor.execute("""
                    SELECT key_id FROM encryption_keys 
                    WHERE retired_at IS NULL 
                    ORDER BY created_at DESC 
                    LIMIT 1
                """)
                result = cursor.fetchone()
                return result[0] if result else None
        except Exception as e:
            # Очистка абортированной транзакции
            try:
                self.db_connection.rollback()
                logger.info("Rolled back aborted transaction")
            except:
                pass
            raise e
    
    async def get_telegram_session(self, tenant_id: str, user_id: str) -> Optional[dict]:
        """
        Context7 best practice: получение активной Telegram сессии.
        
        Returns:
            dict с данными сессии или None
        """
        if not self.db_connection:
            return None
        
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._get_session_sync,
                tenant_id,
                user_id
            )
        except Exception as e:
            logger.error("Failed to get Telegram session", error=str(e))
            return None
    
    async def update_telegram_session_status(self, session_id: str, status: str, reason: str = None):
        """Context7 best practice: обновление статуса Telegram сессии."""
        if not self.db_connection:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._update_session_status_sync,
                session_id,
                status,
                reason
            )
        except Exception as e:
            logger.error("Failed to update session status", error=str(e))
            return False
    
    def _update_session_status_sync(self, session_id: str, status: str, reason: str = None):
        """Синхронное обновление статуса сессии."""
        with self.db_connection.cursor() as cursor:
            # Обновляем статус в users таблице
            cursor.execute("""
                UPDATE users 
                SET 
                    telegram_auth_status = %s,
                    telegram_auth_updated_at = NOW(),
                    telegram_auth_error = %s
                WHERE id = %s
            """, (status, reason, session_id))
            
            # Логируем событие
            cursor.execute("""
                INSERT INTO telegram_auth_events (
                    id, user_id, event, reason, at
                ) VALUES (
                    %s, %s, 'session_status_updated', %s, NOW()
                )
            """, (
                str(uuid.uuid4()),
                session_id,
                reason
            ))
            
            self.db_connection.commit()
    
    def _get_session_sync(self, tenant_id: str, user_id: str) -> Optional[dict]:
        """
        Context7 best practice: получение сессии из telegram_sessions (single source of truth).
        
        Fallback на users для обратной совместимости.
        """
        with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
            conditions = ["ts.status = 'authorized'"]
            params: list[str] = []

            if tenant_id:
                conditions.append("ts.tenant_id::text = %s")
                params.append(tenant_id)

            use_user_filter = bool(user_id) and user_id != tenant_id
            if use_user_filter:
                conditions.append("ts.user_id::text = %s")
                params.append(user_id)

            query = f"""
                SELECT 
                    ts.id,
                    ts.session_string_enc,
                    ts.status,
                    ts.created_at,
                    ts.key_id,
                    ts.updated_at,
                    u.telegram_id as telegram_user_id
                FROM telegram_sessions ts
                LEFT JOIN users u ON u.id::uuid = ts.user_id::uuid
                WHERE {' AND '.join(conditions)}
                ORDER BY ts.updated_at DESC
                LIMIT 1
            """

            cursor.execute(query, params)
            result = cursor.fetchone()
            if result:
                return dict(result)

            # 2) Fallback: получаем из users (обратная совместимость)
            if use_user_filter:
                cursor.execute("""
                    SELECT 
                        u.id,
                        u.telegram_session_enc as session_string_enc,
                        u.telegram_auth_status as status,
                        u.telegram_auth_created_at as created_at,
                        u.telegram_session_key_id as key_id,
                        u.telegram_auth_updated_at as updated_at,
                        u.telegram_id as telegram_user_id
                    FROM users u
                    WHERE u.id::text = %s AND u.telegram_auth_status = 'authorized'
                    ORDER BY u.telegram_auth_created_at DESC
                    LIMIT 1
                """, (user_id,))
            else:
                cursor.execute("""
                    SELECT 
                        u.id,
                        u.telegram_session_enc as session_string_enc,
                        u.telegram_auth_status as status,
                        u.telegram_auth_created_at as created_at,
                        u.telegram_session_key_id as key_id,
                        u.telegram_auth_updated_at as updated_at,
                        u.telegram_id as telegram_user_id
                    FROM users u
                    WHERE u.telegram_auth_status = 'authorized'
                      AND u.telegram_id = %s
                    ORDER BY u.telegram_auth_created_at DESC
                    LIMIT 1
                """, (tenant_id,))

            result = cursor.fetchone()
            return dict(result) if result else None
    
    async def revoke_telegram_session(self, session_id: str, reason: str = "manual"):
        """Отзыв Telegram сессии."""
        if not self.db_connection:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._revoke_session_sync,
                session_id,
                reason
            )
        except Exception as e:
            logger.error("Failed to revoke Telegram session", error=str(e))
            return False
    
    def _revoke_session_sync(self, session_id: str, reason: str):
        """Синхронный отзыв сессии."""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                UPDATE telegram_sessions 
                SET status = 'revoked', updated_at = NOW()
                WHERE id = %s
            """, (session_id,))
            
            cursor.execute("""
                INSERT INTO telegram_auth_logs (
                    id, session_id, event, reason, at
                ) VALUES (
                    %s, %s, 'session_revoked', %s, NOW()
                )
            """, (str(uuid.uuid4()), session_id, reason))
            
            self.db_connection.commit()
            return True
    
    async def cleanup_expired_sessions(self, days: int = 30):
        """Очистка истекших сессий."""
        if not self.db_connection:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._cleanup_sessions_sync,
                days
            )
        except Exception as e:
            logger.error("Failed to cleanup expired sessions", error=str(e))
            return False
    
    def _cleanup_sessions_sync(self, days: int):
        """Синхронная очистка сессий."""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                UPDATE telegram_sessions 
                SET status = 'expired', updated_at = NOW()
                WHERE status = 'authorized' 
                AND created_at < NOW() - INTERVAL '%s days'
            """, (days,))
            
            affected = cursor.rowcount
            self.db_connection.commit()
            
            if affected > 0:
                logger.info("Cleaned up expired sessions", count=affected)
            
            return True


# Context7 best practice: глобальный экземпляр сервиса
session_storage = SessionStorageService()
