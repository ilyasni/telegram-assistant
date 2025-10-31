"""Context7 best practice: Фабрики БД для разделения sync/async подключений.

Цель: Изолировать sync/async код и обеспечить стабильность БД-пайплайна.
Архитектура: telethon-ingest → события → PostPersistenceWorker → БД
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from typing import Generator, Optional
import structlog

logger = structlog.get_logger()

# Context7: Singleton для sync подключений
class SyncDBManager:
    """Менеджер синхронных подключений к БД для telethon-ingest."""
    
    _instance: Optional['SyncDBManager'] = None
    _connection: Optional[psycopg2.extensions.connection] = None
    
    def __new__(cls) -> 'SyncDBManager':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self.database_url = self._get_sync_database_url()
            self._initialized = True
    
    def _get_sync_database_url(self) -> str:
        """Context7: Получение sync DSN из переменных окружения."""
        # Приоритет: DB_URL_SYNC > DATABASE_URL (конвертированный)
        sync_url = os.getenv('DB_URL_SYNC')
        if sync_url:
            return sync_url
        
        # Fallback: конвертируем async DSN в sync
        async_url = os.getenv('DATABASE_URL', '')
        if async_url:
            return async_url.replace('postgresql+asyncpg://', 'postgresql://')
        
        raise ValueError("No database URL configured. Set DB_URL_SYNC or DATABASE_URL")
    
    def get_connection(self) -> psycopg2.extensions.connection:
        """Context7: Получение подключения с автоматическим переподключением."""
        if self._connection is None or self._connection.closed:
            try:
                self._connection = psycopg2.connect(
                    self.database_url,
                    cursor_factory=RealDictCursor,
                    connect_timeout=10
                )
                logger.info("Sync DB connection established")
            except Exception as e:
                logger.error("Failed to connect to sync DB", error=str(e))
                raise
        
        return self._connection
    
    @contextmanager
    def get_cursor(self) -> Generator[psycopg2.extras.RealDictCursor, None, None]:
        """Context7: Context manager для безопасной работы с курсором."""
        conn = self.get_connection()
        cursor = None
        try:
            cursor = conn.cursor()
            yield cursor
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error("Database operation failed", error=str(e))
            raise
        finally:
            if cursor:
                cursor.close()
    
    def close(self):
        """Context7: Закрытие подключения."""
        if self._connection and not self._connection.closed:
            self._connection.close()
            logger.info("Sync DB connection closed")


# Context7: Глобальные фабрики
_sync_db_manager: Optional[SyncDBManager] = None

def get_sync_db_manager() -> SyncDBManager:
    """Context7: Получение singleton sync DB менеджера."""
    global _sync_db_manager
    if _sync_db_manager is None:
        _sync_db_manager = SyncDBManager()
    return _sync_db_manager

def get_sync_connection() -> psycopg2.extensions.connection:
    """Context7: Получение sync подключения."""
    return get_sync_db_manager().get_connection()

@contextmanager
def get_sync_cursor() -> Generator[psycopg2.extras.RealDictCursor, None, None]:
    """Context7: Получение sync курсора через context manager."""
    with get_sync_db_manager().get_cursor() as cursor:
        yield cursor

def close_sync_connections():
    """Context7: Закрытие всех sync подключений."""
    global _sync_db_manager
    if _sync_db_manager:
        _sync_db_manager.close()
        _sync_db_manager = None