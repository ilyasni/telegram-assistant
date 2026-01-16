#!/usr/bin/env python3
"""
Диагностика проблемы с дайджестом группы.
Проверяет связь между пользователем, группой, окном обсуждения и дайджестом.

Использование:
    python scripts/diagnose_group_digest_issue.py --history-id b1a19877-cd9d-4712-85bc-94379c35bcab
    python scripts/diagnose_group_digest_issue.py --user-id 389326685 --group-title "Core Banking design team"
"""

import asyncio
import sys
import os
import argparse
from datetime import datetime
from typing import Optional
from uuid import UUID

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


async def diagnose_by_history_id(history_id: str, db_url_async: str):
    """Диагностика по history_id дайджеста."""
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "diagnose_group_digest"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
        print("=" * 80)
        print(f"ДИАГНОСТИКА ДАЙДЖЕСТА: {history_id}")
        print("=" * 80)
        print()
        
        # 1. Проверяем DigestHistory
        print("1. Проверка DigestHistory:")
        history_result = await session.execute(text("""
            SELECT 
                dh.id,
                dh.user_id,
                dh.tenant_id,
                dh.digest_date,
                dh.content,
                dh.posts_count,
                dh.status,
                dh.created_at,
                u.telegram_id,
                u.username
            FROM digest_history dh
            JOIN users u ON dh.user_id = u.id
            WHERE dh.id = CAST(:history_id AS uuid)
        """), {"history_id": history_id})
        
        history = history_result.fetchone()
        if not history:
            print(f"   ❌ Дайджест с history_id {history_id} не найден")
            return
        
        print(f"   ✅ Найден дайджест:")
        print(f"      - History ID: {history.id}")
        print(f"      - Пользователь: @{history.username or 'N/A'} (Telegram ID: {history.telegram_id})")
        print(f"      - User UUID: {history.user_id}")
        print(f"      - Tenant ID: {history.tenant_id}")
        print(f"      - Дата: {format_datetime(history.digest_date)}")
        print(f"      - Статус: {history.status}")
        print(f"      - Количество постов: {history.posts_count}")
        print(f"      - Создан: {format_datetime(history.created_at)}")
        print()
        
        # 2. Ищем связанное окно обсуждения через события или group_conversation_windows
        print("2. Поиск связанного окна обсуждения:")
        
        # Ищем окна для этого пользователя и tenant
        windows_result = await session.execute(text("""
            SELECT 
                gcw.id,
                gcw.group_id,
                gcw.tenant_id,
                gcw.window_start,
                gcw.window_end,
                gcw.message_count,
                gcw.participant_count,
                gcw.status,
                g.title as group_title,
                g.tg_chat_id
            FROM group_conversation_windows gcw
            JOIN groups g ON gcw.group_id = g.id
            WHERE gcw.tenant_id = :tenant_id
            ORDER BY gcw.window_end DESC
            LIMIT 20
        """), {"tenant_id": history.tenant_id})
        
        windows = windows_result.fetchall()
        print(f"   Найдено окон для tenant: {len(windows)}")
        
        # Ищем окно, которое могло создать этот дайджест (близкое по времени)
        matching_windows = []
        for win in windows:
            if win.group_title == "Core Banking design team":
                matching_windows.append(win)
        
        if matching_windows:
            print(f"   ✅ Найдены окна для группы 'Core Banking design team': {len(matching_windows)}")
            for win in matching_windows:
                print(f"      - Окно: {win.id}")
                print(f"        Период: {format_datetime(win.window_start)} - {format_datetime(win.window_end)}")
                print(f"        Сообщений: {win.message_count}, Участников: {win.participant_count}")
                print(f"        Статус: {win.status}")
                print()
        else:
            print(f"   ⚠️  Окна для группы 'Core Banking design team' не найдены")
            if windows:
                print("   Последние окна для этого tenant:")
                for win in windows[:5]:
                    print(f"      - {win.group_title}: {win.message_count} сообщений, статус {win.status}")
            print()
        
        # 3. Проверяем подписки пользователя на группы
        print("3. Проверка подписок пользователя на группы:")
        subscriptions_result = await session.execute(text("""
            SELECT 
                g.id,
                g.title,
                g.tg_chat_id,
                g.username,
                ug.subscribed_at,
                ug.is_active,
                ug.monitor_mentions
            FROM user_group ug
            JOIN groups g ON ug.group_id = g.id
            WHERE ug.user_id = :user_id
            ORDER BY ug.subscribed_at DESC
        """), {"user_id": history.user_id})
        
        subscriptions = subscriptions_result.fetchall()
        if subscriptions:
            print(f"   ✅ Найдено подписок: {len(subscriptions)}")
            for sub in subscriptions:
                status = "✅" if sub.is_active else "❌"
                print(f"   {status} {sub.title} (@{sub.username or 'N/A'})")
                print(f"      - UUID: {sub.id}")
                print(f"      - Telegram Chat ID: {sub.tg_chat_id}")
                print(f"      - Подписка: {'активна' if sub.is_active else 'неактивна'}")
                print()
        else:
            print(f"   ❌ Подписок на группы не найдено")
            print()
        
        # 4. Ищем группу "Core Banking design team" для этого tenant
        print("4. Поиск группы 'Core Banking design team':")
        group_result = await session.execute(text("""
            SELECT 
                g.id,
                g.title,
                g.tg_chat_id,
                g.username,
                g.tenant_id,
                g.is_active,
                (SELECT COUNT(*) FROM group_messages gm WHERE gm.group_id = g.id) as total_messages,
                (SELECT COUNT(DISTINCT sender_tg_id) FROM group_messages gm WHERE gm.group_id = g.id) as unique_participants
            FROM groups g
            WHERE LOWER(g.title) = LOWER(:group_title)
            AND g.tenant_id = :tenant_id
        """), {
            "group_title": "Core Banking design team",
            "tenant_id": history.tenant_id
        })
        
        group = group_result.fetchone()
        if group:
            print(f"   ✅ Найдена группа:")
            print(f"      - UUID: {group.id}")
            print(f"      - Telegram Chat ID: {group.tg_chat_id}")
            print(f"      - Username: @{group.username or 'N/A'}")
            print(f"      - Статус: {'активна' if group.is_active else 'неактивна'}")
            print(f"      - Всего сообщений: {group.total_messages}")
            print(f"      - Участников: {group.unique_participants}")
            print()
            
            # Проверяем, есть ли подписка пользователя на эту группу
            user_sub_result = await session.execute(text("""
                SELECT 
                    ug.is_active,
                    ug.subscribed_at,
                    ug.monitor_mentions
                FROM user_group ug
                WHERE ug.user_id = :user_id
                AND ug.group_id = :group_id
            """), {
                "user_id": history.user_id,
                "group_id": group.id
            })
            
            user_sub = user_sub_result.fetchone()
            if user_sub:
                print(f"   ✅ Подписка пользователя на эту группу найдена:")
                print(f"      - Статус: {'активна' if user_sub.is_active else 'неактивна'}")
                print(f"      - Подписка создана: {format_datetime(user_sub.subscribed_at)}")
            else:
                print(f"   ❌ ПОДПИСКА ПОЛЬЗОВАТЕЛЯ НА ЭТУ ГРУППУ ОТСУТСТВУЕТ!")
                print(f"      ⚠️  Это объясняет, почему дайджест создан, но нет сообщений")
            print()
            
            # Проверяем последние сообщения в группе
            if group.total_messages > 0:
                messages_result = await session.execute(text("""
                    SELECT 
                        posted_at,
                        sender_tg_id,
                        sender_username,
                        LEFT(content, 100) as content_preview
                    FROM group_messages
                    WHERE group_id = :group_id
                    ORDER BY posted_at DESC
                    LIMIT 10
                """), {"group_id": group.id})
                
                messages = messages_result.fetchall()
                print(f"   Последние сообщения в группе:")
                for msg in messages:
                    print(f"      - {format_datetime(msg.posted_at)}: @{msg.sender_username or 'N/A'}")
                    if msg.content_preview:
                        print(f"        {msg.content_preview[:80]}...")
            else:
                print(f"   ❌ В группе нет сообщений")
                print(f"      ⚠️  Это объясняет, почему в дайджесте 0 сообщений и 0 участников")
            print()
        else:
            print(f"   ❌ Группа 'Core Banking design team' не найдена для tenant {history.tenant_id}")
            print()
        
        # 5. Итоговый диагноз
        print("=" * 80)
        print("ИТОГОВЫЙ ДИАГНОЗ:")
        print("=" * 80)
        
        issues = []
        
        if not subscriptions:
            issues.append("❌ У пользователя нет подписок на группы")
        
        if group and group.total_messages == 0:
            issues.append("❌ В группе 'Core Banking design team' нет сообщений")
        
        if history.posts_count == 0:
            issues.append("❌ В дайджесте 0 сообщений")
        
        if group:
            user_sub_check = await session.execute(text("""
                SELECT COUNT(*) as cnt
                FROM user_group
                WHERE user_id = :user_id AND group_id = :group_id
            """), {
                "user_id": history.user_id,
                "group_id": group.id
            })
            has_sub = user_sub_check.fetchone().cnt > 0
            if not has_sub:
                issues.append(f"❌ У пользователя нет подписки на группу '{group.title}'")
        
        if issues:
            print("Обнаружены проблемы:")
            for issue in issues:
                print(f"   {issue}")
            print()
            print("РЕКОМЕНДАЦИИ:")
            print("   1. Проверить, почему дайджест создан для группы без подписки")
            print("   2. Убедиться, что группа парсится (есть сообщения в group_messages)")
            print("   3. Создать подписку пользователя на группу через API")
            print("   4. Перезапросить дайджест после создания подписки и появления сообщений")
        else:
            print("✅ Все проверки пройдены, проблема не обнаружена")
        
        print()


async def main():
    parser = argparse.ArgumentParser(description="Диагностика проблемы с дайджестом группы")
    parser.add_argument("--history-id", help="History ID дайджеста")
    
    args = parser.parse_args()
    
    if not args.history_id:
        parser.print_help()
        return
    
    # Получаем URL БД
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    
    # Преобразуем в async URL
    parsed = urlparse(db_url)
    if parsed.scheme == "postgresql":
        new_scheme = "postgresql+asyncpg"
    else:
        new_scheme = parsed.scheme
    
    db_url_async = urlunparse((new_scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    
    await diagnose_by_history_id(args.history_id, db_url_async)


if __name__ == "__main__":
    asyncio.run(main())


