#!/usr/bin/env python3
"""
Скрипт для проверки группы и её дайджестов.
Проверяет подписки, сообщения, участников и статус дайджестов.

Использование:
    python scripts/check_group_digest.py --group-title "Core Banking design team"
    python scripts/check_group_digest.py --history-id b1a19877-cd9d-4712-85bc-94379c35bcab
    python scripts/check_group_digest.py --user-telegram-id 389326685
"""

import asyncio
import sys
import os
import argparse
from datetime import datetime
from typing import Optional
from uuid import UUID

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


async def check_group_by_title(group_title: str, db_url_async: str):
    """Проверка группы по названию."""
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "check_group_digest"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
        print("=" * 80)
        print(f"ПРОВЕРКА ГРУППЫ: {group_title}")
        print("=" * 80)
        print()
        
        # Ищем группу по названию
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
            WHERE LOWER(g.title) LIKE LOWER(:group_title)
            ORDER BY g.created_at DESC
        """), {"group_title": f"%{group_title}%"})
        
        groups = group_result.fetchall()
        
        if not groups:
            print(f"❌ Группа '{group_title}' не найдена")
            print()
            print("Поиск похожих групп...")
            similar_result = await session.execute(text("""
                SELECT 
                    g.id,
                    g.title,
                    g.username,
                    g.tg_chat_id
                FROM groups g
                WHERE g.title ILIKE :pattern
                ORDER BY g.created_at DESC
                LIMIT 10
            """), {"pattern": f"%{group_title.split()[0] if group_title.split() else ''}%"})
            
            similar = similar_result.fetchall()
            if similar:
                print("Найдены похожие группы:")
                for gr in similar:
                    print(f"  - {gr.title} (@{gr.username or 'N/A'}) [ID: {gr.id}]")
            return
        
        # Если несколько групп, показываем все
        for group in groups:
            await check_group_details(session, group, db_url_async)


async def check_group_by_history_id(history_id: str, db_url_async: str):
    """Проверка дайджеста по history_id."""
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "check_group_digest"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
        print("=" * 80)
        print(f"ПРОВЕРКА ДАЙДЖЕСТА: {history_id}")
        print("=" * 80)
        print()
        
        # Ищем DigestHistory по ID (используем bindparam для правильной работы с UUID)
        from sqlalchemy import bindparam
        history_result = await session.execute(text("""
            SELECT 
                dh.id,
                dh.user_id,
                dh.tenant_id,
                dh.digest_date,
                dh.content,
                dh.posts_count,
                dh.status,
                dh.created_at
            FROM digest_history dh
            WHERE dh.id = CAST(:history_id AS uuid)
        """), {"history_id": history_id})
        
        history = history_result.fetchone()
        
        if not history:
            print(f"❌ Дайджест с history_id {history_id} не найден")
            return
        
        print(f"✅ Найден дайджест:")
        print(f"   - History ID: {history.id}")
        print(f"   - User ID: {history.user_id}")
        print(f"   - Tenant ID: {history.tenant_id}")
        print(f"   - Дата: {format_datetime(history.digest_date)}")
        print(f"   - Статус: {history.status}")
        print(f"   - Количество постов: {history.posts_count}")
        print(f"   - Создан: {format_datetime(history.created_at)}")
        print()
        
        # Ищем связанное окно обсуждения
        window_result = await session.execute(text("""
            SELECT 
                gcw.id,
                gcw.group_id,
                gcw.tenant_id,
                gcw.window_start,
                gcw.window_end,
                gcw.message_count,
                gcw.participant_count,
                gcw.status,
                gcw.generated_at,
                g.title as group_title,
                g.tg_chat_id,
                g.username as group_username
            FROM group_conversation_windows gcw
            JOIN groups g ON gcw.group_id = g.id
            WHERE gcw.tenant_id = :tenant_id
            ORDER BY gcw.window_end DESC
            LIMIT 10
        """), {"tenant_id": history.tenant_id})
        
        windows = window_result.fetchall()
        
        print(f"Окна обсуждения для этого tenant (последние 10):")
        for win in windows:
            print(f"   - Окно: {win.id}")
            print(f"     Группа: {win.group_title} (@{win.group_username or 'N/A'})")
            print(f"     Период: {format_datetime(win.window_start)} - {format_datetime(win.window_end)}")
            print(f"     Сообщений: {win.message_count}, Участников: {win.participant_count}")
            print(f"     Статус: {win.status}")
            print()
        
        # Ищем группу, связанную с дайджестом через окно
        if windows:
            group_id = windows[0].group_id
            await check_group_by_id(str(group_id), db_url_async)


async def check_group_by_id(group_id: str, db_url_async: str):
    """Проверка группы по UUID."""
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "check_group_digest"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
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
            WHERE g.id = :group_id::uuid
        """), {"group_id": group_id})
        
        group = group_result.fetchone()
        
        if not group:
            print(f"❌ Группа с ID {group_id} не найдена")
            return
        
        await check_group_details(session, group, db_url_async)


async def check_group_details(session: AsyncSession, group, db_url_async: str):
    """Детальная проверка группы."""
    print(f"✅ Найдена группа:")
    print(f"   - UUID: {group.id}")
    print(f"   - Telegram Chat ID: {group.tg_chat_id}")
    print(f"   - Username: @{group.username or 'N/A'}")
    print(f"   - Название: {group.title}")
    print(f"   - Tenant ID: {group.tenant_id}")
    print(f"   - Статус: {'активна' if group.is_active else 'неактивна'}")
    print(f"   - Последняя проверка: {format_datetime(group.last_checked_at)}")
    print(f"   - Создана: {format_datetime(group.created_at)}")
    print()
    
    # Подписки пользователей
    print("1. Подписки пользователей на группу:")
    subscribers_result = await session.execute(text("""
        SELECT 
            u.id as user_id,
            u.telegram_id,
            u.username,
            u.first_name,
            u.last_name,
            ug.subscribed_at,
            ug.is_active,
            ug.monitor_mentions
        FROM user_group ug
        JOIN users u ON ug.user_id = u.id
        WHERE ug.group_id = :group_id
        ORDER BY ug.subscribed_at DESC
    """), {"group_id": group.id})
    
    subscribers = subscribers_result.fetchall()
    if subscribers:
        print(f"   Найдено подписчиков: {len(subscribers)}")
        for sub in subscribers:
            status = "✅" if sub.is_active else "❌"
            print(f"   {status} @{sub.username or 'N/A'} ({sub.telegram_id}) - {'активна' if sub.is_active else 'неактивна'}")
            if sub.monitor_mentions:
                print(f"      Мониторинг упоминаний: включён")
    else:
        print("   ❌ Подписчиков не найдено")
    print()
    
    # Статистика сообщений
    print("2. Статистика сообщений в группе:")
    messages_stats_result = await session.execute(text("""
        SELECT 
            COUNT(*) as total_messages,
            COUNT(CASE WHEN posted_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as messages_last_24h,
            COUNT(CASE WHEN posted_at >= NOW() - INTERVAL '7 days' THEN 1 END) as messages_last_7d,
            MIN(posted_at) as first_message,
            MAX(posted_at) as last_message
        FROM group_messages
        WHERE group_id = :group_id
    """), {"group_id": group.id})
    
    messages_stats = messages_stats_result.fetchone()
    if messages_stats and messages_stats.total_messages > 0:
        print(f"   - Всего сообщений: {messages_stats.total_messages}")
        print(f"   - За последние 24 часа: {messages_stats.messages_last_24h}")
        print(f"   - За последние 7 дней: {messages_stats.messages_last_7d}")
        print(f"   - Первое сообщение: {format_datetime(messages_stats.first_message)}")
        print(f"   - Последнее сообщение: {format_datetime(messages_stats.last_message)}")
    else:
        print("   ❌ Сообщений в группе не найдено")
    print()
    
    # Уникальные участники
    print("3. Участники группы (отправители сообщений):")
    participants_result = await session.execute(text("""
        SELECT 
            COUNT(DISTINCT sender_tg_id) as unique_participants,
            COUNT(*) as total_messages
        FROM group_messages
        WHERE group_id = :group_id
    """), {"group_id": group.id})
    
    participants_stats = participants_result.fetchone()
    if participants_stats and participants_stats.unique_participants > 0:
        print(f"   - Уникальных участников: {participants_stats.unique_participants}")
        print(f"   - Всего сообщений: {participants_stats.total_messages}")
        
        # Детали участников
        top_participants_result = await session.execute(text("""
            SELECT 
                sender_tg_id,
                sender_username,
                COUNT(*) as message_count
            FROM group_messages
            WHERE group_id = :group_id
            GROUP BY sender_tg_id, sender_username
            ORDER BY message_count DESC
            LIMIT 10
        """), {"group_id": group.id})
        
        top_participants = top_participants_result.fetchall()
        if top_participants:
            print("   Топ участников:")
            for part in top_participants:
                print(f"      - @{part.sender_username or 'N/A'} ({part.sender_tg_id}): {part.message_count} сообщений")
    else:
        print("   ❌ Участников не найдено")
    print()
    
    # Окна обсуждения
    print("4. Окна обсуждения (conversation windows):")
    windows_result = await session.execute(text("""
        SELECT 
            gcw.id,
            gcw.window_start,
            gcw.window_end,
            gcw.message_count,
            gcw.participant_count,
            gcw.status,
            gcw.generated_at
        FROM group_conversation_windows gcw
        WHERE gcw.group_id = :group_id
        ORDER BY gcw.window_end DESC
        LIMIT 10
    """), {"group_id": group.id})
    
    windows = windows_result.fetchall()
    if windows:
        print(f"   Найдено окон: {len(windows)}")
        for win in windows:
            print(f"   - Окно: {win.id}")
            print(f"     Период: {format_datetime(win.window_start)} - {format_datetime(win.window_end)}")
            print(f"     Сообщений: {win.message_count}, Участников: {win.participant_count}")
            print(f"     Статус: {win.status}")
            if win.generated_at:
                print(f"     Сгенерировано: {format_datetime(win.generated_at)}")
            print()
    else:
        print("   ❌ Окон обсуждения не найдено")
    print()
    
    # Дайджесты
    print("5. Дайджесты группы:")
    digests_result = await session.execute(text("""
        SELECT 
            gd.id,
            gd.window_id,
            gd.title,
            gd.generated_at,
            gd.delivered_at,
            gd.delivery_status,
            gcw.window_start,
            gcw.window_end,
            gcw.message_count,
            gcw.participant_count
        FROM group_digests gd
        JOIN group_conversation_windows gcw ON gd.window_id = gcw.id
        WHERE gcw.group_id = :group_id
        ORDER BY gd.generated_at DESC
        LIMIT 10
    """), {"group_id": group.id})
    
    digests = digests_result.fetchall()
    if digests:
        print(f"   Найдено дайджестов: {len(digests)}")
        for dig in digests:
            print(f"   - Дайджест: {dig.id}")
            print(f"     Окно: {format_datetime(dig.window_start)} - {format_datetime(dig.window_end)}")
            print(f"     Сообщений в окне: {dig.message_count}, Участников: {dig.participant_count}")
            print(f"     Заголовок: {dig.title or 'N/A'}")
            print(f"     Статус доставки: {dig.delivery_status or 'N/A'}")
            if dig.generated_at:
                print(f"     Сгенерирован: {format_datetime(dig.generated_at)}")
            print()
    else:
        print("   ❌ Дайджестов не найдено")
    print()


async def check_user_groups(telegram_id: int, db_url_async: str):
    """Проверка всех групп пользователя."""
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "check_group_digest"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
        print("=" * 80)
        print(f"ПРОВЕРКА ГРУПП ПОЛЬЗОВАТЕЛЯ: {telegram_id}")
        print("=" * 80)
        print()
        
        # Находим пользователя
        user_result = await session.execute(text("""
            SELECT 
                u.id,
                u.telegram_id,
                u.username,
                u.tenant_id
            FROM users u
            WHERE u.telegram_id = :telegram_id
        """), {"telegram_id": telegram_id})
        
        user = user_result.fetchone()
        if not user:
            print(f"❌ Пользователь с Telegram ID {telegram_id} не найден")
            return
        
        print(f"✅ Пользователь: @{user.username or 'N/A'} (UUID: {user.id})")
        print()
        
        # Получаем подписки на группы
        groups_result = await session.execute(text("""
            SELECT 
                g.id,
                g.tg_chat_id,
                g.username,
                g.title,
                g.is_active,
                ug.subscribed_at,
                ug.is_active as subscription_active,
                ug.monitor_mentions,
                (SELECT COUNT(*) FROM group_messages gm WHERE gm.group_id = g.id) as total_messages,
                (SELECT COUNT(DISTINCT sender_tg_id) FROM group_messages gm WHERE gm.group_id = g.id) as unique_participants
            FROM user_group ug
            JOIN groups g ON ug.group_id = g.id
            WHERE ug.user_id = :user_id
            ORDER BY ug.subscribed_at DESC
        """), {"user_id": user.id})
        
        groups = groups_result.fetchall()
        
        if groups:
            print(f"Найдено подписок на группы: {len(groups)}")
            print()
            
            for gr in groups:
                status = "✅" if gr.subscription_active else "❌"
                group_status = "✅" if gr.is_active else "❌"
                print(f"{status} {gr.title}")
                print(f"   - UUID: {gr.id}")
                print(f"   - Telegram Chat ID: {gr.tg_chat_id}")
                print(f"   - Username: @{gr.username or 'N/A'}")
                print(f"   - Статус группы: {group_status}")
                print(f"   - Подписка: {'активна' if gr.subscription_active else 'неактивна'}")
                print(f"   - Мониторинг упоминаний: {'да' if gr.monitor_mentions else 'нет'}")
                print(f"   - Всего сообщений: {gr.total_messages}")
                print(f"   - Участников: {gr.unique_participants}")
                print()
        else:
            print("❌ Подписок на группы не найдено")
            print()


async def main():
    parser = argparse.ArgumentParser(description="Проверка группы и её дайджестов")
    parser.add_argument("--group-title", help="Название группы для поиска")
    parser.add_argument("--history-id", help="History ID дайджеста")
    parser.add_argument("--group-id", help="UUID группы")
    parser.add_argument("--user-telegram-id", type=int, help="Telegram ID пользователя для проверки его групп")
    
    args = parser.parse_args()
    
    # Получаем URL БД
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    
    # Преобразуем в async URL
    parsed = urlparse(db_url)
    if parsed.scheme == "postgresql":
        new_scheme = "postgresql+asyncpg"
    else:
        new_scheme = parsed.scheme
    
    db_url_async = urlunparse((new_scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    
    if args.user_telegram_id:
        await check_user_groups(args.user_telegram_id, db_url_async)
    elif args.history_id:
        await check_group_by_history_id(args.history_id, db_url_async)
    elif args.group_id:
        await check_group_by_id(args.group_id, db_url_async)
    elif args.group_title:
        await check_group_by_title(args.group_title, db_url_async)
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())

