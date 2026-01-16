#!/usr/bin/env python3
"""
Скрипт для диагностики подписки на темы.

Context7: Выполняет 6 SQL-запросов для проверки целостности данных подписки на темы.
Используется для диагностики проблем при добавлении/отключении подборок.

Usage:
    python scripts/diagnose_theme_subscription.py --theme-slug <slug> --user-id <telegram_id>
    python scripts/diagnose_theme_subscription.py --theme-slug <slug> --user-id <uuid>
"""

import argparse
import sys
import os
from uuid import UUID

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import settings
import structlog

logger = structlog.get_logger()


def diagnose_theme_subscription(theme_slug: str, user_id: str, db_session):
    """
    Диагностика подписки на тему.
    
    Args:
        theme_slug: Slug подборки
        user_id: ID пользователя (telegram_id или UUID)
        db_session: SQLAlchemy сессия
    """
    print(f"\n{'='*80}")
    print(f"Диагностика подписки на тему: {theme_slug}")
    print(f"Пользователь: {user_id}")
    print(f"{'='*80}\n")
    
    # 1. Получить theme_id по slug
    theme_result = db_session.execute(
        text("SELECT id, name FROM themes WHERE slug = :slug"),
        {"slug": theme_slug}
    )
    theme_row = theme_result.fetchone()
    
    if not theme_row:
        print(f"❌ Тема '{theme_slug}' не найдена в БД")
        return
    
    theme_id = theme_row.id
    theme_name = theme_row.name
    print(f"✅ Тема найдена: {theme_name} (ID: {theme_id})")
    
    # Определить user_uuid
    try:
        telegram_id = int(user_id)
        user_result = db_session.execute(
            text("SELECT id FROM users WHERE telegram_id = :telegram_id"),
            {"telegram_id": telegram_id}
        )
        user_row = user_result.fetchone()
        if not user_row:
            print(f"❌ Пользователь с telegram_id={telegram_id} не найден")
            return
        user_uuid = user_row.id
    except ValueError:
        user_uuid = UUID(user_id)
        user_result = db_session.execute(
            text("SELECT id FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        )
        if not user_result.fetchone():
            print(f"❌ Пользователь с UUID={user_id} не найден")
            return
    
    print(f"✅ Пользователь найден: {user_uuid}\n")
    
    # Запрос 1: Тема существует и содержит каналы
    print("1️⃣ Проверка: Тема содержит каналы")
    print("-" * 80)
    channels_count_result = db_session.execute(
        text("SELECT COUNT(*) as count FROM theme_channels WHERE theme_id = :theme_id"),
        {"theme_id": theme_id}
    )
    channels_count = channels_count_result.fetchone().count
    print(f"   Каналов в теме: {channels_count}")
    if channels_count == 0:
        print("   ⚠️ Тема не содержит каналов!")
    print()
    
    # Запрос 2: Подписка на тему создана/активна
    print("2️⃣ Проверка: Подписка пользователя на тему")
    print("-" * 80)
    user_theme_result = db_session.execute(
        text("""
            SELECT user_id, theme_id, is_active, subscribed_at
            FROM user_theme
            WHERE user_id = :user_id AND theme_id = :theme_id
        """),
        {
            "user_id": user_uuid,
            "theme_id": theme_id
        }
    )
    user_theme_row = user_theme_result.fetchone()
    if user_theme_row:
        print(f"   ✅ Подписка найдена:")
        print(f"      is_active: {user_theme_row.is_active}")
        print(f"      subscribed_at: {user_theme_row.subscribed_at}")
    else:
        print("   ❌ Подписка на тему не найдена!")
    print()
    
    # Запрос 3: Сколько каналов темы реально активны у пользователя
    print("3️⃣ Проверка: Активные каналы темы у пользователя")
    print("-" * 80)
    active_channels_result = db_session.execute(
        text("""
            SELECT COUNT(*) as count
            FROM user_channel
            WHERE user_id = :user_id 
              AND source = 'theme' 
              AND theme_id = :theme_id 
              AND is_active = true
        """),
        {
            "user_id": user_uuid,
            "theme_id": theme_id
        }
    )
    active_channels_count = active_channels_result.fetchone().count
    print(f"   Активных каналов темы у пользователя: {active_channels_count}")
    if active_channels_count == 0:
        print("   ⚠️ Нет активных каналов темы у пользователя!")
    elif active_channels_count < channels_count:
        print(f"   ⚠️ Активных каналов меньше, чем в теме ({active_channels_count} < {channels_count})")
    print()
    
    # Запрос 4: Какие конкретно каналы не создались
    print("4️⃣ Проверка: Каналы темы, которые не созданы в channels")
    print("-" * 80)
    missing_channels_result = db_session.execute(
        text("""
            SELECT tc.channel_username, tc.title
            FROM theme_channels tc
            LEFT JOIN channels c ON LTRIM(c.username, '@') = tc.channel_username
            WHERE tc.theme_id = :theme_id AND c.id IS NULL
        """),
        {"theme_id": theme_id}
    )
    missing_channels = missing_channels_result.fetchall()
    if missing_channels:
        print(f"   ⚠️ Найдено {len(missing_channels)} каналов, которые не созданы в channels:")
        for ch in missing_channels[:10]:  # Показываем первые 10
            print(f"      - @{ch.channel_username} ({ch.title})")
        if len(missing_channels) > 10:
            print(f"      ... и еще {len(missing_channels) - 10} каналов")
    else:
        print("   ✅ Все каналы темы созданы в channels")
    print()
    
    # Запрос 5: Конфликты уникальности/дубликаты
    print("5️⃣ Проверка: Дубликаты в user_channel")
    print("-" * 80)
    duplicates_result = db_session.execute(
        text("""
            SELECT user_id, channel_id, source, theme_id, COUNT(*) as count
            FROM user_channel
            GROUP BY user_id, channel_id, source, theme_id
            HAVING COUNT(*) > 1
        """)
    )
    duplicates = duplicates_result.fetchall()
    if duplicates:
        print(f"   ⚠️ Найдено {len(duplicates)} дубликатов в user_channel:")
        for dup in duplicates[:10]:  # Показываем первые 10
            print(f"      user_id={dup.user_id}, channel_id={dup.channel_id}, "
                  f"source={dup.source}, theme_id={dup.theme_id}, count={dup.count}")
        if len(duplicates) > 10:
            print(f"      ... и еще {len(duplicates) - 10} дубликатов")
    else:
        print("   ✅ Дубликатов не найдено")
    print()
    
    # Запрос 6: Попадает ли канал в парсинг
    print("6️⃣ Проверка: Попадание каналов темы в парсинг")
    print("-" * 80)
    parsing_channels_result = db_session.execute(
        text("""
            SELECT c.id, c.username, c.title
            FROM channels c
            WHERE EXISTS (
                SELECT 1 FROM user_channel uc 
                WHERE uc.channel_id = c.id AND uc.is_active = true
            )
            AND EXISTS (
                SELECT 1 FROM theme_channels tc
                WHERE tc.theme_id = :theme_id
                  AND LTRIM(c.username, '@') = tc.channel_username
            )
        """),
        {"theme_id": theme_id}
    )
    parsing_channels = parsing_channels_result.fetchall()
    print(f"   Каналов темы, попадающих в парсинг: {len(parsing_channels)}")
    if parsing_channels:
        print("   Примеры каналов:")
        for ch in parsing_channels[:5]:  # Показываем первые 5
            print(f"      - @{ch.username} ({ch.title})")
    print()
    
    print(f"{'='*80}")
    print("Диагностика завершена")
    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(description="Диагностика подписки на тему")
    parser.add_argument("--theme-slug", required=True, help="Slug подборки")
    parser.add_argument("--user-id", required=True, help="ID пользователя (telegram_id или UUID)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    
    args = parser.parse_args()
    
    # Создаем подключение к БД
    engine = create_engine(settings.database_url)
    SessionLocal = sessionmaker(bind=engine)
    db_session = SessionLocal()
    
    try:
        diagnose_theme_subscription(args.theme_slug, args.user_id, db_session)
    except Exception as e:
        logger.error("Error in diagnosis", error=str(e), exc_info=True)
        print(f"\n❌ Ошибка при диагностике: {e}\n")
        sys.exit(1)
    finally:
        db_session.close()


if __name__ == "__main__":
    main()
