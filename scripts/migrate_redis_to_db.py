#!/usr/bin/env python3
"""Скрипт для переноса данных регистрации из Redis в БД."""

import os
import sys
import uuid
from datetime import datetime

# Добавляем пути для импорта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'telethon-ingest'))

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from telethon.sessions import StringSession

# Импорт утилит
from utils.identity_membership import upsert_identity_sync, upsert_membership_sync
from crypto_utils import encrypt_session
from config import settings


def get_redis_data(redis_url: str, tenant_id: str) -> dict:
    """Получить данные сессии из Redis."""
    redis_client = redis.from_url(redis_url, decode_responses=True)
    key = f"t:{tenant_id}:qr:session"
    
    data = redis_client.hgetall(key)
    if not data:
        raise ValueError(f"No data found in Redis for key: {key}")
    
    return data


def get_dc_id_from_session(session_string: str) -> int:
    """Извлечь dc_id из session_string."""
    try:
        session = StringSession(session_string)
        dc_id = getattr(session, 'dc_id', None) or 2
        return dc_id
    except Exception as e:
        print(f"Warning: Failed to extract dc_id from session, using default: {e}")
        return 2


def get_active_encryption_key(db_session) -> str:
    """Получить активный ключ шифрования."""
    result = db_session.execute(
        text("""
            SELECT key_id FROM encryption_keys 
            WHERE retired_at IS NULL 
            ORDER BY created_at DESC 
            LIMIT 1
        """)
    )
    row = result.fetchone()
    if not row:
        raise ValueError("No active encryption key found")
    return row[0]


def migrate_session_to_db(
    redis_url: str,
    database_url: str,
    tenant_id: str,
    telegram_id: int
):
    """Перенести сессию из Redis в БД."""
    
    print(f"🔍 Получение данных из Redis для tenant_id={tenant_id}...")
    redis_data = get_redis_data(redis_url, tenant_id)
    
    session_string = redis_data.get('session_string')
    if not session_string:
        raise ValueError("No session_string found in Redis data")
    
    status = redis_data.get('status', 'pending')
    if status != 'authorized':
        print(f"⚠️  Warning: Session status is '{status}', not 'authorized'")
    
    print(f"✅ Найдена сессия в Redis (status={status})")
    
    # Подключение к БД
    print(f"🔌 Подключение к БД...")
    engine = create_engine(database_url)
    SessionLocal = sessionmaker(bind=engine)
    db_session = SessionLocal()
    
    try:
        # Получаем данные пользователя
        print(f"👤 Получение данных пользователя telegram_id={telegram_id}...")
        user_result = db_session.execute(
            text("""
                SELECT u.id, u.tenant_id, u.username, u.first_name, u.last_name, u.identity_id
                FROM users u
                WHERE u.telegram_id = :telegram_id
                LIMIT 1
            """),
            {"telegram_id": telegram_id}
        )
        user_row = user_result.fetchone()
        
        if not user_row:
            raise ValueError(f"User with telegram_id={telegram_id} not found in database")
        
        user_id, db_tenant_id, username, first_name, last_name, identity_id = user_row
        print(f"✅ Пользователь найден: {username or first_name or telegram_id}")
        
        # Проверяем, есть ли уже сессия
        existing_session = db_session.execute(
            text("""
                SELECT id, is_active FROM telegram_sessions
                WHERE identity_id = :identity_id AND is_active = true
                LIMIT 1
            """),
            {"identity_id": identity_id}
        ).fetchone()
        
        if existing_session:
            print(f"⚠️  Найдена существующая активная сессия: {existing_session[0]}")
            print(f"🔄 Деактивируем старую сессию и создаём новую...")
            
            # Деактивируем старую сессию
            db_session.execute(
                text("""
                    UPDATE telegram_sessions 
                    SET is_active = false, updated_at = NOW()
                    WHERE identity_id = :identity_id AND is_active = true
                """),
                {"identity_id": identity_id}
            )
            print(f"✅ Старая сессия деактивирована")
        
        # Получаем ключ шифрования
        print(f"🔑 Получение ключа шифрования...")
        key_id = get_active_encryption_key(db_session)
        print(f"✅ Ключ шифрования: {key_id}")
        
        # Шифруем сессию
        print(f"🔐 Шифрование сессии...")
        encrypted_session = encrypt_session(session_string)
        print(f"✅ Сессия зашифрована")
        
        # Извлекаем dc_id
        print(f"🌐 Извлечение dc_id из сессии...")
        dc_id = get_dc_id_from_session(session_string)
        print(f"✅ dc_id: {dc_id}")
        
        # Создаём новую сессию
        session_uuid = uuid.uuid4()
        print(f"💾 Сохранение сессии в БД (id={session_uuid})...")
        
        db_session.execute(
            text("""
                INSERT INTO telegram_sessions (
                    id, identity_id, telegram_id, session_string_enc, dc_id, is_active, created_at, updated_at
                ) VALUES (
                    :session_id, :identity_id, :telegram_id, :encrypted_session, :dc_id, true, NOW(), NOW()
                )
                ON CONFLICT (identity_id, dc_id) 
                DO UPDATE SET
                    session_string_enc = EXCLUDED.session_string_enc,
                    is_active = true,
                    updated_at = NOW()
            """),
            {
                "session_id": session_uuid,
                "identity_id": identity_id,
                "telegram_id": telegram_id,
                "encrypted_session": encrypted_session,
                "dc_id": dc_id
            }
        )
        
        # Коммитим транзакцию
        db_session.commit()
        print(f"✅ Сессия успешно сохранена в БД!")
        print(f"   Session ID: {session_uuid}")
        print(f"   Identity ID: {identity_id}")
        print(f"   Telegram ID: {telegram_id}")
        print(f"   DC ID: {dc_id}")
        
    except Exception as e:
        db_session.rollback()
        print(f"❌ Ошибка при переносе данных: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        db_session.close()


if __name__ == "__main__":
    # Параметры
    telegram_id = 389326685
    tenant_id = "0905ea18-a243-42d1-9852-2a9377e9c9af"
    
    # Получаем настройки из окружения
    redis_url = os.getenv("REDIS_URL", settings.redis_url if hasattr(settings, 'redis_url') else "redis://redis:6379")
    database_url = os.getenv("DATABASE_URL", settings.database_url if hasattr(settings, 'database_url') else "")
    
    if not database_url:
        print("❌ DATABASE_URL не установлен")
        sys.exit(1)
    
    print("=" * 60)
    print("Перенос данных регистрации из Redis в БД")
    print("=" * 60)
    print(f"Telegram ID: {telegram_id}")
    print(f"Tenant ID: {tenant_id}")
    print(f"Redis URL: {redis_url}")
    print(f"Database URL: {database_url[:50]}...")
    print("=" * 60)
    print()
    
    try:
        migrate_session_to_db(redis_url, database_url, tenant_id, telegram_id)
        print()
        print("=" * 60)
        print("✅ Перенос завершён успешно!")
        print("=" * 60)
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ Ошибка: {e}")
        print("=" * 60)
        sys.exit(1)

