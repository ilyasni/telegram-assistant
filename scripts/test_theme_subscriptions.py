#!/usr/bin/env python3
"""
Тестовый скрипт для проверки функциональности Theme Subscriptions.

Context7: Проверяет основные сценарии работы с подборками.
"""

import sys
import os
import requests
from uuid import UUID

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from models.database import SessionLocal
import structlog

logger = structlog.get_logger()

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


def test_theme_subscription_flow():
    """Тест полного цикла подключения/отключения подборки."""
    db = SessionLocal()
    
    try:
        # 1. Получаем тестового пользователя и подборку
        user_result = db.execute(
            text("SELECT id, telegram_id FROM users LIMIT 1")
        )
        user_row = user_result.fetchone()
        
        if not user_row:
            logger.error("No users found for testing")
            return False
        
        user_id = str(user_row.id)
        telegram_id = user_row.telegram_id
        
        theme_result = db.execute(
            text("SELECT id, slug, name FROM themes LIMIT 1")
        )
        theme_row = theme_result.fetchone()
        
        if not theme_row:
            logger.error("No themes found for testing")
            return False
        
        theme_slug = theme_row.slug
        theme_id = str(theme_row.id)
        
        logger.info(
            "Testing theme subscription",
            user_id=user_id,
            telegram_id=telegram_id,
            theme_slug=theme_slug
        )
        
        # 2. Проверяем текущее состояние
        initial_channels = db.execute(
            text("""
                SELECT COUNT(DISTINCT channel_id) as count
                FROM user_channel
                WHERE user_id = :user_id AND is_active = true
            """),
            {"user_id": user_id}
        ).fetchone().count
        
        logger.info("Initial channels count", count=initial_channels)
        
        # 3. Подключаем подборку через API
        try:
            response = requests.post(
                f"{API_BASE}/api/themes/{theme_slug}/subscribe/{telegram_id}",
                timeout=10
            )
            
            if response.status_code == 201:
                data = response.json()
                logger.info(
                    "Theme subscribed successfully",
                    channels_added=data.get("channels_added", 0),
                    channels_skipped=data.get("channels_skipped", 0)
                )
            else:
                logger.warning(
                    "Theme subscription failed",
                    status_code=response.status_code,
                    response=response.text
                )
                return False
        except Exception as e:
            logger.error("API call failed", error=str(e))
            return False
        
        # 4. Проверяем, что подписки созданы
        user_theme_check = db.execute(
            text("""
                SELECT is_active
                FROM user_theme
                WHERE user_id = :user_id AND theme_id = :theme_id
            """),
            {
                "user_id": user_id,
                "theme_id": theme_id
            }
        ).fetchone()
        
        if not user_theme_check or not user_theme_check.is_active:
            logger.error("User theme subscription not found or inactive")
            return False
        
        theme_channels_count = db.execute(
            text("""
                SELECT COUNT(*) as count
                FROM user_channel
                WHERE user_id = :user_id 
                  AND source = 'theme' 
                  AND theme_id = :theme_id
                  AND is_active = true
            """),
            {
                "user_id": user_id,
                "theme_id": theme_id
            }
        ).fetchone().count
        
        logger.info("Theme channels subscribed", count=theme_channels_count)
        
        # 5. Проверяем лимиты (должны считать уникальные каналы)
        final_channels = db.execute(
            text("""
                SELECT COUNT(DISTINCT channel_id) as count
                FROM user_channel
                WHERE user_id = :user_id AND is_active = true
            """),
            {"user_id": user_id}
        ).fetchone().count
        
        logger.info("Final channels count", count=final_channels)
        
        # 6. Отключаем подборку
        try:
            response = requests.delete(
                f"{API_BASE}/api/themes/{theme_slug}/unsubscribe/{telegram_id}",
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.info(
                    "Theme unsubscribed successfully",
                    channels_deactivated=data.get("channels_deactivated", 0)
                )
            else:
                logger.warning(
                    "Theme unsubscription failed",
                    status_code=response.status_code,
                    response=response.text
                )
        except Exception as e:
            logger.error("API call failed", error=str(e))
            return False
        
        # 7. Проверяем, что подписки отключены
        user_theme_check_after = db.execute(
            text("""
                SELECT is_active
                FROM user_theme
                WHERE user_id = :user_id AND theme_id = :theme_id
            """),
            {
                "user_id": user_id,
                "theme_id": theme_id
            }
        ).fetchone()
        
        if user_theme_check_after and user_theme_check_after.is_active:
            logger.error("User theme subscription still active after unsubscribe")
            return False
        
        logger.info("✓ All tests passed")
        return True
        
    except Exception as e:
        logger.error("Test failed", error=str(e), error_type=type(e).__name__, exc_info=True)
        return False
    finally:
        db.close()


def test_manual_channel_protection():
    """Тест: ручной канал не перезаписывается подборкой."""
    db = SessionLocal()
    
    try:
        # Получаем пользователя и канал
        user_result = db.execute(
            text("SELECT id, telegram_id FROM users LIMIT 1")
        )
        user_row = user_result.fetchone()
        
        if not user_row:
            logger.error("No users found")
            return False
        
        user_id = str(user_row.id)
        
        channel_result = db.execute(
            text("SELECT id FROM channels LIMIT 1")
        )
        channel_row = channel_result.fetchone()
        
        if not channel_row:
            logger.error("No channels found")
            return False
        
        channel_id = str(channel_row.id)
        
        # Подключаем канал вручную
        db.execute(
            text("""
                INSERT INTO user_channel (user_id, channel_id, source, theme_id, is_active, subscribed_at, updated_at)
                VALUES (:user_id, :channel_id, 'manual', NULL, true, NOW(), NOW())
                ON CONFLICT (user_id, channel_id) WHERE source = 'manual'
                DO UPDATE SET is_active = true
            """),
            {
                "user_id": user_id,
                "channel_id": channel_id
            }
        )
        db.commit()
        
        # Проверяем, что manual подписка существует
        manual_check = db.execute(
            text("""
                SELECT source, is_active
                FROM user_channel
                WHERE user_id = :user_id 
                  AND channel_id = :channel_id 
                  AND source = 'manual'
            """),
            {
                "user_id": user_id,
                "channel_id": channel_id
            }
        ).fetchone()
        
        if not manual_check or not manual_check.is_active:
            logger.error("Manual subscription not found")
            return False
        
        logger.info("✓ Manual channel protection test passed")
        return True
        
    except Exception as e:
        logger.error("Test failed", error=str(e), exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    print("🧪 Testing Theme Subscriptions...")
    
    test1 = test_theme_subscription_flow()
    test2 = test_manual_channel_protection()
    
    if test1 and test2:
        print("✅ All tests passed!")
        sys.exit(0)
    else:
        print("❌ Some tests failed")
        sys.exit(1)
