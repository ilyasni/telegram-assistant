#!/usr/bin/env python3
"""
Скрипт для проверки доступа пользователя к дайджесту канала.
Context7: Диагностика проблемы доступа к дайджесту.
"""

import sys
import os
from uuid import UUID

# Добавляем путь к api для импорта моделей
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from sqlalchemy import text
from models.database import SessionLocal

def check_channel_digest_access(user_id: str, channel_id: str):
    """Проверка доступа пользователя к дайджесту канала."""
    db = SessionLocal()
    try:
        # Проверка пользователя
        user_result = db.execute(
            text("SELECT id, telegram_id, username, tenant_id FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        )
        user_row = user_result.fetchone()
        if not user_row:
            print(f"❌ Пользователь {user_id} не найден")
            return
        
        user_dict = dict(user_row._mapping) if hasattr(user_row, '_mapping') else dict(user_row)
        print(f"✅ Пользователь найден:")
        print(f"   - ID: {user_dict.get('id')}")
        print(f"   - Telegram ID: {user_dict.get('telegram_id')}")
        print(f"   - Username: {user_dict.get('username')}")
        print(f"   - Tenant ID: {user_dict.get('tenant_id')}")
        print()
        
        # Проверка канала
        channel_result = db.execute(
            text("SELECT id, title, username, tg_channel_id, is_active FROM channels WHERE id = :channel_id"),
            {"channel_id": channel_id}
        )
        channel_row = channel_result.fetchone()
        if not channel_row:
            print(f"❌ Канал {channel_id} не найден")
            return
        
        channel_dict = dict(channel_row._mapping) if hasattr(channel_row, '_mapping') else dict(channel_row)
        print(f"✅ Канал найден:")
        print(f"   - ID: {channel_dict.get('id')}")
        print(f"   - Title: {channel_dict.get('title')}")
        print(f"   - Username: {channel_dict.get('username')}")
        print(f"   - Telegram Channel ID: {channel_dict.get('tg_channel_id')}")
        print(f"   - Is Active: {channel_dict.get('is_active')}")
        print()
        
        # Проверка подписки
        subscription_result = db.execute(
            text("""
                SELECT uc.user_id, uc.channel_id, uc.is_active, uc.subscribed_at
                FROM user_channel uc
                WHERE uc.user_id = :user_id AND uc.channel_id = :channel_id
            """),
            {"user_id": user_id, "channel_id": channel_id}
        )
        subscription_row = subscription_result.fetchone()
        
        if not subscription_row:
            print(f"❌ Подписка не найдена:")
            print(f"   - Пользователь {user_id} не подписан на канал {channel_id}")
            print()
            
            # Проверяем все подписки пользователя
            all_subs_result = db.execute(
                text("""
                    SELECT uc.channel_id, c.title, uc.is_active, uc.subscribed_at
                    FROM user_channel uc
                    JOIN channels c ON c.id = uc.channel_id
                    WHERE uc.user_id = :user_id
                    ORDER BY uc.subscribed_at DESC
                    LIMIT 10
                """),
                {"user_id": user_id}
            )
            all_subs = all_subs_result.fetchall()
            if all_subs:
                print(f"📋 Подписки пользователя (первые 10):")
                for sub in all_subs:
                    sub_dict = dict(sub._mapping) if hasattr(sub, '_mapping') else dict(sub)
                    print(f"   - {sub_dict.get('title')} ({sub_dict.get('channel_id')}) - Active: {sub_dict.get('is_active')}")
            else:
                print(f"   - У пользователя нет подписок")
            return
        
        subscription_dict = dict(subscription_row._mapping) if hasattr(subscription_row, '_mapping') else dict(subscription_row)
        print(f"✅ Подписка найдена:")
        print(f"   - User ID: {subscription_dict.get('user_id')}")
        print(f"   - Channel ID: {subscription_dict.get('channel_id')}")
        print(f"   - Is Active: {subscription_dict.get('is_active')}")
        print(f"   - Subscribed At: {subscription_dict.get('subscribed_at')}")
        print()
        
        # Проверка доступа (как в endpoint)
        access_result = db.execute(
            text("""
                SELECT 
                    uc.channel_id,
                    uc.user_id,
                    uc.is_active as user_channel_is_active,
                    c.id as channel_exists,
                    c.is_active as channel_is_active,
                    u.tenant_id
                FROM user_channel uc
                JOIN channels c ON c.id = uc.channel_id
                JOIN users u ON u.id = uc.user_id
                WHERE uc.user_id = :user_id 
                    AND uc.channel_id = :channel_id 
                    AND uc.is_active = true
                LIMIT 1
            """),
            {"user_id": user_id, "channel_id": channel_id}
        )
        access_row = access_result.fetchone()
        
        if access_row:
            access_dict = dict(access_row._mapping) if hasattr(access_row, '_mapping') else dict(access_row)
            print(f"✅ Доступ разрешен:")
            print(f"   - User Channel Is Active: {access_dict.get('user_channel_is_active')}")
            print(f"   - Channel Exists: {access_dict.get('channel_exists') is not None}")
            print(f"   - Channel Is Active: {access_dict.get('channel_is_active')}")
            print(f"   - Tenant ID: {access_dict.get('tenant_id')}")
        else:
            print(f"❌ Доступ запрещен:")
            print(f"   - Подписка существует, но неактивна или канал неактивен")
            print(f"   - User Channel Is Active: {subscription_dict.get('is_active')}")
            print(f"   - Channel Is Active: {channel_dict.get('is_active')}")
            
    finally:
        db.close()

if __name__ == "__main__":
    user_id = "cc1e70c9-9058-4fd0-9b52-94012623f0e0"
    channel_id = "abf7d27b-234b-4ed0-a33d-4a94931d0c2c"
    
    print("=" * 60)
    print("Проверка доступа к дайджесту канала")
    print("=" * 60)
    print(f"User ID: {user_id}")
    print(f"Channel ID: {channel_id}")
    print("=" * 60)
    print()
    
    check_channel_digest_access(user_id, channel_id)
