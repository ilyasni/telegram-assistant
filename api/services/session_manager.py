"""
Session Manager Service
======================

Атомарный, идемпотентный, наблюдаемый сервис для управления Telegram сессиями
с понятным rollback и интеграцией с ImprovedSessionSaver.
"""

import asyncio
import uuid
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, List
from contextlib import asynccontextmanager
import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from config import settings
from crypto_utils import encrypt_session, decrypt_session
from prometheus_client import Counter, Histogram, Gauge

logger = structlog.get_logger()

# Prometheus метрики
SESSION_SAVE_ATTEMPTS = Counter(
    'session_save_attempts_total', 
    'Total session save attempts', 
    ['status', 'error_type']
)

SESSION_SAVE_DURATION = Histogram(
    'session_save_duration_seconds',
    'Session save duration',
    ['status']
)

SESSION_SAVE_ERRORS = Counter(
    'session_save_errors_total',
    'Session save errors by type',
    ['error_type', 'error_code']
)

ACTIVE_SESSIONS = Gauge(
    'active_sessions_total',
    'Total active sessions',
    ['tenant_id', 'status']
)


class SessionManagerService:
    """
    Атомарный, идемпотентный, наблюдаемый сервис для управления Telegram сессиями.
    
    Принципы:
    - Атомарность: все операции в транзакциях
    - Идемпотентность: повторные вызовы безопасны
    - Наблюдаемость: детальные метрики и логи
    - Rollback: четкие стратегии отката
    """
    
    def __init__(self, database_url: str):
        self.engine = create_engine(
            database_url.replace('+asyncpg', ''), 
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10
        )
        self._session_cache: Dict[str, Dict[str, Any]] = {}
    
    @asynccontextmanager
    async def _get_db_session(self):
        """Контекстный менеджер для работы с БД."""
        session = None
        try:
            session = Session(self.engine)
            yield session
        except Exception as e:
            if session:
                session.rollback()
            raise e
        finally:
            if session:
                session.close()
    
    async def save_telegram_session(
        self,
        tenant_id: str,
        user_id: str,
        session_string: str,
        telegram_user_id: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        username: Optional[str] = None,
        invite_code: Optional[str] = None,
        force_update: bool = False
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Атомарное сохранение Telegram сессии с детальной диагностикой.
        
        Args:
            tenant_id: ID арендатора
            user_id: ID пользователя
            session_string: Строка сессии Telethon
            telegram_user_id: Telegram ID пользователя
            first_name: Имя пользователя
            last_name: Фамилия пользователя
            username: Username пользователя
            invite_code: Код приглашения
            force_update: Принудительное обновление существующей сессии
            
        Returns:
            (success, session_id, error_code, error_details)
        """
        start_time = time.time()
        session_id = None
        
        try:
            # Логирование начала операции
            logger.info(
                "Starting session save",
                tenant_id=tenant_id,
                user_id=user_id,
                telegram_user_id=telegram_user_id,
                session_length=len(session_string),
                force_update=force_update
            )
            
            # Валидация входных данных
            validation_error = await self._validate_session_data(
                tenant_id, user_id, session_string, telegram_user_id
            )
            if validation_error:
                SESSION_SAVE_ATTEMPTS.labels(status='failed', error_type='validation').inc()
                return False, None, 'validation_error', validation_error
            
            # Получение активного ключа шифрования
            key_id = await self._get_active_encryption_key()
            if not key_id:
                SESSION_SAVE_ATTEMPTS.labels(status='failed', error_type='no_encryption_key').inc()
                return False, None, 'no_encryption_key', 'No active encryption key found'
            
            # Шифрование сессии
            encrypted_session = encrypt_session(session_string)
            
            # Атомарное сохранение
            async with self._get_db_session() as db:
                session_id = await self._atomic_save_session(
                    db, tenant_id, user_id, encrypted_session, key_id,
                    telegram_user_id, first_name, last_name, username,
                    invite_code, force_update
                )
                
                if not session_id:
                    SESSION_SAVE_ATTEMPTS.labels(status='failed', error_type='atomic_save_failed').inc()
                    return False, None, 'atomic_save_failed', 'Failed to save session atomically'
            
            # Обновление кэша
            self._session_cache[f"{tenant_id}:{user_id}"] = {
                'session_id': session_id,
                'status': 'authorized',
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            # Обновление метрик
            duration = time.time() - start_time
            SESSION_SAVE_ATTEMPTS.labels(status='success', error_type='none').inc()
            SESSION_SAVE_DURATION.labels(status='success').observe(duration)
            ACTIVE_SESSIONS.labels(tenant_id=tenant_id, status='authorized').inc()
            
            logger.info(
                "Session saved successfully",
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                duration=duration
            )
            
            return True, session_id, None, None
            
        except SQLAlchemyError as e:
            error_code = self._classify_database_error(e)
            error_details = str(e)
            
            SESSION_SAVE_ATTEMPTS.labels(status='failed', error_type='database_error').inc()
            SESSION_SAVE_ERRORS.labels(error_type='database_error', error_code=error_code).inc()
            SESSION_SAVE_DURATION.labels(status='failed').observe(time.time() - start_time)
            
            logger.error(
                "Database error saving session",
                error_code=error_code,
                error_details=error_details,
                tenant_id=tenant_id,
                user_id=user_id
            )
            
            return False, session_id, error_code, error_details
            
        except Exception as e:
            error_code = 'unexpected_error'
            error_details = str(e)
            
            SESSION_SAVE_ATTEMPTS.labels(status='failed', error_type='unexpected_error').inc()
            SESSION_SAVE_ERRORS.labels(error_type='unexpected_error', error_code=error_code).inc()
            SESSION_SAVE_DURATION.labels(status='failed').observe(time.time() - start_time)
            
            logger.error(
                "Unexpected error saving session",
                error_code=error_code,
                error_details=error_details,
                tenant_id=tenant_id,
                user_id=user_id
            )
            
            return False, session_id, error_code, error_details
    
    async def _validate_session_data(
        self, 
        tenant_id: str, 
        user_id: str, 
        session_string: str, 
        telegram_user_id: int
    ) -> Optional[str]:
        """Валидация входных данных."""
        if not tenant_id or not user_id:
            return "tenant_id and user_id are required"
        
        if not session_string or len(session_string) < 10:
            return "session_string is too short or empty"
        
        if len(session_string) > 10000:  # Разумный лимит
            return "session_string is too long"
        
        if not telegram_user_id or telegram_user_id <= 0:
            return "telegram_user_id must be positive"
        
        return None
    
    async def _get_active_encryption_key(self) -> Optional[str]:
        """Получение активного ключа шифрования."""
        try:
            async with self._get_db_session() as db:
                result = db.execute(text("""
                    SELECT key_id FROM encryption_keys 
                    WHERE retired_at IS NULL 
                    ORDER BY created_at DESC 
                    LIMIT 1
                """))
                row = result.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error("Failed to get active encryption key", error=str(e))
            return None
    
    async def _atomic_save_session(
        self, db: Session, tenant_id: str, user_id: str, 
        encrypted_session: str, key_id: str, telegram_user_id: int,
        first_name: Optional[str], last_name: Optional[str], 
        username: Optional[str], invite_code: Optional[str],
        force_update: bool
    ) -> Optional[str]:
        """Атомарное сохранение сессии с использованием функции upsert."""
        try:
            # Используем нашу функцию upsert_telegram_session
            result = db.execute(text("""
                SELECT upsert_telegram_session(
                    :tenant_id, :user_id, :session, :key_id, :status, :auth_error, :error_details
                )
            """), {
                'tenant_id': tenant_id,
                'user_id': user_id,
                'session': encrypted_session,
                'key_id': key_id,
                'status': 'authorized',
                'auth_error': None,
                'error_details': None
            })
            
            session_id = result.scalar()
            
            if session_id:
                # Обновляем пользователя
                db.execute(text("""
                    UPDATE users 
                    SET 
                        telegram_session_enc = :session,
                        telegram_session_key_id = :key_id,
                        telegram_auth_status = 'authorized',
                        telegram_auth_created_at = NOW(),
                        telegram_auth_updated_at = NOW(),
                        telegram_auth_error = NULL,
                        first_name = COALESCE(:first_name, first_name),
                        last_name = COALESCE(:last_name, last_name),
                        username = COALESCE(:username, username)
                    WHERE telegram_id = :telegram_user_id
                """), {
                    'session': encrypted_session,
                    'key_id': key_id,
                    'first_name': first_name,
                    'last_name': last_name,
                    'username': username,
                    'telegram_user_id': telegram_user_id
                })
                
                # Логируем событие авторизации
                db.execute(text("""
                    INSERT INTO telegram_auth_events (
                        id, user_id, event, reason, at, meta
                    ) VALUES (
                        :event_id, (SELECT id FROM users WHERE telegram_id = :telegram_user_id), 
                        'qr_authorized', :reason, NOW(), :meta
                    )
                """), {
                    'event_id': str(uuid.uuid4()),
                    'telegram_user_id': telegram_user_id,
                    'reason': f"telegram_user_id={telegram_user_id}",
                    'meta': f'{{"invite_code": "{invite_code}"}}' if invite_code else '{}'
                })
                
                db.commit()
                return str(session_id)
            
            return None
            
        except Exception as e:
            db.rollback()
            logger.error("Atomic save failed", error=str(e))
            raise e
    
    def _classify_database_error(self, error: SQLAlchemyError) -> str:
        """Классификация ошибок базы данных."""
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
    
    async def get_session_status(
        self, tenant_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получение статуса сессии."""
        cache_key = f"{tenant_id}:{user_id}"
        
        # Проверяем кэш
        if cache_key in self._session_cache:
            return self._session_cache[cache_key]
        
        try:
            async with self._get_db_session() as db:
                result = db.execute(text("""
                    SELECT id, status, created_at, updated_at, auth_error, error_details
                    FROM telegram_sessions 
                    WHERE tenant_id = :tenant_id AND user_id = :user_id
                    ORDER BY updated_at DESC 
                    LIMIT 1
                """), {
                    'tenant_id': tenant_id,
                    'user_id': user_id
                })
                
                row = result.fetchone()
                if row:
                    session_data = {
                        'session_id': str(row[0]),
                        'status': row[1],
                        'created_at': row[2].isoformat() if row[2] else None,
                        'updated_at': row[3].isoformat() if row[3] else None,
                        'auth_error': row[4],
                        'error_details': row[5]
                    }
                    
                    # Обновляем кэш
                    self._session_cache[cache_key] = session_data
                    return session_data
                
                return None
                
        except Exception as e:
            logger.error("Failed to get session status", error=str(e))
            return None
    
    async def revoke_session(
        self, tenant_id: str, user_id: str, reason: str = "manual_revoke"
    ) -> bool:
        """Отзыв сессии с логированием."""
        try:
            async with self._get_db_session() as db:
                # Обновляем статус сессии
                db.execute(text("""
                    UPDATE telegram_sessions 
                    SET status = 'revoked', updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND user_id = :user_id
                """), {
                    'tenant_id': tenant_id,
                    'user_id': user_id
                })
                
                # Обновляем статус пользователя
                db.execute(text("""
                    UPDATE users 
                    SET telegram_auth_status = 'revoked', telegram_auth_updated_at = NOW()
                    WHERE telegram_id = :user_id
                """), {
                    'user_id': user_id
                })
                
                # Логируем событие отзыва
                db.execute(text("""
                    INSERT INTO telegram_auth_events (
                        id, user_id, event, reason, at, meta
                    ) VALUES (
                        :event_id, (SELECT id FROM users WHERE telegram_id = :user_id), 
                        'session_revoked', :reason, NOW(), :meta
                    )
                """), {
                    'event_id': str(uuid.uuid4()),
                    'user_id': user_id,
                    'reason': reason,
                    'meta': '{}'
                })
                
                db.commit()
                
                # Очищаем кэш
                cache_key = f"{tenant_id}:{user_id}"
                if cache_key in self._session_cache:
                    del self._session_cache[cache_key]
                
                logger.info("Session revoked", tenant_id=tenant_id, user_id=user_id, reason=reason)
                return True
                
        except Exception as e:
            logger.error("Failed to revoke session", error=str(e))
            return False
    
    async def cleanup_expired_sessions(self, hours: int = 24) -> int:
        """Очистка просроченных сессий."""
        try:
            async with self._get_db_session() as db:
                result = db.execute(text("""
                    SELECT cleanup_old_telegram_sessions(:hours)
                """), {
                    'hours': hours
                })
                
                cleaned_count = result.scalar()
                logger.info("Expired sessions cleaned", count=cleaned_count, hours=hours)
                return cleaned_count
                
        except Exception as e:
            logger.error("Failed to cleanup expired sessions", error=str(e))
            return 0


# Глобальный экземпляр сервиса
session_manager = SessionManagerService(settings.database_url)
