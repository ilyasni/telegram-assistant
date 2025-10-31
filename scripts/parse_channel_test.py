#!/usr/bin/env python3
"""
Скрипт для тестового запуска парсинга канала business_ru
"""
import asyncio
import sys
import os
sys.path.insert(0, '/opt/telegram-assistant')

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from telethon_ingest.services.channel_parser import ChannelParser, ParserConfig
from telethon_ingest.services.atomic_db_saver import AtomicDBSaver
from telethon_ingest.services.telegram_client_manager import TelegramClientManager
import redis.asyncio as redis

async def main():
    # Настройки
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # Преобразуем db_url для asyncpg
    db_url_async = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    # Создаем engine и session
    engine = create_async_engine(db_url_async, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    # Находим канал business_ru
    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT id, username, title, tg_channel_id FROM channels WHERE username LIKE :username LIMIT 1"),
            {"username": "%business_ru%"}
        )
        channel_row = result.fetchone()
        
        if not channel_row:
            print("Канал business_ru не найден в БД")
            print("Доступные каналы:")
            result = await session.execute(text("SELECT id, username, title FROM channels LIMIT 10"))
            for row in result.fetchall():
                print(f"  - {row.username}: {row.title} (ID: {row.id})")
            return
        
        channel_id = channel_row.id
        channel_username = channel_row.username
        print(f"Найден канал: {channel_username} (ID: {channel_id}, TG ID: {channel_row.tg_channel_id})")
        
        # Получаем user_id и tenant_id
        result = await session.execute(
            text("SELECT id, tenant_id FROM users LIMIT 1")
        )
        user_row = result.fetchone()
        if not user_row:
            print("Пользователь не найден в БД")
            return
        
        user_id = user_row.id
        tenant_id = user_row.tenant_id
        
        print(f"Используем user_id: {user_id}, tenant_id: {tenant_id}")
    
    # Создаем компоненты
    config = ParserConfig()
    config.db_url = db_url
    config.redis_url = redis_url
    
    redis_client = redis.from_url(redis_url, decode_responses=True)
    atomic_saver = AtomicDBSaver()
    
    # Инициализируем TelegramClientManager
    telegram_client_manager = TelegramClientManager(
        redis_client=redis_client,
        config=config
    )
    
    # Создаем парсер
    async with async_session_factory() as session:
        parser = ChannelParser(
            config=config,
            db_session=session,
            event_publisher=None,
            redis_client=redis_client,
            atomic_saver=atomic_saver,
            telegram_client_manager=telegram_client_manager
        )
        
        print(f"\nЗапускаю парсинг канала {channel_username} в режиме incremental...")
        
        try:
            result = await parser.parse_channel_messages(
                channel_id=channel_id,
                user_id=user_id,
                tenant_id=tenant_id,
                mode="incremental"
            )
            
            print(f"\nПарсинг завершен:")
            print(f"  Статус: {result.get('status')}")
            print(f"  Обработано сообщений: {result.get('messages_processed', 0)}")
            print(f"  Максимальная дата: {result.get('max_message_date')}")
            print(f"  Время обработки: {result.get('processing_time', 0):.2f} сек")
            
        except Exception as e:
            print(f"Ошибка при парсинге: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await telegram_client_manager.close_all()
            await redis_client.aclose()
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())

