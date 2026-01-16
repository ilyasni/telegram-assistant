#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы IngestAccountPool.

Context7: Проверяет выбор аккаунтов из пула, работу с Redis, фильтрацию по blocked_until.
"""

import asyncio
import os
import sys
from pathlib import Path

# Добавляем пути - в контейнере рабочая директория /app
if Path("/app").exists():
    # В контейнере
    sys.path.insert(0, "/app")
    PROJECT_ROOT = Path("/app")
else:
    # Локально
    PROJECT_ROOT = Path("/opt/telegram-assistant")
    sys.path.insert(0, str(PROJECT_ROOT / "telethon-ingest"))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import redis.asyncio as redis
from services.ingest_account_pool import IngestAccountPool
import structlog

logger = structlog.get_logger()


async def test_ingest_account_pool():
    """Тестирование IngestAccountPool."""
    
    # Подключение к БД
    from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
    
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    # Context7: Удаляем unsupported параметры для asyncpg
    parsed = urlparse(db_url)
    qs = parse_qs(parsed.query)
    for key in ['connect_timeout', 'application_name', 'keepalives', 'keepalives_idle', 'keepalives_interval', 'keepalives_count']:
        qs.pop(key, None)
    new_query = urlencode(qs, doseq=True)
    db_url_clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    
    # Context7: asyncpg не поддерживает connect_timeout в create_async_engine
    engine = create_async_engine(
        db_url_clean,
        poolclass=None,
        pool_pre_ping=True,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "test_ingest_pool"
            }
        }
    )
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    # Подключение к Redis
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    async with async_session_factory() as db_session:
        pool = IngestAccountPool(db_session, redis_client)
        
        print("=" * 60)
        print("Тестирование IngestAccountPool")
        print("=" * 60)
        
        # 1. Получение доступных аккаунтов для read
        print("\n1. Получение доступных аккаунтов для task_type='read':")
        accounts_read = await pool.get_available_accounts('read')
        print(f"   Найдено аккаунтов: {len(accounts_read)}")
        for acc in accounts_read:
            print(f"   - telegram_id={acc['telegram_id']}, priority={acc['priority']}, "
                  f"role={acc['role']}, inflight={acc['inflight']}")
        
        # 2. Получение доступных аккаунтов для resolver
        print("\n2. Получение доступных аккаунтов для task_type='resolver':")
        accounts_resolver = await pool.get_available_accounts('resolver')
        print(f"   Найдено аккаунтов: {len(accounts_resolver)}")
        for acc in accounts_resolver:
            print(f"   - telegram_id={acc['telegram_id']}, priority={acc['priority']}, "
                  f"role={acc['role']}, inflight={acc['inflight']}")
        
        # 3. Выбор аккаунта для канала (read)
        print("\n3. Выбор аккаунта для канала (task_type='read'):")
        test_channel_id = "00000000-0000-0000-0000-000000000001"
        result = await pool.select_account_for_channel(
            channel_id=test_channel_id,
            task_type='read'
        )
        if result:
            telegram_id, ingest_account_id = result
            print(f"   ✅ Выбран аккаунт: telegram_id={telegram_id}, ingest_account_id={ingest_account_id}")
            
            # Проверяем inflight в Redis
            inflight_key = f"ingest:acct:{ingest_account_id}:inflight"
            inflight_value = await redis_client.get(inflight_key)
            print(f"   ✅ Inflight в Redis: {inflight_value}")
            
            # Освобождаем inflight
            await pool.mark_account_complete(ingest_account_id)
            inflight_after = await redis_client.get(inflight_key)
            print(f"   ✅ Inflight после освобождения: {inflight_after}")
        else:
            print("   ❌ Не удалось выбрать аккаунт")
        
        # 4. Выбор аккаунта для resolver
        print("\n4. Выбор аккаунта для канала (task_type='resolver'):")
        result2 = await pool.select_account_for_channel(
            channel_id=test_channel_id,
            task_type='resolver'
        )
        if result2:
            telegram_id, ingest_account_id = result2
            print(f"   ✅ Выбран аккаунт: telegram_id={telegram_id}, ingest_account_id={ingest_account_id}")
            await pool.mark_account_complete(ingest_account_id)
        else:
            print("   ❌ Не удалось выбрать аккаунт")
        
        # 5. Проверка получения аккаунта по telegram_id
        print("\n5. Получение аккаунта по telegram_id=8124731874:")
        account = await pool.get_account_by_telegram_id(8124731874)
        if account:
            print(f"   ✅ Найден аккаунт: id={account['id']}, priority={account['priority']}, "
                  f"role={account['role']}, is_active={account['is_active']}")
        else:
            print("   ❌ Аккаунт не найден")
        
        # 6. Проверка Redis ключей
        print("\n6. Проверка Redis ключей:")
        keys = await redis_client.keys("ingest:acct:*")
        print(f"   Найдено ключей: {len(keys)}")
        for key in keys[:10]:  # Показываем первые 10
            value = await redis_client.get(key)
            print(f"   - {key}: {value}")
        
        print("\n" + "=" * 60)
        print("✅ Тестирование завершено")
        print("=" * 60)
    
    await redis_client.aclose()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_ingest_account_pool())
