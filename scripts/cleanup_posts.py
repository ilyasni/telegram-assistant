#!/usr/bin/env python3
"""
Скрипт для полной очистки всех данных постов и связанных таблиц
"""

import asyncio
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
import structlog

# Добавляем путь к проекту
sys.path.append('/opt/telegram-assistant')

logger = structlog.get_logger()

async def cleanup_posts_data():
    """Очистка всех данных постов и связанных таблиц."""
    
    # Получаем URL базы данных
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
    
    print("🧹 Начинаем очистку данных постов...")
    print(f"Database URL: {db_url}")
    
    # Создаем асинхронное соединение
    engine = create_async_engine(db_url)
    
    try:
        async with AsyncSession(engine) as session:
            # Список таблиц для очистки в правильном порядке (с учетом foreign keys)
            tables_to_clean = [
                "post_reactions",      # Ссылается на posts
                "post_forwards",       # Ссылается на posts  
                "post_replies",        # Ссылается на posts
                "post_media",          # Ссылается на posts
                "post_enrichment",     # Ссылается на posts
                "indexing_status",     # Ссылается на posts
                "posts"                # Основная таблица постов
            ]
            
            for table in tables_to_clean:
                try:
                    print(f"🗑️  Очищаем таблицу {table}...")
                    
                    # Получаем количество записей перед удалением
                    count_result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = count_result.scalar()
                    
                    if count > 0:
                        # Удаляем все записи
                        await session.execute(text(f"DELETE FROM {table}"))
                        await session.commit()
                        print(f"✅ Удалено {count} записей из таблицы {table}")
                    else:
                        print(f"ℹ️  Таблица {table} уже пуста")
                        
                except Exception as e:
                    print(f"❌ Ошибка при очистке таблицы {table}: {e}")
                    await session.rollback()
                    raise
            
            # Проверяем результат очистки
            print("\n📊 Проверяем результат очистки...")
            for table in tables_to_clean:
                count_result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = count_result.scalar()
                print(f"  {table}: {count} записей")
            
            print("\n✅ Очистка данных постов завершена успешно!")
            
    except Exception as e:
        print(f"❌ Ошибка при очистке: {e}")
        raise
    finally:
        await engine.dispose()

async def main():
    """Основная функция."""
    try:
        await cleanup_posts_data()
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
