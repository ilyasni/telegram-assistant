"""
Endpoint для проверки здоровья пайплайна обработки постов.

Context7: Комплексная проверка всех этапов пайплайна с метриками.
"""

from fastapi import APIRouter
from typing import Dict, Any, List, Optional
import asyncio
import structlog
import time
from datetime import datetime, timezone, timedelta

import redis.asyncio as redis
from prometheus_client import Counter, Gauge

logger = structlog.get_logger()

router = APIRouter()

# Context7: Метрики Prometheus для пайплайна
posts_parsed_total = Gauge(
    'pipeline_posts_parsed_total',
    'Total posts parsed in pipeline',
)

posts_tagged_total = Gauge(
    'pipeline_posts_tagged_total',
    'Total posts tagged in pipeline',
)

posts_vision_total = Gauge(
    'pipeline_posts_vision_total',
    'Total posts with vision analysis',
)

posts_enriched_total = Gauge(
    'pipeline_posts_enriched_total',
    'Total posts enriched',
)

posts_indexed_total = Gauge(
    'pipeline_posts_indexed_total',
    'Total posts indexed in Qdrant',
)

pipeline_lag_seconds = Gauge(
    'pipeline_lag_seconds',
    'Pipeline lag between stages in seconds',
    ['stage']  # stage: parsed->tagged, tagged->enriched, enriched->indexed
)

redis_stream_pending = Gauge(
    'redis_stream_pending_messages',
    'Pending messages in Redis streams',
    ['stream']
)


async def get_redis_client():
    """Создать Redis клиент."""
    from config import settings
    return redis.from_url(settings.redis_url, decode_responses=True)


async def get_db_pool():
    """Получить пул соединений с БД."""
    from config import settings
    import asyncpg
    
    # Конвертируем SQLAlchemy URL в asyncpg DSN
    dsn = settings.database_url
    if dsn.startswith('postgresql+asyncpg://'):
        dsn = dsn.replace('postgresql+asyncpg://', 'postgresql://', 1)
    elif dsn.startswith('postgresql://'):
        pass  # Уже правильный формат
    
    return await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=10,
        command_timeout=30
    )


async def check_redis_streams(redis_client) -> Dict[str, Any]:
    """
    Проверка Redis Streams пайплайна.
    
    Context7: Проверка лагов и pending сообщений в streams.
    """
    streams = [
        'stream:posts:parsed',
        'stream:posts:tagged',
        'stream:posts:vision:analyzed',
        'stream:posts:enriched',
        'stream:posts:indexed',
    ]
    
    result = {
        'streams': {},
        'total_pending': 0,
        'max_lag_seconds': 0
    }
    
    for stream_name in streams:
        try:
            # Получаем длину stream
            stream_len = await redis_client.xlen(stream_name)
            
            # Получаем информацию о группах
            groups_info = await redis_client.xinfo_groups(stream_name)
            
            pending_total = 0
            for group in groups_info:
                # Обработка разных форматов ответа
                if isinstance(group, dict):
                    pending = group.get(b'pending', 0) or group.get('pending', 0)
                elif isinstance(group, list):
                    g_dict = dict(zip(group[::2], group[1::2]))
                    pending = g_dict.get(b'pending', 0) or g_dict.get('pending', 0)
                else:
                    pending = 0
                pending = int(pending) if pending else 0
                pending_total += pending
                
                # Обновляем метрику
                redis_stream_pending.labels(stream=stream_name).set(pending)
            
            result['streams'][stream_name] = {
                'length': stream_len,
                'pending': pending_total
            }
            result['total_pending'] += pending_total
            
        except Exception as e:
            logger.warning("Failed to check stream", stream=stream_name, error=str(e))
            result['streams'][stream_name] = {'error': str(e)}
    
    return result


async def check_database_stats(db_pool) -> Dict[str, Any]:
    """
    Проверка статистики пайплайна в БД.
    
    Context7: Агрегированная статистика по всем этапам пайплайна.
    """
    async with db_pool.acquire() as conn:
        # Статистика постов
        posts_stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total_posts,
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour,
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as last_24h,
                MAX(created_at) as newest_post,
                MIN(created_at) as oldest_post
            FROM posts
        """)
        
        # Статистика обогащений
        enrichment_stats = await conn.fetchrow("""
            SELECT 
                COUNT(DISTINCT post_id) FILTER (WHERE kind = 'tags' AND status = 'ok') as posts_with_tags,
                COUNT(DISTINCT post_id) FILTER (WHERE kind = 'vision' AND status = 'ok') as posts_with_vision,
                COUNT(DISTINCT post_id) FILTER (WHERE kind = 'crawl' AND status = 'ok') as posts_with_crawl,
                COUNT(DISTINCT post_id) FILTER (WHERE indexing_status = 'indexed') as posts_indexed
            FROM post_enrichment
        """)
        
        # Обновляем метрики
        posts_parsed_total.set(posts_stats['total_posts'] or 0)
        posts_tagged_total.set(enrichment_stats['posts_with_tags'] or 0)
        posts_vision_total.set(enrichment_stats['posts_with_vision'] or 0)
        posts_enriched_total.set(enrichment_stats['posts_with_crawl'] or 0)
        posts_indexed_total.set(enrichment_stats['posts_indexed'] or 0)
        
        return {
            'posts': {
                'total': posts_stats['total_posts'],
                'last_hour': posts_stats['last_hour'],
                'last_24h': posts_stats['last_24h'],
                'newest': posts_stats['newest_post'].isoformat() if posts_stats['newest_post'] else None,
            },
            'enrichments': {
                'tags': enrichment_stats['posts_with_tags'],
                'vision': enrichment_stats['posts_with_vision'],
                'crawl': enrichment_stats['posts_with_crawl'],
                'indexed': enrichment_stats['posts_indexed'],
            }
        }


async def check_qdrant() -> Dict[str, Any]:
    """
    Проверка Qdrant коллекций.
    
    Context7: Проверка коллекций и количества векторов.
    """
    try:
        from qdrant_client import QdrantClient
        from config import settings
        
        qdrant_url = getattr(settings, 'qdrant_url', 'http://qdrant:6333')
        client = QdrantClient(url=qdrant_url)
        
        collections = client.get_collections()
        
        total_vectors = 0
        collections_info = {}
        
        for collection in collections.collections:
            collection_info = client.get_collection(collection.name)
            vectors_count = collection_info.points_count
            total_vectors += vectors_count
            
            collections_info[collection.name] = {
                'vectors_count': vectors_count,
                'status': collection_info.status
            }
        
        return {
            'collections': collections_info,
            'total_vectors': total_vectors,
            'collections_count': len(collections_info)
        }
        
    except Exception as e:
        logger.error("Failed to check Qdrant", error=str(e))
        return {'error': str(e)}


async def check_neo4j() -> Dict[str, Any]:
    """
    Проверка Neo4j графа.
    
    Context7: Проверка узлов и связей в графе.
    """
    try:
        from neo4j import AsyncGraphDatabase
        from config import settings
        
        neo4j_uri = getattr(settings, 'neo4j_uri', 'neo4j://neo4j:7687')
        neo4j_user = getattr(settings, 'neo4j_user', 'neo4j')
        neo4j_password = getattr(settings, 'neo4j_password', 'changeme')
        
        driver = AsyncGraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password)
        )
        
        async with driver.session() as session:
            # Статистика узлов
            posts_result = await session.run("MATCH (p:Post) RETURN count(p) as count")
            posts_record = await posts_result.single()
            posts_count = posts_record['count'] if posts_record else 0
            
            tags_result = await session.run("MATCH (t:Tag) RETURN count(t) as count")
            tags_record = await tags_result.single()
            tags_count = tags_record['count'] if tags_record else 0
            
            # Статистика связей
            tagged_result = await session.run("""
                MATCH (:Post)-[r:TAGGED_AS]->(:Tag) 
                RETURN count(r) as count
            """)
            tagged_record = await tagged_result.single()
            tagged_count = tagged_record['count'] if tagged_record else 0
            
            vision_result = await session.run("""
                MATCH (:Post)-[r:HAS_VISION]->() 
                RETURN count(r) as count
            """)
            vision_record = await vision_result.single()
            vision_count = vision_record['count'] if vision_record else 0
        
        await driver.close()
        
        return {
            'nodes': {
                'posts': posts_count,
                'tags': tags_count,
            },
            'relationships': {
                'tagged_as': tagged_count,
                'has_vision': vision_count,
            }
        }
        
    except Exception as e:
        logger.error("Failed to check Neo4j", error=str(e))
        return {'error': str(e)}


@router.get("/health")
async def pipeline_health():
    """
    Комплексная проверка здоровья пайплайна.
    
    Context7: Агрегированная информация о состоянии всех этапов пайплайна.
    """
    start_time = time.time()
    
    try:
        # Параллельные проверки для производительности
        redis_client = await get_redis_client()
        db_pool = await get_db_pool()
        
        redis_streams_result, db_stats_result, qdrant_result, neo4j_result = await asyncio.gather(
            check_redis_streams(redis_client),
            check_database_stats(db_pool),
            check_qdrant(),
            check_neo4j(),
            return_exceptions=True
        )
        
        # Обработка исключений
        if isinstance(redis_streams_result, Exception):
            redis_streams_result = {'error': str(redis_streams_result)}
        if isinstance(db_stats_result, Exception):
            db_stats_result = {'error': str(db_stats_result)}
        if isinstance(qdrant_result, Exception):
            qdrant_result = {'error': str(qdrant_result)}
        if isinstance(neo4j_result, Exception):
            neo4j_result = {'error': str(neo4j_result)}
        
        # Закрываем соединения
        await redis_client.aclose()
        await db_pool.close()
        
        # Формируем ответ
        duration = time.time() - start_time
        
        return {
            'status': 'healthy',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'duration_seconds': round(duration, 3),
            'pipeline': {
                'redis_streams': redis_streams_result,
                'database': db_stats_result,
                'qdrant': qdrant_result,
                'neo4j': neo4j_result,
            },
            'summary': {
                'total_pending': redis_streams_result.get('total_pending', 0) if isinstance(redis_streams_result, dict) else 0,
                'has_errors': any(
                    isinstance(result, dict) and 'error' in result
                    for result in [redis_streams_result, db_stats_result, qdrant_result, neo4j_result]
                )
            }
        }
        
    except Exception as e:
        logger.error("Pipeline health check failed", error=str(e), exc_info=True)
        return {
            'status': 'error',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'error': str(e)
        }

