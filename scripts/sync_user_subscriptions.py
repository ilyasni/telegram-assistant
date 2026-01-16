#!/usr/bin/env python3
"""
Скрипт для синхронизации подписок пользователей.
Удаляет лишние подписки, добавляет недостающие, проверяет тестовые каналы и дубли.
"""

import asyncio
import sys
import os
from typing import Dict, List, Set
from datetime import datetime

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from urllib.parse import urlparse, urlunparse

# Ожидаемые подписки для пользователей
EXPECTED_SUBSCRIPTIONS = {
    "139883458": [
        "AGI_and_RL",
        "business_ru",
        "okolo_art",
        "ilyabirman_channel",
        "breakingtrends",
        "ruspm",
        "awdee",
        "bbe_school",
        "neuro_code",
        "b_goncharenko",
        "techno_yandex",
        "tehnomaniak07",
        "ozondesign",
        "jun_hi",
        "How2AI",
        "new_yorko_times",
        "editboat",
        "proudobstvo",
        "designsniper",
        "rybolos_channel",
        "uxnotes",
        "aiwizards",
        "uxhorn",
        "ai_newz",
        "llm_under_hood",
        "uxidesign",
        "betamoscow",
        "desprod",
        "pdigest",
        "monkeyinlaw",
        "ponchiknews",
        "hardclient",
        "dsoloveev",
        "postpostresearch",
        "slashdesigner",
        "mosinkru",
        "Who_X",
        "fffworks",
    ],
    "8124731874": [
        "banksta",
        "naebnet",
        "styleinchina",
        "carsnosleep",
        "tbank",
        "autoreview2022",
        "bankiruofficial",
        "protradein",
        "MarketOverview",
        "autopotoknews",
        "chinamashina_news",
        "AlfaBank",
        "yandex",
        "auto_ru_business",
        "MKuldiaev",
    ],
}

async def sync_user_subscriptions():
    """Синхронизация подписок пользователей."""
    
    # Получаем URL БД из переменных окружения
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    
    # Преобразуем в async URL
    parsed = urlparse(db_url)
    if parsed.scheme == "postgresql":
        new_scheme = "postgresql+asyncpg"
    else:
        new_scheme = parsed.scheme
    
    db_url_async = urlunparse((new_scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "sync_user_subscriptions"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
        print("=" * 80)
        print("СИНХРОНИЗАЦИЯ ПОДПИСОК ПОЛЬЗОВАТЕЛЕЙ")
        print("=" * 80)
        print()
        
        # 1. Проверка текущих подписок
        print("1. Проверка текущих подписок...")
        for telegram_id, expected_channels in EXPECTED_SUBSCRIPTIONS.items():
            print(f"\n   Пользователь {telegram_id}:")
            
            # Получаем текущие подписки
            result = await session.execute(text("""
                SELECT 
                    u.id as user_id,
                    c.username,
                    c.id as channel_id,
                    uc.is_active
                FROM users u
                JOIN user_channel uc ON u.id = uc.user_id
                JOIN channels c ON uc.channel_id = c.id
                WHERE u.telegram_id = :telegram_id
                  AND uc.is_active = true
                ORDER BY c.username
            """), {"telegram_id": int(telegram_id)})
            
            current_subscriptions = result.fetchall()
            current_usernames = {row.username.lower() if row.username else None for row in current_subscriptions if row.username}
            current_usernames = {u for u in current_usernames if u}  # Убираем None
            
            expected_usernames = {ch.lower() for ch in expected_channels}
            
            # Находим лишние подписки
            extra_subscriptions = current_usernames - expected_usernames
            missing_subscriptions = expected_usernames - current_usernames
            
            print(f"      Ожидается: {len(expected_channels)} каналов")
            print(f"      Текущих: {len(current_usernames)} каналов")
            print(f"      Лишних: {len(extra_subscriptions)}")
            print(f"      Отсутствующих: {len(missing_subscriptions)}")
            
            if extra_subscriptions:
                print(f"\n      ⚠️  Лишние подписки (не указаны в списке):")
                for username in sorted(extra_subscriptions):
                    print(f"         - @{username}")
            
            if missing_subscriptions:
                print(f"\n      ⚠️  Отсутствующие подписки:")
                for username in sorted(missing_subscriptions):
                    print(f"         - @{username}")
        
        print("\n" + "=" * 80)
        print("2. Удаление лишних подписок...")
        print("=" * 80)
        
        for telegram_id, expected_channels in EXPECTED_SUBSCRIPTIONS.items():
            expected_usernames = {ch.lower() for ch in expected_channels}
            
            # Получаем лишние подписки
            # Получаем все подписки пользователя
            all_result = await session.execute(text("""
                SELECT 
                    uc.user_id,
                    uc.channel_id,
                    c.username
                FROM users u
                JOIN user_channel uc ON u.id = uc.user_id
                JOIN channels c ON uc.channel_id = c.id
                WHERE u.telegram_id = :telegram_id
                  AND uc.is_active = true
            """), {"telegram_id": int(telegram_id)})
            
            all_rows = all_result.fetchall()
            extra_rows = []
            for row in all_rows:
                username_lower = row.username.lower() if row.username else None
                if username_lower:
                    username_lower = username_lower.lstrip('@')
                    if username_lower not in expected_usernames:
                        extra_rows.append(row)
            
            if extra_rows:
                print(f"\n   Пользователь {telegram_id}: удаление {len(extra_rows)} лишних подписок")
                for row in extra_rows:
                    await session.execute(text("""
                        UPDATE user_channel 
                        SET is_active = false
                        WHERE user_id = :user_id AND channel_id = :channel_id
                    """), {
                        "user_id": str(row.user_id),
                        "channel_id": str(row.channel_id)
                    })
                    print(f"      - Отписано от @{row.username or 'N/A'}")
                await session.commit()
            else:
                print(f"\n   Пользователь {telegram_id}: лишних подписок не найдено")
        
        print("\n" + "=" * 80)
        print("3. Добавление недостающих подписок...")
        print("=" * 80)
        
        for telegram_id, expected_channels in EXPECTED_SUBSCRIPTIONS.items():
            # Получаем user_id
            user_result = await session.execute(text("""
                SELECT id, tenant_id FROM users WHERE telegram_id = :telegram_id LIMIT 1
            """), {"telegram_id": int(telegram_id)})
            user_row = user_result.fetchone()
            
            if not user_row:
                print(f"\n   ⚠️  Пользователь {telegram_id} не найден")
                continue
            
            user_id = str(user_row.id)
            tenant_id = str(user_row.tenant_id)
            
            # Получаем текущие подписки
            current_result = await session.execute(text("""
                SELECT LOWER(LTRIM(c.username, '@')) as username
                FROM user_channel uc
                JOIN channels c ON uc.channel_id = c.id
                WHERE uc.user_id = :user_id AND uc.is_active = true
            """), {"user_id": user_id})
            current_usernames = {row.username for row in current_result.fetchall() if row.username}
            
            missing_count = 0
            for expected_username in expected_channels:
                expected_lower = expected_username.lower()
                if expected_lower not in current_usernames:
                    # Ищем или создаем канал
                    channel_result = await session.execute(text("""
                        SELECT id FROM channels 
                        WHERE LOWER(LTRIM(username, '@')) = :username
                        LIMIT 1
                    """), {"username": expected_lower})
                    channel_row = channel_result.fetchone()
                    
                    if channel_row:
                        channel_id = str(channel_row.id)
                    else:
                        # Создаем канал
                        import uuid
                        channel_id = str(uuid.uuid4())
                        await session.execute(text("""
                            INSERT INTO channels (id, username, title, is_active, created_at)
                            VALUES (:id, :username, :title, true, NOW())
                        """), {
                            "id": channel_id,
                            "username": expected_username,
                            "title": expected_username
                        })
                    
                    # Создаем подписку
                    await session.execute(text("""
                        INSERT INTO user_channel (user_id, channel_id, is_active, settings, subscribed_at)
                        VALUES (:user_id, :channel_id, true, '{}'::jsonb, NOW())
                        ON CONFLICT (user_id, channel_id) DO UPDATE
                        SET is_active = true
                    """), {
                        "user_id": user_id,
                        "channel_id": channel_id
                    })
                    missing_count += 1
                    print(f"      + Подписано на @{expected_username}")
            
            if missing_count > 0:
                await session.commit()
                print(f"\n   Пользователь {telegram_id}: добавлено {missing_count} подписок")
            else:
                print(f"\n   Пользователь {telegram_id}: все подписки на месте")
        
        print("\n" + "=" * 80)
        print("4. Проверка тестовых каналов...")
        print("=" * 80)
        
        # Ищем тестовые каналы (без подписок)
        test_result = await session.execute(text("""
            SELECT 
                c.id,
                c.username,
                c.title,
                COUNT(uc.user_id) as subscription_count
            FROM channels c
            LEFT JOIN user_channel uc ON c.id = uc.channel_id AND uc.is_active = true
            WHERE (
                LOWER(c.username) LIKE '%test%' 
                OR LOWER(c.title) LIKE '%test%'
                OR LOWER(c.username) LIKE '%тест%'
                OR LOWER(c.title) LIKE '%тест%'
            )
            GROUP BY c.id, c.username, c.title
            HAVING COUNT(uc.user_id) = 0
            ORDER BY c.username
        """))
        
        test_channels = test_result.fetchall()
        
        if test_channels:
            print(f"\n   Найдено {len(test_channels)} тестовых каналов без подписок:")
            for row in test_channels:
                print(f"      - @{row.username or 'N/A'} ({row.title})")
            
            print(f"\n   Удаление тестовых каналов...")
            for row in test_channels:
                # Проверяем, нет ли постов
                posts_result = await session.execute(text("""
                    SELECT COUNT(*) as posts_count FROM posts WHERE channel_id = :channel_id
                """), {"channel_id": str(row.id)})
                posts_count = posts_result.fetchone().posts_count
                
                if posts_count == 0:
                    await session.execute(text("""
                        DELETE FROM channels WHERE id = :channel_id
                    """), {"channel_id": str(row.id)})
                    print(f"      - Удален канал @{row.username or 'N/A'} (постов: {posts_count})")
                else:
                    print(f"      - Пропущен канал @{row.username or 'N/A'} (есть {posts_count} постов)")
            
            await session.commit()
        else:
            print("\n   Тестовых каналов без подписок не найдено")
        
        print("\n" + "=" * 80)
        print("5. Проверка дублей каналов...")
        print("=" * 80)
        
        # Ищем дубли по username
        duplicates_result = await session.execute(text("""
            SELECT 
                LOWER(LTRIM(username, '@')) as normalized_username,
                COUNT(*) as channel_count,
                array_agg(id::text) as channel_ids,
                array_agg(username) as usernames
            FROM channels
            WHERE username IS NOT NULL
            GROUP BY LOWER(LTRIM(username, '@'))
            HAVING COUNT(*) > 1
            ORDER BY normalized_username
        """))
        
        duplicates = duplicates_result.fetchall()
        
        if duplicates:
            print(f"\n   Найдено {len(duplicates)} дублей каналов:")
            for row in duplicates:
                print(f"\n      @{row.normalized_username}: {row.channel_count} каналов")
                for i, (channel_id, username) in enumerate(zip(row.channel_ids, row.usernames)):
                    # Проверяем подписки и посты
                    stats_result = await session.execute(text("""
                        SELECT 
                            COUNT(DISTINCT uc.user_id) as subscriptions,
                            COUNT(p.id) as posts
                        FROM channels c
                        LEFT JOIN user_channel uc ON c.id = uc.channel_id AND uc.is_active = true
                        LEFT JOIN posts p ON c.id = p.channel_id
                        WHERE c.id = :channel_id
                        GROUP BY c.id
                    """), {"channel_id": channel_id})
                    stats = stats_result.fetchone()
                    
                    marker = "★" if i == 0 else " "
                    print(f"         {marker} {channel_id[:8]}... (@{username or 'N/A'}) - "
                          f"подписок: {stats.subscriptions or 0}, постов: {stats.posts or 0}")
        else:
            print("\n   Дублей каналов не найдено")
        
        print("\n" + "=" * 80)
        print("6. Итоговый отчет по подпискам...")
        print("=" * 80)
        
        for telegram_id, expected_channels in EXPECTED_SUBSCRIPTIONS.items():
            result = await session.execute(text("""
                SELECT 
                    c.username,
                    c.title
                FROM users u
                JOIN user_channel uc ON u.id = uc.user_id
                JOIN channels c ON uc.channel_id = c.id
                WHERE u.telegram_id = :telegram_id
                  AND uc.is_active = true
                ORDER BY c.username
            """), {"telegram_id": int(telegram_id)})
            
            current_subscriptions = result.fetchall()
            current_usernames = {row.username.lower() if row.username else None for row in current_subscriptions if row.username}
            current_usernames = {u for u in current_usernames if u}
            
            expected_usernames = {ch.lower() for ch in expected_channels}
            extra_subscriptions = current_usernames - expected_usernames
            
            print(f"\n   Пользователь {telegram_id}:")
            print(f"      Всего подписок: {len(current_subscriptions)}")
            print(f"      Ожидается: {len(expected_channels)}")
            
            if extra_subscriptions:
                print(f"\n      ⚠️  Каналы, не указанные в списке:")
                for username in sorted(extra_subscriptions):
                    print(f"         - @{username}")
        
        print("\n" + "=" * 80)
        print("СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА")
        print("=" * 80)

if __name__ == "__main__":
    asyncio.run(sync_user_subscriptions())

