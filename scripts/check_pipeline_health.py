#!/usr/bin/env python3
"""
Pipeline Health Check - Production-ready monitoring with Context7 best practices.

Режимы:
- smoke (≤30s): базовая проверка сервисов
- e2e (≤90s): полная проверка пайплайна с порогами SLO
- deep (≤5min): детальная диагностика с gap analysis

Context7 best practices:
- asyncpg connection pool с lifecycle callbacks
- Redis SCAN вместо KEYS (безопасность)
- Qdrant через httpx.AsyncClient (async)
- Neo4j агрегаты вместо сканов (производительность)
- Prometheus Pushgateway с low cardinality labels
- Идемпотентные проверки с trace_id
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from xml.sax.saxutils import escape

import asyncpg
import redis.asyncio as redis
import structlog
import httpx
from neo4j import AsyncGraphDatabase

# Prometheus client (опционально)
try:
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Конфигурация
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j123")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "telegram_posts")

# ============================================================================
# УТИЛИТЫ
# ============================================================================

def ensure_dt_utc(x) -> Optional[datetime]:
    """Безопасная конвертация любого типа в aware datetime UTC."""
    if x is None:
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    if isinstance(x, (bytes, bytearray)):
        x = x.decode("utf-8", errors="ignore")
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        s = s.replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            if len(s) > 5 and (s[-5] in ['+', '-']) and ':' not in s[-5:]:
                try:
                    dt = datetime.fromisoformat(s[:-2] + ':' + s[-2:])
                except Exception:
                    return None
            else:
                return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


async def scan_iter(client: redis.Redis, pattern: str, count: int = 200):
    """Безопасная итерация по ключам Redis через SCAN."""
    cursor = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=pattern, count=count)
        for k in keys:
            yield k
        if cursor == 0:
            break


# ============================================================================
# CTE VIEWS ДЛЯ ENRICHMENTS (Context7 best practice)
# ============================================================================

ENRICHMENT_VIEWS_CTE = """
WITH recent_posts AS (
    SELECT id, posted_at, created_at
    FROM posts
    WHERE posted_at > NOW() - INTERVAL '1 hour'
),
enrichment_tags AS (
    SELECT 
        p.id as post_id,
        CASE 
            WHEN pe.kind = 'tags' AND pe.status = 'ok' THEN true
            ELSE false
        END as has_tags
    FROM posts p
    LEFT JOIN post_enrichment pe ON pe.post_id = p.id AND pe.kind = 'tags'
),
enrichment_vision AS (
    SELECT 
        p.id as post_id,
        CASE WHEN pe.kind = 'vision' AND pe.status = 'ok' THEN true ELSE false END as has_vision
    FROM posts p
    LEFT JOIN post_enrichment pe ON pe.post_id = p.id AND pe.kind = 'vision'
),
enrichment_crawl AS (
    SELECT 
        p.id as post_id,
        CASE WHEN pe.kind = 'crawl' AND pe.status = 'ok' THEN true ELSE false END as has_crawl
    FROM posts p
    LEFT JOIN post_enrichment pe ON pe.post_id = p.id AND pe.kind = 'crawl'
)
SELECT 
    COUNT(DISTINCT p.id) as total_posts,
    COUNT(DISTINCT CASE WHEN et.has_tags THEN p.id END) as posts_with_tags,
    COUNT(DISTINCT CASE WHEN ev.has_vision THEN p.id END) as posts_with_vision,
    COUNT(DISTINCT CASE WHEN ec.has_crawl THEN p.id END) as posts_with_crawl
FROM posts p
LEFT JOIN enrichment_tags et ON et.post_id = p.id
LEFT JOIN enrichment_vision ev ON ev.post_id = p.id
LEFT JOIN enrichment_crawl ec ON ec.post_id = p.id
WHERE p.posted_at > NOW() - INTERVAL '1 hour'
"""


# ============================================================================
# PIPELINE HEALTH CHECKER
# ============================================================================

class PipelineHealthChecker:
    """
    Production-ready pipeline health checker с Context7 best practices.
    
    Архитектура:
    - asyncpg connection pool с lifecycle callbacks
    - Redis SCAN вместо KEYS
    - Qdrant через httpx.AsyncClient
    - Neo4j async driver с агрегатами
    - Prometheus Pushgateway с low cardinality
    - Идемпотентные проверки с trace_id
    """
    
    def __init__(self, mode: str, window_seconds: int, thresholds: dict, now: datetime = None):
        self.mode = mode  # smoke/e2e/deep
        self.window_seconds = window_seconds
        self.thresholds = thresholds
        self.now = now or datetime.now(timezone.utc)
        self.trace_id = str(uuid.uuid4())
        
        # Connection pools
        self.db_pool = None
        self.redis_client = None
        self.neo4j_driver = None
        
        # Health flags
        self.db_healthy = False
        self.redis_healthy = False
        self.qdrant_healthy = False
        self.neo4j_healthy = False
        
        # Results storage
        self.results = {
            'trace_id': self.trace_id,
            'timestamp': self.now.isoformat(),
            'mode': self.mode,
            'window_seconds': self.window_seconds,
            'database': {},
            'redis_streams': {},
            'qdrant': {},
            'neo4j': {},
            'gaps': {},
            'breaches': [],
            'summary': {}
        }
        
        # Output paths
        self.output_json = None
        self.output_md = None
        self.prometheus_gateway_url = None
    
    async def initialize(self):
        """Context7: инициализация с lifecycle callbacks."""
        logger.info("Initializing connections", mode=self.mode, trace_id=self.trace_id)
        
        # Context7: asyncpg pool с lifecycle callbacks
        async def init_connection(conn):
            await conn.execute("SET timezone TO 'UTC'")
            await conn.execute("SET application_name TO 'pipeline_health_check'")
        
        try:
            timeout = 5 if self.mode == "smoke" else 30
            self.db_pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=5,
                command_timeout=timeout,
                max_inactive_connection_lifetime=300.0,
                init=init_connection
            )
            logger.info("Database pool created", timeout=timeout)
            self.db_healthy = True
        except Exception as e:
            logger.error("Failed to create DB pool", error=str(e))
            raise
        
        # Redis
        try:
            self.redis_client = redis.from_url(
                REDIS_URL,
                socket_connect_timeout=5,
                socket_timeout=10,
                retry_on_timeout=True
            )
            await asyncio.wait_for(self.redis_client.ping(), timeout=2)
            logger.info("Redis connected")
            self.redis_healthy = True
        except Exception as e:
            logger.error("Failed to connect Redis", error=str(e))
            raise
        
        # Neo4j
        try:
            self.neo4j_driver = AsyncGraphDatabase.driver(
                NEO4J_URI,
                auth=(NEO4J_USER, NEO4J_PASSWORD),
                max_connection_lifetime=300
            )
            await asyncio.wait_for(self.neo4j_driver.verify_connectivity(), timeout=5)
            logger.info("Neo4j connected")
            self.neo4j_healthy = True
        except Exception as e:
            logger.error("Failed to connect Neo4j", error=str(e))
            raise
    
    async def cleanup(self):
        """Закрытие подключений."""
        if self.db_pool:
            await self.db_pool.close()
        if self.redis_client:
            await self.redis_client.aclose()
        if self.neo4j_driver:
            await self.neo4j_driver.close()
    
    async def check_database_health(self):
        """Context7: проверка PostgreSQL с CTE view для enrichments."""
        logger.info("Checking database health")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Context7: Prepared statement для повторяющихся запросов
                stmt = await conn.prepare(ENRICHMENT_VIEWS_CTE)
                result = await stmt.fetchrow()
                
                # Gap analysis
                total_posts = result['total_posts']
                posts_with_tags = result['posts_with_tags']
                posts_with_vision = result['posts_with_vision']
                posts_with_crawl = result['posts_with_crawl']
                
                tags_pct = (posts_with_tags / total_posts * 100) if total_posts > 0 else 0
                vision_pct = (posts_with_vision / total_posts * 100) if total_posts > 0 else 0
                crawl_pct = (posts_with_crawl / total_posts * 100) if total_posts > 0 else 0
                
                # SLO validation
                breaches = []
                if tags_pct < self.thresholds.get('enrichment_tags_min_pct', 80):
                    breaches.append({
                        'type': 'enrichment_tags',
                        'actual': tags_pct,
                        'threshold': self.thresholds.get('enrichment_tags_min_pct', 80)
                    })
                if vision_pct < self.thresholds.get('enrichment_vision_min_pct', 50):
                    breaches.append({
                        'type': 'enrichment_vision',
                        'actual': vision_pct,
                        'threshold': self.thresholds.get('enrichment_vision_min_pct', 50)
                    })
                if crawl_pct < self.thresholds.get('enrichment_crawl_min_pct', 30):
                    breaches.append({
                        'type': 'enrichment_crawl',
                        'actual': crawl_pct,
                        'threshold': self.thresholds.get('enrichment_crawl_min_pct', 30)
                    })
                
                # Indexing status
                indexing_stats = await conn.fetchrow("""
                    SELECT 
                        embedding_status,
                        COUNT(*) as count
                    FROM indexing_status
                    GROUP BY embedding_status
                """)
                
                indexing_breakdown = {row['embedding_status']: row['count'] for row in await conn.fetch("""
                    SELECT embedding_status, COUNT(*) as count
                    FROM indexing_status
                    GROUP BY embedding_status
                """)}
                
                total_indexed = sum(indexing_breakdown.values())
                failed_pct = (indexing_breakdown.get('failed', 0) / total_indexed * 100) if total_indexed > 0 else 0
                pending_pct = (indexing_breakdown.get('pending', 0) / total_indexed * 100) if total_indexed > 0 else 0
                
                if failed_pct > self.thresholds.get('indexing_failed_max_pct', 20):
                    breaches.append({
                        'type': 'indexing_failed',
                        'actual': failed_pct,
                        'threshold': self.thresholds.get('indexing_failed_max_pct', 20)
                    })
                if pending_pct > self.thresholds.get('indexing_pending_max_pct', 30):
                    breaches.append({
                        'type': 'indexing_pending',
                        'actual': pending_pct,
                        'threshold': self.thresholds.get('indexing_pending_max_pct', 30)
                    })
                
                self.results['database'] = {
                    'total_posts': total_posts,
                    'enrichments': {
                        'tags': {'count': posts_with_tags, 'pct': tags_pct},
                        'vision': {'count': posts_with_vision, 'pct': vision_pct},
                        'crawl': {'count': posts_with_crawl, 'pct': crawl_pct}
                    },
                    'indexing': {
                        'total': total_indexed,
                        'breakdown': indexing_breakdown,
                        'failed_pct': failed_pct,
                        'pending_pct': pending_pct
                    },
                    'breaches': breaches
                }
                
                # Context7: Проверка качества данных в post_enrichment
                sample_tags = await conn.fetch("""
                    SELECT 
                        pe.post_id,
                        pe.data->'tags' as tags,
                        jsonb_array_length(pe.data->'tags') as tags_count
                    FROM post_enrichment pe
                    WHERE pe.kind = 'tags' AND pe.status = 'ok'
                    ORDER BY pe.updated_at DESC
                    LIMIT 5
                """)
                
                empty_tags_count = await conn.fetchval("""
                    SELECT COUNT(*) FROM post_enrichment
                    WHERE kind = 'tags' AND status = 'ok'
                    AND (data->'tags' IS NULL OR jsonb_array_length(data->'tags') = 0)
                """)
                
                self.results['database']['tags_quality'] = {
                    'empty_tags_count': empty_tags_count,
                    'sample_tags': [
                        {
                            'post_id': str(row['post_id']),
                            'tags_count': row['tags_count'],
                            'tags_sample': (list(row['tags'])[:3] if isinstance(row['tags'], list) else [])
                        }
                        for row in sample_tags
                    ]
                }
                
                # Context7: Переносим breaches в корневой массив для агрегации
                self.results['breaches'].extend(breaches)
                
                logger.info("Database check completed",
                           total_posts=total_posts,
                           breaches_count=len(breaches),
                           empty_tags_count=empty_tags_count)
        
        except Exception as e:
            logger.error("Database check failed", error=str(e))
            self.results['database'] = {'error': str(e)}
            self.db_healthy = False
    
    def _calculate_lag(self, last_generated: str, last_delivered: str) -> int:
        """Парсинг Redis Stream ID формата <ms>-<seq> для расчёта lag."""
        try:
            gen_ms, gen_seq = map(int, last_generated.split('-'))
            del_ms, del_seq = map(int, last_delivered.split('-'))
            # Lag в миллисекундах (упрощённо, без учёта seq)
            return gen_ms - del_ms
        except Exception:
            return 0
    
    async def check_redis_stream(self, stream_key: str, groups: List[str]) -> Dict[str, Any]:
        """Context7: безопасная проверка stream с корректным lag."""
        logger.debug("Checking Redis stream", stream=stream_key, groups=groups)
        
        try:
            # XINFO STREAM для last-generated ID
            stream_info_raw = await self.redis_client.execute_command('XINFO', 'STREAM', stream_key)
            stream_info = {}
            
            # Парсинг ответа XINFO STREAM
            if isinstance(stream_info_raw, list):
                for i in range(0, len(stream_info_raw) - 1, 2):
                    key = stream_info_raw[i]
                    value = stream_info_raw[i + 1]
                    if isinstance(key, bytes):
                        key = key.decode()
                    stream_info[key] = value
            
            last_generated_id = stream_info.get('last-generated-id', '0-0')
            if isinstance(last_generated_id, bytes):
                last_generated_id = last_generated_id.decode()
            
            results = {}
            for group in groups:
                try:
                    # XINFO GROUPS для last-delivered-id
                    groups_info_raw = await self.redis_client.execute_command('XINFO', 'GROUPS', stream_key)
                    
                    # Найти группу
                    last_delivered_id = '0-0'
                    if isinstance(groups_info_raw, list):
                        for g in groups_info_raw:
                            if isinstance(g, list):
                                g_dict = {}
                                for i in range(0, len(g) - 1, 2):
                                    k = g[i]
                                    v = g[i + 1]
                                    if isinstance(k, bytes):
                                        k = k.decode()
                                    g_dict[k] = v
                            else:
                                g_dict = g if isinstance(g, dict) else {}
                            
                            group_name = g_dict.get('name', '')
                            if isinstance(group_name, bytes):
                                group_name = group_name.decode()
                            
                            if group_name == group:
                                last_delivered_raw = g_dict.get('last-delivered-id', '0-0')
                                if isinstance(last_delivered_raw, bytes):
                                    last_delivered_id = last_delivered_raw.decode()
                                else:
                                    last_delivered_id = str(last_delivered_raw)
                                break
                    
                    # XPENDING для pending summary (без IDLE в smoke mode)
                    pending_info = await self.redis_client.execute_command('XPENDING', stream_key, group)
                    pending_count = 0
                    
                    if isinstance(pending_info, list) and len(pending_info) >= 1:
                        pending_count = pending_info[0] if isinstance(pending_info[0], int) else 0
                    
                    # Lag calculation
                    lag = self._calculate_lag(last_generated_id, last_delivered_id)
                    
                    results[group] = {
                        'pending': pending_count,
                        'lag': lag,
                        'status': 'ok' if lag < self.thresholds.get('redis_lag_max', 1000) else 'breach'
                    }
                    
                    # SLO breach для lag
                    if lag > self.thresholds.get('redis_lag_max', 1000):
                        self.results['breaches'].append({
                            'type': 'redis_stream_lag',
                            'stream': stream_key,
                            'group': group,
                            'actual': lag,
                            'threshold': self.thresholds.get('redis_lag_max', 1000)
                        })
                    
                    # SLO breach для pending
                    if pending_count > self.thresholds.get('redis_pending_max', 100):
                        self.results['breaches'].append({
                            'type': 'redis_stream_pending',
                            'stream': stream_key,
                            'group': group,
                            'actual': pending_count,
                            'threshold': self.thresholds.get('redis_pending_max', 100)
                        })
                
                except Exception as e:
                    if 'NOGROUP' in str(e):
                        results[group] = {'status': 'missing', 'error': 'NOGROUP'}
                        logger.debug("Consumer group missing", stream=stream_key, group=group)
                    else:
                        results[group] = {'status': 'error', 'error': str(e)}
                        logger.warning("Group check failed", stream=stream_key, group=group, error=str(e))
            
            return results
        
        except Exception as e:
            logger.error("Redis stream check failed", stream=stream_key, error=str(e))
            return {'status': 'error', 'error': str(e)}
    
    async def check_redis_streams(self):
        """Context7: безопасная проверка всех streams."""
        logger.info("Checking Redis streams")
        
        streams_config = {
            'stream:posts:parsed': ['post_persist_workers', 'tagging_workers'],
            'stream:posts:tagged': ['tag_persist_workers', 'enrich_workers', 'crawl_trigger_workers'],
            'stream:posts:enriched': ['indexing_workers'],
            'stream:posts:vision': ['vision_workers']
        }
        
        results = {}
        for stream_key, groups in streams_config.items():
            results[stream_key] = await self.check_redis_stream(stream_key, groups)
        
        self.results['redis_streams'] = results
        logger.info("Redis streams check completed", streams_count=len(streams_config))
    
    async def check_qdrant(self):
        """Context7: async проверка Qdrant через REST API."""
        logger.info("Checking Qdrant")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # GET /collections/{name}
                response = await client.get(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}")
                
                if response.status_code != 200:
                    self.results['qdrant'] = {'error': f"HTTP {response.status_code}"}
                    self.qdrant_healthy = False
                    return
                
                collection_info = response.json()['result']
                
                points_count = collection_info.get('points_count', 0)
                vectors_count = collection_info.get('vectors_count', 0)
                status = collection_info.get('status')
                
                # Сравнение с indexing_status.embedding_status='completed'
                async with self.db_pool.acquire() as conn:
                    eligible_count = await conn.fetchval("""
                        SELECT COUNT(*) FROM indexing_status 
                        WHERE embedding_status = 'completed'
                    """)
                
                gap_pct = ((eligible_count - points_count) / eligible_count * 100) if eligible_count > 0 else 0
                breach = gap_pct > self.thresholds.get('qdrant_gap_max_pct', 10)
                
                if breach:
                    self.results['breaches'].append({
                        'type': 'qdrant_gap',
                        'actual': gap_pct,
                        'threshold': self.thresholds.get('qdrant_gap_max_pct', 10)
                    })
                
                self.results['qdrant'] = {
                    'points_count': points_count,
                    'vectors_count': vectors_count,
                    'status': status,
                    'eligible_for_index': eligible_count,
                    'gap_pct': gap_pct,
                    'breach': breach
                }
                
                self.qdrant_healthy = True
                logger.info("Qdrant check completed",
                           points_count=points_count,
                           gap_pct=gap_pct,
                           breach=breach)
        
        except Exception as e:
            logger.error("Qdrant check failed", error=str(e))
            self.results['qdrant'] = {'error': str(e)}
            self.qdrant_healthy = False
    
    async def check_neo4j(self):
        """Context7: агрегированные проверки Neo4j без сканов."""
        logger.info("Checking Neo4j")
        
        try:
            async with self.neo4j_driver.session() as session:
                # Context7: Агрегированные проверки Neo4j без сканов
                posts_result = await session.run("MATCH (p:Post) RETURN count(p) as count")
                posts_record = await posts_result.single()
                posts_count = posts_record['count'] if posts_record else 0
                
                channels_result = await session.run("MATCH (c:Channel) RETURN count(c) as count")
                channels_record = await channels_result.single()
                channels_count = channels_record['count'] if channels_record else 0
                
                users_result = await session.run("MATCH (u:User) RETURN count(u) as count")
                users_record = await users_result.single()
                users_count = users_record['count'] if users_record else 0
                
                tags_result = await session.run("MATCH (t:Tag) RETURN count(t) as count")
                tags_record = await tags_result.single()
                tags_count = tags_record['count'] if tags_record else 0
                
                # Агрегаты связей
                tagged_result = await session.run("""
                    MATCH (:Post)-[r:TAGGED_AS]->(:Tag) 
                    RETURN count(r) as count
                """)
                tagged_record = await tagged_result.single()
                tagged_as_count = tagged_record['count'] if tagged_record else 0
                
                vision_result = await session.run("""
                    MATCH (:Post)-[r:HAS_VISION]->() 
                    RETURN count(r) as count
                """)
                vision_record = await vision_result.single()
                has_vision_count = vision_record['count'] if vision_record else 0
                
                # Сравнение с post_enrichment
                async with self.db_pool.acquire() as conn:
                    pg_tags_result = await conn.fetchval("""
                        SELECT COUNT(*) FROM post_enrichment 
                        WHERE kind = 'tags' AND status = 'ok'
                    """)
                    pg_vision_result = await conn.fetchval("""
                        SELECT COUNT(*) FROM post_enrichment 
                        WHERE kind = 'vision' AND status = 'ok'
                    """)
                
                tags_gap_pct = ((pg_tags_result - tagged_as_count) / pg_tags_result * 100) if pg_tags_result > 0 else 0
                vision_gap_pct = ((pg_vision_result - has_vision_count) / pg_vision_result * 100) if pg_vision_result > 0 else 0
                
                max_gap = max(tags_gap_pct, vision_gap_pct)
                breach = max_gap > self.thresholds.get('neo4j_gap_max_pct', 10)
                
                if breach:
                    self.results['breaches'].append({
                        'type': 'neo4j_gap',
                        'actual': max_gap,
                        'threshold': self.thresholds.get('neo4j_gap_max_pct', 10),
                        'tags_gap': tags_gap_pct,
                        'vision_gap': vision_gap_pct
                    })
                
                self.results['neo4j'] = {
                    'nodes': {
                        'posts': posts_count,
                        'channels': channels_count,
                        'users': users_count,
                        'tags': tags_count
                    },
                    'relationships': {
                        'tagged_as': tagged_as_count,
                        'has_vision': has_vision_count
                    },
                    'postgres_enrichments': {
                        'tags': pg_tags_result,
                        'vision': pg_vision_result
                    },
                    'gaps': {
                        'tags_gap_pct': tags_gap_pct,
                        'vision_gap_pct': vision_gap_pct
                    },
                    'breach': breach
                }
                
                logger.info("Neo4j check completed",
                           posts_count=posts_count,
                           tagged_as_count=tagged_as_count,
                           max_gap=max_gap,
                           breach=breach)
        
        except Exception as e:
            logger.error("Neo4j check failed", error=str(e))
            self.results['neo4j'] = {'error': str(e)}
            self.neo4j_healthy = False
    
    async def generate_report(self):
        """Генерация JSON + Markdown отчётов."""
        # JSON
        if self.output_json:
            output_dir = os.path.dirname(self.output_json)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            with open(self.output_json, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, indent=2, default=str)
            logger.info("JSON report saved", path=self.output_json)
        
        # Markdown
        if self.output_md:
            output_dir = os.path.dirname(self.output_md)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            md = self._generate_markdown_report()
            with open(self.output_md, 'w', encoding='utf-8') as f:
                f.write(md)
            logger.info("Markdown report saved", path=self.output_md)
    
    def _generate_markdown_report(self) -> str:
        """Context7: краткий MD отчёт с топ-3 проблемами."""
        breaches = self.results.get('breaches', [])
        
        db_breaches = len(self.results.get('database', {}).get('breaches', []))
        redis_breaches = sum(1 for s in self.results.get('redis_streams', {}).values() 
                            if any(g.get('status') == 'breach' for g in s.values() if isinstance(g, dict)))
        qdrant_breach = self.results.get('qdrant', {}).get('breach', False)
        neo4j_breach = self.results.get('neo4j', {}).get('breach', False)
        
        md = f"""# Pipeline Health Check Report

**Timestamp**: {self.results['timestamp']}
**Trace ID**: {self.results['trace_id']}
**Mode**: {self.results['mode']}
**Window**: {self.results['window_seconds']}s

## Summary

- **Database**: {'✅ OK' if db_breaches == 0 else f'❌ {db_breaches} breach(es)'}
- **Redis Streams**: {'✅ OK' if redis_breaches == 0 else f'❌ {redis_breaches} breach(es)'}
- **Qdrant**: {'✅ OK' if not qdrant_breach else '❌ BREACH'}
- **Neo4j**: {'✅ OK' if not neo4j_breach else '❌ BREACH'}

## Top Issues

"""
        
        if breaches:
            for i, breach in enumerate(breaches[:3], 1):
                breach_msg = f"{breach['type']}: {breach.get('actual', 0):.2f} vs threshold {breach.get('threshold', 0)}"
                md += f"{i}. **{breach_msg}**\n"
        else:
            md += "No issues found.\n"
        
        md += f"""
## Detailed Results

### Database
- Total posts (1h): {self.results.get('database', {}).get('total_posts', 0)}
- Tags enrichment: {self.results.get('database', {}).get('enrichments', {}).get('tags', {}).get('pct', 0):.1f}%
- Vision enrichment: {self.results.get('database', {}).get('enrichments', {}).get('vision', {}).get('pct', 0):.1f}%
- Crawl enrichment: {self.results.get('database', {}).get('enrichments', {}).get('crawl', {}).get('pct', 0):.1f}%

### Redis Streams
"""
        for stream, groups_data in self.results.get('redis_streams', {}).items():
            if isinstance(groups_data, dict):
                md += f"- **{stream}**:\n"
                for group, data in groups_data.items():
                    if isinstance(data, dict) and 'pending' in data:
                        md += f"  - {group}: pending={data.get('pending', 0)}, lag={data.get('lag', 0)}ms\n"
        
        qdrant_data = self.results.get('qdrant', {})
        neo4j_data = self.results.get('neo4j', {})
        
        md += f"""
### Qdrant
- Points: {qdrant_data.get('points_count', 0)}
- Eligible: {qdrant_data.get('eligible_for_index', 0)}
- Gap: {qdrant_data.get('gap_pct', 0):.1f}%

### Neo4j
- Nodes: {neo4j_data.get('nodes', {}).get('posts', 0)} posts, {neo4j_data.get('nodes', {}).get('channels', 0)} channels
- Relationships: {neo4j_data.get('relationships', {}).get('tagged_as', 0)} TAGGED_AS, {neo4j_data.get('relationships', {}).get('has_vision', 0)} HAS_VISION
- Gaps: tags={neo4j_data.get('gaps', {}).get('tags_gap_pct', 0):.1f}%, vision={neo4j_data.get('gaps', {}).get('vision_gap_pct', 0):.1f}%
"""
        
        return md
    
    def push_metrics_to_gateway(self):
        """Context7: Pushgateway для batch jobs с low cardinality."""
        if not PROMETHEUS_AVAILABLE:
            logger.warning("Prometheus client not available, skipping metrics push")
            return
        
        if not self.prometheus_gateway_url:
            logger.debug("Prometheus Pushgateway URL not configured")
            return
        
        try:
            # Изолированный registry для batch job
            registry = CollectorRegistry()
            
            # Gauges с low cardinality labels
            pipeline_health_up = Gauge('pipeline_health_up', 'Pipeline health status',
                                       ['component'], registry=registry)
            
            redis_stream_lag = Gauge('redis_stream_lag', 'Redis stream lag',
                                     ['stream', 'group'], registry=registry)
            
            redis_stream_pending = Gauge('redis_stream_pending', 'Redis stream pending',
                                         ['stream', 'group'], registry=registry)
            
            indexing_gap_pct = Gauge('indexing_gap_pct', 'Indexing gap percentage',
                                     ['target'], registry=registry)
            
            # Установка значений
            pipeline_health_up.labels(component='database').set(1 if self.db_healthy else 0)
            pipeline_health_up.labels(component='redis').set(1 if self.redis_healthy else 0)
            pipeline_health_up.labels(component='qdrant').set(1 if self.qdrant_healthy else 0)
            pipeline_health_up.labels(component='neo4j').set(1 if self.neo4j_healthy else 0)
            
            for stream, groups_data in self.results.get('redis_streams', {}).items():
                if isinstance(groups_data, dict):
                    for group, data in groups_data.items():
                        if isinstance(data, dict) and 'lag' in data:
                            redis_stream_lag.labels(stream=stream, group=group).set(data.get('lag', 0))
                            redis_stream_pending.labels(stream=stream, group=group).set(data.get('pending', 0))
            
            indexing_gap_pct.labels(target='qdrant').set(self.results['qdrant'].get('gap_pct', 0))
            neo4j_gaps = self.results.get('neo4j', {}).get('gaps', {})
            indexing_gap_pct.labels(target='neo4j_tags').set(neo4j_gaps.get('tags_gap_pct', 0))
            indexing_gap_pct.labels(target='neo4j_vision').set(neo4j_gaps.get('vision_gap_pct', 0))
            
            # Push с grouping key и timeout
            push_to_gateway(
                gateway=self.prometheus_gateway_url,
                job='pipeline_health_check',
                registry=registry,
                grouping_key={'instance': os.getenv('HOSTNAME', 'unknown')},
                timeout=10
            )
            
            logger.info("Metrics pushed to Pushgateway", gateway=self.prometheus_gateway_url)
        
        except Exception as e:
            logger.error("Failed to push metrics", error=str(e))
    
    async def run_all_checks(self):
        """Запуск всех проверок в зависимости от режима."""
        logger.info("Starting pipeline health check", mode=self.mode, trace_id=self.trace_id)
        
        await self.initialize()
        
        try:
            if self.mode == "smoke":
                # Только базовая проверка сервисов
                await self.check_database_health()
                await self.check_redis_streams()
            elif self.mode == "e2e":
                await self.check_database_health()
                await self.check_redis_streams()
                await self.check_qdrant()
                await self.check_neo4j()
            else:  # deep
                await self.check_database_health()
                await self.check_redis_streams()
                await self.check_qdrant()
                await self.check_neo4j()
        finally:
            await self.cleanup()
        
        # Генерация отчётов
        await self.generate_report()
        
        # Push metrics to Prometheus
        self.push_metrics_to_gateway()
        
        return self.results


# ============================================================================
# CLI И MAIN
# ============================================================================

def load_thresholds(thresholds_file: Optional[str]) -> dict:
    """Загрузка порогов из JSON с ENV override."""
    # Context7: Дефолтные пороги с реалистичными значениями для dev
    default_thresholds = {
        "posts_per_hour_min": 1,
        "posts_per_30min_min": 0,
        "redis_pending_max": 100,
        "redis_lag_max": 1000,
        "indexing_failed_max_pct": 20,
        "indexing_pending_max_pct": 30,
        "qdrant_gap_max_pct": 10,
        "neo4j_gap_max_pct": 20,
        "enrichment_tags_min_pct": 50,
        "enrichment_vision_min_pct": 10,
        "enrichment_crawl_min_pct": 10
    }
    
    # Загрузка из JSON
    if thresholds_file and os.path.exists(thresholds_file):
        try:
            with open(thresholds_file, 'r') as f:
                loaded = json.load(f)
                default_thresholds.update(loaded)
                logger.info("Thresholds loaded from file", path=thresholds_file)
        except Exception as e:
            logger.warning("Failed to load thresholds file", path=thresholds_file, error=str(e))
    elif not thresholds_file:
        # Автоматическая загрузка из config/
        default_config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'slo_thresholds.json')
        if os.path.exists(default_config_path):
            try:
                with open(default_config_path, 'r') as f:
                    loaded = json.load(f)
                    default_thresholds.update(loaded)
                    logger.info("Thresholds loaded from default config", path=default_config_path)
            except Exception as e:
                logger.warning("Failed to load default thresholds file", path=default_config_path, error=str(e))
    
    # ENV override (ПРИОРИТЕТ)
    for key in default_thresholds:
        env_value = os.getenv(f"SLO_{key.upper()}")
        if env_value:
            try:
                default_thresholds[key] = float(env_value)
                logger.info("Threshold overridden from ENV", key=key, value=default_thresholds[key])
            except ValueError:
                logger.warning("Invalid ENV threshold value", key=key, value=env_value)
    
    return default_thresholds


def parse_args():
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(description="Pipeline health check с Context7")
    parser.add_argument(
        "--mode",
        choices=["smoke", "e2e", "deep"],
        default="e2e",
        help="Режим проверки (smoke ≤30с, e2e ≤90с, deep ≤5мин)"
    )
    parser.add_argument(
        "--window",
        type=int,
        default=3600,
        help="Временное окно в секундах для анализа (по умолчанию 3600s = 1 час)"
    )
    parser.add_argument(
        "--output-json",
        type=str,
        help="Путь для сохранения JSON результата"
    )
    parser.add_argument(
        "--output-md",
        type=str,
        help="Путь для сохранения Markdown результата"
    )
    parser.add_argument(
        "--prometheus-pushgateway",
        type=str,
        help="URL Pushgateway для отправки метрик (опционально)"
    )
    parser.add_argument(
        "--thresholds-file",
        type=str,
        help="Путь к JSON файлу с порогами SLO"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести только JSON без форматирования"
    )
    parser.add_argument(
        "--no-exit-nonzero",
        action="store_true",
        help="Не возвращать ненулевой exit code при ошибках"
    )
    return parser.parse_args()


async def main():
    """Главная функция."""
    args = parse_args()
    
    # Загрузка порогов
    thresholds = load_thresholds(args.thresholds_file)
    
    checker = PipelineHealthChecker(
        mode=args.mode,
        window_seconds=args.window,
        thresholds=thresholds
    )
    
    # Настройка выходных путей
    checker.output_json = args.output_json
    checker.output_md = args.output_md
    checker.prometheus_gateway_url = args.prometheus_pushgateway or os.getenv("PROMETHEUS_PUSHGATEWAY_URL")
    
    try:
        # Глобальный таймаут на режим
        timeout = 30 if args.mode == "smoke" else 90 if args.mode == "e2e" else 300
        results = await asyncio.wait_for(
            checker.run_all_checks(),
            timeout=timeout
        )
        
        # Вывод результатов
        if not args.json:
            print("\n" + "="*80)
            print("PIPELINE HEALTH CHECK RESULTS")
            print("="*80)
            print(json.dumps(results, indent=2, default=str))
            print("="*80)
        else:
            print(json.dumps(results, default=str))
        
        # Оценка результата
        breaches = results.get('breaches', [])
        all_ok = len(breaches) == 0
        
        if not args.json:
            if all_ok:
                print("✅ Pipeline is healthy")
            else:
                print(f"❌ {len(breaches)} breach(es) found:")
                for b in breaches[:5]:
                    print(f"  - {b['type']}: {b.get('actual', 0):.2f} vs {b.get('threshold', 0)}")
        
        exit_code = 0 if (all_ok or args.no_exit_nonzero) else 1
        sys.exit(exit_code)
    
    except asyncio.TimeoutError:
        logger.error("Health check timeout", timeout=timeout, mode=args.mode)
        print(f"❌ Health check timeout after {timeout}s", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.error("Health check failed", error=str(e), exc_info=True)
        print(f"❌ Health check failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

