"""Подключение к PostgreSQL.
Context7: Connection pooling для производительности и таймауты для предотвращения зависаний.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
import structlog

from config import settings

logger = structlog.get_logger()


class DatabaseConnection:
    """Управление подключением к PostgreSQL.
    
    Context7: Использует connection pooling и таймауты для надёжности и производительности.
    """
    
    def __init__(self, database_url: str = None):
        """Инициализация подключения к БД.
        
        Args:
            database_url: URL подключения к PostgreSQL (Context7: по умолчанию из настроек)
        """
        self.database_url = database_url or settings.database_url
        
        # Context7: Создание engine с connection pooling
        self.engine = create_engine(
            self.database_url,
            poolclass=QueuePool,
            pool_size=5,  # Context7: размер пула соединений
            max_overflow=10,  # Context7: максимальное количество дополнительных соединений
            pool_pre_ping=True,  # Context7: проверка соединений перед использованием
            pool_recycle=3600,  # Context7: переиспользование соединений каждый час
            connect_args={
                "connect_timeout": 10,  # Context7: таймаут подключения
                "options": "-c statement_timeout=30000"  # Context7: таймаут запросов (30 сек)
            }
        )
        
        # Context7: Создание session factory
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )
        
        logger.info(
            "Database connection initialized",
            pool_size=5,
            max_overflow=10
        )
    
    def get_session(self) -> Session:
        """Получение новой сессии БД.
        
        Context7: Каждая сессия должна быть закрыта после использования.
        
        Returns:
            SQLAlchemy Session
        """
        return self.SessionLocal()
    
    def close(self) -> None:
        """Закрытие всех соединений.
        
        Context7: Освобождение ресурсов при завершении работы.
        """
        self.engine.dispose()
        logger.info("Database connections closed")


# Context7: Глобальный экземпляр для использования во всём приложении
db_connection = DatabaseConnection()
