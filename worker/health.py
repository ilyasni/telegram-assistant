"""
Health checks для всех интеграций.
[C7-ID: HEALTH-CHECK-001]
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, Any

import redis.asyncio as redis
import psycopg2
import requests
from worker.feature_flags import feature_flags
from worker.config import settings
import structlog

logger = structlog.get_logger()

async def check_integrations() -> dict:
    """Проверка всех интеграций с учетом feature flags."""
    results = {
        "redis": await check_redis(),
        "postgres": await check_postgres(),
        "qdrant": await check_qdrant()
    }
    
    if feature_flags.neo4j_enabled:
        results["neo4j"] = await check_neo4j()
    else:
        results["neo4j"] = {"status": "disabled"}
        logger.debug("Neo4j health check skipped (feature disabled)")
    
    if feature_flags.get_available_ai_providers():
        results["ai_providers"] = await check_ai_providers()
    else:
        results["ai_providers"] = {"status": "no_providers"}
        logger.warning("No AI providers available")
    
    if feature_flags.crawl4ai_enabled:
        results["crawl4ai"] = await check_crawl4ai()
    else:
        results["crawl4ai"] = {"status": "disabled"}
        logger.debug("Crawl4AI health check skipped (feature disabled)")
    
    return results

async def check_redis() -> Dict[str, Any]:
    """Проверка Redis с реальным подключением."""
    start_time = time.time()
    try:
        redis_client = redis.from_url(settings.redis_url)
        await redis_client.ping()
        
        # Проверяем основные потоки
        streams_info = {}
        for stream in ['stream:posts:parsed', 'stream:posts:enriched', 'stream:posts:indexed']:
            try:
                length = await redis_client.xlen(stream)
                streams_info[stream] = length
            except Exception as e:
                streams_info[stream] = f"error: {str(e)}"
        
        await redis_client.close()
        
        return {
            "status": "ok",
            "service": "redis",
            "response_time_ms": round((time.time() - start_time) * 1000, 2),
            "streams": streams_info
        }
    except Exception as e:
        return {
            "status": "error",
            "service": "redis",
            "error": str(e),
            "response_time_ms": round((time.time() - start_time) * 1000, 2)
        }

async def check_postgres() -> Dict[str, Any]:
    """Проверка PostgreSQL с реальным подключением."""
    start_time = time.time()
    try:
        conn = psycopg2.connect(settings.database_url)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        return {
            "status": "ok",
            "service": "postgres",
            "response_time_ms": round((time.time() - start_time) * 1000, 2),
            "test_query": "SELECT 1" if result else "failed"
        }
    except Exception as e:
        return {
            "status": "error",
            "service": "postgres",
            "error": str(e),
            "response_time_ms": round((time.time() - start_time) * 1000, 2)
        }

async def check_qdrant() -> Dict[str, Any]:
    """Проверка Qdrant с реальным API вызовом."""
    start_time = time.time()
    try:
        response = requests.get(f"{settings.qdrant_url}/collections", timeout=5)
        if response.status_code == 200:
            collections = response.json().get('result', {}).get('collections', [])
            return {
                "status": "ok",
                "service": "qdrant",
                "response_time_ms": round((time.time() - start_time) * 1000, 2),
                "collections_count": len(collections)
            }
        else:
            return {
                "status": "error",
                "service": "qdrant",
                "error": f"HTTP {response.status_code}",
                "response_time_ms": round((time.time() - start_time) * 1000, 2)
            }
    except Exception as e:
        return {
            "status": "error",
            "service": "qdrant",
            "error": str(e),
            "response_time_ms": round((time.time() - start_time) * 1000, 2)
        }

async def check_neo4j() -> Dict[str, Any]:
    """Проверка Neo4j с реальным подключением."""
    start_time = time.time()
    try:
        from integrations.neo4j_client import Neo4jClient
        neo4j_client = Neo4jClient(settings.neo4j_url, settings.neo4j_username, settings.neo4j_password)
        await neo4j_client.connect()
        
        # Простой тестовый запрос
        async with neo4j_client._driver.session() as session:
            result = await session.run("RETURN 1 as test")
            record = await result.single()
            test_value = record["test"] if record else None
        
        await neo4j_client.close()
        
        return {
            "status": "ok",
            "service": "neo4j",
            "response_time_ms": round((time.time() - start_time) * 1000, 2),
            "test_query": "RETURN 1" if test_value == 1 else "failed"
        }
    except Exception as e:
        return {
            "status": "error",
            "service": "neo4j",
            "error": str(e),
            "response_time_ms": round((time.time() - start_time) * 1000, 2)
        }

async def check_ai_providers() -> Dict[str, Any]:
    """Проверка AI провайдеров."""
    start_time = time.time()
    try:
        from ai_providers.gigachain_adapter import create_gigachain_adapter
        
        # Проверяем GigaChat через адаптер
        adapter = await create_gigachain_adapter()
        if adapter:
            # Простая проверка доступности
            health_status = await adapter.health_check()
            return {
                "status": "ok" if health_status else "degraded",
                "service": "ai_providers",
                "response_time_ms": round((time.time() - start_time) * 1000, 2),
                "gigachat_available": health_status
            }
        else:
            return {
                "status": "error",
                "service": "ai_providers",
                "error": "No AI adapter available",
                "response_time_ms": round((time.time() - start_time) * 1000, 2)
            }
    except Exception as e:
        return {
            "status": "error",
            "service": "ai_providers",
            "error": str(e),
            "response_time_ms": round((time.time() - start_time) * 1000, 2)
        }

async def check_crawl4ai() -> Dict[str, Any]:
    """Проверка Crawl4AI сервиса."""
    start_time = time.time()
    try:
        # Проверяем доступность Crawl4AI сервиса
        response = requests.get("http://crawl4ai:8080/health", timeout=5)
        if response.status_code == 200:
            return {
                "status": "ok",
                "service": "crawl4ai",
                "response_time_ms": round((time.time() - start_time) * 1000, 2)
            }
        else:
            return {
                "status": "error",
                "service": "crawl4ai",
                "error": f"HTTP {response.status_code}",
                "response_time_ms": round((time.time() - start_time) * 1000, 2)
            }
    except Exception as e:
        return {
            "status": "error",
            "service": "crawl4ai",
            "error": str(e),
            "response_time_ms": round((time.time() - start_time) * 1000, 2)
        }
