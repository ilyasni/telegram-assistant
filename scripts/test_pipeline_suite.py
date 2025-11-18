#!/usr/bin/env python3
"""
Context7: Единый test suite для проверки всего пайплайна.

Объединяет все проверки:
1. E2E проверка пайплайна (check_pipeline_e2e.py)
2. Health check (check_pipeline_health.py)
3. Multi-tenant изоляция (test_pipeline_multitenant.py)

Использует pytest структуру с фикстурами из conftest.py.

Usage:
    pytest scripts/test_pipeline_suite.py -v --test-mode e2e
    pytest scripts/test_pipeline_suite.py -v --test-mode smoke -m smoke
    pytest scripts/test_pipeline_suite.py -v --test-mode deep -m "not smoke"
"""
import pytest
import pytest_asyncio
import asyncio
from typing import Dict, Any
import sys
import os

# Импорт основных проверок
sys.path.insert(0, os.path.dirname(__file__))

try:
    from check_pipeline_e2e import PipelineChecker, SLOThresholds
    from check_pipeline_health import PipelineHealthChecker
    E2E_AVAILABLE = True
except ImportError:
    E2E_AVAILABLE = False

try:
    from test_pipeline_multitenant import (
        check_rls_isolation,
        check_redis_namespacing,
        check_qdrant_collections
    )
    MULTITENANT_AVAILABLE = True
except ImportError:
    MULTITENANT_AVAILABLE = False

import structlog

logger = structlog.get_logger()


class TestPipelineE2E:
    """E2E проверка пайплайна через pytest."""
    
    @pytest.mark.asyncio
    @pytest.mark.e2e
    async def test_pipeline_e2e_smoke(self, db_pool, redis_client, qdrant_client, neo4j_driver, test_mode, trace_id):
        """Smoke тест пайплайна (быстрая проверка)."""
        if not E2E_AVAILABLE:
            pytest.skip("check_pipeline_e2e.py not available")
        
        if test_mode != "smoke":
            pytest.skip(f"Test mode is {test_mode}, not smoke")
        
        thresholds = SLOThresholds(mode="smoke")
        checker = PipelineChecker(mode="smoke", thresholds=thresholds, limit=5)
        
        # Используем фикстуры вместо создания новых подключений
        checker.db_pool = db_pool
        checker.redis_client = redis_client
        checker.qdrant_client = qdrant_client
        checker.neo4j_driver = neo4j_driver
        
        try:
            results = await checker.run_all_checks()
            
            # Проверяем, что все smoke проверки прошли
            checks = results.get('checks', [])
            failed = [c for c in checks if not c.get('ok', True)]
            
            assert len(failed) == 0, f"Smoke checks failed: {[c['name'] for c in failed]}"
            
            logger.info("Smoke test passed", trace_id=trace_id, checks_count=len(checks))
            
        except Exception as e:
            logger.error("Smoke test failed", error=str(e), trace_id=trace_id)
            raise
    
    @pytest.mark.asyncio
    @pytest.mark.e2e
    async def test_pipeline_e2e_full(self, db_pool, redis_client, qdrant_client, neo4j_driver, test_mode, trace_id):
        """Полная E2E проверка пайплайна."""
        if not E2E_AVAILABLE:
            pytest.skip("check_pipeline_e2e.py not available")
        
        if test_mode == "smoke":
            pytest.skip("Skipping full E2E in smoke mode")
        
        thresholds = SLOThresholds(mode=test_mode)
        checker = PipelineChecker(mode=test_mode, thresholds=thresholds, limit=10)
        
        # Используем фикстуры
        checker.db_pool = db_pool
        checker.redis_client = redis_client
        checker.qdrant_client = qdrant_client
        checker.neo4j_driver = neo4j_driver
        
        try:
            results = await checker.run_all_checks()
            
            # Проверяем, что пайплайн работает
            pipeline_complete = results.get('summary', {}).get('pipeline_complete', False)
            checks = results.get('checks', [])
            failed = [c for c in checks if not c.get('ok', True)]
            
            # В deep режиме допускаем больше проверок
            max_failures = 3 if test_mode == "deep" else 0
            
            assert len(failed) <= max_failures, \
                f"Too many checks failed: {[c['name'] for c in failed]}"
            
            if test_mode == "e2e":
                assert pipeline_complete, "Pipeline is not processing posts correctly"
            
            logger.info("E2E test passed", 
                       trace_id=trace_id, 
                       mode=test_mode,
                       checks_count=len(checks),
                       pipeline_complete=pipeline_complete)
            
        except Exception as e:
            logger.error("E2E test failed", error=str(e), trace_id=trace_id)
            raise


class TestPipelineHealth:
    """Health check пайплайна через pytest."""
    
    @pytest.mark.asyncio
    @pytest.mark.e2e
    async def test_pipeline_health(self, db_pool, redis_client, test_mode, trace_id, pytestconfig):
        """Health check пайплайна."""
        if not E2E_AVAILABLE:
            pytest.skip("check_pipeline_health.py not available")
        
        # Загрузка порогов
        thresholds_file = pytestconfig.getoption("--thresholds-file", default=None)
        thresholds = {}
        
        if thresholds_file and os.path.exists(thresholds_file):
            import json
            with open(thresholds_file, 'r') as f:
                thresholds = json.load(f)
        else:
            # Дефолтные пороги
            thresholds = {
                'enrichment_tags_min_pct': 80,
                'enrichment_vision_min_pct': 50,
                'enrichment_crawl_min_pct': 30,
                'indexing_failed_max_pct': 20,
                'indexing_pending_max_pct': 30
            }
        
        window_seconds = 3600  # 1 час
        
        checker = PipelineHealthChecker(
            mode=test_mode,
            window_seconds=window_seconds,
            thresholds=thresholds
        )
        
        # Используем фикстуры
        checker.db_pool = db_pool
        checker.redis_client = redis_client
        
        try:
            await checker.initialize()
            
            # Запускаем проверки
            await checker.check_database_health()
            await checker.check_redis_streams_health()
            await checker.check_qdrant_health()
            await checker.check_neo4j_health()
            
            # Проверяем результаты
            breaches = checker.results.get('breaches', [])
            
            # В deep режиме допускаем больше breaches для анализа
            max_breaches = 5 if test_mode == "deep" else 0
            
            assert len(breaches) <= max_breaches, \
                f"Too many SLO breaches: {breaches}"
            
            logger.info("Health check passed",
                       trace_id=trace_id,
                       mode=test_mode,
                       breaches_count=len(breaches))
            
            await checker.cleanup()
            
        except Exception as e:
            logger.error("Health check failed", error=str(e), trace_id=trace_id)
            await checker.cleanup()
            raise


class TestMultiTenant:
    """Проверка multi-tenant изоляции."""
    
    @pytest.mark.asyncio
    @pytest.mark.multitenant
    async def test_rls_isolation(self, db_pool, trace_id):
        """Проверка RLS изоляции между tenants."""
        if not MULTITENANT_AVAILABLE:
            pytest.skip("test_pipeline_multitenant.py not available")
        
        try:
            results = await check_rls_isolation(db_pool)
            
            assert results['rls_enabled'], "RLS is not enabled"
            assert results['tenant_isolation'], "Tenant isolation is not working"
            assert len(results['errors']) == 0, f"RLS check errors: {results['errors']}"
            
            logger.info("RLS isolation check passed", trace_id=trace_id)
            
        except Exception as e:
            logger.error("RLS isolation check failed", error=str(e), trace_id=trace_id)
            raise
    
    @pytest.mark.asyncio
    @pytest.mark.multitenant
    async def test_redis_namespacing(self, redis_client, trace_id):
        """Проверка Redis namespacing."""
        if not MULTITENANT_AVAILABLE:
            pytest.skip("test_pipeline_multitenant.py not available")
        
        try:
            results = await check_redis_namespacing(redis_client)
            
            assert results['namespacing_correct'], "Redis namespacing is not correct"
            assert len(results['errors']) == 0, f"Redis namespacing errors: {results['errors']}"
            
            logger.info("Redis namespacing check passed", trace_id=trace_id)
            
        except Exception as e:
            logger.error("Redis namespacing check failed", error=str(e), trace_id=trace_id)
            raise
    
    @pytest.mark.asyncio
    @pytest.mark.multitenant
    async def test_qdrant_collections(self, qdrant_client, trace_id):
        """Проверка Qdrant collections per tenant."""
        if not MULTITENANT_AVAILABLE:
            pytest.skip("test_pipeline_multitenant.py not available")
        
        try:
            results = await check_qdrant_collections(qdrant_client)
            
            # В production должно быть коллекции per tenant
            # В dev/test может не быть, поэтому проверяем только структуру
            assert len(results['errors']) == 0, f"Qdrant collections errors: {results['errors']}"
            
            logger.info("Qdrant collections check passed",
                       trace_id=trace_id,
                       collections=results['tenant_collections'])
            
        except Exception as e:
            logger.error("Qdrant collections check failed", error=str(e), trace_id=trace_id)
            raise


# Интеграционный тест для всего пайплайна
@pytest.mark.asyncio
@pytest.mark.e2e
async def test_pipeline_complete(
    db_pool,
    redis_client,
    qdrant_client,
    neo4j_driver,
    test_mode,
    trace_id
):
    """
    Полная интеграционная проверка пайплайна.
    
    Запускает все проверки последовательно:
    1. E2E проверка
    2. Health check
    3. Multi-tenant изоляция (если не smoke режим)
    """
    if test_mode == "smoke":
        pytest.skip("Skipping complete test in smoke mode")
    
    logger.info("Starting complete pipeline test", trace_id=trace_id, mode=test_mode)
    
    # 1. E2E проверка
    if E2E_AVAILABLE:
        thresholds = SLOThresholds(mode=test_mode)
        checker = PipelineChecker(mode=test_mode, thresholds=thresholds, limit=10)
        checker.db_pool = db_pool
        checker.redis_client = redis_client
        checker.qdrant_client = qdrant_client
        checker.neo4j_driver = neo4j_driver
        
        e2e_results = await checker.run_all_checks()
        e2e_ok = all(c.get('ok', True) for c in e2e_results.get('checks', []))
        
        assert e2e_ok, "E2E checks failed"
        logger.info("E2E check passed", trace_id=trace_id)
    
    # 2. Health check (только в e2e/deep режимах)
    if test_mode in ["e2e", "deep"] and E2E_AVAILABLE:
        thresholds = {
            'enrichment_tags_min_pct': 80,
            'enrichment_vision_min_pct': 50,
            'enrichment_crawl_min_pct': 30
        }
        
        health_checker = PipelineHealthChecker(
            mode=test_mode,
            window_seconds=3600,
            thresholds=thresholds
        )
        health_checker.db_pool = db_pool
        health_checker.redis_client = redis_client
        
        await health_checker.initialize()
        await health_checker.check_database_health()
        await health_checker.check_redis_streams_health()
        await health_checker.cleanup()
        
        breaches = health_checker.results.get('breaches', [])
        max_breaches = 5 if test_mode == "deep" else 0
        
        assert len(breaches) <= max_breaches, f"Too many health breaches: {breaches}"
        logger.info("Health check passed", trace_id=trace_id)
    
    # 3. Multi-tenant изоляция (только в e2e/deep режимах)
    if test_mode in ["e2e", "deep"] and MULTITENANT_AVAILABLE:
        rls_results = await check_rls_isolation(db_pool)
        redis_results = await check_redis_namespacing(redis_client)
        qdrant_results = await check_qdrant_collections(qdrant_client)
        
        assert rls_results['rls_enabled'] and rls_results['tenant_isolation'], \
            "RLS isolation failed"
        assert redis_results['namespacing_correct'], \
            "Redis namespacing failed"
        assert len(qdrant_results['errors']) == 0, \
            f"Qdrant collections errors: {qdrant_results['errors']}"
        
        logger.info("Multi-tenant checks passed", trace_id=trace_id)
    
    logger.info("Complete pipeline test passed", trace_id=trace_id, mode=test_mode)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


