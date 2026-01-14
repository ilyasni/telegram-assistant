"""
Theme Sync Service для синхронизации подписок пользователей при изменении подборок.

Context7: Идемпотентный сервис, работающий по принципу "desired state → reconcile".
Синхронизирует подписки пользователей при изменении состава подборки администратором.
"""

from typing import Dict, Any, List
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import text
import structlog

logger = structlog.get_logger()


class ThemeSyncService:
    """
    Сервис для синхронизации подписок пользователей при изменении подборок.
    
    Context7: Идемпотентный - повторный прогон не меняет результат.
    Работает по принципу "desired state → reconcile".
    """
    
    def __init__(self):
        """Инициализация ThemeSyncService."""
        logger.info("ThemeSyncService initialized")
    
    def sync_theme_subscriptions(
        self,
        theme_id: UUID,
        db: Session,
        batch_size: int = 100
    ) -> Dict[str, Any]:
        """
        Синхронизирует подписки пользователей при изменении подборки.
        
        Context7: Идемпотентный: повторный прогон не меняет результат.
        Работает по принципу "desired state → reconcile".
        
        Args:
            theme_id: ID подборки для синхронизации
            db: SQLAlchemy сессия
            batch_size: Размер батча для обработки пользователей
        
        Returns:
            Статистика синхронизации
        """
        logger.info(
            "Starting theme subscriptions sync",
            theme_id=str(theme_id),
            batch_size=batch_size
        )
        
        try:
            # 1. Получить всех пользователей с активной подборкой
            users_result = db.execute(
                text("""
                    SELECT user_id, theme_id
                    FROM user_theme
                    WHERE theme_id = :theme_id AND is_active = true
                """),
                {"theme_id": theme_id}
            )
            users = users_result.fetchall()
            
            if not users:
                logger.info(
                    "No active users for theme",
                    theme_id=str(theme_id)
                )
                return {
                    "theme_id": str(theme_id),
                    "users_processed": 0,
                    "channels_added": 0,
                    "channels_deactivated": 0
                }
            
            # 2. Получить desired state (каналы в подборке)
            # Получаем channel_username из theme_channels и находим соответствующие channels
            desired_channels_result = db.execute(
                text("""
                    SELECT DISTINCT c.id as channel_id
                    FROM theme_channels tc
                    JOIN channels c ON LTRIM(c.username, '@') = tc.channel_username
                    WHERE tc.theme_id = :theme_id
                """),
                {"theme_id": theme_id}
            )
            desired_channels = {row.channel_id for row in desired_channels_result.fetchall()}
            
            logger.info(
                "Desired channels for theme",
                theme_id=str(theme_id),
                channels_count=len(desired_channels)
            )
            
            # 3. Batch-обработка пользователей
            total_channels_added = 0
            total_channels_deactivated = 0
            
            for i in range(0, len(users), batch_size):
                user_batch = users[i:i + batch_size]
                
                for user_row in user_batch:
                    user_id = user_row.user_id
                    
                    # 4. Получить current state (текущие подписки через эту подборку)
                    current_theme_subscriptions_result = db.execute(
                        text("""
                            SELECT channel_id
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
                    )
                    current_theme_subscriptions = {
                        row.channel_id for row in current_theme_subscriptions_result.fetchall()
                    }
                    
                    # 5. Вычислить diff
                    to_add = desired_channels - current_theme_subscriptions
                    to_disable = current_theme_subscriptions - desired_channels
                    
                    # 6. Bulk UPSERT для добавления (идемпотентно)
                    if to_add:
                        # Для каждого канала проверяем и создаем/обновляем подписку
                        for channel_id in to_add:
                            # Проверяем существующую подписку
                            existing_result = db.execute(
                                text("""
                                    SELECT user_id, channel_id, is_active
                                    FROM user_channel
                                    WHERE user_id = :user_id 
                                      AND channel_id = :channel_id 
                                      AND source = 'theme'
                                      AND theme_id = :theme_id
                                """),
                                {
                                    "user_id": user_id,
                                    "channel_id": channel_id,
                                    "theme_id": theme_id
                                }
                            )
                            existing_row = existing_result.fetchone()
                            
                            if existing_row:
                                # Обновляем существующую подписку
                                db.execute(
                                    text("""
                                        UPDATE user_channel
                                        SET is_active = true, updated_at = NOW()
                                        WHERE user_id = :user_id 
                                          AND channel_id = :channel_id 
                                          AND source = 'theme'
                                          AND theme_id = :theme_id
                                    """),
                                    {
                                        "user_id": user_id,
                                        "channel_id": channel_id,
                                        "theme_id": theme_id
                                    }
                                )
                            else:
                                # Создаем новую подписку
                                try:
                                    db.execute(
                                        text("""
                                            INSERT INTO user_channel (user_id, channel_id, source, theme_id, is_active, subscribed_at, updated_at)
                                            VALUES (:user_id, :channel_id, 'theme', :theme_id, true, NOW(), NOW())
                                        """),
                                        {
                                            "user_id": user_id,
                                            "channel_id": channel_id,
                                            "theme_id": theme_id
                                        }
                                    )
                                except Exception:
                                    # Игнорируем конфликты (race condition)
                                    pass
                        
                        total_channels_added += len(to_add)
                    
                    # 7. Bulk UPDATE для отключения (только source='theme' и theme_id=...)
                    if to_disable:
                        channel_ids_list = list(to_disable)
                        db.execute(
                            text("""
                                UPDATE user_channel
                                SET is_active = false, updated_at = NOW()
                                WHERE user_id = :user_id
                                  AND channel_id = ANY(CAST(:channel_ids AS uuid[]))
                                  AND source = 'theme'
                                  AND theme_id = :theme_id
                            """),
                            {
                                "user_id": user_id,
                                "channel_ids": channel_ids_list,
                                "theme_id": theme_id
                            }
                        )
                        total_channels_deactivated += len(to_disable)
                
                # Batch commit
                db.commit()
                
                logger.debug(
                    "Processed user batch",
                    theme_id=str(theme_id),
                    batch_start=i,
                    batch_end=min(i + batch_size, len(users))
                )
            
            logger.info(
                "Theme subscriptions sync completed",
                theme_id=str(theme_id),
                users_processed=len(users),
                channels_added=total_channels_added,
                channels_deactivated=total_channels_deactivated
            )
            
            return {
                "theme_id": str(theme_id),
                "users_processed": len(users),
                "channels_added": total_channels_added,
                "channels_deactivated": total_channels_deactivated
            }
            
        except Exception as e:
            logger.error(
                "Error syncing theme subscriptions",
                theme_id=str(theme_id),
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            db.rollback()
            raise


# Singleton instance
_theme_sync_service = None


def get_theme_sync_service() -> ThemeSyncService:
    """Получить экземпляр ThemeSyncService (singleton)."""
    global _theme_sync_service
    if _theme_sync_service is None:
        _theme_sync_service = ThemeSyncService()
    return _theme_sync_service
