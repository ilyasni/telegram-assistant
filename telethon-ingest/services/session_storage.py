"""Context7 best practice: сервис для сохранения Telegram сессий в БД."""

import asyncio
import uuid
from datetime import datetime
from typing import Optional
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
    ) -> Optional[str]:
        """
        Context7 best practice: сохранение Telegram сессии в БД.
        
        Args:
            tenant_id: ID арендатора
            user_id: ID пользователя в системе
            session_string: StringSession от Telethon
            telegram_user_id: ID пользователя в Telegram
            invite_code: Код приглашения (опционально)
            
        Returns:
            session_id: UUID сохраненной сессии или None при ошибке
        """
        if not self.db_connection:
            logger.error("DB connection not initialized")
            return None
        
        try:
            # Context7 best practice: шифрование сессии
            encrypted_session = encrypt_session(session_string)
            
            # Context7 best practice: получение активного ключа шифрования
            key_id = await self._get_active_encryption_key()
            if not key_id:
                logger.error("No active encryption key found")
                return None
            
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
            
            return session_id
            
        except Exception as e:
            logger.error("Failed to save Telegram session", error=str(e))
            return None
    
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
        """Синхронное сохранение сессии в упрощенной схеме (прямо в users)."""
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
            # Context7 best practice: обновление существующего пользователя с данными профиля
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
                    tenant_id = COALESCE(%s::uuid, tenant_id),
                    role = COALESCE(%s, role)
                WHERE telegram_id = %s
            """, (encrypted_session, key_id, first_name, last_name, username, resolved_tenant_id, resolved_role, telegram_user_id))
            
            # Context7 best practice: логирование события авторизации
            cursor.execute("""
                INSERT INTO telegram_auth_events (
                    id, user_id, event, reason, at, meta
                ) VALUES (
                    %s, (SELECT id FROM users WHERE telegram_id = %s), 'qr_authorized', %s, NOW(), %s
                )
            """, (
                str(uuid.uuid4()),
                tenant_id,
                f"telegram_user_id={telegram_user_id}",
                Json({"invite_code": invite_code} if invite_code else {})
            ))
            
            self.db_connection.commit()
    
    async def _get_active_encryption_key(self) -> Optional[str]:
        """Получение активного ключа шифрования."""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._get_active_key_sync
            )
        except Exception as e:
            logger.error("Failed to get active encryption key", error=str(e))
            return None
    
    def _get_active_key_sync(self) -> Optional[str]:
        """Синхронное получение активного ключа."""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                SELECT key_id FROM encryption_keys 
                WHERE retired_at IS NULL 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
            result = cursor.fetchone()
            return result[0] if result else None
    
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
        """Синхронное получение сессии из упрощенной схемы (прямо в users)."""
        with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
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
