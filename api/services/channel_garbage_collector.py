"""
Channel Garbage Collector для очистки неиспользуемых каналов.

Context7: Находит каналы без активных подписок и помечает их как неактивные.
Используется для обслуживания БД и предотвращения накопления неиспользуемых данных.
"""

from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
import structlog

logger = structlog.get_logger()


class ChannelGarbageCollector:
    """
    Сервис для очистки неиспользуемых каналов.
    
    Context7: Находит каналы, где нет ни одной активной user_channel,
    и помечает их как is_active=false.
    """
    
    def __init__(self):
        """Инициализация ChannelGarbageCollector."""
        logger.info("ChannelGarbageCollector initialized")
    
    def collect_unused_channels(
        self,
        db: Session,
        batch_size: int = 1000,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Находит и деактивирует неиспользуемые каналы.
        
        Context7: Канал считается неиспользуемым, если нет ни одной активной user_channel.
        
        Args:
            db: SQLAlchemy сессия
            batch_size: Размер батча для обработки (для больших объемов)
            dry_run: Если True, только подсчитывает каналы без изменений
        
        Returns:
            Статистика очистки
        """
        logger.info(
            "Starting channel garbage collection",
            batch_size=batch_size,
            dry_run=dry_run
        )
        
        try:
            # 1. Найти каналы без активных подписок
            unused_channels_result = db.execute(
                text("""
                    SELECT c.id, c.username, c.title
                    FROM channels c
                    WHERE c.is_active = true
                      AND NOT EXISTS (
                          SELECT 1 FROM user_channel uc
                          WHERE uc.channel_id = c.id AND uc.is_active = true
                      )
                """)
            )
            unused_channels = unused_channels_result.fetchall()
            
            total_unused = len(unused_channels)
            
            if total_unused == 0:
                logger.info("No unused channels found")
                return {
                    "status": "completed",
                    "unused_channels_found": 0,
                    "channels_deactivated": 0,
                    "dry_run": dry_run
                }
            
            logger.info(
                "Found unused channels",
                count=total_unused
            )
            
            if dry_run:
                # Только логируем, не деактивируем
                logger.info(
                    "Dry run: would deactivate channels",
                    count=total_unused,
                    sample_channels=[
                        {"id": str(ch.id), "username": ch.username, "title": ch.title}
                        for ch in unused_channels[:10]
                    ]
                )
                return {
                    "status": "dry_run",
                    "unused_channels_found": total_unused,
                    "channels_deactivated": 0,
                    "dry_run": True
                }
            
            # 2. Batch-деактивация каналов
            channels_deactivated = 0
            
            for i in range(0, total_unused, batch_size):
                batch = unused_channels[i:i + batch_size]
                channel_ids = [str(ch.id) for ch in batch]
                
                # Деактивируем батч
                update_result = db.execute(
                    text("""
                        UPDATE channels
                        SET is_active = false
                        WHERE id = ANY(CAST(:channel_ids AS uuid[]))
                          AND is_active = true
                    """),
                    {"channel_ids": channel_ids}
                )
                
                batch_deactivated = update_result.rowcount
                channels_deactivated += batch_deactivated
                
                logger.debug(
                    "Deactivated channel batch",
                    batch_start=i,
                    batch_end=min(i + batch_size, total_unused),
                    batch_deactivated=batch_deactivated
                )
            
            # Коммит изменений
            db.commit()
            
            logger.info(
                "Channel garbage collection completed",
                unused_channels_found=total_unused,
                channels_deactivated=channels_deactivated
            )
            
            return {
                "status": "completed",
                "unused_channels_found": total_unused,
                "channels_deactivated": channels_deactivated,
                "dry_run": False
            }
            
        except Exception as e:
            logger.error(
                "Error in channel garbage collection",
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            db.rollback()
            raise


# Singleton instance
_channel_garbage_collector = None


def get_channel_garbage_collector() -> ChannelGarbageCollector:
    """Получить экземпляр ChannelGarbageCollector (singleton)."""
    global _channel_garbage_collector
    if _channel_garbage_collector is None:
        _channel_garbage_collector = ChannelGarbageCollector()
    return _channel_garbage_collector
