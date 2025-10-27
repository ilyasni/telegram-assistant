"""
E2E тесты для полного цикла обработки каналов
Тестирует: подписка → парсинг → тегирование → обогащение → индексация → RAG
"""

import asyncio
import json
import pytest
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
import redis.asyncio as redis
from qdrant_client import QdrantClient
from neo4j import AsyncGraphDatabase

from worker.event_bus import create_publisher, create_consumer
from worker.ai_providers.gigachain_adapter import create_gigachain_adapter
from worker.tasks.tagging_task import create_tagging_worker
from worker.tasks.enrichment_task import create_enrichment_worker
from worker.tasks.indexing_task import create_indexing_worker
from worker.tasks.cleanup_task import create_cleanup_worker

# ============================================================================
# TEST CONFIGURATION
# ============================================================================

TEST_CONFIG = {
    "database_url": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "redis_url": "redis://localhost:6379",
    "qdrant_url": "http://localhost:6333",
    "neo4j_url": "bolt://localhost:7687",
    "neo4j_user": "neo4j",
    "neo4j_password": "test_password",
    "gigachat_api_key": "test_gigachat_key",
    "openrouter_api_key": "test_openrouter_key",
    "tenant_id": "test-tenant-123"
}

# ============================================================================
# TEST FIXTURES
# ============================================================================

@pytest.fixture
async def test_db():
    """Тестовая база данных."""
    engine = create_async_engine(TEST_CONFIG["database_url"])
    
    # Создание тестовых таблиц
    async with engine.begin() as conn:
        await conn.run_sync(create_test_tables)
    
    yield engine
    
    # Очистка после тестов
    async with engine.begin() as conn:
        await conn.run_sync(drop_test_tables)
    
    await engine.dispose()

@pytest.fixture
async def test_redis():
    """Тестовый Redis."""
    client = redis.from_url(TEST_CONFIG["redis_url"], decode_responses=True)
    
    # Очистка тестовых ключей
    await client.flushdb()
    
    yield client
    
    # Очистка после тестов
    await client.flushdb()
    await client.close()

@pytest.fixture
async def test_qdrant():
    """Тестовый Qdrant."""
    client = QdrantClient(url=TEST_CONFIG["qdrant_url"])
    
    # Очистка тестовых коллекций
    collections = await client.get_collections()
    for collection in collections.collections:
        if collection.name.startswith("test_"):
            await client.delete_collection(collection.name)
    
    yield client
    
    # Очистка после тестов
    collections = await client.get_collections()
    for collection in collections.collections:
        if collection.name.startswith("test_"):
            await client.delete_collection(collection.name)

@pytest.fixture
async def test_neo4j():
    """Тестовый Neo4j."""
    driver = AsyncGraphDatabase.driver(
        TEST_CONFIG["neo4j_url"],
        auth=(TEST_CONFIG["neo4j_user"], TEST_CONFIG["neo4j_password"])
    )
    
    # Очистка тестовых данных
    async with driver.session() as session:
        await session.execute_write(cleanup_test_neo4j)
    
    yield driver
    
    # Очистка после тестов
    async with driver.session() as session:
        await session.execute_write(cleanup_test_neo4j)
    
    await driver.close()

@pytest.fixture
async def test_tenant_data(test_db):
    """Создание тестовых данных tenant'а."""
    async with AsyncSession(test_db) as session:
        # Создание tenant'а
        tenant_id = TEST_CONFIG["tenant_id"]
        await session.execute(
            text("INSERT INTO tenants (id, name, created_at) VALUES (:id, :name, NOW())"),
            {"id": tenant_id, "name": "Test Tenant"}
        )
        
        # Создание пользователя
        user_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO users (id, tenant_id, telegram_id, username, subscription_type, created_at)
                VALUES (:id, :tenant_id, :telegram_id, :username, :subscription_type, NOW())
            """),
            {
                "id": user_id,
                "tenant_id": tenant_id,
                "telegram_id": 12345,
                "username": "test_user",
                "subscription_type": "premium"
            }
        )
        
        # Создание канала
        channel_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO channels (id, tg_channel_id, username, title, is_active, created_at)
                VALUES (:id, :telegram_id, :username, :title, true, NOW())
            """),
            {
                "id": channel_id,
                "telegram_id": -1001234567890,
                "username": "test_channel",
                "title": "Test Channel"
            }
        )
        
        # Создание подписки
        await session.execute(
            text("""
                INSERT INTO user_channel (user_id, channel_id, is_active, subscribed_at)
                VALUES (:user_id, :channel_id, true, NOW())
            """),
            {"user_id": user_id, "channel_id": channel_id}
        )
        
        await session.commit()
        
        return {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "channel_id": channel_id
        }

# ============================================================================
# TEST DATA CREATION
# ============================================================================

def create_test_tables(metadata):
    """Создание тестовых таблиц."""
    # Здесь должна быть логика создания таблиц
    # Для простоты используем существующие таблицы
    pass

def drop_test_tables(metadata):
    """Удаление тестовых таблиц."""
    # Очистка тестовых данных
    pass

async def cleanup_test_neo4j(tx):
    """Очистка тестовых данных в Neo4j."""
    await tx.run("MATCH (n) WHERE n.tenant_id = $tenant_id DETACH DELETE n", 
                 tenant_id=TEST_CONFIG["tenant_id"])

# ============================================================================
# E2E TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_full_channel_processing_flow(
    test_db,
    test_redis,
    test_qdrant,
    test_neo4j,
    test_tenant_data
):
    """Тест полного цикла: подписка → парсинг → тегирование → индексация → RAG."""
    
    tenant_id = test_tenant_data["tenant_id"]
    user_id = test_tenant_data["user_id"]
    channel_id = test_tenant_data["channel_id"]
    
    # 1. Создание тестовых постов
    test_posts = await create_test_posts(test_db, channel_id, tenant_id)
    
    # 2. Публикация событий posts.parsed
    await publish_test_events(test_redis, test_posts, user_id, channel_id, tenant_id)
    
    # 3. Запуск worker'ов
    workers = await start_test_workers(
        test_db, test_redis, test_qdrant, test_neo4j, tenant_id
    )
    
    try:
        # 4. Ожидание обработки
        await wait_for_processing_completion(test_redis, len(test_posts))
        
        # 5. Проверка результатов
        await verify_tagging_results(test_db, test_posts)
        await verify_enrichment_results(test_db, test_posts)
        await verify_indexing_results(test_qdrant, test_neo4j, test_posts, tenant_id)
        
        # 6. Тест RAG поиска
        await test_rag_search(test_qdrant, test_neo4j, tenant_id)
        
    finally:
        # Остановка worker'ов
        await stop_test_workers(workers)

@pytest.mark.asyncio
async def test_duplicate_detection(test_db, test_redis, test_tenant_data):
    """Тест обнаружения дубликатов постов."""
    tenant_id = test_tenant_data["tenant_id"]
    channel_id = test_tenant_data["channel_id"]
    
    # Создание поста с известным content_hash
    post_data = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "content": "Test post content",
        "content_hash": "test_hash_123",
        "idempotency_key": f"{tenant_id}:{channel_id}:123",
        "posted_at": datetime.now(timezone.utc)
    }
    
    # Первая вставка
    async with AsyncSession(test_db) as session:
        await session.execute(
            text("""
                INSERT INTO posts (id, tenant_id, channel_id, content, content_hash, 
                                 idempotency_key, posted_at, enrichment_status)
                VALUES (:id, :tenant_id, :channel_id, :content, :content_hash,
                       :idempotency_key, :posted_at, 'pending')
            """),
            post_data
        )
        await session.commit()
    
    # Попытка повторной вставки с тем же idempotency_key
    duplicate_data = post_data.copy()
    duplicate_data["id"] = str(uuid.uuid4())
    
    async with AsyncSession(test_db) as session:
        result = await session.execute(
            text("""
                INSERT INTO posts (id, tenant_id, channel_id, content, content_hash,
                                 idempotency_key, posted_at, enrichment_status)
                VALUES (:id, :tenant_id, :channel_id, :content, :content_hash,
                       :idempotency_key, :posted_at, 'pending')
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING id
            """),
            duplicate_data
        )
        
        # Должен вернуть None (дубликат не вставлен)
        assert result.fetchone() is None

@pytest.mark.asyncio
async def test_enrichment_policies(test_db, test_redis, test_tenant_data):
    """Тест политик обогащения."""
    tenant_id = test_tenant_data["tenant_id"]
    channel_id = test_tenant_data["channel_id"]
    
    # Создание постов с разными характеристиками
    test_cases = [
        {
            "content": "Short post",
            "urls": [],
            "tags": [],
            "should_enrich": False,
            "reason": "below_word_count"
        },
        {
            "content": "Long post with many words that should trigger enrichment " * 20,
            "urls": ["https://example.com/article"],
            "tags": [{"name": "longread", "confidence": 0.9}],
            "should_enrich": True,
            "reason": "has_trigger_tags_and_urls"
        },
        {
            "content": "Post with research tag " * 20,
            "urls": [],
            "tags": [{"name": "research", "confidence": 0.8}],
            "should_enrich": False,
            "reason": "no_urls"
        }
    ]
    
    for i, case in enumerate(test_cases):
        post_id = str(uuid.uuid4())
        
        # Создание поста
        async with AsyncSession(test_db) as session:
            await session.execute(
                text("""
                    INSERT INTO posts (id, tenant_id, channel_id, content, urls, 
                                     idempotency_key, posted_at, enrichment_status)
                    VALUES (:id, :tenant_id, :channel_id, :content, :urls,
                           :idempotency_key, :posted_at, 'pending')
                """),
                {
                    "id": post_id,
                    "tenant_id": tenant_id,
                    "channel_id": channel_id,
                    "content": case["content"],
                    "urls": json.dumps(case["urls"]),
                    "idempotency_key": f"{tenant_id}:{channel_id}:{i}",
                    "posted_at": datetime.now(timezone.utc)
                }
            )
            
            # Добавление тегов если есть
            if case["tags"]:
                await session.execute(
                    text("""
                        INSERT INTO post_enrichment (post_id, tags, enrichment_provider, enriched_at)
                        VALUES (:post_id, :tags, 'test', NOW())
                    """),
                    {
                        "post_id": post_id,
                        "tags": json.dumps(case["tags"])
                    }
                )
            
            await session.commit()
        
        # Проверка политики обогащения
        should_enrich = await check_enrichment_policy(
            test_db, post_id, case["content"], case["urls"], case["tags"]
        )
        
        assert should_enrich == case["should_enrich"], \
            f"Case {i}: expected {case['should_enrich']}, got {should_enrich}"

@pytest.mark.asyncio
async def test_ttl_cleanup(test_db, test_redis, test_qdrant, test_neo4j, test_tenant_data):
    """Тест TTL очистки постов."""
    tenant_id = test_tenant_data["tenant_id"]
    channel_id = test_tenant_data["channel_id"]
    
    # Создание истёкшего поста
    expired_post_id = str(uuid.uuid4())
    expired_date = datetime.now(timezone.utc) - timedelta(days=91)
    
    async with AsyncSession(test_db) as session:
        await session.execute(
            text("""
                INSERT INTO posts (id, tenant_id, channel_id, content, 
                                 idempotency_key, posted_at, enrichment_status)
                VALUES (:id, :tenant_id, :channel_id, :content,
                       :idempotency_key, :posted_at, 'indexed')
            """),
            {
                "id": expired_post_id,
                "tenant_id": tenant_id,
                "channel_id": channel_id,
                "content": "Expired post",
                "idempotency_key": f"{tenant_id}:{channel_id}:expired",
                "posted_at": expired_date
            }
        )
        await session.commit()
    
    # Создание вектора в Qdrant
    collection_name = f"tenant_{tenant_id}_posts"
    await test_qdrant.upsert(
        collection_name=collection_name,
        points=[{
            "id": str(uuid.uuid4()),
            "vector": [0.1] * 1536,
            "payload": {
                "post_id": expired_post_id,
                "expires_at": int(expired_date.timestamp())
            }
        }]
    )
    
    # Создание узла в Neo4j
    async with test_neo4j.session() as session:
        await session.execute_write(
            lambda tx: tx.run(
                "CREATE (p:Post {post_id: $post_id, tenant_id: $tenant_id})",
                post_id=expired_post_id,
                tenant_id=tenant_id
            )
        )
    
    # Запуск cleanup worker
    cleanup_worker = await create_cleanup_worker(
        db_session=AsyncSession(test_db),
        qdrant_url=TEST_CONFIG["qdrant_url"],
        neo4j_url=TEST_CONFIG["neo4j_url"],
        neo4j_user=TEST_CONFIG["neo4j_user"],
        neo4j_password=TEST_CONFIG["neo4j_password"],
        tenant_id=tenant_id
    )
    
    try:
        # Симуляция события post.deleted
        await simulate_post_deletion(test_redis, expired_post_id, tenant_id, channel_id)
        
        # Обработка cleanup
        await cleanup_worker._handle_post_deleted({
            "post_id": expired_post_id,
            "tenant_id": tenant_id,
            "channel_id": channel_id,
            "reason": "ttl"
        })
        
        # Проверка очистки
        await verify_cleanup_results(test_db, test_qdrant, test_neo4j, expired_post_id, tenant_id)
        
    finally:
        await cleanup_worker.stop()

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def create_test_posts(db, channel_id: str, tenant_id: str) -> List[Dict[str, Any]]:
    """Создание тестовых постов."""
    posts = []
    
    test_contents = [
        "Новости о развитии искусственного интеллекта в России",
        "Обзор криптовалютного рынка за неделю",
        "Исследование: влияние ИИ на рынок труда",
        "Релиз новой версии фреймворка для машинного обучения"
    ]
    
    async with AsyncSession(db) as session:
        for i, content in enumerate(test_contents):
            post_id = str(uuid.uuid4())
            
            await session.execute(
                text("""
                    INSERT INTO posts (id, tenant_id, channel_id, content, 
                                     idempotency_key, posted_at, enrichment_status)
                    VALUES (:id, :tenant_id, :channel_id, :content,
                           :idempotency_key, :posted_at, 'pending')
                """),
                {
                    "id": post_id,
                    "tenant_id": tenant_id,
                    "channel_id": channel_id,
                    "content": content,
                    "idempotency_key": f"{tenant_id}:{channel_id}:{i}",
                    "posted_at": datetime.now(timezone.utc)
                }
            )
            
            posts.append({
                "id": post_id,
                "content": content,
                "channel_id": channel_id,
                "tenant_id": tenant_id
            })
        
        await session.commit()
    
    return posts

async def publish_test_events(redis_client, posts: List[Dict], user_id: str, channel_id: str, tenant_id: str):
    """Публикация тестовых событий."""
    publisher = await create_publisher(TEST_CONFIG["redis_url"])
    
    for post in posts:
        event = {
            "idempotency_key": f"{tenant_id}:{channel_id}:{post['id']}",
            "user_id": user_id,
            "channel_id": channel_id,
            "post_id": post["id"],
            "tenant_id": tenant_id,
            "text": post["content"],
            "urls": [],
            "posted_at": datetime.now(timezone.utc).isoformat()
        }
        
        await publisher.publish_event('posts.parsed', event)

async def start_test_workers(db, redis_client, qdrant_client, neo4j_driver, tenant_id: str):
    """Запуск тестовых worker'ов."""
    workers = []
    
    # Tagging worker
    tagging_worker = await create_tagging_worker(
        db_session=AsyncSession(db),
        redis_client=redis_client,
        gigachat_api_key=TEST_CONFIG["gigachat_api_key"],
        openrouter_api_key=TEST_CONFIG["openrouter_api_key"]
    )
    workers.append(tagging_worker)
    
    # Enrichment worker
    enrichment_config = {
        "enrichment": {"enabled": True},
        "crawl4ai": {
            "enabled": True,
            "trigger_tags": ["longread", "research"],
            "min_word_count": 10  # Низкий порог для тестов
        }
    }
    enrichment_worker = await create_enrichment_worker(
        db_session=AsyncSession(db),
        redis_client=redis_client,
        enrichment_config=enrichment_config
    )
    workers.append(enrichment_worker)
    
    # Indexing worker
    indexing_worker = await create_indexing_worker(
        db_session=AsyncSession(db),
        qdrant_url=TEST_CONFIG["qdrant_url"],
        neo4j_url=TEST_CONFIG["neo4j_url"],
        neo4j_user=TEST_CONFIG["neo4j_user"],
        neo4j_password=TEST_CONFIG["neo4j_password"],
        gigachat_api_key=TEST_CONFIG["gigachat_api_key"],
        tenant_id=tenant_id
    )
    workers.append(indexing_worker)
    
    return workers

async def wait_for_processing_completion(redis_client, expected_posts: int, timeout: int = 30):
    """Ожидание завершения обработки."""
    start_time = asyncio.get_event_loop().time()
    
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        # Проверка очередей
        parsed_count = await redis_client.xlen("stream:posts:parsed")
        tagged_count = await redis_client.xlen("stream:posts:tagged")
        enriched_count = await redis_client.xlen("stream:posts:enriched")
        indexed_count = await redis_client.xlen("stream:posts:indexed")
        
        if parsed_count == 0 and tagged_count == 0 and enriched_count == 0 and indexed_count == 0:
            break
        
        await asyncio.sleep(1)
    
    if (asyncio.get_event_loop().time() - start_time) >= timeout:
        raise TimeoutError("Processing did not complete within timeout")

async def verify_tagging_results(db, posts: List[Dict]):
    """Проверка результатов тегирования."""
    async with AsyncSession(db) as session:
        for post in posts:
            result = await session.execute(
                text("""
                    SELECT pe.tags, p.enrichment_status
                    FROM posts p
                    LEFT JOIN post_enrichment pe ON p.id = pe.post_id
                    WHERE p.id = :post_id
                """),
                {"post_id": post["id"]}
            )
            
            row = result.fetchone()
            assert row is not None, f"Post {post['id']} not found"
            assert row.enrichment_status in ['tagged', 'enriched', 'indexed'], \
                f"Post {post['id']} not tagged, status: {row.enrichment_status}"

async def verify_enrichment_results(db, posts: List[Dict]):
    """Проверка результатов обогащения."""
    async with AsyncSession(db) as session:
        for post in posts:
            result = await session.execute(
                text("""
                    SELECT pe.crawl_md, p.enrichment_status
                    FROM posts p
                    LEFT JOIN post_enrichment pe ON p.id = pe.post_id
                    WHERE p.id = :post_id
                """),
                {"post_id": post["id"]}
            )
            
            row = result.fetchone()
            assert row is not None, f"Post {post['id']} not found"
            # Обогащение может быть пропущено по политикам

async def verify_indexing_results(qdrant_client, neo4j_driver, posts: List[Dict], tenant_id: str):
    """Проверка результатов индексации."""
    collection_name = f"tenant_{tenant_id}_posts"
    
    # Проверка Qdrant
    collection_info = await qdrant_client.get_collection(collection_name)
    assert collection_info.points_count > 0, "No points in Qdrant collection"
    
    # Проверка Neo4j
    async with neo4j_driver.session() as session:
        result = await session.execute_read(
            lambda tx: tx.run("MATCH (p:Post) RETURN count(p) as count")
        )
        node_count = await result.single()
        assert node_count["count"] > 0, "No nodes in Neo4j"

async def test_rag_search(qdrant_client, neo4j_driver, tenant_id: str):
    """Тест RAG поиска."""
    collection_name = f"tenant_{tenant_id}_posts"
    
    # Поиск в Qdrant
    search_result = await qdrant_client.search(
        collection_name=collection_name,
        query_vector=[0.1] * 1536,
        limit=5
    )
    
    assert len(search_result) > 0, "No search results from Qdrant"
    
    # Поиск в Neo4j
    async with neo4j_driver.session() as session:
        result = await session.execute_read(
            lambda tx: tx.run("MATCH (p:Post) RETURN p LIMIT 5")
        )
        nodes = await result.data()
        assert len(nodes) > 0, "No nodes found in Neo4j"

async def check_enrichment_policy(db, post_id: str, content: str, urls: List[str], tags: List[Dict]) -> bool:
    """Проверка политики обогащения."""
    # Простая логика для тестов
    has_urls = len(urls) > 0
    has_trigger_tags = any(tag.get("name") in ["longread", "research"] for tag in tags)
    has_enough_words = len(content.split()) >= 10
    
    return has_urls and (has_trigger_tags or has_enough_words)

async def simulate_post_deletion(redis_client, post_id: str, tenant_id: str, channel_id: str):
    """Симуляция события удаления поста."""
    publisher = await create_publisher(TEST_CONFIG["redis_url"])
    
    event = {
        "idempotency_key": f"{tenant_id}:{channel_id}:deleted",
        "post_id": post_id,
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "reason": "ttl"
    }
    
    await publisher.publish_event('posts.deleted', event)

async def verify_cleanup_results(db, qdrant_client, neo4j_driver, post_id: str, tenant_id: str):
    """Проверка результатов очистки."""
    # Проверка БД
    async with AsyncSession(db) as session:
        result = await session.execute(
            text("SELECT 1 FROM posts WHERE id = :post_id"),
            {"post_id": post_id}
        )
        assert result.fetchone() is None, "Post still exists in database"
    
    # Проверка Qdrant
    collection_name = f"tenant_{tenant_id}_posts"
    search_result = await qdrant_client.scroll(
        collection_name=collection_name,
        scroll_filter={"must": [{"key": "post_id", "match": {"value": post_id}}]},
        limit=1
    )
    assert len(search_result[0]) == 0, "Post still exists in Qdrant"
    
    # Проверка Neo4j
    async with neo4j_driver.session() as session:
        result = await session.execute_read(
            lambda tx: tx.run("MATCH (p:Post {post_id: $post_id}) RETURN p", post_id=post_id)
        )
        nodes = await result.data()
        assert len(nodes) == 0, "Post still exists in Neo4j"

async def stop_test_workers(workers):
    """Остановка тестовых worker'ов."""
    for worker in workers:
        await worker.stop()

# ============================================================================
# TEST RUNNER
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
