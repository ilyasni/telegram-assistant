"""
Trend Refinement Task для периодического улучшения качества кластеров.

Context7: Фоновая задача, запускаемая периодически для рефайнмента кластеров.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import structlog

# Импорт с учетом структуры worker контейнера
import sys
from pathlib import Path

# Добавляем пути для импорта (аналогично другим задачам)
_current_file = Path(__file__).resolve()
_path_candidates = [
    _current_file.parents[1],  # /app
    _current_file.parents[1] / "worker",
    _current_file.parents[1] / "api",
    Path("/opt/telegram-assistant"),
    Path("/opt/telegram-assistant/worker"),
    Path("/opt/telegram-assistant/api"),
]

for path_obj in _path_candidates:
    try:
        if path_obj and path_obj.exists():
            path_str = str(path_obj)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
    except Exception:
        continue

# Импорт trends_refinement_service с fallback через несколько путей
import importlib.util

def _load_refinement_service():
    """Загрузка trends_refinement_service через несколько путей."""
    candidates = [
        Path("/opt/telegram-assistant/api/worker/trends_refinement_service.py"),
        Path("/app/api/worker/trends_refinement_service.py"),
        _current_file.parent.parent / "trends_refinement_service.py",
    ]
    
    for candidate in candidates:
        if candidate.exists():
            try:
                spec = importlib.util.spec_from_file_location("trends_refinement_service", candidate)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module.create_refinement_service, module.TrendRefinementService
            except Exception:
                continue
    
    # Fallback: пробуем обычный импорт
    try:
        if "/opt/telegram-assistant" not in sys.path:
            sys.path.insert(0, "/opt/telegram-assistant")
        from api.worker.trends_refinement_service import create_refinement_service, TrendRefinementService
        return create_refinement_service, TrendRefinementService
    except ImportError as e:
        import structlog
        logger = structlog.get_logger()
        logger.error("Failed to import trends_refinement_service", error=str(e), candidates=[str(c) for c in candidates])
        raise

create_refinement_service, TrendRefinementService = _load_refinement_service()

from config import settings

logger = structlog.get_logger()


# ============================================================================
# TREND REFINEMENT TASK
# ============================================================================


class TrendRefinementTask:
    """
    Задача периодического рефайнмента кластеров трендов.
    
    Запускает TrendRefinementService в периодическом режиме.
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        qdrant_url: Optional[str] = None,
    ):
        """
        Инициализация TrendRefinementTask.
        
        Args:
            database_url: URL подключения к БД (если None - из settings)
            qdrant_url: URL подключения к Qdrant (если None - из settings)
        """
        self.database_url = database_url or getattr(
            settings, "database_url", os.getenv("DATABASE_URL")
        )
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://qdrant:6333")
        
        self.refinement_service: Optional[TrendRefinementService] = None

    async def start(self):
        """Запуск задачи периодического рефайнмента."""
        try:
            logger.info("Starting TrendRefinementTask")
            
            # Создаём и инициализируем сервис
            self.refinement_service = create_refinement_service(
                database_url=self.database_url,
                qdrant_url=self.qdrant_url,
            )
            
            # Запускаем периодический рефайнмент
            await self.refinement_service.start_periodic_refinement()
            
        except Exception as e:
            logger.error("Failed to start TrendRefinementTask", error=str(e))
            raise

    async def stop(self):
        """Остановка задачи."""
        if self.refinement_service:
            await self.refinement_service.close()
        logger.info("TrendRefinementTask stopped")


def create_refinement_task() -> TrendRefinementTask:
    """Создание экземпляра TrendRefinementTask."""
    return TrendRefinementTask()

