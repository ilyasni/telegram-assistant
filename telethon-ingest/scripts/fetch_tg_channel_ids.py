#!/usr/bin/env python3
"""
Скрипт для получения tg_channel_id для каналов без него.
Context7: Использует Telethon для получения entity и сохраняет в БД.
"""

import asyncio
import os
import sys
from typing import Optional

# Добавляем путь к проекту
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import structlog
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, UsernameNotOccupiedError

from config import settings

logger = structlog.get_logger()


async def get_tg_channel_id(client: TelegramClient, username: str) -> Optional[int]:
    """
    Получить tg_channel_id для канала по username.
    Context7: Обработка FloodWait и ошибок.
    """
    try:
        # Убираем @ из начала username
        clean_username = username.lstrip('@')
        
        # Получаем entity
        entity = await client.get_entity(clean_username)
        
        # Проверяем, что это канал
        if hasattr(entity, 'id'):
            tg_channel_id = entity.id
            logger.info(
                "Got tg_channel_id for channel",
                username=username,
                tg_channel_id=tg_channel_id
            )
            return tg_channel_id
        else:
            logger.warning(
                "Entity has no valid ID",
                username=username
            )
            return None
            
    except FloodWaitError as e:
        logger.warning(
            "FloodWait error, skipping channel",
            username=username,
            wait_seconds=e.seconds
        )
        return None
    except UsernameNotOccupiedError:
        logger.warning(
            "Username not occupied",
            username=username
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to get tg_channel_id",
            username=username,
            error=str(e)
        )
        return None


async def update_tg_channel_id(db_session: AsyncSession, channel_id: str, tg_channel_id: int):
    """
    Обновить tg_channel_id в БД.
    Context7: Используем async session и проверяем уникальность.
    """
    try:
        # Проверяем, не используется ли уже этот tg_channel_id
        check_query = text("""
            SELECT id FROM channels 
            WHERE tg_channel_id = :tg_channel_id AND id != :channel_id
        """)
        result = await db_session.execute(
            check_query,
            {"tg_channel_id": tg_channel_id, "channel_id": channel_id}
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.warning(
                "tg_channel_id already exists for another channel",
                channel_id=channel_id,
                tg_channel_id=tg_channel_id,
                existing_channel_id=existing
            )
            return False
        
        # Обновляем tg_channel_id
        update_query = text("""
            UPDATE channels 
            SET tg_channel_id = :tg_channel_id
            WHERE id = :channel_id
        """)
        await db_session.execute(
            update_query,
            {"tg_channel_id": tg_channel_id, "channel_id": channel_id}
        )
        await db_session.commit()
        
        logger.info(
            "Updated tg_channel_id",
            channel_id=channel_id,
            tg_channel_id=tg_channel_id
        )
        return True
        
    except Exception as e:
        await db_session.rollback()
        logger.error(
            "Failed to update tg_channel_id",
            channel_id=channel_id,
            error=str(e)
        )
        return False


async def fetch_missing_tg_channel_ids():
    """
    Получить tg_channel_id для всех каналов без него.
    Context7: Использует TelegramClientManager для получения клиента.
    """
    # Инициализация БД
    db_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    # Инициализация Redis для TelegramClientManager
    import redis.asyncio as redis
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    
    # Инициализация TelegramClientManager
    from services.telegram_client_manager import TelegramClientManager
    import psycopg2
    # Context7: psycopg2 использует postgresql:// напрямую, без +psycopg2
    db_url_psycopg2 = settings.database_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    db_conn = psycopg2.connect(db_url_psycopg2)
    
    client_manager = TelegramClientManager(redis_client, db_conn)
    await client_manager.initialize()
    
    # Получаем клиент (используем первый доступный telegram_id)
    # Context7: Используем master telegram_id из настроек или первый доступный
    master_telegram_id = os.getenv("TELEGRAM_MASTER_ID", None)
    if master_telegram_id:
        try:
            master_telegram_id = int(master_telegram_id)
        except:
            master_telegram_id = None
    
    # Если нет master_telegram_id, используем первый доступный клиент
    if not master_telegram_id:
        # Получаем первый telegram_id из БД
        async with async_session() as temp_session:
            query = text("""
                SELECT telegram_id FROM telegram_sessions 
                WHERE is_active = true 
                LIMIT 1
            """)
            result = await temp_session.execute(query)
            row = result.scalar_one_or_none()
            if row:
                master_telegram_id = int(row)
    
    if not master_telegram_id:
        logger.error("No active Telegram session found")
        return
    
    client = await client_manager.get_client(master_telegram_id)
    
    if not client:
        logger.error("Failed to get Telegram client")
        return
    
    try:
        logger.info("Telethon client obtained from TelegramClientManager")
        
        async with async_session() as db_session:
            # Получаем каналы без tg_channel_id
            query = text("""
                SELECT id, username, is_active
                FROM channels
                WHERE tg_channel_id IS NULL 
                  AND username IS NOT NULL
                ORDER BY is_active DESC, username
            """)
            result = await db_session.execute(query)
            channels = result.fetchall()
            
            logger.info(
                "Found channels without tg_channel_id",
                count=len(channels)
            )
            
            updated_count = 0
            failed_count = 0
            skipped_count = 0
            
            for channel in channels:
                channel_id, username, is_active = channel
                
                logger.info(
                    "Processing channel",
                    channel_id=channel_id,
                    username=username,
                    is_active=is_active
                )
                
                # Получаем tg_channel_id
                tg_channel_id = await get_tg_channel_id(client, username)
                
                if tg_channel_id is None:
                    skipped_count += 1
                    continue
                
                # Обновляем в БД
                success = await update_tg_channel_id(db_session, channel_id, tg_channel_id)
                
                if success:
                    updated_count += 1
                else:
                    failed_count += 1
                
                # Небольшая задержка между запросами
                await asyncio.sleep(1)
            
            logger.info(
                "Completed fetching tg_channel_ids",
                total=len(channels),
                updated=updated_count,
                failed=failed_count,
                skipped=skipped_count
            )
    
    except Exception as e:
        logger.error("Error in fetch_missing_tg_channel_ids", error=str(e), exc_info=True)
    finally:
        # Context7: Не отключаем клиент, так как он управляется TelegramClientManager
        await engine.dispose()
        db_conn.close()


if __name__ == "__main__":
    asyncio.run(fetch_missing_tg_channel_ids())

