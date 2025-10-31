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
            
            # Получаем UUID tenant_id и user_id из БД
            cursor.execute("""
                SELECT t.id, u.id 
                FROM tenants t
                LEFT JOIN users u ON u.telegram_id = %s AND u.tenant_id = t.id
                WHERE t.id::text = %s OR t.id IS NULL
                LIMIT 1
            """, (telegram_user_id, resolved_tenant_id))
            tenant_user_result = cursor.fetchone()
            
            db_tenant_uuid = None
            db_user_uuid = None
            if tenant_user_result:
                db_tenant_uuid = tenant_user_result[0]
                db_user_uuid = tenant_user_result[1]
            else:
                # Fallback: создаем UUID из строки или используем NULL
                try:
                    import uuid as _uuid
                    db_tenant_uuid = _uuid.UUID(resolved_tenant_id) if resolved_tenant_id else None
                except:
                    db_tenant_uuid = None
            
            # Context7: UPSERT в telegram_sessions с уникальным ограничением
            cursor.execute("""
                INSERT INTO telegram_sessions (
                    id, tenant_id, user_id, session_string_enc, key_id, status, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'authorized', NOW(), NOW()
                )
                ON CONFLICT (tenant_id, user_id, status) 
                WHERE status = 'authorized'
                DO UPDATE SET
                    session_string_enc = EXCLUDED.session_string_enc,
                    key_id = EXCLUDED.key_id,
                    updated_at = NOW()
            """, (session_id, db_tenant_uuid, db_user_uuid, encrypted_session, key_id))
            
            # Context7 best practice: обновление users для обратной совместимости
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
            """, (encrypted_session, key_id, first_name, last_name, username, db_tenant_uuid, resolved_role, telegram_user_id))
            
            # Context7 best practice: логирование события авторизации
            cursor.execute("""
                INSERT INTO telegram_auth_events (
                    id, user_id, event, reason, at, meta
                ) VALUES (
                    %s, (SELECT id FROM users WHERE telegram_id = %s), 'qr_authorized', %s, NOW(), %s
                )
            """, (
                str(uuid.uuid4()),
                telegram_user_id,  # Используем telegram_user_id (bigint), а не tenant_id (uuid)
                f"telegram_user_id={telegram_user_id}",
                Json({"invite_code": invite_code} if invite_code else {})
            ))
            
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
            # 1) Пробуем получить из telegram_sessions
            cursor.execute("""
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
                WHERE ts.tenant_id::text = %s 
                  AND ts.status = 'authorized'
                ORDER BY ts.updated_at DESC
                LIMIT 1
            """, (tenant_id,))
            
            result = cursor.fetchone()
            if result:
                return dict(result)
            
            # 2) Fallback: получаем из users (обратная совместимость)
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
                WHERE u.telegram_id = %s AND u.telegram_auth_status = 'authorized'
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
