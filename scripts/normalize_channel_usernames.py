#!/usr/bin/env python3
"""
Скрипт для нормализации username каналов в БД.
Убирает @ из начала всех username для единообразия.

Context7: [C7-ID: username-normalization-migration-001]
"""

import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor

# Получение DATABASE_URL из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")

def normalize_usernames(dry_run: bool = True):
    """Нормализация username каналов."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        try:
            # Проверка текущего состояния
            print("=== ПРОВЕРКА ТЕКУЩЕГО СОСТОЯНИЯ ===")
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_channels,
                    COUNT(*) FILTER (WHERE username LIKE '@%') as channels_with_at,
                    COUNT(*) FILTER (WHERE username IS NOT NULL AND username NOT LIKE '@%' AND username != '') as channels_without_at,
                    COUNT(*) FILTER (WHERE username IS NULL OR username = '') as channels_empty
                FROM channels
                WHERE username IS NOT NULL
            """)
            stats = cursor.fetchone()
            
            print(f"Всего каналов с username: {stats['total_channels']}")
            print(f"  - С @ в начале: {stats['channels_with_at']}")
            print(f"  - Без @: {stats['channels_without_at']}")
            print(f"  - Пустые: {stats['channels_empty']}")
            print()
            
            if stats['channels_with_at'] == 0:
                print("✅ Все username уже нормализованы, изменений не требуется.")
                return
            
            # Показываем примеры каналов с @
            print("=== ПРИМЕРЫ КАНАЛОВ С @ ===")
            cursor.execute("""
                SELECT id, username, title
                FROM channels
                WHERE username LIKE '@%'
                LIMIT 10
            """)
            examples = cursor.fetchall()
            for ex in examples:
                print(f"  - {ex['username']} -> {ex['username'].lstrip('@')} ({ex['title']})")
            print()
            
            if dry_run:
                print("=== РЕЖИМ ПРОВЕРКИ (dry-run) ===")
                print(f"Будет обновлено {stats['channels_with_at']} каналов")
                print("Для выполнения обновления запустите скрипт с --execute")
                return
            
            # Выполнение обновления
            print("=== ВЫПОЛНЕНИЕ ОБНОВЛЕНИЯ ===")
            cursor.execute("""
                UPDATE channels
                SET username = LTRIM(username, '@')
                WHERE username IS NOT NULL 
                  AND username LIKE '@%'
            """)
            
            affected_rows = cursor.rowcount
            conn.commit()
            
            print(f"✅ Обновлено каналов: {affected_rows}")
            
            # Проверка результата
            print("\n=== ПРОВЕРКА РЕЗУЛЬТАТА ===")
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_channels,
                    COUNT(*) FILTER (WHERE username LIKE '@%') as channels_with_at_after,
                    COUNT(*) FILTER (WHERE username IS NOT NULL AND username NOT LIKE '@%' AND username != '') as channels_without_at_after
                FROM channels
                WHERE username IS NOT NULL
            """)
            stats_after = cursor.fetchone()
            
            print(f"Всего каналов с username: {stats_after['total_channels']}")
            print(f"  - С @ в начале: {stats_after['channels_with_at_after']}")
            print(f"  - Без @: {stats_after['channels_without_at_after']}")
            
            if stats_after['channels_with_at_after'] == 0:
                print("\n✅ Все username успешно нормализованы!")
            else:
                print(f"\n⚠️ Осталось {stats_after['channels_with_at_after']} каналов с @")
                
        finally:
            cursor.close()
            conn.close()
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Нормализация username каналов в БД")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Выполнить обновление (по умолчанию только проверка)"
    )
    
    args = parser.parse_args()
    
    normalize_usernames(dry_run=not args.execute)

