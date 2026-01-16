#!/usr/bin/env python3
"""
Комплексная проверка всего пайплайна постов и альбомов.

Context7: Полная проверка всех этапов от парсинга до сохранения в БД, Qdrant и Neo4j.
Проверяет:
- Парсинг постов и альбомов
- Vision анализ
- Тегирование
- Обогащение с Crawl4AI
- Сохранение в БД
- Индексация в Qdrant
- Индексация в Neo4j
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import json

import asyncpg
import redis.asyncio as redis
import structlog

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
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/telegram_assistant")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")


class ComprehensivePipelineChecker:
    """Context7: Комплексная проверка всего пайплайна."""
    
    def __init__(self):
        self.db_pool: Optional[asyncpg.Pool] = None
        self.redis_client: Optional[redis.Redis] = None
        self.results: Dict[str, Any] = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'checks': {},
            'summary': {},
            'issues': []
        }
    
    async def initialize(self):
        """Инициализация подключений."""
        try:
            # Конвертируем SQLAlchemy URL в asyncpg DSN
            dsn = DATABASE_URL
            if dsn.startswith('postgresql+asyncpg://'):
                dsn = dsn.replace('postgresql+asyncpg://', 'postgresql://', 1)
            elif dsn.startswith('postgresql://'):
                pass
            
            self.db_pool = await asyncpg.create_pool(
                dsn,
                min_size=2,
                max_size=10,
                command_timeout=30
            )
            
            self.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            
            logger.info("Initialized connections", db_pool=bool(self.db_pool), redis=bool(self.redis_client))
            
        except Exception as e:
            logger.error("Failed to initialize", error=str(e), exc_info=True)
            raise
    
    async def cleanup(self):
        """Закрытие подключений."""
        if self.db_pool:
            await self.db_pool.close()
        if self.redis_client:
            await self.redis_client.aclose()
    
    async def check_parsing(self) -> Dict[str, Any]:
        """Проверка парсинга постов и альбомов."""
        logger.info("Checking parsing stage")
        
        result = {
            'status': 'unknown',
            'posts': {},
            'albums': {},
            'issues': []
        }
        
        try:
            async with self.db_pool.acquire() as conn:
                # Статистика постов
                posts_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as last_24h,
                        MAX(created_at) as newest,
                        MIN(created_at) as oldest
                    FROM posts
                """)
                
                result['posts'] = {
                    'total': posts_stats['total'],
                    'last_hour': posts_stats['last_hour'],
                    'last_24h': posts_stats['last_24h'],
                    'newest': posts_stats['newest'].isoformat() if posts_stats['newest'] else None,
                    'oldest': posts_stats['oldest'].isoformat() if posts_stats['oldest'] else None
                }
                
                # Статистика альбомов (media_groups)
                albums_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as last_24h
                    FROM media_groups
                """)
                
                result['albums'] = {
                    'total': albums_stats['total'],
                    'last_hour': albums_stats['last_hour'],
                    'last_24h': albums_stats['last_24h']
                }
                
                # Проверка Redis Streams для парсинга
                parsed_stream_len = await self.redis_client.xlen('stream:posts:parsed')
                
                result['redis_stream'] = {
                    'stream:posts:parsed': parsed_stream_len
                }
                
                # Оценка статуса
                if posts_stats['last_24h'] and posts_stats['last_24h'] > 0:
                    result['status'] = 'healthy'
                elif posts_stats['total'] and posts_stats['total'] > 0:
                    result['status'] = 'degraded'
                    result['issues'].append("Нет новых постов за последние 24 часа")
                else:
                    result['status'] = 'unhealthy'
                    result['issues'].append("Нет постов в базе данных")
                
                logger.info("Parsing check complete", status=result['status'])
                
        except Exception as e:
            logger.error("Failed to check parsing", error=str(e), exc_info=True)
            result['status'] = 'error'
            result['issues'].append(f"Ошибка проверки: {str(e)}")
        
        return result
    
    async def check_vision_analysis(self) -> Dict[str, Any]:
        """Проверка Vision анализа."""
        logger.info("Checking vision analysis")
        
        result = {
            'status': 'unknown',
            'streams': {},
            'db_enrichments': {},
            'issues': []
        }
        
        try:
            # Проверка Redis Streams
            uploaded_len = await self.redis_client.xlen('stream:posts:vision:uploaded')
            analyzed_len = await self.redis_client.xlen('stream:posts:vision:analyzed')
            
            result['streams'] = {
                'uploaded': uploaded_len,
                'analyzed': analyzed_len
            }
            
            # Проверка enrichments в БД
            async with self.db_pool.acquire() as conn:
                vision_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(DISTINCT post_id) as total_posts,
                        COUNT(*) FILTER (WHERE status = 'ok') as successful,
                        COUNT(*) FILTER (WHERE status = 'error') as failed,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour
                    FROM post_enrichment
                    WHERE kind = 'vision'
                """)
                
                result['db_enrichments'] = {
                    'total_posts': vision_stats['total_posts'],
                    'successful': vision_stats['successful'],
                    'failed': vision_stats['failed'],
                    'last_hour': vision_stats['last_hour']
                }
                
                # Проверка лага между uploaded и analyzed
                if uploaded_len > 0 and analyzed_len > 0:
                    lag = uploaded_len - analyzed_len
                    if lag > 100:
                        result['issues'].append(f"Высокий lag между uploaded и analyzed: {lag}")
                
                # Оценка статуса
                if vision_stats['total_posts'] and vision_stats['total_posts'] > 0:
                    success_rate = vision_stats['successful'] / vision_stats['total_posts'] if vision_stats['total_posts'] > 0 else 0
                    if success_rate > 0.9:
                        result['status'] = 'healthy'
                    elif success_rate > 0.5:
                        result['status'] = 'degraded'
                        result['issues'].append(f"Низкий success rate: {success_rate:.2%}")
                    else:
                        result['status'] = 'unhealthy'
                        result['issues'].append(f"Очень низкий success rate: {success_rate:.2%}")
                else:
                    result['status'] = 'unhealthy'
                    result['issues'].append("Нет vision enrichments в БД")
                
                logger.info("Vision analysis check complete", status=result['status'])
                
        except Exception as e:
            logger.error("Failed to check vision analysis", error=str(e), exc_info=True)
            result['status'] = 'error'
            result['issues'].append(f"Ошибка проверки: {str(e)}")
        
        return result
    
    async def check_tagging(self) -> Dict[str, Any]:
        """Проверка тегирования."""
        logger.info("Checking tagging")
        
        result = {
            'status': 'unknown',
            'enrichments': {},
            'streams': {},
            'issues': []
        }
        
        try:
            # Проверка Redis Streams
            tagged_len = await self.redis_client.xlen('stream:posts:tagged')
            
            result['streams'] = {
                'stream:posts:tagged': tagged_len
            }
            
            # Проверка enrichments в БД
            async with self.db_pool.acquire() as conn:
                tags_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(DISTINCT post_id) as total_posts,
                        COUNT(*) FILTER (WHERE status = 'ok') as successful,
                        COUNT(*) FILTER (WHERE status = 'error') as failed,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour
                    FROM post_enrichment
                    WHERE kind = 'tags'
                """)
                
                # Проверка постов с тегами
                posts_with_tags = await conn.fetchval("""
                    SELECT COUNT(*) 
                    FROM posts 
                    WHERE tags IS NOT NULL AND jsonb_array_length(tags) > 0
                """)
                
                result['enrichments'] = {
                    'total_posts': tags_stats['total_posts'],
                    'successful': tags_stats['successful'],
                    'failed': tags_stats['failed'],
                    'last_hour': tags_stats['last_hour'],
                    'posts_with_tags_in_posts_table': posts_with_tags
                }
                
                # Оценка статуса
                if tags_stats['total_posts'] and tags_stats['total_posts'] > 0:
                    success_rate = tags_stats['successful'] / tags_stats['total_posts'] if tags_stats['total_posts'] > 0 else 0
                    if success_rate > 0.9:
                        result['status'] = 'healthy'
                    else:
                        result['status'] = 'degraded'
                        result['issues'].append(f"Success rate: {success_rate:.2%}")
                else:
                    result['status'] = 'unhealthy'
                    result['issues'].append("Нет tag enrichments в БД")
                
                logger.info("Tagging check complete", status=result['status'])
                
        except Exception as e:
            logger.error("Failed to check tagging", error=str(e), exc_info=True)
            result['status'] = 'error'
            result['issues'].append(f"Ошибка проверки: {str(e)}")
        
        return result
    
    async def check_enrichment_crawl4ai(self) -> Dict[str, Any]:
        """Проверка обогащения через Crawl4AI."""
        logger.info("Checking Crawl4AI enrichment")
        
        result = {
            'status': 'unknown',
            'enrichments': {},
            'streams': {},
            'issues': []
        }
        
        try:
            # Проверка Redis Streams
            crawl_stream_len = await self.redis_client.xlen('stream:posts:crawl')
            enriched_len = await self.redis_client.xlen('stream:posts:enriched')
            
            result['streams'] = {
                'stream:posts:crawl': crawl_stream_len,
                'stream:posts:enriched': enriched_len
            }
            
            # Проверка enrichments в БД
            async with self.db_pool.acquire() as conn:
                crawl_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(DISTINCT post_id) as total_posts,
                        COUNT(*) FILTER (WHERE status = 'ok') as successful,
                        COUNT(*) FILTER (WHERE status = 'error') as failed,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as last_24h
                    FROM post_enrichment
                    WHERE kind = 'crawl'
                """)
                
                result['enrichments'] = {
                    'total_posts': crawl_stats['total_posts'],
                    'successful': crawl_stats['successful'],
                    'failed': crawl_stats['failed'],
                    'last_hour': crawl_stats['last_hour'],
                    'last_24h': crawl_stats['last_24h']
                }
                
                # Оценка статуса
                if crawl_stats['total_posts'] and crawl_stats['total_posts'] > 0:
                    success_rate = crawl_stats['successful'] / crawl_stats['total_posts'] if crawl_stats['total_posts'] > 0 else 0
                    if success_rate > 0.8:
                        result['status'] = 'healthy'
                    elif success_rate > 0.5:
                        result['status'] = 'degraded'
                        result['issues'].append(f"Success rate: {success_rate:.2%}")
                    else:
                        result['status'] = 'unhealthy'
                        result['issues'].append(f"Низкий success rate: {success_rate:.2%}")
                else:
                    result['status'] = 'warning'
                    result['issues'].append("Нет crawl enrichments (может быть нормально, если не все посты требуют обогащения)")
                
                logger.info("Crawl4AI enrichment check complete", status=result['status'])
                
        except Exception as e:
            logger.error("Failed to check Crawl4AI enrichment", error=str(e), exc_info=True)
            result['status'] = 'error'
            result['issues'].append(f"Ошибка проверки: {str(e)}")
        
        return result
    
    async def check_database_save(self) -> Dict[str, Any]:
        """Проверка сохранения в БД."""
        logger.info("Checking database save")
        
        result = {
            'status': 'unknown',
            'posts': {},
            'enrichments': {},
            'issues': []
        }
        
        try:
            async with self.db_pool.acquire() as conn:
                # Проверка постов
                posts_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour,
                        COUNT(*) FILTER (WHERE text IS NOT NULL AND text != '') as with_text,
                        COUNT(*) FILTER (WHERE tags IS NOT NULL AND jsonb_array_length(tags) > 0) as with_tags
                    FROM posts
                """)
                
                result['posts'] = {
                    'total': posts_stats['total'],
                    'last_hour': posts_stats['last_hour'],
                    'with_text': posts_stats['with_text'],
                    'with_tags': posts_stats['with_tags']
                }
                
                # Проверка enrichments
                enrichments_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(DISTINCT post_id) as unique_posts,
                        COUNT(DISTINCT kind) as unique_kinds,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour
                    FROM post_enrichment
                """)
                
                result['enrichments'] = {
                    'total': enrichments_stats['total'],
                    'unique_posts': enrichments_stats['unique_posts'],
                    'unique_kinds': enrichments_stats['unique_kinds'],
                    'last_hour': enrichments_stats['last_hour']
                }
                
                # Оценка статуса
                if posts_stats['total'] and posts_stats['total'] > 0:
                    result['status'] = 'healthy'
                    if posts_stats['last_hour'] == 0:
                        result['issues'].append("Нет новых постов за последний час")
                else:
                    result['status'] = 'unhealthy'
                    result['issues'].append("Нет постов в базе данных")
                
                logger.info("Database save check complete", status=result['status'])
                
        except Exception as e:
            logger.error("Failed to check database save", error=str(e), exc_info=True)
            result['status'] = 'error'
            result['issues'].append(f"Ошибка проверки: {str(e)}")
        
        return result
    
    async def check_qdrant_indexing(self) -> Dict[str, Any]:
        """Проверка индексации в Qdrant."""
        logger.info("Checking Qdrant indexing")
        
        result = {
            'status': 'unknown',
            'collections': {},
            'points': {},
            'issues': []
        }
        
        try:
            from qdrant_client import QdrantClient
            
            client = QdrantClient(url=QDRANT_URL)
            
            # Получить список коллекций
            collections = client.get_collections()
            
            total_vectors = 0
            collections_info = {}
            
            for collection in collections.collections:
                collection_info = client.get_collection(collection.name)
                vectors_count = collection_info.points_count
                total_vectors += vectors_count
                
                collections_info[collection.name] = {
                    'points_count': vectors_count,
                    'status': collection_info.status
                }
            
            result['collections'] = collections_info
            result['points'] = {
                'total': total_vectors
            }
            
            # Проверка соответствия с БД
            async with self.db_pool.acquire() as conn:
                indexed_posts = await conn.fetchval("""
                    SELECT COUNT(DISTINCT post_id)
                    FROM post_enrichment
                    WHERE indexing_status = 'indexed'
                """)
                
                result['db_indexed'] = indexed_posts
                
                # Оценка статуса
                if total_vectors > 0:
                    if indexed_posts and abs(total_vectors - indexed_posts) / max(total_vectors, 1) < 0.1:
                        result['status'] = 'healthy'
                    else:
                        result['status'] = 'warning'
                        result['issues'].append(f"Несоответствие: Qdrant={total_vectors}, БД={indexed_posts}")
                else:
                    result['status'] = 'unhealthy'
                    result['issues'].append("Нет векторов в Qdrant")
                
                logger.info("Qdrant indexing check complete", status=result['status'])
                
        except Exception as e:
            logger.error("Failed to check Qdrant indexing", error=str(e), exc_info=True)
            result['status'] = 'error'
            result['issues'].append(f"Ошибка проверки: {str(e)}")
        
        return result
    
    async def check_neo4j_indexing(self) -> Dict[str, Any]:
        """Проверка индексации в Neo4j."""
        logger.info("Checking Neo4j indexing")
        
        result = {
            'status': 'unknown',
            'nodes': {},
            'relationships': {},
            'issues': []
        }
        
        try:
            from neo4j import AsyncGraphDatabase
            
            driver = AsyncGraphDatabase.driver(
                NEO4J_URI,
                auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            
            async with driver.session() as session:
                # Статистика узлов
                posts_result = await session.run("MATCH (p:Post) RETURN count(p) as count")
                posts_record = await posts_result.single()
                posts_count = posts_record['count'] if posts_record else 0
                
                channels_result = await session.run("MATCH (c:Channel) RETURN count(c) as count")
                channels_record = await channels_result.single()
                channels_count = channels_record['count'] if channels_record else 0
                
                # Статистика связей
                tagged_result = await session.run("""
                    MATCH (:Post)-[r:TAGGED_AS]->(:Tag) 
                    RETURN count(r) as count
                """)
                tagged_record = await tagged_result.single()
                tagged_count = tagged_record['count'] if tagged_record else 0
                
                result['nodes'] = {
                    'posts': posts_count,
                    'channels': channels_count
                }
                
                result['relationships'] = {
                    'tagged_as': tagged_count
                }
                
                # Оценка статуса
                if posts_count > 0:
                    result['status'] = 'healthy'
                else:
                    result['status'] = 'unhealthy'
                    result['issues'].append("Нет постов в Neo4j")
                
                logger.info("Neo4j indexing check complete", status=result['status'])
            
            await driver.close()
                
        except Exception as e:
            logger.error("Failed to check Neo4j indexing", error=str(e), exc_info=True)
            result['status'] = 'error'
            result['issues'].append(f"Ошибка проверки: {str(e)}")
        
        return result
    
    async def check_album_pipeline(self) -> Dict[str, Any]:
        """Проверка пайплайна альбомов."""
        logger.info("Checking album pipeline")
        
        result = {
            'status': 'unknown',
            'albums': {},
            'streams': {},
            'issues': []
        }
        
        try:
            # Проверка Redis Streams
            albums_parsed_len = await self.redis_client.xlen('stream:albums:parsed')
            albums_assembled_len = await self.redis_client.xlen('stream:albums:assembled')
            
            result['streams'] = {
                'stream:albums:parsed': albums_parsed_len,
                'stream:albums:assembled': albums_assembled_len
            }
            
            # Проверка альбомов в БД
            async with self.db_pool.acquire() as conn:
                albums_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as last_24h,
                        COUNT(*) FILTER (WHERE meta->>'enrichment' IS NOT NULL) as with_enrichment
                    FROM media_groups
                """)
                
                result['albums'] = {
                    'total': albums_stats['total'],
                    'last_24h': albums_stats['last_24h'],
                    'with_enrichment': albums_stats['with_enrichment']
                }
                
                # Оценка статуса
                if albums_stats['total'] and albums_stats['total'] > 0:
                    result['status'] = 'healthy'
                else:
                    result['status'] = 'warning'
                    result['issues'].append("Нет альбомов (может быть нормально)")
                
                logger.info("Album pipeline check complete", status=result['status'])
                
        except Exception as e:
            logger.error("Failed to check album pipeline", error=str(e), exc_info=True)
            result['status'] = 'error'
            result['issues'].append(f"Ошибка проверки: {str(e)}")
        
        return result
    
    async def run_all_checks(self):
        """Выполнить все проверки."""
        logger.info("Starting comprehensive pipeline check")
        
        try:
            await self.initialize()
            
            # Выполняем все проверки параллельно где возможно
            parsing_result, vision_result, tagging_result = await asyncio.gather(
                self.check_parsing(),
                self.check_vision_analysis(),
                self.check_tagging(),
                return_exceptions=True
            )
            
            enrichment_result, db_result, qdrant_result = await asyncio.gather(
                self.check_enrichment_crawl4ai(),
                self.check_database_save(),
                self.check_qdrant_indexing(),
                return_exceptions=True
            )
            
            neo4j_result, album_result = await asyncio.gather(
                self.check_neo4j_indexing(),
                self.check_album_pipeline(),
                return_exceptions=True
            )
            
            # Сохраняем результаты
            self.results['checks'] = {
                'parsing': parsing_result if not isinstance(parsing_result, Exception) else {'status': 'error', 'error': str(parsing_result)},
                'vision_analysis': vision_result if not isinstance(vision_result, Exception) else {'status': 'error', 'error': str(vision_result)},
                'tagging': tagging_result if not isinstance(tagging_result, Exception) else {'status': 'error', 'error': str(tagging_result)},
                'enrichment_crawl4ai': enrichment_result if not isinstance(enrichment_result, Exception) else {'status': 'error', 'error': str(enrichment_result)},
                'database_save': db_result if not isinstance(db_result, Exception) else {'status': 'error', 'error': str(db_result)},
                'qdrant_indexing': qdrant_result if not isinstance(qdrant_result, Exception) else {'status': 'error', 'error': str(qdrant_result)},
                'neo4j_indexing': neo4j_result if not isinstance(neo4j_result, Exception) else {'status': 'error', 'error': str(neo4j_result)},
                'album_pipeline': album_result if not isinstance(album_result, Exception) else {'status': 'error', 'error': str(album_result)}
            }
            
            # Формируем summary
            all_statuses = [check.get('status', 'unknown') for check in self.results['checks'].values()]
            healthy_count = sum(1 for s in all_statuses if s == 'healthy')
            total_count = len(all_statuses)
            
            self.results['summary'] = {
                'total_checks': total_count,
                'healthy': healthy_count,
                'degraded': sum(1 for s in all_statuses if s == 'degraded'),
                'unhealthy': sum(1 for s in all_statuses if s == 'unhealthy'),
                'errors': sum(1 for s in all_statuses if s == 'error'),
                'overall_status': 'healthy' if healthy_count == total_count else 'degraded' if healthy_count > total_count / 2 else 'unhealthy'
            }
            
            # Собираем все проблемы
            for check_name, check_result in self.results['checks'].items():
                if isinstance(check_result, dict) and 'issues' in check_result:
                    for issue in check_result['issues']:
                        self.results['issues'].append({
                            'check': check_name,
                            'issue': issue
                        })
            
            logger.info("Comprehensive pipeline check complete", summary=self.results['summary'])
            
        finally:
            await self.cleanup()
    
    def print_report(self):
        """Вывести отчет в консоль."""
        print("\n" + "="*80)
        print("COMPREHENSIVE PIPELINE CHECK REPORT")
        print("="*80)
        print(f"Timestamp: {self.results['timestamp']}")
        print(f"\nOverall Status: {self.results['summary']['overall_status'].upper()}")
        print(f"Healthy: {self.results['summary']['healthy']}/{self.results['summary']['total_checks']}")
        
        print("\n" + "-"*80)
        print("CHECKS DETAILS")
        print("-"*80)
        
        for check_name, check_result in self.results['checks'].items():
            if isinstance(check_result, dict):
                status = check_result.get('status', 'unknown')
                status_icon = "✅" if status == 'healthy' else "⚠️" if status == 'degraded' else "❌"
                print(f"\n{status_icon} {check_name.upper().replace('_', ' ')}: {status}")
                
                if 'issues' in check_result and check_result['issues']:
                    for issue in check_result['issues']:
                        print(f"   - {issue}")
        
        if self.results['issues']:
            print("\n" + "-"*80)
            print("ALL ISSUES")
            print("-"*80)
            for issue_item in self.results['issues']:
                print(f"  [{issue_item['check']}] {issue_item['issue']}")
        
        print("\n" + "="*80)


async def main():
    """Главная функция."""
    checker = ComprehensivePipelineChecker()
    
    try:
        await checker.run_all_checks()
        checker.print_report()
        
        # Сохранить результат в JSON
        output_file = f"reports/pipeline_check_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        os.makedirs('reports', exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(checker.results, f, indent=2, default=str)
        
        print(f"\n📄 Full report saved to: {output_file}")
        
        # Вернуть код выхода
        if checker.results['summary']['overall_status'] == 'healthy':
            sys.exit(0)
        elif checker.results['summary']['overall_status'] == 'degraded':
            sys.exit(1)
        else:
            sys.exit(2)
            
    except Exception as e:
        logger.error("Pipeline check failed", error=str(e), exc_info=True)
        sys.exit(3)


if __name__ == "__main__":
    asyncio.run(main())

