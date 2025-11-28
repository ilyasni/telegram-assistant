#!/usr/bin/env python3
"""
Скрипт для проверки привязки каналов к пользователям.
Проверяет, что у пользователей есть только те каналы, на которые они подписывались.
"""

import asyncio
import sys
import os
from datetime import datetime, timezone

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from urllib.parse import urlparse, urlunparse

async def check_user_subscriptions():
    """Проверка подписок пользователей на каналы."""
    
    # Получаем URL БД из переменных окружения
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    
    # Преобразуем в async URL
    parsed = urlparse(db_url)
    if parsed.scheme == "postgresql":
        new_scheme = "postgresql+asyncpg"
    else:
        new_scheme = parsed.scheme
    
    # asyncpg не поддерживает async_fallback, используем оригинальный query
    db_url_async = urlunparse((new_scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "check_user_subscriptions"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
        print("=" * 80)
        print("ПРОВЕРКА ПРИВЯЗКИ КАНАЛОВ К ПОЛЬЗОВАТЕЛЯМ")
        print("=" * 80)
        print()
        
        # 1. Проверка: есть ли неактивные подписки, которые показываются пользователям
        print("1. Проверка неактивных подписок...")
        inactive_result = await session.execute(text("""
            SELECT 
                u.telegram_id,
                u.username as user_username,
                c.username as channel_username,
                c.title as channel_title,
                uc.subscribed_at,
                uc.is_active
            FROM user_channel uc
            JOIN users u ON uc.user_id = u.id
            JOIN channels c ON uc.channel_id = c.id
            WHERE uc.is_active = false
            ORDER BY u.telegram_id, uc.subscribed_at DESC
            LIMIT 50
        """))
        
        inactive_subscriptions = inactive_result.fetchall()
        if inactive_subscriptions:
            print(f"   ⚠️  Найдено {len(inactive_subscriptions)} неактивных подписок:")
            for row in inactive_subscriptions[:10]:  # Показываем первые 10
                print(f"      - Пользователь {row.telegram_id} (@{row.user_username or 'N/A'}) "
                      f"→ Канал @{row.channel_username or 'N/A'} ({row.channel_title}) "
                      f"[is_active={row.is_active}]")
            if len(inactive_subscriptions) > 10:
                print(f"      ... и еще {len(inactive_subscriptions) - 10} неактивных подписок")
        else:
            print("   ✅ Неактивных подписок не найдено")
        print()
        
        # 2. Проверка: каналы с постами, созданными ДО подписки
        print("2. Проверка каналов с постами, созданными до подписки...")
        early_posts_result = await session.execute(text("""
            SELECT 
                u.telegram_id,
                u.username as user_username,
                c.username as channel_username,
                c.title as channel_title,
                uc.subscribed_at,
                MIN(p.posted_at) as first_post_at,
                COUNT(p.id) as posts_count
            FROM channels c
            JOIN user_channel uc ON c.id = uc.channel_id
            JOIN users u ON uc.user_id = u.id
            LEFT JOIN posts p ON p.channel_id = c.id
            WHERE uc.is_active = true
            GROUP BY u.telegram_id, u.username, c.id, c.username, c.title, uc.subscribed_at
            HAVING COUNT(p.id) > 0 AND MIN(p.posted_at) < uc.subscribed_at
            ORDER BY (MIN(p.posted_at) - uc.subscribed_at) ASC
            LIMIT 20
        """))
        
        early_posts = early_posts_result.fetchall()
        if early_posts:
            print(f"   ⚠️  Найдено {len(early_posts)} каналов с постами до подписки:")
            for row in early_posts:
                time_diff = (row.subscribed_at - row.first_post_at).total_seconds() / 3600
                print(f"      - Пользователь {row.telegram_id} (@{row.user_username or 'N/A'}) "
                      f"→ Канал @{row.channel_username or 'N/A'} ({row.channel_title})")
                print(f"        Первый пост: {row.first_post_at}, Подписка: {row.subscribed_at} "
                      f"(разница: {time_diff:.1f} часов)")
                print(f"        Постов: {row.posts_count}")
        else:
            print("   ✅ Каналов с постами до подписки не найдено")
        print()
        
        # 3. Статистика по пользователям
        print("3. Статистика подписок по пользователям...")
        stats_result = await session.execute(text("""
            SELECT 
                u.telegram_id,
                u.username,
                COUNT(DISTINCT CASE WHEN uc.is_active = true THEN uc.channel_id END) as active_subscriptions,
                COUNT(DISTINCT CASE WHEN uc.is_active = false THEN uc.channel_id END) as inactive_subscriptions,
                COUNT(DISTINCT uc.channel_id) as total_subscriptions
            FROM users u
            LEFT JOIN user_channel uc ON u.id = uc.user_id
            GROUP BY u.telegram_id, u.username
            HAVING COUNT(DISTINCT uc.channel_id) > 0
            ORDER BY active_subscriptions DESC
            LIMIT 20
        """))
        
        stats = stats_result.fetchall()
        if stats:
            print(f"   Найдено {len(stats)} пользователей с подписками:")
            for row in stats:
                print(f"      - Пользователь {row.telegram_id} (@{row.username or 'N/A'}): "
                      f"активных: {row.active_subscriptions}, "
                      f"неактивных: {row.inactive_subscriptions}, "
                      f"всего: {row.total_subscriptions}")
        else:
            print("   Пользователей с подписками не найдено")
        print()
        
        # 4. Проверка: подписки без явного создания через API
        print("4. Проверка подписок, созданных автоматически (без явной подписки)...")
        # Это сложно проверить напрямую, но можно проверить подписки без событий в outbox
        # или подписки, где subscribed_at совпадает с временем парсинга
        
        print("   ℹ️  Для полной проверки нужно анализировать логи парсера и события outbox")
        print()
        
        # 5. Рекомендации
        print("=" * 80)
        print("РЕКОМЕНДАЦИИ:")
        print("=" * 80)
        print()
        print("1. Убедитесь, что парсер проверяет is_active = true перед парсингом")
        print("2. Проверьте, что функция _ensure_user_channel не вызывается в парсере")
        print("3. Удалите неактивные подписки, если они не нужны:")
        print("   DELETE FROM user_channel WHERE is_active = false;")
        print("4. Для каналов с постами до подписки - проверьте логику создания подписок")
        print()
        
        print("=" * 80)
        print("ПРОВЕРКА ЗАВЕРШЕНА")
        print("=" * 80)

if __name__ == "__main__":
    asyncio.run(check_user_subscriptions())

