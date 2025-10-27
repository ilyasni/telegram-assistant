#!/usr/bin/env python3
"""
Скрипт для добавления колонки tg_channel_id в таблицу channels.
"""
import psycopg2
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

def add_tg_channel_id_column():
    """Добавляет колонку tg_channel_id в таблицу channels."""
    db_conn = psycopg2.connect(settings.database_url)
    cursor = db_conn.cursor()
    
    try:
        # Проверяем, существует ли колонка
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'channels' 
            AND column_name = 'tg_channel_id'
        """)
        
        if cursor.fetchone():
            print("Колонка tg_channel_id уже существует")
            return
        
        # Добавляем колонку
        cursor.execute("""
            ALTER TABLE public.channels 
            ADD COLUMN tg_channel_id BIGINT
        """)
        
        # Добавляем индекс для производительности
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_channels_tg_channel_id 
            ON public.channels(tg_channel_id)
        """)
        
        # Добавляем уникальный индекс для tg_channel_id (если нужно)
        try:
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_tg_channel_id_unique 
                ON public.channels(tg_channel_id) 
                WHERE tg_channel_id IS NOT NULL
            """)
        except psycopg2.errors.DuplicateTable:
            print("Уникальный индекс уже существует")
        
        db_conn.commit()
        print("Колонка tg_channel_id успешно добавлена")
        
    except Exception as e:
        print(f"Ошибка: {e}")
        db_conn.rollback()
        raise
    finally:
        cursor.close()
        db_conn.close()

if __name__ == "__main__":
    add_tg_channel_id_column()
