#!/usr/bin/env python3
"""
Neo4j Health Server
===================

HTTP health endpoint для Neo4j с метриками Prometheus.
Порт: 7475 (стандартный Neo4j HTTP порт + 1)

Метрики:
- neo4j_query_duration_seconds - время выполнения запросов
- neo4j_nodes_total - количество узлов по типам
- neo4j_relationships_total - количество связей по типам
- neo4j_connections_active - активные соединения
"""

import asyncio
import logging
import os
import time
from typing import Dict, Any

import uvicorn
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST, REGISTRY
from starlette.responses import Response

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Context7: Предотвращаем дублирование метрик при hot reload uvicorn
# При перезапуске модуля старые метрики остаются в REGISTRY, нужно их удалить
def safe_create_metric(metric_type, name, description, **kwargs):
    """Создать метрику с проверкой на существование (Context7: предотвращение дублирования)."""
    try:
        # Пытаемся найти существующую метрику в регистре
        if hasattr(REGISTRY, '_names_to_collectors'):
            existing = REGISTRY._names_to_collectors.get(name)
            if existing:
                # Удаляем существующую метрику перед созданием новой
                try:
                    REGISTRY.unregister(existing)
                    logger.debug(f"Unregistered existing metric before recreation: {name}")
                except (KeyError, ValueError):
                    pass  # Метрика уже удалена или не найдена
    except (AttributeError, KeyError, TypeError):
        pass
    
    # Создаем новую метрику
    return metric_type(name, description, **kwargs)

# Prometheus метрики (Context7: защита от дублирования при hot reload)
neo4j_query_duration_seconds = safe_create_metric(
    Histogram,
    'neo4j_query_duration_seconds',
    'Neo4j query duration in seconds',
    labelnames=['query_type']
)

neo4j_nodes_total = safe_create_metric(
    Gauge,
    'neo4j_nodes_total',
    'Total number of nodes in the graph',
    labelnames=['label']
)

neo4j_relationships_total = safe_create_metric(
    Gauge,
    'neo4j_relationships_total',
    'Total number of relationships',
    labelnames=['type']
)

neo4j_connections_active = safe_create_metric(
    Gauge,
    'neo4j_connections_active',
    'Active Neo4j connections'
)

# FastAPI приложение
app = FastAPI(
    title="Neo4j Health Server",
    description="Health endpoint and metrics for Neo4j",
    version="1.0.0"
)

# Neo4j connection
neo4j_uri = os.getenv('NEO4J_URI', 'neo4j://neo4j:7687')
neo4j_user = os.getenv('NEO4J_USER', 'neo4j')
neo4j_password = os.getenv('NEO4J_PASSWORD', 'changeme')

try:
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    logger.info(f"Connected to Neo4j at {neo4j_uri}")
except ImportError:
    logger.error("neo4j driver not installed. Install with: pip install neo4j")
    driver = None
except Exception as e:
    logger.error(f"Failed to connect to Neo4j: {e}")
    driver = None


async def check_neo4j_health() -> Dict[str, Any]:
    """Проверка здоровья Neo4j"""
    if not driver:
        return {
            "status": "unhealthy",
            "error": "Neo4j driver not available"
        }
    
    try:
        with driver.session() as session:
            # Простой запрос для проверки соединения
            result = session.run("RETURN 1 as test")
            record = result.single()
            
            if record and record["test"] == 1:
                return {
                    "status": "healthy",
                    "neo4j_uri": neo4j_uri,
                    "timestamp": time.time()
                }
            else:
                return {
                    "status": "unhealthy",
                    "error": "Query returned unexpected result"
                }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


async def get_neo4j_stats() -> Dict[str, Any]:
    """Получение статистики Neo4j"""
    if not driver:
        return {}
    
    stats = {}
    
    try:
        with driver.session() as session:
            # Количество узлов по типам
            result = session.run("MATCH (n) RETURN labels(n) as labels, count(n) as count")
            node_counts = {}
            for record in result:
                labels = record["labels"]
                count = record["count"]
                if labels:
                    label_key = ":".join(labels)
                    node_counts[label_key] = count
                    neo4j_nodes_total.labels(label=label_key).set(count)
                else:
                    node_counts["unlabeled"] = count
                    neo4j_nodes_total.labels(label="unlabeled").set(count)
            
            stats["nodes"] = node_counts
            
            # Количество связей по типам
            result = session.run("MATCH ()-[r]->() RETURN type(r) as type, count(r) as count")
            rel_counts = {}
            for record in result:
                rel_type = record["type"]
                count = record["count"]
                rel_counts[rel_type] = count
                neo4j_relationships_total.labels(type=rel_type).set(count)
            
            stats["relationships"] = rel_counts
            
            # Общее количество узлов и связей
            result = session.run("MATCH (n) RETURN count(n) as total_nodes")
            total_nodes = result.single()["total_nodes"]
            stats["total_nodes"] = total_nodes
            
            result = session.run("MATCH ()-[r]->() RETURN count(r) as total_relationships")
            total_relationships = result.single()["total_relationships"]
            stats["total_relationships"] = total_relationships
            
            # Активные соединения (приблизительно)
            neo4j_connections_active.set(1)  # Если мы здесь, соединение активно
            
    except Exception as e:
        logger.error(f"Failed to get Neo4j stats: {e}")
        stats["error"] = str(e)
    
    return stats


@app.get("/health")
async def health():
    """Health check endpoint"""
    health_data = await check_neo4j_health()
    
    if health_data["status"] == "healthy":
        return {"status": "healthy", "service": "neo4j"}
    else:
        raise HTTPException(status_code=503, detail=health_data)


@app.get("/health/detailed")
async def detailed_health():
    """Детальная проверка здоровья с метриками"""
    health_data = await check_neo4j_health()
    stats = await get_neo4j_stats()
    
    return {
        "status": health_data["status"],
        "service": "neo4j",
        "neo4j_uri": neo4j_uri,
        "timestamp": time.time(),
        "stats": stats,
        "health": health_data
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    # Обновляем метрики
    await get_neo4j_stats()
    
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Neo4j Health Server",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "detailed_health": "/health/detailed",
            "metrics": "/metrics"
        }
    }


if __name__ == "__main__":
    uvicorn.run(
        "health_server:app",
        host="0.0.0.0",
        port=7475,
        log_level="info"
    )
