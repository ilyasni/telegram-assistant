#!/usr/bin/env python3
"""
Скрипт для восстановления тем дайджеста в БД.

Использование:
    python scripts/restore_digest_topics.py <user_id> [topics...]

Пример:
    python scripts/restore_digest_topics.py cc1e70c9-9058-4fd0-9b52-94012623f0e0 "ai" "дизайн" "искусство"
"""

import sys
import os
from uuid import UUID
from pathlib import Path

# Добавляем корневую директорию проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "api"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import settings
import json

def restore_topics(user_id: str, topics: list[str]):
    """Восстановить темы дайджеста для пользователя."""
    
    # Подключение к БД
    engine = create_engine(settings.database_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        # Проверяем существование пользователя
        user_check = db.execute(
            text("SELECT id FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        ).fetchone()
        
        if not user_check:
            print(f"❌ Пользователь с ID {user_id} не найден")
            return False
        
        # Проверяем текущие настройки
        current_settings = db.execute(
            text("""
                SELECT topics, enabled, updated_at 
                FROM digest_settings 
                WHERE user_id = :user_id
            """),
            {"user_id": user_id}
        ).fetchone()
        
        if current_settings:
            print(f"📋 Текущие темы: {current_settings[0]}")
            print(f"   Включен: {current_settings[1]}")
            print(f"   Обновлено: {current_settings[2]}")
        else:
            print("⚠️  Настройки дайджеста не найдены, будут созданы")
        
        # Обновляем или создаем настройки
        topics_json = json.dumps(topics, ensure_ascii=False)
        
        db.execute(
            text("""
                INSERT INTO digest_settings (user_id, topics, updated_at)
                VALUES (:user_id, :topics::jsonb, NOW())
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    topics = :topics::jsonb,
                    updated_at = NOW()
            """),
            {
                "user_id": user_id,
                "topics": topics_json
            }
        )
        
        db.commit()
        
        # Проверяем результат
        updated = db.execute(
            text("SELECT topics FROM digest_settings WHERE user_id = :user_id"),
            {"user_id": user_id}
        ).fetchone()
        
        if updated:
            print(f"✅ Темы успешно восстановлены!")
            print(f"   Восстановлено тем: {len(topics)}")
            print(f"   Темы: {', '.join(topics)}")
            return True
        else:
            print("❌ Ошибка: настройки не обновлены")
            return False
            
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка при восстановлении тем: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


def main():
    if len(sys.argv) < 2:
        print("Использование: python restore_digest_topics.py <user_id> [topic1] [topic2] ...")
        print("\nПример:")
        print('  python restore_digest_topics.py cc1e70c9-9058-4fd0-9b52-94012623f0e0 "ai" "дизайн" "искусство"')
        sys.exit(1)
    
    user_id = sys.argv[1]
    
    # Валидация UUID
    try:
        UUID(user_id)
    except ValueError:
        print(f"❌ Неверный формат UUID: {user_id}")
        sys.exit(1)
    
    # Получаем темы из аргументов или используем дефолтные
    if len(sys.argv) > 2:
        topics = sys.argv[2:]
    else:
        # Дефолтные темы для этого пользователя
        topics = [
            "ai",
            "дизайн",
            "искусство",
            "ai сервисы и стартапы",
            "нейросети"
        ]
        print(f"📝 Используются дефолтные темы для восстановления")
    
    print(f"🔄 Восстановление тем для пользователя: {user_id}")
    print(f"   Темы: {', '.join(topics)}")
    print()
    
    success = restore_topics(user_id, topics)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

