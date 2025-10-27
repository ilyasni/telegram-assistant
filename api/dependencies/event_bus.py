"""
Event Bus Dependency для FastAPI.
[C7-ID: API-EVENTS-001]

Предоставляет EventPublisher для публикации событий из API.
"""

from typing import Optional
import structlog
from worker.event_bus import EventPublisher

logger = structlog.get_logger()

# Глобальный publisher
_publisher: Optional[EventPublisher] = None

async def init_event_publisher(redis_url: str):
    """Инициализация event publisher."""
    global _publisher
    
    try:
        _publisher = EventPublisher(redis_url)
        await _publisher.connect()
        
        logger.info("Event publisher initialized", redis_url=redis_url)
        
    except Exception as e:
        logger.error("Failed to initialize event publisher", error=str(e))
        raise

def get_event_publisher() -> EventPublisher:
    """Получение event publisher."""
    if not _publisher:
        raise RuntimeError("Event publisher not initialized")
    return _publisher

async def close_event_publisher():
    """Закрытие event publisher."""
    global _publisher
    
    if _publisher:
        await _publisher.close()
        _publisher = None
        logger.info("Event publisher closed")
