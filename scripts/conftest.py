"""
Context7: Общие pytest фикстуры для тестирования пайплайна.

Best practices:
- asyncpg connection pool с lifecycle callbacks
- Переиспользование подключений между тестами
- Идемпотентные проверки с trace_id
- Параметризация для разных режимов и окружений
"""
import asyncio
import os
import pytest
import pytest_asyncio
import asyncpg
import redis.asyncio as redis
from typing import AsyncGenerator, Optional
from datetime import datetime, timezone
from qdrant_client import QdrantClient
from neo4j import AsyncGraphDatabase, AsyncDriver
import structlog

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Конфигурация из ENV
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")


@pytest_asyncio.fixture(scope="session")
async def event_loop():
    """Session-scoped event loop для всех async тестов."""
    loop = asyncio.get_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    """
    Context7: asyncpg connection pool с lifecycle callbacks.
    
    Scope: session - переиспользуется между всеми тестами для производительности.
    """
    async def init_connection(conn):
        """Lifecycle callback: настройка соединения при создании."""
        await conn.execute("SET timezone TO 'UTC'")
        await conn.execute("SET application_name TO 'pytest_pipeline_tests'")
    
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
        max_inactive_connection_lifetime=300.0,
        init=init_connection
    )
    
    logger.info("Database pool created for pytest", size=10)
    
    yield pool
    
    await pool.close()
    logger.info("Database pool closed")


@pytest_asyncio.fixture(scope="session")
async def redis_client() -> AsyncGenerator[redis.Redis, None]:
    """Session-scoped Redis client."""
    client = redis.from_url(
        REDIS_URL,
        socket_connect_timeout=5,
        socket_timeout=10,
        retry_on_timeout=True
    )
    
    # Проверка подключения
    await asyncio.wait_for(client.ping(), timeout=2)
    logger.info("Redis connected for pytest")
    
    yield client
    
    await client.aclose()
    logger.info("Redis connection closed")


@pytest_asyncio.fixture(scope="session")
async def qdrant_client() -> AsyncGenerator[QdrantClient, None]:
    """Session-scoped Qdrant client."""
    client = QdrantClient(url=QDRANT_URL)
    
    # Проверка подключения
    collections = client.get_collections()
    logger.info("Qdrant connected for pytest", collections=[c.name for c in collections.collections])
    
    yield client


@pytest_asyncio.fixture(scope="session")
async def neo4j_driver() -> AsyncGenerator[AsyncDriver, None]:
    """Session-scoped Neo4j driver."""
    driver = AsyncGraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        max_connection_lifetime=300
    )
    
    # Проверка подключения
    await asyncio.wait_for(driver.verify_connectivity(), timeout=5)
    logger.info("Neo4j connected for pytest")
    
    yield driver
    
    await driver.close()
    logger.info("Neo4j connection closed")


@pytest.fixture
def trace_id() -> str:
    """Уникальный trace_id для идемпотентных проверок."""
    import uuid
    return str(uuid.uuid4())


@pytest.fixture
def test_mode(pytestconfig) -> str:
    """Режим тестирования (smoke/e2e/deep) из pytest markers или CLI."""
    marker = pytestconfig.getoption("--test-mode", default="e2e")
    return marker


def pytest_addoption(parser):
    """Добавление кастомных опций pytest."""
    parser.addoption(
        "--test-mode",
        action="store",
        default="e2e",
        choices=["smoke", "e2e", "deep"],
        help="Режим тестирования: smoke (быстро), e2e (полный), deep (детальный)"
    )
    parser.addoption(
        "--thresholds-file",
        action="store",
        default=None,
        help="Путь к JSON файлу с порогами SLO"
    )
    parser.addoption(
        "--output-dir",
        action="store",
        default="test_results",
        help="Директория для сохранения результатов тестов"
    )


def pytest_configure(config):
    """Конфигурация pytest markers."""
    config.addinivalue_line(
        "markers", "smoke: быстрая проверка базовой функциональности"
    )
    config.addinivalue_line(
        "markers", "e2e: полная проверка пайплайна"
    )
    config.addinivalue_line(
        "markers", "deep: детальная диагностика с gap analysis"
    )
    config.addinivalue_line(
        "markers", "multitenant: проверка multi-tenant изоляции"
    )
    config.addinivalue_line(
        "markers", "security: проверка безопасности"
    )


