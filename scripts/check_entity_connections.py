#!/usr/bin/env python3
"""
Скрипт для проверки всех связей с каналами и группами для заданного Telegram ID.
Проверяет связи как для пользователей, так и для каналов/групп.

Использование:
    python scripts/check_entity_connections.py 389326685
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
from typing import Optional

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from urllib.parse import urlparse, urlunparse


def format_datetime(dt: Optional[datetime]) -> str:
    """Форматирование даты для вывода."""
    if dt is None:
        return "N/A"
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)


async def check_entity_connections(telegram_id: int):
    """Проверка всех связей для заданного Telegram ID."""
    
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
                "application_name": "check_entity_connections"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
        print("=" * 80)
        print(f"ПРОВЕРКА СВЯЗЕЙ ДЛЯ TELEGRAM ID: {telegram_id}")
        print("=" * 80)
        print()
        
        # 1. Проверяем, является ли это пользователем
        print("1. Проверка как пользователь...")
        user_result = await session.execute(text("""
            SELECT 
                u.id,
                u.telegram_id,
                u.username,
                u.first_name,
                u.last_name,
                u.tenant_id,
                u.created_at,
                u.last_active_at,
                u.tier
            FROM users u
            WHERE u.telegram_id = :telegram_id
        """), {"telegram_id": telegram_id})
        
        user = user_result.fetchone()
        if user:
            print(f"   ✅ Найден пользователь:")
            print(f"      - UUID: {user.id}")
            print(f"      - Telegram ID: {user.telegram_id}")
            print(f"      - Username: @{user.username or 'N/A'}")
            print(f"      - Имя: {user.first_name or 'N/A'} {user.last_name or 'N/A'}")
            print(f"      - Tenant ID: {user.tenant_id}")
            print(f"      - Tier: {user.tier}")
            print(f"      - Создан: {format_datetime(user.created_at)}")
            print(f"      - Последняя активность: {format_datetime(user.last_active_at)}")
            print()
            
            # 1.1. Подписки на каналы
            print("   1.1. Подписки на каналы:")
            channels_result = await session.execute(text("""
                SELECT 
                    c.id as channel_id,
                    c.tg_channel_id,
                    c.username as channel_username,
                    c.title as channel_title,
                    c.is_active as channel_active,
                    uc.subscribed_at,
                    uc.is_active as subscription_active,
                    uc.settings
                FROM user_channel uc
                JOIN channels c ON uc.channel_id = c.id
                WHERE uc.user_id = :user_id
                ORDER BY uc.subscribed_at DESC
            """), {"user_id": user.id})
            
            channels = channels_result.fetchall()
            if channels:
                print(f"      Найдено подписок: {len(channels)}")
                active_count = sum(1 for c in channels if c.subscription_active)
                inactive_count = len(channels) - active_count
                print(f"      - Активных: {active_count}")
                print(f"      - Неактивных: {inactive_count}")
                print()
                print("      Детали подписок:")
                for ch in channels:
                    status = "✅" if ch.subscription_active else "❌"
                    channel_status = "✅" if ch.channel_active else "❌"
                    print(f"      {status} Канал: @{ch.channel_username or 'N/A'} ({ch.channel_title})")
                    print(f"         - Telegram ID канала: {ch.tg_channel_id}")
                    print(f"         - UUID канала: {ch.channel_id}")
                    print(f"         - Статус канала: {channel_status} ({'активен' if ch.channel_active else 'неактивен'})")
                    print(f"         - Подписка создана: {format_datetime(ch.subscribed_at)}")
                    print(f"         - Статус подписки: {'активна' if ch.subscription_active else 'неактивна'}")
                    if ch.settings:
                        print(f"         - Настройки: {ch.settings}")
                    print()
            else:
                print("      Подписок на каналы не найдено")
                print()
            
            # 1.2. Подписки на группы
            print("   1.2. Подписки на группы:")
            groups_result = await session.execute(text("""
                SELECT 
                    g.id as group_id,
                    g.tg_chat_id,
                    g.username as group_username,
                    g.title as group_title,
                    g.tenant_id,
                    g.is_active as group_active,
                    ug.subscribed_at,
                    ug.is_active as subscription_active,
                    ug.monitor_mentions,
                    ug.settings
                FROM user_group ug
                JOIN groups g ON ug.group_id = g.id
                WHERE ug.user_id = :user_id
                ORDER BY ug.subscribed_at DESC
            """), {"user_id": user.id})
            
            groups = groups_result.fetchall()
            if groups:
                print(f"      Найдено подписок: {len(groups)}")
                active_count = sum(1 for g in groups if g.subscription_active)
                inactive_count = len(groups) - active_count
                print(f"      - Активных: {active_count}")
                print(f"      - Неактивных: {inactive_count}")
                print()
                print("      Детали подписок:")
                for gr in groups:
                    status = "✅" if gr.subscription_active else "❌"
                    group_status = "✅" if gr.group_active else "❌"
                    print(f"      {status} Группа: @{gr.group_username or 'N/A'} ({gr.group_title})")
                    print(f"         - Telegram Chat ID: {gr.tg_chat_id}")
                    print(f"         - UUID группы: {gr.group_id}")
                    print(f"         - Tenant ID: {gr.tenant_id}")
                    print(f"         - Статус группы: {group_status} ({'активна' if gr.group_active else 'неактивна'})")
                    print(f"         - Подписка создана: {format_datetime(gr.subscribed_at)}")
                    print(f"         - Статус подписки: {'активна' if gr.subscription_active else 'неактивна'}")
                    print(f"         - Мониторинг упоминаний: {'да' if gr.monitor_mentions else 'нет'}")
                    if gr.settings:
                        print(f"         - Настройки: {gr.settings}")
                    print()
            else:
                print("      Подписок на группы не найдено")
                print()
        else:
            print(f"   ❌ Пользователь с Telegram ID {telegram_id} не найден")
            print()
        
        # 2. Проверяем, является ли это каналом
        print("2. Проверка как канал...")
        channel_result = await session.execute(text("""
            SELECT 
                c.id,
                c.tg_channel_id,
                c.username,
                c.title,
                c.is_active,
                c.last_message_at,
                c.created_at,
                c.settings
            FROM channels c
            WHERE c.tg_channel_id = :tg_channel_id
        """), {"tg_channel_id": telegram_id})
        
        channel = channel_result.fetchone()
        if channel:
            print(f"   ✅ Найден канал:")
            print(f"      - UUID: {channel.id}")
            print(f"      - Telegram ID: {channel.tg_channel_id}")
            print(f"      - Username: @{channel.username or 'N/A'}")
            print(f"      - Название: {channel.title}")
            print(f"      - Статус: {'активен' if channel.is_active else 'неактивен'}")
            print(f"      - Последнее сообщение: {format_datetime(channel.last_message_at)}")
            print(f"      - Создан: {format_datetime(channel.created_at)}")
            if channel.settings:
                print(f"      - Настройки: {channel.settings}")
            print()
            
            # 2.1. Пользователи, подписанные на этот канал
            print("   2.1. Пользователи, подписанные на канал:")
            subscribers_result = await session.execute(text("""
                SELECT 
                    u.id as user_id,
                    u.telegram_id,
                    u.username,
                    u.first_name,
                    u.last_name,
                    u.tenant_id,
                    uc.subscribed_at,
                    uc.is_active,
                    uc.settings
                FROM user_channel uc
                JOIN users u ON uc.user_id = u.id
                WHERE uc.channel_id = :channel_id
                ORDER BY uc.subscribed_at DESC
            """), {"channel_id": channel.id})
            
            subscribers = subscribers_result.fetchall()
            if subscribers:
                print(f"      Найдено подписчиков: {len(subscribers)}")
                active_count = sum(1 for s in subscribers if s.is_active)
                inactive_count = len(subscribers) - active_count
                print(f"      - Активных подписок: {active_count}")
                print(f"      - Неактивных подписок: {inactive_count}")
                print()
                print("      Детали подписчиков:")
                for sub in subscribers:
                    status = "✅" if sub.is_active else "❌"
                    print(f"      {status} Пользователь: @{sub.username or 'N/A'} ({sub.first_name or ''} {sub.last_name or ''})".strip())
                    print(f"         - Telegram ID: {sub.telegram_id}")
                    print(f"         - UUID: {sub.user_id}")
                    print(f"         - Tenant ID: {sub.tenant_id}")
                    print(f"         - Подписка создана: {format_datetime(sub.subscribed_at)}")
                    print(f"         - Статус: {'активна' if sub.is_active else 'неактивна'}")
                    if sub.settings:
                        print(f"         - Настройки: {sub.settings}")
                    print()
            else:
                print("      Подписчиков не найдено")
                print()
            
            # 2.2. Статистика постов в канале
            print("   2.2. Статистика постов в канале:")
            posts_stats_result = await session.execute(text("""
                SELECT 
                    COUNT(*) as total_posts,
                    COUNT(CASE WHEN posted_at >= NOW() - INTERVAL '7 days' THEN 1 END) as posts_last_7d,
                    COUNT(CASE WHEN posted_at >= NOW() - INTERVAL '30 days' THEN 1 END) as posts_last_30d,
                    MIN(posted_at) as first_post,
                    MAX(posted_at) as last_post
                FROM posts
                WHERE channel_id = :channel_id
            """), {"channel_id": channel.id})
            
            posts_stats = posts_stats_result.fetchone()
            if posts_stats and posts_stats.total_posts > 0:
                print(f"      - Всего постов: {posts_stats.total_posts}")
                print(f"      - За последние 7 дней: {posts_stats.posts_last_7d}")
                print(f"      - За последние 30 дней: {posts_stats.posts_last_30d}")
                print(f"      - Первый пост: {format_datetime(posts_stats.first_post)}")
                print(f"      - Последний пост: {format_datetime(posts_stats.last_post)}")
            else:
                print("      Постов в канале не найдено")
            print()
        else:
            print(f"   ❌ Канал с Telegram ID {telegram_id} не найден")
            print()
        
        # 3. Проверяем, является ли это группой
        print("3. Проверка как группа...")
        group_result = await session.execute(text("""
            SELECT 
                g.id,
                g.tg_chat_id,
                g.username,
                g.title,
                g.tenant_id,
                g.is_active,
                g.last_checked_at,
                g.created_at,
                g.settings
            FROM groups g
            WHERE g.tg_chat_id = :tg_chat_id
        """), {"tg_chat_id": telegram_id})
        
        group = group_result.fetchone()
        if group:
            print(f"   ✅ Найдена группа:")
            print(f"      - UUID: {group.id}")
            print(f"      - Telegram Chat ID: {group.tg_chat_id}")
            print(f"      - Username: @{group.username or 'N/A'}")
            print(f"      - Название: {group.title}")
            print(f"      - Tenant ID: {group.tenant_id}")
            print(f"      - Статус: {'активна' if group.is_active else 'неактивна'}")
            print(f"      - Последняя проверка: {format_datetime(group.last_checked_at)}")
            print(f"      - Создана: {format_datetime(group.created_at)}")
            if group.settings:
                print(f"      - Настройки: {group.settings}")
            print()
            
            # 3.1. Пользователи, подписанные на эту группу
            print("   3.1. Пользователи, подписанные на группу:")
            members_result = await session.execute(text("""
                SELECT 
                    u.id as user_id,
                    u.telegram_id,
                    u.username,
                    u.first_name,
                    u.last_name,
                    u.tenant_id,
                    ug.subscribed_at,
                    ug.is_active,
                    ug.monitor_mentions,
                    ug.settings
                FROM user_group ug
                JOIN users u ON ug.user_id = u.id
                WHERE ug.group_id = :group_id
                ORDER BY ug.subscribed_at DESC
            """), {"group_id": group.id})
            
            members = members_result.fetchall()
            if members:
                print(f"      Найдено подписчиков: {len(members)}")
                active_count = sum(1 for m in members if m.is_active)
                inactive_count = len(members) - active_count
                print(f"      - Активных подписок: {active_count}")
                print(f"      - Неактивных подписок: {inactive_count}")
                print()
                print("      Детали подписчиков:")
                for mem in members:
                    status = "✅" if mem.is_active else "❌"
                    print(f"      {status} Пользователь: @{mem.username or 'N/A'} ({mem.first_name or ''} {mem.last_name or ''})".strip())
                    print(f"         - Telegram ID: {mem.telegram_id}")
                    print(f"         - UUID: {mem.user_id}")
                    print(f"         - Tenant ID: {mem.tenant_id}")
                    print(f"         - Подписка создана: {format_datetime(mem.subscribed_at)}")
                    print(f"         - Статус: {'активна' if mem.is_active else 'неактивна'}")
                    print(f"         - Мониторинг упоминаний: {'да' if mem.monitor_mentions else 'нет'}")
                    if mem.settings:
                        print(f"         - Настройки: {mem.settings}")
                    print()
            else:
                print("      Подписчиков не найдено")
                print()
            
            # 3.2. Статистика сообщений в группе
            print("   3.2. Статистика сообщений в группе:")
            messages_stats_result = await session.execute(text("""
                SELECT 
                    COUNT(*) as total_messages,
                    COUNT(CASE WHEN sent_at >= NOW() - INTERVAL '7 days' THEN 1 END) as messages_last_7d,
                    COUNT(CASE WHEN sent_at >= NOW() - INTERVAL '30 days' THEN 1 END) as messages_last_30d,
                    MIN(sent_at) as first_message,
                    MAX(sent_at) as last_message
                FROM group_messages
                WHERE group_id = :group_id
            """), {"group_id": group.id})
            
            messages_stats = messages_stats_result.fetchone()
            if messages_stats and messages_stats.total_messages > 0:
                print(f"      - Всего сообщений: {messages_stats.total_messages}")
                print(f"      - За последние 7 дней: {messages_stats.messages_last_7d}")
                print(f"      - За последние 30 дней: {messages_stats.messages_last_30d}")
                print(f"      - Первое сообщение: {format_datetime(messages_stats.first_message)}")
                print(f"      - Последнее сообщение: {format_datetime(messages_stats.last_message)}")
            else:
                print("      Сообщений в группе не найдено")
            print()
        else:
            print(f"   ❌ Группа с Telegram Chat ID {telegram_id} не найдена")
            print()
        
        # Итоговая сводка
        print("=" * 80)
        print("ИТОГОВАЯ СВОДКА:")
        print("=" * 80)
        
        found_entities = []
        if user:
            found_entities.append(f"Пользователь (@{user.username or 'N/A'})")
        if channel:
            found_entities.append(f"Канал (@{channel.username or 'N/A'})")
        if group:
            found_entities.append(f"Группа (@{group.username or 'N/A'})")
        
        if found_entities:
            print(f"Найдено сущностей: {len(found_entities)}")
            for entity in found_entities:
                print(f"  - {entity}")
        else:
            print(f"❌ Не найдено ни одной сущности с Telegram ID {telegram_id}")
            print()
            print("Возможные причины:")
            print("  - ID не существует в базе данных")
            print("  - ID указан неправильно")
            print("  - Данные еще не синхронизированы")
        
        print()
        print("=" * 80)
        print("ПРОВЕРКА ЗАВЕРШЕНА")
        print("=" * 80)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python scripts/check_entity_connections.py <telegram_id>")
        sys.exit(1)
    
    try:
        telegram_id = int(sys.argv[1])
    except ValueError:
        print(f"Ошибка: '{sys.argv[1]}' не является числом")
        sys.exit(1)
    
    asyncio.run(check_entity_connections(telegram_id))


