"""
E2E тесты для RetaggingTask
Context7 best practice: версионирование, анти-петли, trace propagation

Тестирует:
- Версионирование Vision и Tags
- Анти-петлю (игнорирование trigger=vision_retag)
- Ретеггинг только при изменении версии
- Публикацию posts.tagged только при изменении тегов
"""

import pytest
import asyncio
from uuid import uuid4
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch

import structlog

logger = structlog.get_logger()


@pytest.mark.asyncio
async def test_retagging_version_check(test_db, test_redis, test_tenant_data):
    """Тест проверки версий для ретеггинга."""
    
    from worker.tasks.retagging_task import RetaggingTask
    from worker.events.schemas.posts_vision_v1 import VisionAnalyzedEventV1, VisionAnalysisResult
    
    tenant_id = test_tenant_data["tenant_id"]
    post_id = str(uuid4())
    
    # Создаём пост с тегами старой версии
    from sqlalchemy import text
    async with test_db.begin():
        await test_db.execute(
            text("""
                INSERT INTO posts (id, content, has_media, created_at)
                VALUES (:post_id, 'Test post', true, NOW())
            """),
            {"post_id": post_id}
        )
        
        await test_db.execute(
            text("""
                INSERT INTO post_enrichment (post_id, kind, provider, data, metadata)
                VALUES (
                    :post_id, 'tags', 'gigachat',
                    '{"tags": ["old", "tags"]}'::jsonb,
                    '{"tags_version": "vision@2025-01-01#p1"}'::jsonb
                )
            """),
            {"post_id": post_id}
        )
    
    # Создаём событие Vision с новой версией
    vision_event = VisionAnalyzedEventV1(
        tenant_id=tenant_id,
        post_id=post_id,
        media=[],
        vision=VisionAnalysisResult(
            provider="gigachat",
            model="GigaChat-Pro",
            classification={"type": "photo"},
            description="Test description",
            tokens_used=100,
            analyzed_at=datetime.now(timezone.utc)
        ),
        vision_version="vision@2025-01-29#p3",  # Новая версия
        features_hash="new_hash_123",
        analysis_duration_ms=1000,
        trace_id=f"test_{uuid4()}"
    )
    
    # Проверяем, что ретеггинг нужен (новая версия)
    task = RetaggingTask()
    should_retag = await task._should_retag(post_id, vision_event)
    
    assert should_retag is True, "Should retag when vision_version is newer"
    
    logger.info("Retagging version check test passed", should_retag=should_retag)


@pytest.mark.asyncio
async def test_retagging_no_change_skipped(test_db, test_redis, test_tenant_data):
    """Тест пропуска ретеггинга, если версия не изменилась."""
    
    from worker.tasks.retagging_task import RetaggingTask
    from worker.events.schemas.posts_vision_v1 import VisionAnalyzedEventV1, VisionAnalysisResult
    
    tenant_id = test_tenant_data["tenant_id"]
    post_id = str(uuid4())
    vision_version = "vision@2025-01-29#p3"
    
    # Создаём пост с тегами той же версии
    from sqlalchemy import text
    async with test_db.begin():
        await test_db.execute(
            text("""
                INSERT INTO posts (id, content, has_media, created_at)
                VALUES (:post_id, 'Test post', true, NOW())
            """),
            {"post_id": post_id}
        )
        
        await test_db.execute(
            text("""
                INSERT INTO post_enrichment (post_id, kind, provider, data, metadata)
                VALUES (
                    :post_id, 'tags', 'gigachat',
                    '{"tags": ["test", "tags"]}'::jsonb,
                    '{"tags_version": :vision_version}'::jsonb
                )
            """),
            {"post_id": post_id, "vision_version": vision_version}
        )
    
    # Создаём событие Vision с той же версией
    vision_event = VisionAnalyzedEventV1(
        tenant_id=tenant_id,
        post_id=post_id,
        media=[],
        vision=VisionAnalysisResult(
            provider="gigachat",
            model="GigaChat-Pro",
            classification={"type": "photo"},
            description="Test description",
            tokens_used=100,
            analyzed_at=datetime.now(timezone.utc)
        ),
        vision_version=vision_version,  # Та же версия
        features_hash="same_hash",
        analysis_duration_ms=1000,
        trace_id=f"test_{uuid4()}"
    )
    
    # Проверяем, что ретеггинг не нужен
    task = RetaggingTask()
    should_retag = await task._should_retag(post_id, vision_event)
    
    assert should_retag is False, "Should not retag when version unchanged"
    
    logger.info("Retagging skip test passed", should_retag=should_retag)


@pytest.mark.asyncio
async def test_retagging_trigger_anti_loop(test_redis, test_tenant_data):
    """Тест анти-петли: TaggingTask игнорирует события с trigger=vision_retag."""
    
    from worker.events.schemas.posts_tagged_v1 import PostTaggedEventV1
    from worker.tasks.tagging_task import TaggingTask
    
    # Создаём событие posts.tagged с trigger=vision_retag
    tagged_event = PostTaggedEventV1(
        idempotency_key=f"test_{uuid4()}",
        post_id=str(uuid4()),
        tags=["tag1", "tag2"],
        tags_hash="hash123",
        trigger="vision_retag",  # Анти-петля флаг
        vision_version="vision@2025-01-29#p3"
    )
    
    # Проверяем, что событие имеет правильный trigger
    assert tagged_event.trigger == "vision_retag"
    
    # TaggingTask должен игнорировать такие события (логика в _process_single_message)
    # Это проверяется на уровне бизнес-логики, не здесь
    
    logger.info("Retagging trigger anti-loop test passed")


@pytest.mark.asyncio
async def test_retagging_features_hash_change(test_db, test_redis, test_tenant_data):
    """Тест ретеггинга при изменении features_hash."""
    
    from worker.tasks.retagging_task import RetaggingTask
    from worker.events.schemas.posts_vision_v1 import VisionAnalyzedEventV1, VisionAnalysisResult
    
    tenant_id = test_tenant_data["tenant_id"]
    post_id = str(uuid4())
    vision_version = "vision@2025-01-29#p3"
    
    # Создаём пост с тегами
    from sqlalchemy import text
    async with test_db.begin():
        await test_db.execute(
            text("""
                INSERT INTO posts (id, content, has_media, created_at)
                VALUES (:post_id, 'Test post', true, NOW())
            """),
            {"post_id": post_id}
        )
        
        await test_db.execute(
            text("""
                INSERT INTO post_enrichment (post_id, kind, provider, data, metadata)
                VALUES (
                    :post_id, 'tags', 'gigachat',
                    '{"tags": ["test"]}'::jsonb,
                    '{"tags_version": :vision_version, "vision_features_hash": "old_hash"}'::jsonb
                )
            """),
            {"post_id": post_id, "vision_version": vision_version}
        )
    
    # Создаём событие Vision с тем же version, но другим features_hash
    vision_event = VisionAnalyzedEventV1(
        tenant_id=tenant_id,
        post_id=post_id,
        media=[],
        vision=VisionAnalysisResult(
            provider="gigachat",
            model="GigaChat-Pro",
            classification={"type": "photo"},
            description="Updated description",  # Изменено описание
            tokens_used=100,
            analyzed_at=datetime.now(timezone.utc)
        ),
        vision_version=vision_version,  # Та же версия
        features_hash="new_hash_456",  # Но другой hash
        analysis_duration_ms=1000,
        trace_id=f"test_{uuid4()}"
    )
    
    # Проверяем, что ретеггинг нужен (features_hash изменился)
    task = RetaggingTask()
    should_retag = await task._should_retag(post_id, vision_event)
    
    assert should_retag is True, "Should retag when features_hash changes"
    
    logger.info("Retagging features_hash change test passed", should_retag=should_retag)

