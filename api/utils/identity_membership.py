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
        user.tier = tier
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
            tier = EXCLUDED.tier,
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

