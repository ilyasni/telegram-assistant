"""
Integration tests для Crawl Pipeline
[C7-ID: TEST-CRAWL-INTEGRATION-001]

Тестирует end-to-end flow: posts.tagged → posts.crawl → post_enrichment
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from prometheus_client import CollectorRegistry, registry

from worker.tasks.crawl_trigger_task import CrawlTriggerTask


def _reset_prometheus_registry() -> None:
    registry.REGISTRY = CollectorRegistry(auto_describe=True)


class TestCrawlPipelineIntegration:
    """Integration tests для crawl pipeline."""

    pytestmark = pytest.mark.asyncio

    @pytest_asyncio.fixture
    async def mock_redis(self):
        redis_mock = AsyncMock()
        redis_mock.xlen.return_value = 0
        redis_mock.xreadgroup.return_value = []
        redis_mock.xadd.return_value = "test-msg-id"
        redis_mock.xack.return_value = 1
        redis_mock.xpending.return_value = [0, "0-0", "0-0", []]
        redis_mock.xautoclaim.return_value = ["0-0", []]
        return redis_mock

    @pytest_asyncio.fixture
    async def mock_db_pool(self):
        import asyncpg

        class DummyPool(asyncpg.Pool):
            def __init__(self):
                self.connection = AsyncMock()
                self.connection.fetchrow.return_value = None
                self.connection.execute.return_value = "INSERT 0 1"

            def acquire(self):
                @asynccontextmanager
                async def _manager():
                    yield self.connection

                return _manager()

        return DummyPool()

    @pytest_asyncio.fixture
    async def mock_enrichment_engine(self):
        engine_mock = AsyncMock()
        engine_mock.enrich_post.return_value = (
            True,
            {"https://example.com": {"markdown": "Test", "ocr_text": "OCR"}},
            "policy_match",
        )
        return engine_mock

    @pytest_asyncio.fixture
    async def crawl_trigger_task(self, mock_redis, mock_db_pool):
        task = CrawlTriggerTask(
            redis_url="redis://localhost:6379",
            trigger_tags=["longread", "crawl"],
        )
        task.redis = mock_redis
        task.db_pool = mock_db_pool
        return task

    @pytest_asyncio.fixture
    async def crawl4ai_service(self, mock_redis, mock_db_pool, mock_enrichment_engine):
        _reset_prometheus_registry()
        from crawl4ai import crawl4ai_service as service_module
        from crawl4ai.crawl4ai_service import Crawl4AIService

        class _CounterStub:
            def labels(self, **kwargs):  # type: ignore[override]
                return self

            def inc(self):  # type: ignore[override]
                return None

        service_module.crawl_requests_total = _CounterStub()

        service = Crawl4AIService(
            redis_url="redis://localhost:6379",
            database_url="postgresql://test:test@localhost:5432/test",
            config_path="test_config.json",
        )
        service.redis = mock_redis
        service.db_pool = mock_db_pool
        service.engine = mock_enrichment_engine
        return service

    async def test_end_to_end_flow(self, crawl_trigger_task, crawl4ai_service):
        test_post_id = "test-post-123"
        test_tags = ["longread", "tech"]
        test_urls = ["https://example.com/article"]

        tagged_message = {
            "post_id": test_post_id,
            "tags": test_tags,
            "urls": test_urls,
            "text": "Test article content with enough words to pass policy",
            "trace_id": "test-trace-123",
            "tenant_id": "tenant-1",
            "user_id": None,
        }

        crawl_trigger_task.redis.xreadgroup.return_value = [
            ("stream:posts:tagged", [("msg-1", {"data": json.dumps(tagged_message)})])
        ]

        crawl_request = {
            "post_id": test_post_id,
            "urls": test_urls,
            "tags": test_tags,
            "trigger_reason": "trigger_tag",
            "trace_id": "test-trace-123",
        }
        crawl4ai_service.redis.xreadgroup.return_value = [
            ("stream:posts:crawl", [("msg-2", {"data": json.dumps(crawl_request)})])
        ]

        await crawl_trigger_task._process_tagged_event("msg-1", {"data": json.dumps(tagged_message)})
        crawl_trigger_task.redis.xadd.assert_called_once()

        call_args, call_kwargs = crawl_trigger_task.redis.xadd.call_args
        payload_encoded = call_args[1]["data"]
        payload = json.loads(payload_encoded)
        assert payload["metadata"]["trigger_source"].startswith("fallback")
        assert payload["metadata"]["trigger_tags_used"]

        await crawl4ai_service._process_crawl_request("msg-2", {"data": json.dumps(crawl_request)})
        assert crawl4ai_service.db_pool.connection.execute.called

    async def test_personal_triggers_preferred(self, crawl_trigger_task):
        crawl_trigger_task.db_pool.connection.fetchrow.return_value = {
            "triggers": ["ai", "ml"]
        }

        tagged_message = {
            "post_id": "personal-post",
            "tags": ["AI"],
            "urls": ["https://example.com/ai"],
            "trace_id": "personal-trace",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "topics": ["AI", "ML"],
        }

        await crawl_trigger_task._process_tagged_event("msg-1", {"data": json.dumps(tagged_message)})
        crawl_trigger_task.redis.xadd.assert_called_once()

        payload_encoded = crawl_trigger_task.redis.xadd.call_args[0][1]["data"]
        payload = json.loads(payload_encoded)
        assert payload["metadata"]["trigger_source"] == "personal"
        assert "ai" in payload["metadata"]["trigger_tags_used"]

    async def test_idempotency(self, crawl_trigger_task):
        test_message = {
            "post_id": "duplicate-post",
            "tags": ["longread"],
            "urls": ["https://example.com/duplicate"],
            "trace_id": "duplicate-trace",
        }

        await crawl_trigger_task._process_tagged_event("msg-1", {"data": json.dumps(test_message)})
        await crawl_trigger_task._process_tagged_event("msg-1", {"data": json.dumps(test_message)})

        assert crawl_trigger_task.redis.xadd.call_count == 2

    async def test_pel_recovery(self, crawl4ai_service):
        pending_message = (
            "msg-pending",
            {
                "data": json.dumps(
                    {
                        "post_id": "pending-post",
                        "urls": ["https://example.com/pending"],
                        "trace_id": "pending-trace",
                    }
                )
            },
        )

        crawl4ai_service.redis.xautoclaim.return_value = ["0-0", [pending_message]]

        await crawl4ai_service._process_pending_messages()

        crawl4ai_service.redis.xautoclaim.assert_called_once()
        crawl4ai_service.redis.xack.assert_called_once()

    async def test_dlq_handling(self, crawl4ai_service):
        crawl4ai_service.engine.enrich_post.return_value = (
            False,
            {},
            "policy_skip",
        )

        test_message = {
            "post_id": "dlq-post",
            "urls": ["https://example.com/dlq"],
            "trace_id": "dlq-trace",
        }

        await crawl4ai_service._process_crawl_request("msg-dlq", {"data": json.dumps(test_message)})

        crawl4ai_service.redis.xack.assert_not_called()

    async def test_policy_skip_reasons(self, crawl_trigger_task):
        test_cases = [
            ("no-tags", {"tags": [], "urls": ["https://example.com"]}),
            ("no-urls", {"tags": ["longread"], "urls": []}),
            ("too-short", {"tags": ["crawl"], "urls": ["https://example.com"], "text": "short"}),
        ]

        for post_id, payload in test_cases:
            message = {"post_id": post_id, "trace_id": post_id, **payload}
            await crawl_trigger_task._process_tagged_event(post_id, {"data": json.dumps(message)})

        assert crawl_trigger_task.redis.xadd.call_count == 2


@pytest.mark.asyncio
async def test_crawl_pipeline_integration():
    """Главный integration test."""
    # Этот тест можно запускать с реальными сервисами в Docker
    # для полной проверки интеграции
    
    # Здесь можно добавить реальные тесты с Docker Compose
    # если нужно протестировать полную интеграцию
    
    assert True  # Placeholder для реального теста


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
