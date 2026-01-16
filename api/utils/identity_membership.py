"""
Context7: Общие утилиты для работы с Identity и Membership.
Избегаем дублирования логики upsert identity/membership между API и воркерами.
"""

from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import uuid
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger()


def upsert_identity_sync(db: Session, telegram_id: int) -> uuid.UUID:
    """
    Context7: Upsert identity по telegram_id (синхронная версия для API).
    
    Returns:
        identity.id (UUID)
    """
    from models.database import Identity
    
    # Ищем существующую identity
    identity = db.query(Identity).filter(Identity.telegram_id == telegram_id).first()
    
    if not identity:
        # Создаём новую identity
        identity = Identity(telegram_id=telegram_id)
        db.add(identity)
        db.flush()
        logger.debug("Identity created", telegram_id=telegram_id, identity_id=str(identity.id))
    else:
        logger.debug("Identity found", telegram_id=telegram_id, identity_id=str(identity.id))
    
    return identity.id


async def upsert_identity_async(db_session: AsyncSession, telegram_id: int) -> uuid.UUID:
    """
    Context7: Upsert identity по telegram_id (асинхронная версия для воркеров).
    
    Returns:
        identity.id (UUID)
    """
    # 1) Upsert identities по telegram_id
    identity_sql = text("""
        INSERT INTO identities (id, telegram_id, created_at, meta)
        VALUES (:id, :telegram_id, :created_at, '{}'::json)
        ON CONFLICT (telegram_id) DO NOTHING
    """)
    # Context7: asyncpg требует naive datetime для created_at (PostgreSQL TIMESTAMP без timezone)
    # Используем datetime.utcnow() вместо datetime.now(timezone.utc)
    identity_record = {
        'id': str(uuid.uuid4()),
        'telegram_id': telegram_id,
        'created_at': datetime.utcnow()
    }
    await db_session.execute(identity_sql, identity_record)
    
    # 2) Получаем identity_id
    identity_select_sql = text("SELECT id FROM identities WHERE telegram_id = :telegram_id")
    result = await db_session.execute(identity_select_sql, {'telegram_id': telegram_id})
    identity_row = result.first()
    
    if not identity_row:
        raise RuntimeError(f"Failed to resolve identity after upsert for telegram_id={telegram_id}")
    
    identity_id_raw = identity_row[0]
    # Context7: asyncpg возвращает asyncpg.pgproto.pgproto.UUID, нужно преобразовать в uuid.UUID
    identity_id = uuid.UUID(str(identity_id_raw))
    logger.debug("Identity upserted", telegram_id=telegram_id, identity_id=str(identity_id))
    
    return identity_id


def upsert_membership_sync(
    db: Session,
    tenant_id: uuid.UUID,
    identity_id: uuid.UUID,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    tier: str = "free"
) -> uuid.UUID:
    """
    Context7: Upsert membership в users по (tenant_id, identity_id) (синхронная версия).
    
    Returns:
        user.id (UUID)
    """
    from models.database import User
    
    # Ищем существующее membership
    user = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.identity_id == identity_id
    ).first()
    
    if not user:
        # Создаём новое membership
        user = User(
            tenant_id=tenant_id,
            identity_id=identity_id,
            telegram_id=telegram_id,  # Dual-write для обратной совместимости
            username=username,
            first_name=first_name,
            last_name=last_name,
            tier=tier
        )
        db.add(user)
        db.flush()
        logger.debug("Membership created", 
                    tenant_id=str(tenant_id),
                    identity_id=str(identity_id),
                    user_id=str(user.id))
    else:
        # Обновляем существующее membership
        user.telegram_id = telegram_id  # Dual-write
        if username is not None:
            user.username = username
        if first_name is not None:
            user.first_name = first_name
        if last_name is not None:
            user.last_name = last_name
        # Context7: НЕ перезаписываем tier при обновлении существующего пользователя
        # tier обновляется только при создании нового пользователя
        # user.tier = tier  # УДАЛЕНО: защита от сброса tier при обновлении
        # Context7: Используем utcnow() для совместимости с asyncpg
        user.last_active_at = datetime.utcnow()
        db.flush()
        logger.debug("Membership updated",
                    tenant_id=str(tenant_id),
                    identity_id=str(identity_id),
                    user_id=str(user.id))
    
    return user.id


async def upsert_membership_async(
    db_session: AsyncSession,
    tenant_id: str,
    identity_id: uuid.UUID,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    tier: str = "free"
) -> uuid.UUID:
    """
    Context7: Upsert membership в users по (tenant_id, identity_id) (асинхронная версия).
    
    Returns:
        user.id (UUID)
    """
    # Context7: asyncpg требует naive datetime для created_at/last_active_at
    # Используем datetime.utcnow() вместо datetime.now(timezone.utc)
    user_record = {
        'id': str(uuid.uuid4()),
        'tenant_id': tenant_id,
        'identity_id': str(identity_id),
        'telegram_id': telegram_id,  # Dual-write для обратной совместимости
        'first_name': first_name or '',
        'last_name': last_name or '',
        'username': username or '',
        'tier': tier,
        'created_at': datetime.utcnow(),
        'last_active_at': datetime.utcnow()
    }

    sql = text("""
        INSERT INTO users (id, tenant_id, identity_id, telegram_id, first_name, last_name, username, tier, created_at, last_active_at)
        VALUES (:id, :tenant_id, :identity_id, :telegram_id, :first_name, :last_name, :username, :tier, :created_at, :last_active_at)
        ON CONFLICT (tenant_id, identity_id)
        DO UPDATE SET
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            username = EXCLUDED.username,
            -- Context7: НЕ обновляем tier при конфликте (при обновлении существующего пользователя)
            -- tier = EXCLUDED.tier,  -- УДАЛЕНО: защита от сброса tier
            last_active_at = EXCLUDED.last_active_at,
            telegram_id = EXCLUDED.telegram_id  -- Ensure telegram_id is updated for dual-write
        RETURNING id
    """)

    result = await db_session.execute(sql, user_record)
    user_id_raw = result.scalar_one()
    # Context7: asyncpg возвращает asyncpg.pgproto.pgproto.UUID, нужно преобразовать в uuid.UUID
    user_id = uuid.UUID(str(user_id_raw))
    
    logger.debug("Membership upserted",
                tenant_id=tenant_id,
                identity_id=str(identity_id),
                user_id=str(user_id))
    
    return user_id


def upsert_identity_and_membership_sync(
    db: Session,
    tenant_id: uuid.UUID,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    tier: str = "free"
) -> Tuple[uuid.UUID, uuid.UUID]:
    """
    Context7: Обёртка для upsert identity и membership (синхронная версия).
    
    Returns:
        Tuple[identity_id, user_id]
    """
    identity_id = upsert_identity_sync(db, telegram_id)
    user_id = upsert_membership_sync(
        db=db,
        tenant_id=tenant_id,
        identity_id=identity_id,
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        tier=tier
    )
    return identity_id, user_id


async def upsert_identity_and_membership_async(
    db_session: AsyncSession,
    tenant_id: str,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    tier: str = "free"
) -> Tuple[uuid.UUID, uuid.UUID]:
    """
    Context7: Обёртка для upsert identity и membership (асинхронная версия).
    
    Returns:
        Tuple[identity_id, user_id]
    """
    identity_id = await upsert_identity_async(db_session, telegram_id)
    user_id = await upsert_membership_async(
        db_session=db_session,
        tenant_id=tenant_id,
        identity_id=identity_id,
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        tier=tier
    )
    return identity_id, user_id


def save_telegram_session_sync(
    db: Session,
    tenant_id: uuid.UUID,
    session_string: str,
    telegram_user_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    dc_id: int = 2
) -> Tuple[bool, Optional[uuid.UUID], Optional[str]]:
    """
    Context7 best practice: сохранение Telegram сессии в БД (синхронная версия для API).
    
    Сохраняет сессию в telegram_sessions и обновляет telegram_auth_status в users.
    Используется для немедленного сохранения после успешной авторизации.
    
    Args:
        db: SQLAlchemy Session
        tenant_id: UUID tenant
        session_string: StringSession от Telethon
        telegram_user_id: Telegram ID пользователя
        first_name: Имя пользователя
        last_name: Фамилия пользователя
        username: Username пользователя
        dc_id: DataCenter ID (по умолчанию 2)
        
    Returns:
        Tuple[success, session_id, error_message]
    """
    from models.database import Tenant
    from crypto_utils import encrypt_session
    from sqlalchemy import text
    from telethon.sessions import StringSession
    
    try:
        # 1. Получаем/создаём tenant
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            tenant = Tenant(id=tenant_id, name=f"Tenant {telegram_user_id}")
            db.add(tenant)
            db.flush()
        
        # 2. Получаем/создаём identity и membership
        identity_id, user_id = upsert_identity_and_membership_sync(
            db=db,
            tenant_id=tenant_id,
            telegram_id=telegram_user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            tier="free"
        )
        
        # 3. Извлекаем dc_id из session_string если не передан
        if dc_id is None or dc_id == 2:
            try:
                session = StringSession(session_string)
                dc_id = getattr(session, 'dc_id', None) or 2
            except Exception:
                dc_id = 2
        
        # 4. Получаем активный ключ шифрования
        key_result = db.execute(
            text("""
                SELECT key_id FROM encryption_keys 
                WHERE retired_at IS NULL 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        )
        key_row = key_result.fetchone()
        if not key_row:
            return False, None, "No active encryption key found"
        key_id = key_row[0]
        
        # 5. Шифруем сессию
        encrypted_session = encrypt_session(session_string)
        
        # 6. Деактивируем старые сессии для этой Identity
        db.execute(
            text("""
                UPDATE telegram_sessions 
                SET is_active = false, updated_at = NOW()
                WHERE identity_id = :identity_id AND is_active = true
            """),
            {"identity_id": identity_id}
        )
        
        # 7. Сохраняем сессию в telegram_sessions
        session_uuid = uuid.uuid4()
        db.execute(
            text("""
                INSERT INTO telegram_sessions (
                    id, identity_id, telegram_id, session_string_enc, dc_id, is_active, created_at, updated_at
                ) VALUES (
                    :session_id, :identity_id, :telegram_id, :encrypted_session, :dc_id, true, NOW(), NOW()
                )
                ON CONFLICT (identity_id, dc_id) 
                DO UPDATE SET
                    session_string_enc = EXCLUDED.session_string_enc,
                    is_active = true,
                    updated_at = NOW()
            """),
            {
                "session_id": session_uuid,
                "identity_id": identity_id,
                "telegram_id": telegram_user_id,
                "encrypted_session": encrypted_session,
                "dc_id": dc_id
            }
        )
        
        # 8. Обновляем telegram_auth_status в users (если колонки существуют)
        try:
            db.execute(
                text("""
                    UPDATE users 
                    SET 
                        telegram_auth_status = 'authorized',
                        telegram_session_enc = :encrypted_session,
                        telegram_session_key_id = :key_id,
                        telegram_auth_created_at = NOW(),
                        telegram_auth_updated_at = NOW(),
                        telegram_auth_error = NULL
                    WHERE id = :user_id
                """),
                {
                    "encrypted_session": encrypted_session,
                    "key_id": key_id,
                    "user_id": user_id
                }
            )
        except Exception as e:
            # Context7: legacy поля могут отсутствовать - это нормально
            logger.debug("Legacy fields update skipped", error=str(e))
        
        # 9. Коммитим транзакцию
        db.commit()
        
        logger.info(
            "Telegram session saved to database",
            session_id=str(session_uuid),
            identity_id=str(identity_id),
            user_id=str(user_id),
            telegram_id=telegram_user_id,
            tenant_id=str(tenant_id),
            dc_id=dc_id
        )
        
        return True, session_uuid, None
        
    except Exception as e:
        db.rollback()
        error_msg = str(e)
        logger.error(
            "Failed to save Telegram session to database",
            error=error_msg,
            telegram_id=telegram_user_id,
            tenant_id=str(tenant_id),
            exc_info=True
        )
        return False, None, error_msg

