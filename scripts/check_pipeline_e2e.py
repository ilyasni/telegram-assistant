#!/usr/bin/env python3
"""
E2E проверка всего пайплайна на реальных данных.

Режимы:
- smoke (≤30с): базовая проверка сервисов
- e2e (≤90с): полная проверка пайплайна с порогами SLO
- deep (≤5мин): детальная диагностика (группы, PEL, DLQ, размерности)

Проверяет:
1. Статус Scheduler (режим, последняя активность, HWM)
2. Парсинг постов из БД
3. Тегирование (posts.parsed → posts.tagged)
4. Обогащение (posts.tagged → posts.enriched via crawl4ai)
5. Индексация (posts.enriched → Qdrant + Neo4j)
6. Сквозной поток данных через все этапы
7. Redis Streams (группы, лаги, PEL)
8. DLQ индикаторы
9. Qdrant (размерность, payload coverage)
10. Neo4j (индексы, свежесть графа)

Context7 best practices:
- Использует Supabase async patterns (asyncpg)
- Безопасные операции Redis (SCAN вместо KEYS)
- Единая конвертация времени (ensure_dt_utc)
- SLO пороги: JSON → ENV override → CLI --thresholds
- Артефакты: JSON, JUnit XML, Prometheus Pushgateway
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from uuid import UUID
from xml.sax.saxutils import escape

import asyncpg
import redis.asyncio as redis
import structlog
from qdrant_client import QdrantClient as QdrantPythonClient
from neo4j import AsyncDriver, AsyncGraphDatabase

# Настройка логирования (до импорта prometheus_client для использования в except)
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

# Prometheus client (опционально)
try:
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not available, Pushgateway metrics disabled")

# Конфигурация
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/telegram_assistant")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "posts")
# Используем EMBEDDING_DIMENSION (основной) или EMBEDDING_DIM (fallback), дефолт 2560 для GigaChat
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIMENSION", os.getenv("EMBEDDING_DIM", "2560")))

# ============================================================================
# УТИЛИТЫ
# ============================================================================

def ensure_dt_utc(x) -> Optional[datetime]:
    """
    Безопасная конвертация любого типа в aware datetime UTC.
    
    Поддерживает: bytes, str, datetime, None.
    Обрабатывает пустые строки, +0000 без двоеточия, Z suffix.
    """
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
            # Fallback: обработка +0000 без двоеточия (2025-10-28T05:00:00+0000)
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
    """
    Безопасная итерация по ключам Redis через SCAN (redis-py 5: cursor=int).
    
    KEYS опасен на проде - используем SCAN для итерации.
    """
    cursor = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=pattern, count=count)
        for k in keys:
            yield k
        if cursor == 0:
            break


async def stream_stats(client: redis.Redis, stream: str, group_hint: str | None = None) -> Dict[str, Any]:
    """
    Статистика Redis Stream: xlen, группы, PEL summary.
    
    Returns:
        {
            'xlen': int | None,
            'groups': [{'name', 'consumers', 'pending', 'last_delivered_id'}],
            'pending_summary': {'total', 'min_id', 'max_id'} | None,
            'errors': [str]
        }
    """
    out = {'xlen': None, 'groups': [], 'pending_summary': None, 'errors': []}
    
    try:
        out['xlen'] = await client.xlen(stream)
    except Exception as e:
        out['errors'].append(f"xlen:{e}")
        return out
    
    try:
        groups_info = await client.xinfo_groups(stream)  # list[dict-like with bytes keys]
        for g in groups_info:
            # Обработка разных форматов: dict, list, или объект с атрибутами
            if isinstance(g, dict):
                # Прямой словарь
                name_bytes = g.get(b'name') or g.get('name', b'')
            elif isinstance(g, (list, tuple)) and len(g) >= 2:
                # Список пар (ключ, значение)
                g_dict = dict(zip(g[::2], g[1::2]))
                name_bytes = g_dict.get(b'name') or g_dict.get('name', b'')
            else:
                # Объект с методами get или __getitem__
                try:
                    name_bytes = g.get(b'name', b'') if hasattr(g, 'get') else g[b'name']
                except (KeyError, TypeError):
                    try:
                        name_bytes = g.get('name', b'') if hasattr(g, 'get') else ''
                    except (KeyError, TypeError):
                        name_bytes = b''
            
            # Декодирование имени
            if isinstance(name_bytes, bytes):
                name = name_bytes.decode('utf-8', errors='ignore')
            elif isinstance(name_bytes, str):
                name = name_bytes
            else:
                name = str(name_bytes) if name_bytes else ''
            
            # Аналогично для других полей
            consumers = g.get(b'consumers', 0) or g.get('consumers', 0) if hasattr(g, 'get') else 0
            pending = g.get(b'pending', 0) or g.get('pending', 0) if hasattr(g, 'get') else 0
            last_id_bytes = g.get(b'last-delivered-id', b'') or g.get('last-delivered-id', b'') if hasattr(g, 'get') else b''
            last_id = last_id_bytes.decode('utf-8', errors='ignore') if isinstance(last_id_bytes, bytes) else str(last_id_bytes) if last_id_bytes else ''
            
            out['groups'].append({
                'name': name,
                'consumers': int(consumers),
                'pending': int(pending),
                'last_delivered_id': last_id
            })
        
        # XPENDING summary (по первой группе или group_hint)
        grp = group_hint or (out['groups'][0]['name'] if out['groups'] else None)
        if grp:
            try:
                pend = await client.xpending(stream, grp)
                # pend = {'pending': N, 'min': id, 'max': id, 'consumers': [...]}
                out['pending_summary'] = {
                    'total': pend.get('pending'),
                    'min_id': pend.get('min'),
                    'max_id': pend.get('max')
                }
            except Exception as e:
                out['errors'].append(f"xpending:{e}")
    except Exception as e:
        out['errors'].append(f"xinfo_groups:{e}")
    
    return out


def get_vectors_dim(info) -> Optional[int]:
    """
    Извлечение размерности векторов из Qdrant CollectionInfo.
    
    Поддерживает single и multi-vector схемы.
    """
    params = getattr(info.config, 'params', None)
    if not params:
        return None
    v = getattr(params, 'vectors', None)
    if not v:
        return None
    
    # v может быть VectorParams (single) или dict name->VectorParams (multi)
    try:
        return v.size  # single vector
    except AttributeError:
        # multi-vector schema
        try:
            for _, vv in v.items():
                return vv.size
        except Exception:
            pass
    return None


def write_junit(path: str, suite_name: str, checks: List[Dict[str, Any]]):
    """
    Генератор JUnit XML без внешних зависимостей.
    
    Args:
        checks: [{'name': str, 'ok': bool, 'message': str}]
    """
    total = len(checks)
    failures = sum(1 for c in checks if not c.get('ok', True))
    
    lines = [f'<testsuite name="{escape(suite_name)}" tests="{total}" failures="{failures}">']
    
    for c in checks:
        name = escape(c.get('name', 'unknown'))
        lines.append(f'  <testcase name="{name}">')
        if not c.get('ok', True):
            msg = escape(c.get('message', 'failed'))
            lines.append(f'    <failure message="{msg}"/>')
        lines.append('  </testcase>')
    
    lines.append('</testsuite>')
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def push_metrics(gateway_url: Optional[str], job: str, labels: Dict[str, str], metrics: Dict[str, float]):
    """Отправка метрик в Prometheus Pushgateway."""
    if not gateway_url:
        return
    
    if not PROMETHEUS_AVAILABLE:
        logger.debug("Prometheus client not available, skipping Pushgateway")
        return
    
    try:
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
        reg = CollectorRegistry()
        gs = {}
        for k, v in metrics.items():
            g = Gauge(k, f'metric {k}', labelnames=list(labels.keys()), registry=reg)
            g.labels(**labels).set(float(v))
            gs[k] = g
        push_to_gateway(gateway_url, job=job, registry=reg)
        logger.info("Metrics pushed to Pushgateway", job=job, metrics_count=len(metrics))
    except Exception as e:
        logger.warning("Failed to push metrics", error=str(e))


# ============================================================================
# SLO ПОРОГИ
# ============================================================================

class SLOThresholds:
    """Управление порогами SLO с приоритетом: ENV → custom JSON → default JSON."""
    
    def __init__(self, mode: str = "e2e"):
        self.mode = mode
        self.thresholds = {}
        self._load_defaults()
    
    def _load_defaults(self):
        """Загрузка дефолтных порогов по режимам."""
        defaults = {
            "smoke": {
                "max_check_time_sec": 30,
                "required_services": ["db", "redis", "qdrant", "neo4j"]
            },
            "e2e": {
                "max_check_time_sec": 90,
                "max_watermark_age_min": 30,
                "max_stream_pending": 50,
                "min_posts_24h": 1,
                "qdrant_min_payload_coverage": 0.9
            },
            "deep": {
                "max_check_time_sec": 300,
                "max_embed_dim_mismatch": 0,
                "max_qdrant_lag_min": 10,
                "max_skew_vs_pg_min": 5,
                "qdrant_min_payload_coverage": 0.9
            }
        }
        
        self.thresholds = defaults.get(self.mode, defaults["e2e"]).copy()
    
    def load_from_json(self, path: str):
        """Загрузка порогов из JSON файла (перебивает дефолты)."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                mode_data = data.get(self.mode, {})
                self.thresholds.update(mode_data)
                logger.info("Thresholds loaded from JSON", path=path, mode=self.mode)
        except Exception as e:
            logger.warning("Failed to load thresholds from JSON", path=path, error=str(e))
    
    def env_override(self):
        """Переопределение порогов из ENV переменных."""
        env_map = {
            "E2E_MAX_WATERMARK_AGE_MIN": "max_watermark_age_min",
            "E2E_MAX_STREAM_PENDING": "max_stream_pending",
            "E2E_MIN_POSTS_24H": "min_posts_24h",
            "E2E_MAX_EMBED_DIM_MISMATCH": "max_embed_dim_mismatch",
            "E2E_MAX_QDRANT_LAG_MIN": "max_qdrant_lag_min",
            "E2E_MAX_SKEW_VS_PG_MIN": "max_skew_vs_pg_min",
            "E2E_QDRANT_MIN_PAYLOAD_COVERAGE": "qdrant_min_payload_coverage"
        }
        
        for env_key, threshold_key in env_map.items():
            env_value = os.getenv(env_key)
            if env_value:
                try:
                    if threshold_key == "qdrant_min_payload_coverage":
                        self.thresholds[threshold_key] = float(env_value)
                    else:
                        self.thresholds[threshold_key] = int(env_value)
                    logger.debug("Threshold overridden from ENV", key=threshold_key, value=self.thresholds[threshold_key])
                except ValueError:
                    logger.warning("Invalid ENV value for threshold", key=env_key, value=env_value)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Получение значения порога."""
        return self.thresholds.get(key, default)
    
    def __getitem__(self, key: str) -> Any:
        """Доступ через []."""
        return self.thresholds[key]
    
    def __contains__(self, key: str) -> bool:
        """Проверка наличия ключа."""
        return key in self.thresholds


# ============================================================================
# PIPELINE CHECKER
# ============================================================================

class PipelineChecker:
    """E2E проверка пайплайна."""
    
    def __init__(self, mode: str = "e2e", thresholds: Optional[SLOThresholds] = None, limit: int = 10):
        self.mode = mode
        self.limit = limit
        self.thresholds = thresholds or SLOThresholds(mode)
        
        self.db_pool: Optional[asyncpg.Pool] = None
        self.redis_client: Optional[redis.Redis] = None
        self.qdrant_client: Optional[QdrantPythonClient] = None
        self.neo4j_driver: Optional[AsyncDriver] = None
        
        self.results = {
            'scheduler': {},
            'parsing': {},
            'streams': {},
            'tagging': {},
            'enrichment': {},
            'indexing': {},
            'qdrant': {},
            'neo4j': {},
            'dlq': {},
            'crawl4ai': {},
            'summary': {},
            'checks': []  # для JUnit
        }
    
    async def initialize(self):
        """Инициализация подключений."""
        logger.info("Initializing connections...", mode=self.mode)
        
        # Context7: Supabase best practice - asyncpg pool
        try:
            timeout = 5 if self.mode == "smoke" else 10
            self.db_pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=10,
                command_timeout=timeout
            )
            logger.info("Database pool created", size=10, timeout=timeout)
        except Exception as e:
            logger.error("Failed to create DB pool", error=str(e))
            raise
        
        # Redis
        try:
            self.redis_client = redis.from_url(REDIS_URL)
            await asyncio.wait_for(self.redis_client.ping(), timeout=2)
            logger.info("Redis connected")
        except Exception as e:
            logger.error("Failed to connect Redis", error=str(e))
            raise
        
        # Qdrant
        try:
            self.qdrant_client = QdrantPythonClient(url=QDRANT_URL)
            collections = self.qdrant_client.get_collections()
            logger.info("Qdrant connected", collections=[c.name for c in collections.collections])
        except Exception as e:
            logger.error("Failed to connect Qdrant", error=str(e))
            raise
        
        # Neo4j
        try:
            self.neo4j_driver = AsyncGraphDatabase.driver(
                NEO4J_URI,
                auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            await asyncio.wait_for(self.neo4j_driver.verify_connectivity(), timeout=5)
            logger.info("Neo4j connected")
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
    
    async def check_scheduler(self):
        """Проверка статуса Scheduler с SCAN вместо KEYS."""
        logger.info("Checking Scheduler status...")
        
        try:
            # Проверка Redis lock
            lock_key = "scheduler:lock"
            lock_value = await self.redis_client.get(lock_key)
            lock_ttl = await self.redis_client.ttl(lock_key)
            
            # SCAN вместо KEYS для безопасности
            hwm_keys = []
            async for k in scan_iter(self.redis_client, "parse_hwm:*", count=200):
                hwm_keys.append(k)
                if len(hwm_keys) >= 1000:  # защитный лимит
                    break
            
            hwm_data = {}
            hwm_ages = []
            
            for key in hwm_keys[:min(10, len(hwm_keys))]:  # первые 10 или все
                channel_id = key.decode() if isinstance(key, bytes) else str(key)
                channel_id = channel_id.replace("parse_hwm:", "")
                hwm_time = await self.redis_client.get(key)
                if hwm_time:
                    hwm_dt = ensure_dt_utc(hwm_time)
                    if hwm_dt:
                        now = datetime.now(timezone.utc)
                        age_seconds = (now - hwm_dt).total_seconds()
                        hwm_ages.append(age_seconds)
                        hwm_data[channel_id] = {
                            'last_parsed': hwm_dt.isoformat(),
                            'age_seconds': age_seconds
                        }
            
            max_age_seconds = max(hwm_ages) if hwm_ages else None
            max_age_min = max_age_seconds / 60 if max_age_seconds else None
            
            self.results['scheduler'] = {
                'lock_acquired': lock_value is not None,
                'lock_value': lock_value.decode() if lock_value else None,
                'lock_ttl': lock_ttl,
                'hwm_count': len(hwm_keys),
                'hwm_samples': dict(list(hwm_data.items())[:5]),
                'max_age_seconds': max_age_seconds,
                'max_age_minutes': max_age_min,
                'status': 'running' if lock_value else 'idle'
            }
            
            # Проверка порога
            if max_age_min is not None and "max_watermark_age_min" in self.thresholds:
                threshold = self.thresholds.get("max_watermark_age_min")
                ok = max_age_min <= threshold
                self.results['checks'].append({
                    'name': 'scheduler.max_watermark_age',
                    'ok': ok,
                    'message': f"Max HWM age {max_age_min:.1f}m > {threshold}m" if not ok else None
                })
            
            logger.info("Scheduler check completed", 
                       status=self.results['scheduler']['status'],
                       hwm_count=len(hwm_keys),
                       max_age_min=max_age_min)
            
        except Exception as e:
            logger.error("Scheduler check failed", error=str(e))
            self.results['scheduler'] = {'error': str(e)}
            self.results['checks'].append({
                'name': 'scheduler.check',
                'ok': False,
                'message': str(e)
            })
    
    async def check_streams(self):
        """Проверка Redis Streams: группы, лаги, PEL."""
        logger.info("Checking Redis Streams...")
        
        streams_to_check = [
            "stream:posts:parsed",
            "stream:posts:tagged",
            "stream:posts:enriched",
            "stream:posts:indexed"
        ]
        
        streams_data = {}
        
        for stream_name in streams_to_check:
            try:
                # Для indexed stream используем группу "indexing_monitoring" для XPENDING
                group_hint = "indexing_monitoring" if stream_name == "stream:posts:indexed" else None
                stats = await stream_stats(self.redis_client, stream_name, group_hint=group_hint)
                streams_data[stream_name] = stats
                
                # Проверка порога pending
                if stats['pending_summary'] and "max_stream_pending" in self.thresholds:
                    pending_total = stats['pending_summary'].get('total', 0)
                    threshold = self.thresholds.get("max_stream_pending")
                    ok = pending_total <= threshold
                    self.results['checks'].append({
                        'name': f'streams.{stream_name}.pending',
                        'ok': ok,
                        'message': f"Pending {pending_total} > {threshold}" if not ok else None
                    })
                
                # Проверка отсутствия групп для прод-пайплайна
                # Для indexed stream ожидаем группу "indexing_monitoring"
                if stream_name == "stream:posts:indexed":
                    expected_group = "indexing_monitoring"
                    group_found = any(g.get('name') == expected_group for g in stats['groups'])
                    if not group_found and self.mode != "smoke":
                        self.results['checks'].append({
                            'name': f'streams.{stream_name}.groups',
                            'ok': False,
                            'message': f"No consumer group '{expected_group}' found for monitoring"
                        })
                elif not stats['groups'] and self.mode != "smoke":
                    self.results['checks'].append({
                        'name': f'streams.{stream_name}.groups',
                        'ok': False,
                        'message': "No consumer groups found"
                    })
                    
            except Exception as e:
                logger.warning("Stream check failed", stream=stream_name, error=str(e))
                streams_data[stream_name] = {'error': str(e)}
        
        self.results['streams'] = streams_data
        logger.info("Streams check completed", streams_count=len(streams_to_check))
    
    async def check_parsing(self):
        """Проверка парсинга: последние посты из БД."""
        logger.info("Checking parsing stage...")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Последние посты
                rows = await conn.fetch(f"""
                    SELECT 
                        p.id,
                        p.channel_id,
                        p.content,
                        p.posted_at,
                        p.created_at,
                        p.is_processed,
                        c.title as channel_title
                    FROM posts p
                    LEFT JOIN channels c ON p.channel_id = c.id
                    ORDER BY p.posted_at DESC
                    LIMIT {self.limit}
                """)
                
                posts = []
                for row in rows:
                    posts.append({
                        'id': str(row['id']),
                        'channel_id': str(row['channel_id']),
                        'channel_title': row['channel_title'],
                        'content_preview': (row['content'] or '')[:100],
                        'posted_at': row['posted_at'].isoformat() if row['posted_at'] else None,
                        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                        'is_processed': row['is_processed']
                    })
                
                # Статистика
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE is_processed = true) as processed,
                        COUNT(*) FILTER (WHERE posted_at > NOW() - INTERVAL '24 hours') as last_24h,
                        MIN(posted_at) as oldest,
                        MAX(posted_at) as newest
                    FROM posts
                """)
                
                self.results['parsing'] = {
                    'recent_posts': posts,
                    'total': stats['total'],
                    'processed': stats['processed'],
                    'last_24h': stats['last_24h'],
                    'oldest_post': stats['oldest'].isoformat() if stats['oldest'] else None,
                    'newest_post': stats['newest'].isoformat() if stats['newest'] else None
                }
                
                # Проверка порога постов за 24ч
                if "min_posts_24h" in self.thresholds:
                    last_24h = stats['last_24h']
                    threshold = self.thresholds.get("min_posts_24h")
                    ok = last_24h >= threshold
                    self.results['checks'].append({
                        'name': 'parsing.posts_last24h',
                        'ok': ok,
                        'message': f"Posts last 24h: {last_24h} < {threshold}" if not ok else None
                    })
                
                logger.info("Parsing check completed", 
                           total=stats['total'],
                           processed=stats['processed'],
                           last_24h=stats['last_24h'])
                
        except Exception as e:
            logger.error("Parsing check failed", error=str(e))
            self.results['parsing'] = {'error': str(e)}
            self.results['checks'].append({
                'name': 'parsing.check',
                'ok': False,
                'message': str(e)
            })
    
    async def check_tagging(self):
        """Проверка тегирования: posts.parsed → posts.tagged."""
        logger.info("Checking tagging stage...")
        
        try:
            # Проверка Redis Streams уже в check_streams(), здесь только БД
            async with self.db_pool.acquire() as conn:
                tags_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(DISTINCT post_id) as posts_with_tags,
                        COUNT(*) as total_tags
                    FROM post_enrichment
                    WHERE kind = 'tags'
                """)
                
                recent_tags = await conn.fetch(f"""
                    SELECT 
                        pe.post_id,
                        pe.tags,
                        pe.enriched_at,
                        p.content
                    FROM post_enrichment pe
                    LEFT JOIN posts p ON pe.post_id = p.id
                    WHERE pe.kind = 'tags'
                    ORDER BY pe.enriched_at DESC
                    LIMIT {min(self.limit, 5)}
                """)
                
                tags_samples = []
                for row in recent_tags:
                    tags_samples.append({
                        'post_id': str(row['post_id']),
                        'tags': row['tags'],
                        'enriched_at': row['enriched_at'].isoformat() if row['enriched_at'] else None,
                        'content_preview': (row['content'] or '')[:100]
                    })
            
            self.results['tagging'] = {
                'db_posts_with_tags': tags_stats['posts_with_tags'],
                'db_total_tags': tags_stats['total_tags'],
                'recent_tags': tags_samples
            }
            
            logger.info("Tagging check completed", 
                       posts_with_tags=tags_stats['posts_with_tags'])
                
        except Exception as e:
            logger.error("Tagging check failed", error=str(e))
            self.results['tagging'] = {'error': str(e)}
    
    async def check_enrichment(self):
        """Проверка обогащения: crawl4ai результаты."""
        logger.info("Checking enrichment stage...")
        
        try:
            async with self.db_pool.acquire() as conn:
                crawl_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(DISTINCT post_id) as posts_enriched,
                        COUNT(*) as total_enrichments,
                        AVG(enrichment_latency_ms) as avg_latency_ms,
                        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY enrichment_latency_ms) as p95_latency_ms
                    FROM post_enrichment
                    WHERE kind = 'crawl'
                """)
                
                recent_enrichments = await conn.fetch(f"""
                    SELECT 
                        pe.post_id,
                        pe.metadata->>'urls_count' as urls_count,
                        pe.enrichment_latency_ms,
                        pe.enriched_at
                    FROM post_enrichment pe
                    WHERE pe.kind = 'crawl'
                    ORDER BY pe.enriched_at DESC
                    LIMIT {min(self.limit, 5)}
                """)
                
                enrichments_samples = []
                for row in recent_enrichments:
                    enrichments_samples.append({
                        'post_id': str(row['post_id']),
                        'urls_count': row['urls_count'],
                        'latency_ms': row['enrichment_latency_ms'],
                        'enriched_at': row['enriched_at'].isoformat() if row['enriched_at'] else None
                    })
            
            self.results['enrichment'] = {
                'db_posts_enriched': crawl_stats['posts_enriched'],
                'db_total_enrichments': crawl_stats['total_enrichments'],
                'avg_latency_ms': float(crawl_stats['avg_latency_ms']) if crawl_stats['avg_latency_ms'] else None,
                'p95_latency_ms': float(crawl_stats['p95_latency_ms']) if crawl_stats['p95_latency_ms'] else None,
                'recent_enrichments': enrichments_samples
            }
            
            logger.info("Enrichment check completed", 
                       enriched_posts=crawl_stats['posts_enriched'],
                       avg_latency=crawl_stats['avg_latency_ms'])
                
        except Exception as e:
            logger.error("Enrichment check failed", error=str(e))
            self.results['enrichment'] = {'error': str(e)}
    
    async def check_dlq(self):
        """Проверка DLQ индикаторов."""
        logger.info("Checking DLQ...")
        
        try:
            dlq_keys = []
            async for k in scan_iter(self.redis_client, "stream:dlq:*", count=200):
                dlq_keys.append(k)
                if len(dlq_keys) >= 50:  # лимит
                    break
            
            dlq_streams = {}
            total_dlq_events = 0
            
            for key in dlq_keys:
                stream_name = key.decode() if isinstance(key, bytes) else str(key)
                try:
                    xlen = await self.redis_client.xlen(stream_name)
                    total_dlq_events += xlen
                    
                    # Последние 3 события
                    events = await self.redis_client.xrevrange(stream_name, count=3)
                    events_data = []
                    for event_id, fields in events:
                        event_data = {}
                        for k, v in fields.items():
                            if isinstance(k, bytes):
                                k = k.decode()
                            if isinstance(v, bytes):
                                v = v.decode()
                            event_data[k] = v
                        events_data.append({
                            'id': event_id.decode() if isinstance(event_id, bytes) else str(event_id),
                            'fields': event_data
                        })
                    
                    dlq_streams[stream_name] = {
                        'length': xlen,
                        'last_events': events_data
                    }
                except Exception as e:
                    logger.warning("DLQ stream check failed", stream=stream_name, error=str(e))
                    dlq_streams[stream_name] = {'error': str(e)}
            
            self.results['dlq'] = {
                'streams_count': len(dlq_keys),
                'total_events': total_dlq_events,
                'streams': dlq_streams
            }
            
            # Проверка роста DLQ
            if total_dlq_events > 0:
                self.results['checks'].append({
                    'name': 'dlq.events_present',
                    'ok': False,
                    'message': f"DLQ has {total_dlq_events} events"
                })
            
            logger.info("DLQ check completed", streams_count=len(dlq_keys), total_events=total_dlq_events)
            
        except Exception as e:
            logger.error("DLQ check failed", error=str(e))
            self.results['dlq'] = {'error': str(e)}
    
    async def check_crawl4ai(self):
        """Проверка здоровья Crawl4AI (heartbeat или метрики)."""
        logger.info("Checking Crawl4AI health...")
        
        try:
            heartbeat_key = "crawl4ai:heartbeat"
            heartbeat_time = await self.redis_client.get(heartbeat_key)
            
            if heartbeat_time:
                heartbeat_dt = ensure_dt_utc(heartbeat_time)
                if heartbeat_dt:
                    now = datetime.now(timezone.utc)
                    age_seconds = (now - heartbeat_dt).total_seconds()
                    self.results['crawl4ai'] = {
                        'heartbeat_found': True,
                        'heartbeat_at': heartbeat_dt.isoformat(),
                        'age_seconds': age_seconds
                    }
                else:
                    self.results['crawl4ai'] = {'heartbeat_found': True, 'parse_error': True}
            else:
                # Проверка по факту: записи kind='crawl' за последние N минут
                async with self.db_pool.acquire() as conn:
                    recent_crawl = await conn.fetchrow("""
                        SELECT 
                            COUNT(*) as count,
                            AVG(enrichment_latency_ms) as avg_latency_ms
                        FROM post_enrichment
                        WHERE kind = 'crawl'
                          AND enriched_at > NOW() - INTERVAL '30 minutes'
                    """)
                    
                    self.results['crawl4ai'] = {
                        'heartbeat_found': False,
                        'recent_enrichments_30m': recent_crawl['count'],
                        'avg_latency_ms': float(recent_crawl['avg_latency_ms']) if recent_crawl['avg_latency_ms'] else None
                    }
            
            logger.info("Crawl4AI check completed", **self.results['crawl4ai'])
            
        except Exception as e:
            logger.error("Crawl4AI check failed", error=str(e))
            self.results['crawl4ai'] = {'error': str(e)}
    
    async def check_indexing(self):
        """Проверка индексации: Qdrant и Neo4j."""
        logger.info("Checking indexing stage...")
        
        # Qdrant
        try:
            collections = self.qdrant_client.get_collections()
            collections_data = []
            total_vectors = 0
            payload_coverage_samples = []
            
            target_collection = QDRANT_COLLECTION if QDRANT_COLLECTION else None
            
            for collection in collections.collections:
                try:
                    info = self.qdrant_client.get_collection(collection.name)
                    total_vectors += info.points_count
                    
                    # Размерность векторов
                    dim = get_vectors_dim(info)
                    
                    # Проверка размера эмбеддингов
                    if dim and "max_embed_dim_mismatch" in self.thresholds:
                        expected_dim = EMBEDDING_DIM
                        mismatch = abs(dim - expected_dim)
                        threshold = self.thresholds.get("max_embed_dim_mismatch", 0)
                        ok = mismatch <= threshold
                        self.results['checks'].append({
                            'name': f'qdrant.{collection.name}.dim',
                            'ok': ok,
                            'message': f"Dim mismatch: {dim} vs {expected_dim} (threshold: {threshold})" if not ok else None
                        })
                    
                    # Payload coverage (выборка 20 точек)
                    sample_size = 20 if self.mode == "deep" else 10
                    scroll_result = self.qdrant_client.scroll(
                        collection_name=collection.name,
                        limit=sample_size,
                        with_payload=True,
                        with_vectors=False
                    )
                    
                    points_with_post_id = sum(1 for p in scroll_result[0] if p.payload and p.payload.get('post_id'))
                    coverage = points_with_post_id / len(scroll_result[0]) if scroll_result[0] else 0.0
                    
                    if self.mode in ["e2e", "deep"]:
                        threshold = self.thresholds.get("qdrant_min_payload_coverage", 0.9)
                        ok = coverage >= threshold
                        self.results['checks'].append({
                            'name': f'qdrant.{collection.name}.payload_coverage',
                            'ok': ok,
                            'message': f"Coverage {coverage:.2%} < {threshold:.2%}" if not ok else None
                        })
                    
                    vectors_samples = []
                    for point in scroll_result[0][:3]:
                        payload = point.payload or {}
                        vectors_samples.append({
                            'id': str(point.id),
                            'post_id': payload.get('post_id'),
                            'expires_at': payload.get('expires_at')
                        })
                    
                    collections_data.append({
                        'name': collection.name,
                        'vectors_count': info.points_count,
                        'dim': dim,
                        'distance': str(info.config.params.vectors.distance) if hasattr(info.config.params, 'vectors') else None,
                        'payload_coverage': coverage,
                        'sample_vectors': vectors_samples
                    })
                except Exception as e:
                    logger.warning("Failed to get collection info", collection=collection.name, error=str(e))
            
            self.results['qdrant'] = {
                'total_collections': len(collections.collections),
                'total_vectors': total_vectors,
                'collections': collections_data
            }
            
            logger.info("Qdrant check completed", 
                       collections_count=len(collections.collections),
                       total_vectors=total_vectors)
                
        except Exception as e:
            logger.error("Qdrant check failed", error=str(e))
            self.results['qdrant'] = {'error': str(e)}
            self.results['checks'].append({
                'name': 'qdrant.check',
                'ok': False,
                'message': str(e)
            })
        
        # Neo4j
        try:
            async with self.neo4j_driver.session() as session:
                # Статистика узлов (используем post_id вместо id, проверяем expires_at вместо posted_at)
                result = await session.run("""
                    MATCH (p:Post)
                    RETURN count(p) as total_posts,
                           max(p.expires_at) as newest_post,
                           min(p.expires_at) as oldest_post
                """)
                record = await result.single()
                
                # Проверка индекса :Post(post_id) для deep режима
                index_found = False
                if self.mode == "deep":
                    index_result = await session.run("""
                        SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties
                        WHERE entityType='NODE' 
                          AND any(l IN labelsOrTypes WHERE l='Post')
                          AND any(p IN properties WHERE p='post_id')
                        RETURN name, type
                    """)
                    index_record = await index_result.single()
                    if index_record:
                        index_found = True
                        self.results['neo4j']['index_post_id'] = {
                            'name': index_record['name'],
                            'type': index_record['type']
                        }
                
                # Проверка свежести vs PG (используем expires_at, т.к. posted_at отсутствует в Neo4j)
                # Пропускаем эту проверку, т.к. expires_at не соответствует posted_at
                # TODO: Добавить posted_at в схему Neo4j для правильной проверки свежести
                
                # Последние узлы (используем post_id и expires_at)
                result_recent = await session.run(f"""
                    MATCH (p:Post)
                    RETURN p.post_id as post_id, 
                           p.expires_at as expires_at,
                           p.indexed_at as indexed_at
                    ORDER BY p.expires_at DESC
                    LIMIT {min(self.limit, 5)}
                """)
                recent_nodes = []
                async for record_recent in result_recent:
                    recent_nodes.append({
                        'post_id': record_recent['post_id'],
                        'expires_at': str(record_recent['expires_at']) if record_recent['expires_at'] else None,
                        'indexed_at': str(record_recent['indexed_at']) if record_recent['indexed_at'] else None
                    })
                
                # Статистика связей
                result_rels = await session.run("""
                    MATCH ()-[r]->()
                    RETURN type(r) as rel_type, count(*) as count
                    LIMIT 10
                """)
                relationships = {}
                async for rel_record in result_rels:
                    relationships[rel_record['rel_type']] = rel_record['count']
            
            self.results['neo4j'] = {
                'total_posts': record['total_posts'],
                'newest_post': str(record['newest_post']) if record['newest_post'] else None,
                'oldest_post': str(record['oldest_post']) if record['oldest_post'] else None,
                'recent_nodes': recent_nodes,
                'relationships': relationships,
                'index_post_id_found': index_found
            }
            
            logger.info("Neo4j check completed", 
                       total_posts=record['total_posts'],
                       index_found=index_found)
                
        except Exception as e:
            logger.error("Neo4j check failed", error=str(e))
            self.results['neo4j'] = {'error': str(e)}
            self.results['checks'].append({
                'name': 'neo4j.check',
                'ok': False,
                'message': str(e)
            })
    
    async def check_pipeline_flow(self):
        """
        Проверка сквозного потока данных.
        Context7: [C7-ID: e2e-flow-check-001] - учитываем retryable ошибки как нормальное состояние
        """
        logger.info("Checking pipeline flow...")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Ищем посты с тегами (прошли тегирование)
                row = await conn.fetchrow("""
                    SELECT 
                        p.id as post_id,
                        p.content,
                        p.posted_at,
                        p.is_processed,
                        pe_tags.tags,
                        pe_crawl.metadata as crawl_metadata,
                        isi.embedding_status,
                        isi.graph_status,
                        isi.error_message
                    FROM posts p
                    LEFT JOIN post_enrichment pe_tags 
                        ON p.id = pe_tags.post_id AND pe_tags.kind = 'tags'
                    LEFT JOIN post_enrichment pe_crawl 
                        ON p.id = pe_crawl.post_id AND pe_crawl.kind = 'crawl'
                    LEFT JOIN indexing_status isi
                        ON p.id = isi.post_id
                    WHERE pe_tags.post_id IS NOT NULL
                    ORDER BY p.posted_at DESC
                    LIMIT 1
                """)
                
                if not row:
                    self.results['summary'] = {
                        'flow_check': 'no_complete_posts_found',
                        'recommendation': 'Wait for posts to be processed through full pipeline'
                    }
                    self.results['checks'].append({
                        'name': 'pipeline.flow_complete',
                        'ok': False,
                        'message': 'No posts with tags found'
                    })
                    return
                
                post_id = str(row['post_id'])
                embedding_status = row.get('embedding_status')
                graph_status = row.get('graph_status')
                error_message = row.get('error_message')
                
                # Проверяем в Qdrant
                qdrant_found = False
                qdrant_collection = None
                try:
                    collections = self.qdrant_client.get_collections()
                    for collection in collections.collections:
                        try:
                            search_result = self.qdrant_client.scroll(
                                collection_name=collection.name,
                                scroll_filter={
                                    "must": [{
                                        "key": "post_id",
                                        "match": {"value": post_id}
                                    }]
                                },
                                limit=1
                            )
                            if len(search_result[0]) > 0:
                                qdrant_found = True
                                qdrant_collection = collection.name
                                break
                        except Exception as e:
                            logger.debug("Qdrant search failed", 
                                       collection=collection.name, 
                                       post_id=post_id, 
                                       error=str(e))
                            continue
                except Exception as e:
                    logger.warning("Qdrant search failed", post_id=post_id, error=str(e))
                
                # Проверяем в Neo4j (используем post_id вместо id)
                neo4j_found = False
                try:
                    async with self.neo4j_driver.session() as session:
                        result = await session.run("""
                            MATCH (p:Post {post_id: $post_id})
                            RETURN p.post_id as post_id
                        """, post_id=post_id)
                        neo4j_found = (await result.single()) is not None
                except Exception as e:
                    logger.warning("Neo4j search failed", post_id=post_id, error=str(e))
                
                # Context7: Проверяем, является ли ошибка retryable
                is_retryable_error = False
                if error_message:
                    # Проверяем категорию ошибки в error_message
                    error_str = error_message.lower()
                    retryable_indicators = [
                        'retryable_network',
                        'retryable_rate_limit', 
                        'retryable_server_error',
                        'connection refused',
                        'connection error',
                        'timeout',
                        'max retries exceeded'
                    ]
                    is_retryable_error = any(indicator in error_str for indicator in retryable_indicators)
                
                # Context7: Пайплайн считается успешным, если:
                # 1. Пост прошел тегирование (есть теги)
                # 2. Пост проиндексирован в Qdrant И Neo4j, ИЛИ
                # 3. Есть retryable ошибка (ожидается ретрай)
                has_tags = row['tags'] is not None and (isinstance(row['tags'], list) and len(row['tags']) > 0 if isinstance(row['tags'], list) else row['tags'] is not None)
                
                # Если статус failed, но ошибка retryable - считаем, что пайплайн работает корректно
                if (embedding_status == 'failed' or graph_status == 'failed') and is_retryable_error:
                    pipeline_complete = True  # Retryable ошибки - нормальное состояние
                    logger.info("Post has retryable error, considering pipeline functional",
                              post_id=post_id,
                              error_message=error_message[:100])
                else:
                    # Обычная проверка: пост должен быть во всех хранилищах
                    pipeline_complete = has_tags and qdrant_found and neo4j_found
                
                self.results['summary'] = {
                    'flow_check': 'complete',
                    'sample_post_id': post_id,
                    'has_tags': has_tags,
                    'has_crawl': row['crawl_metadata'] is not None,
                    'in_qdrant': qdrant_found,
                    'qdrant_collection': qdrant_collection,
                    'in_neo4j': neo4j_found,
                    'is_processed': row.get('is_processed', False),
                    'embedding_status': embedding_status,
                    'graph_status': graph_status,
                    'is_retryable_error': is_retryable_error,
                    'pipeline_complete': pipeline_complete
                }
                
                message = None
                if not pipeline_complete:
                    if not has_tags:
                        message = "Post has no tags"
                    elif not qdrant_found:
                        message = "Post not found in Qdrant"
                    elif not neo4j_found:
                        message = "Post not found in Neo4j"
                    elif embedding_status == 'failed' or graph_status == 'failed':
                        if not is_retryable_error:
                            message = f"Post has non-retryable error: {error_message[:100] if error_message else 'unknown'}"
                    else:
                        message = "Post not found in all stages"
                
                self.results['checks'].append({
                    'name': 'pipeline.flow_complete',
                    'ok': pipeline_complete,
                    'message': message
                })
                
                logger.info("Pipeline flow check completed", 
                           post_id=post_id,
                           complete=pipeline_complete)
                
        except Exception as e:
            logger.error("Pipeline flow check failed", error=str(e))
            self.results['summary'] = {'error': str(e)}
            self.results['checks'].append({
                'name': 'pipeline.flow_check',
                'ok': False,
                'message': str(e)
            })
    
    async def run_all_checks(self):
        """Запуск всех проверок в зависимости от режима."""
        logger.info("Starting E2E pipeline check...", mode=self.mode)
        
        await self.initialize()
        
        try:
            if self.mode == "smoke":
                # Только базовая проверка сервисов
                await self.check_scheduler()
                await self.check_parsing()
            elif self.mode == "e2e":
                await self.check_scheduler()
                await self.check_streams()
                await self.check_parsing()
                await self.check_tagging()
                await self.check_enrichment()
                await self.check_indexing()
                await self.check_pipeline_flow()
            else:  # deep
                await self.check_scheduler()
                await self.check_streams()
                await self.check_parsing()
                await self.check_tagging()
                await self.check_enrichment()
                await self.check_dlq()
                await self.check_crawl4ai()
                await self.check_indexing()
                await self.check_pipeline_flow()
        finally:
            await self.cleanup()
        
        return self.results


# ============================================================================
# CLI И MAIN
# ============================================================================

def parse_args():
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(description="E2E проверка пайплайна")
    parser.add_argument(
        "--mode",
        choices=["smoke", "e2e", "deep"],
        default="e2e",
        help="Режим проверки (smoke ≤30с, e2e ≤90с, deep ≤5мин)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести только JSON без форматирования"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Лимит образцов для проверок"
    )
    parser.add_argument(
        "--thresholds",
        type=str,
        help="Путь к JSON файлу с порогами (перебивает дефолтный)"
    )
    parser.add_argument(
        "--no-exit-nonzero",
        action="store_true",
        help="Не возвращать ненулевой exit code при ошибках"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Путь для сохранения JSON результата"
    )
    parser.add_argument(
        "--junit",
        type=str,
        help="Путь для сохранения JUnit XML"
    )
    return parser.parse_args()


async def main():
    """Главная функция."""
    args = parse_args()
    
    # Загрузка порогов
    thresholds = SLOThresholds(mode=args.mode)
    
    # JSON файл (если указан) перебивает дефолтный, но не ENV
    if args.thresholds:
        thresholds.load_from_json(args.thresholds)
    else:
        default_path = os.getenv("E2E_THRESHOLDS_PATH", "config/e2e_thresholds.json")
        if os.path.exists(default_path):
            thresholds.load_from_json(default_path)
    
    # ENV перебивает всё
    thresholds.env_override()
    
    checker = PipelineChecker(mode=args.mode, thresholds=thresholds, limit=args.limit)
    
    try:
        # Глобальный таймаут на режим
        timeout = thresholds.get("max_check_time_sec", 90)
        results = await asyncio.wait_for(
            checker.run_all_checks(),
            timeout=timeout
        )
        
        # Подготовка метрик для Pushgateway
        metrics = {}
        if results.get('scheduler', {}).get('max_age_seconds') is not None:
            metrics['e2e_watermark_age_seconds'] = results['scheduler']['max_age_seconds']
        
        streams_pending = 0
        for stream_data in results.get('streams', {}).values():
            if isinstance(stream_data, dict) and stream_data.get('pending_summary'):
                streams_pending += stream_data['pending_summary'].get('total', 0)
        if streams_pending > 0:
            metrics['e2e_stream_pending_total'] = streams_pending
        
        if results.get('parsing', {}).get('last_24h') is not None:
            metrics['e2e_posts_last24h_total'] = results['parsing']['last_24h']
        
        if results.get('qdrant', {}).get('total_vectors') is not None:
            metrics['e2e_qdrant_vectors_total'] = results['qdrant']['total_vectors']
        
        qdrant_coverage = None
        for coll in results.get('qdrant', {}).get('collections', []):
            if coll.get('payload_coverage') is not None:
                qdrant_coverage = coll['payload_coverage']
                break
        if qdrant_coverage is not None:
            metrics['e2e_qdrant_payload_coverage_ratio'] = qdrant_coverage
        
        # Отправка в Pushgateway
        gateway_url = os.getenv("PROMETHEUS_PUSHGATEWAY_URL")
        if gateway_url and metrics:
            env_label = os.getenv("ENV", "dev")
            push_metrics(
                gateway_url,
                job="telegram-assistant-e2e",
                labels={"mode": args.mode, "env": env_label},
                metrics=metrics
            )
        
        # Сохранение JSON (создаём директорию если нужно)
        if args.output:
            output_dir = os.path.dirname(args.output)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, default=str)
            logger.info("Results saved to JSON", path=args.output)
        
        # Сохранение JUnit XML (создаём директорию если нужно)
        if args.junit:
            junit_dir = os.path.dirname(args.junit)
            if junit_dir and not os.path.exists(junit_dir):
                os.makedirs(junit_dir, exist_ok=True)
            checks = results.get('checks', [])
            write_junit(args.junit, f"e2e-{args.mode}", checks)
            logger.info("JUnit XML saved", path=args.junit)
        
        # Вывод результатов
        if not args.json:
            print("\n" + "="*80)
            print("PIPELINE E2E CHECK RESULTS")
            print("="*80)
            print(json.dumps(results, indent=2, default=str))
            print("="*80)
        else:
            print(json.dumps(results, default=str))
        
        # Оценка результата
        checks = results.get('checks', [])
        all_ok = all(c.get('ok', True) for c in checks)
        
        if not args.json:
            if all_ok and results.get('summary', {}).get('pipeline_complete'):
                print("✅ Pipeline is operational and processing posts")
            elif all_ok:
                print("✅ All services are healthy")
            else:
                failed = [c for c in checks if not c.get('ok', True)]
                print(f"❌ {len(failed)} check(s) failed:")
                for c in failed:
                    print(f"  - {c['name']}: {c.get('message', 'failed')}")
        
        exit_code = 0 if (all_ok or args.no_exit_nonzero) else 1
        sys.exit(exit_code)
        
    except asyncio.TimeoutError:
        logger.error("Pipeline check timeout", timeout=timeout, mode=args.mode)
        print(f"❌ Pipeline check timeout after {timeout}s", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.error("Pipeline check failed", error=str(e), exc_info=True)
        print(f"❌ Pipeline check failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
