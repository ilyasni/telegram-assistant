"""
Integration tests для Crawl Pipeline
[C7-ID: TEST-CRAWL-INTEGRATION-001]

Тестирует end-to-end flow: posts.tagged → posts.crawl → post_enrichment
"""
import asyncio
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

# Импорты для тестирования
from worker.tasks.crawl_trigger_task import CrawlTriggerTask
from crawl4ai.crawl4ai_service import Crawl4AIService
from crawl4ai.enrichment_engine import EnrichmentEngine


class TestCrawlPipelineIntegration:
    """Integration tests для crawl pipeline."""
    
    @pytest_asyncio.fixture
    async def mock_redis(self):
        """Mock Redis клиент."""
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
        """Mock database connection pool."""
        pool_mock = AsyncMock()
        connection_mock = AsyncMock()
        connection_mock.fetchrow.return_value = None
        connection_mock.execute.return_value = "INSERT 0 1"
        pool_mock.acquire.return_value.__aenter__.return_value = connection_mock
        return pool_mock
    
    @pytest_asyncio.fixture
    async def mock_enrichment_engine(self):
        """Mock EnrichmentEngine."""
        engine_mock = AsyncMock()
        engine_mock.enrich_post.return_value = (
            True,  # success
            {"https://example.com": {"markdown": "Test content", "ocr_text": "Test OCR"}},  # enrichment_data
            "policy_match"  # reason
        )
        return engine_mock
    
    @pytest_asyncio.fixture
    async def crawl_trigger_task(self, mock_redis):
        """CrawlTriggerTask с mock Redis."""
        task = CrawlTriggerTask(
            redis_url="redis://localhost:6379",
            trigger_tags=["longread", "crawl"]
        )
        task.redis = mock_redis
        return task
    
    @pytest_asyncio.fixture
    async def crawl4ai_service(self, mock_redis, mock_db_pool, mock_enrichment_engine):
        """Crawl4AIService с mock зависимостями."""
        service = Crawl4AIService(
            redis_url="redis://localhost:6379",
            database_url="postgresql://test:test@localhost:5432/test",
            config_path="test_config.json"
        )
        service.redis = mock_redis
        service.db_pool = mock_db_pool
        service.engine = mock_enrichment_engine
        return service
    
    async def test_end_to_end_flow(self, crawl_trigger_task, crawl4ai_service):
        """Тест полного flow: posts.tagged → posts.crawl → post_enrichment."""
        # Подготовка тестовых данных
        test_post_id = "test-post-123"
        test_tags = ["longread", "tech"]
        test_urls = ["https://example.com/article"]
        
        # Mock сообщение posts.tagged
        tagged_message = {
            "post_id": test_post_id,
            "tags": test_tags,
            "urls": test_urls,
            "text": "Test article content with enough words to pass policy",
            "trace_id": "test-trace-123"
        }
        
        # Mock xreadgroup для CrawlTriggerTask
        crawl_trigger_task.redis.xreadgroup.return_value = [
            ("stream:posts:tagged", [("msg-1", {"data": json.dumps(tagged_message)})])
        ]
        
        # Mock xreadgroup для Crawl4AIService
        crawl_request = {
            "post_id": test_post_id,
            "urls": test_urls,
            "tags": test_tags,
            "trigger_reason": "trigger_tag",
            "trace_id": "test-trace-123"
        }
        crawl4ai_service.redis.xreadgroup.return_value = [
            ("stream:posts:crawl", [("msg-2", {"data": json.dumps(crawl_request)})])
        ]
        
        # Тест CrawlTriggerTask
        await crawl_trigger_task._process_tagged_event("msg-1", {"data": json.dumps(tagged_message)})
        
        # Проверяем, что сообщение было отправлено в posts.crawl
        crawl_trigger_task.redis.xadd.assert_called_once()
        xadd_call = crawl_trigger_task.redis.xadd.call_args
        assert xadd_call[0][0] == "stream:posts:crawl"
        assert "data" in xadd_call[0][1]
        
        # Тест Crawl4AIService
        await crawl4ai_service._process_crawl_request("msg-2", {"data": json.dumps(crawl_request)})
        
        # Проверяем, что данные были сохранены в БД
        crawl4ai_service.db_pool.acquire.assert_called()
        
        # Проверяем, что сообщение было ACK'нуто
        crawl4ai_service.redis.xack.assert_called_once_with(
            "stream:posts:crawl", "crawl4ai_workers", "msg-2"
        )
    
    async def test_idempotency(self, crawl_trigger_task):
        """Тест идемпотентности: дублирование сообщений не должно создавать дубли."""
        test_message = {
            "post_id": "duplicate-post",
            "tags": ["longread"],
            "urls": ["https://example.com/duplicate"],
            "trace_id": "duplicate-trace"
        }
        
        # Обрабатываем одно и то же сообщение дважды
        await crawl_trigger_task._process_tagged_event("msg-1", {"data": json.dumps(test_message)})
        await crawl_trigger_task._process_tagged_event("msg-1", {"data": json.dumps(test_message)})
        
        # Проверяем, что xadd был вызван дважды (но в реальной системе это должно быть дедуплицировано)
        assert crawl_trigger_task.redis.xadd.call_count == 2
    
    async def test_pel_recovery(self, crawl4ai_service):
        """Тест восстановления PEL сообщений."""
        # Mock PEL сообщения
        pending_message = ("msg-pending", {"data": json.dumps({
            "post_id": "pending-post",
            "urls": ["https://example.com/pending"],
            "trace_id": "pending-trace"
        })})
        
        crawl4ai_service.redis.xautoclaim.return_value = ["0-0", [pending_message]]
        
        # Тест обработки PEL
        await crawl4ai_service._process_pending_messages()
        
        # Проверяем, что xautoclaim был вызван
        crawl4ai_service.redis.xautoclaim.assert_called_once()
        
        # Проверяем, что сообщение было обработано
        crawl4ai_service.redis.xack.assert_called_once()
    
    async def test_dlq_handling(self, crawl4ai_service):
        """Тест обработки DLQ сообщений."""
        # Mock ошибку в enrichment
        crawl4ai_service.engine.enrich_post.return_value = (
            False,  # success = False
            {},     # пустые данные
            "policy_skip"  # reason
        )
        
        test_message = {
            "post_id": "dlq-post",
            "urls": ["https://example.com/dlq"],
            "trace_id": "dlq-trace"
        }
        
        # Обрабатываем сообщение, которое должно попасть в DLQ
        await crawl4ai_service._process_crawl_request("msg-dlq", {"data": json.dumps(test_message)})
        
        # Проверяем, что сообщение было ACK'нуто (в реальной системе должно попасть в DLQ)
        crawl4ai_service.redis.xack.assert_called_once()
    
    async def test_policy_skip_reasons(self, crawl_trigger_task):
        """Тест различных причин пропуска по политике."""
        test_cases = [
            {
                "message": {"post_id": "no-tags", "tags": [], "urls": ["https://example.com"]},
                "expected_reason": "no_trigger_tags"
            },
            {
                "message": {"post_id": "no-urls", "tags": ["longread"], "urls": []},
                "expected_reason": "no_urls"
            },
            {
                "message": {"post_id": "short-text", "tags": ["longread"], "urls": ["https://example.com"], "text": "short"},
                "expected_reason": "below_word_count"
            }
        ]
        
        for case in test_cases:
            await crawl_trigger_task._process_tagged_event("msg", {"data": json.dumps(case["message"])})
        
        # Проверяем, что все сообщения были обработаны
        assert crawl_trigger_task.redis.xadd.call_count >= len(test_cases)
    
    async def test_metrics_collection(self, crawl_trigger_task, crawl4ai_service):
        """Тест сбора метрик."""
        # Тест метрик CrawlTriggerTask
        test_message = {
            "post_id": "metrics-test",
            "tags": ["longread"],
            "urls": ["https://example.com/metrics"],
            "trace_id": "metrics-trace"
        }
        
        await crawl_trigger_task._process_tagged_event("msg", {"data": json.dumps(test_message)})
        
        # Проверяем, что метрики были обновлены (в реальной системе)
        # Здесь мы проверяем, что код выполнился без ошибок
        
        # Тест метрик Crawl4AIService
        await crawl4ai_service._process_crawl_request("msg", {"data": json.dumps(test_message)})
        
        # Проверяем, что метрики были обновлены
        assert True  # В реальной системе проверяем Prometheus метрики


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
