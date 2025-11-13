#!/usr/bin/env python3
"""
Скрипт диагностики поста в пайплайне обработки.

Проверяет:
- Наличие поста в БД
- Наличие тегов в post_enrichment
- Наличие enrichment данных (vision, crawl)
- Наличие записи в Qdrant
- Наличие записи в Neo4j
- Статус индексации (indexing_status)
- Наличие событий в Redis streams
- Использование эмбэдингов

Context7 best practices:
- Проверка через scroll в Qdrant по post_id (payload filter)
- Проверка через MATCH в Neo4j по post_id property
- Валидация структуры данных на каждом этапе
"""

import asyncio
import os
import sys
import json
import argparse
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import structlog
import psycopg2
from psycopg2.extras import RealDictCursor
import redis.asyncio as redis_async
from redis.asyncio import Redis

# Добавляем пути для импортов
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from integrations.qdrant_client import QdrantClient
from integrations.neo4j_client import Neo4jClient

logger = structlog.get_logger()

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

def get_env_config():
    """Получение конфигурации из переменных окружения."""
    return {
        'database_url': os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres"),
        'redis_url': os.getenv("REDIS_URL", "redis://redis:6379"),
        'qdrant_url': os.getenv("QDRANT_URL", "http://qdrant:6333"),
        'neo4j_uri': os.getenv("NEO4J_URI", "neo4j://neo4j:7687"),
        'neo4j_user': os.getenv("NEO4J_USER", "neo4j"),
        'neo4j_password': os.getenv("NEO4J_PASSWORD", "changeme"),
    }

# ============================================================================
# ДИАГНОСТИКА
# ============================================================================

class PostDiagnostic:
    """Диагностика поста в пайплайне."""
    
    def __init__(self, config: Dict[str, str]):
        self.config = config
        self.qdrant_client: Optional[QdrantClient] = None
        self.neo4j_client: Optional[Neo4jClient] = None
        self.redis_client: Optional[Redis] = None
        self.results: Dict[str, Any] = {}
    
    async def initialize(self):
        """Инициализация клиентов."""
        try:
            # Qdrant
            self.qdrant_client = QdrantClient(self.config['qdrant_url'])
            await self.qdrant_client.connect()
            
            # Neo4j
            self.neo4j_client = Neo4jClient(
                uri=self.config['neo4j_uri'],
                username=self.config['neo4j_user'],
                password=self.config['neo4j_password']
            )
            await self.neo4j_client.connect()
            
            # Redis
            self.redis_client = Redis.from_url(
                self.config['redis_url'],
                decode_responses=True
            )
            
            logger.info("Diagnostic clients initialized")
            
        except Exception as e:
            logger.error("Failed to initialize clients", error=str(e))
            raise
    
    async def close(self):
        """Закрытие клиентов."""
        if self.neo4j_client:
            await self.neo4j_client.close()
        if self.redis_client:
            await self.redis_client.close()
    
    async def diagnose(self, post_id: str) -> Dict[str, Any]:
        """
        Полная диагностика поста.
        
        Returns:
            Dict с результатами проверки всех этапов пайплайна
        """
        self.results = {
            'post_id': post_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'checks': {}
        }
        
        try:
            # 1. Проверка в БД
            await self._check_post_in_db(post_id)
            
            # 2. Проверка тегов
            await self._check_tags(post_id)
            
            # 3. Проверка enrichment данных
            await self._check_enrichment(post_id)
            
            # 4. Проверка статуса индексации
            await self._check_indexing_status(post_id)
            
            # 5. Проверка в Qdrant
            await self._check_qdrant(post_id)
            
            # 6. Проверка в Neo4j
            await self._check_neo4j(post_id)
            
            # 7. Проверка событий в Redis streams
            await self._check_redis_streams(post_id)
            
            # 8. Общая оценка
            self.results['summary'] = self._generate_summary()
            
        except Exception as e:
            logger.error("Diagnostic failed", post_id=post_id, error=str(e))
            self.results['error'] = str(e)
        
        return self.results
    
    async def _check_post_in_db(self, post_id: str):
        """Проверка поста в БД."""
        try:
            conn = psycopg2.connect(self.config['database_url'])
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT 
                    p.id,
                    p.channel_id,
                    p.content,
                    p.telegram_message_id,
                    p.created_at,
                    p.is_processed,
                    c.title as channel_title,
                    c.settings->>'tenant_id' as tenant_id
                FROM posts p
                JOIN channels c ON p.channel_id = c.id
                WHERE p.id = %s
            """, (post_id,))
            
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if row:
                self.results['checks']['post_in_db'] = {
                    'found': True,
                    'data': dict(row),
                    'tenant_id': str(row['tenant_id']),
                    'channel_id': str(row['channel_id']),
                    'is_processed': row['is_processed']
                }
            else:
                self.results['checks']['post_in_db'] = {
                    'found': False,
                    'error': 'Post not found in database'
                }
                
        except Exception as e:
            self.results['checks']['post_in_db'] = {
                'found': False,
                'error': str(e)
            }
    
    async def _check_tags(self, post_id: str):
        """Проверка тегов в post_enrichment."""
        try:
            conn = psycopg2.connect(self.config['database_url'])
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT 
                    data,
                    tags,
                    status,
                    provider,
                    updated_at
                FROM post_enrichment
                WHERE post_id = %s AND kind = 'tags'
                ORDER BY updated_at DESC
                LIMIT 1
            """, (post_id,))
            
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if row:
                tags_data = row['data']
                if isinstance(tags_data, str):
                    tags_data = json.loads(tags_data)
                
                tags_list = tags_data.get('tags', []) if tags_data else (row['tags'] or [])
                
                self.results['checks']['tags'] = {
                    'found': True,
                    'tags': tags_list if isinstance(tags_list, list) else [],
                    'tags_count': len(tags_list) if isinstance(tags_list, list) else 0,
                    'status': row['status'],
                    'provider': row['provider'],
                    'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
                    'has_data': bool(tags_data)
                }
            else:
                self.results['checks']['tags'] = {
                    'found': False,
                    'error': 'Tags not found in post_enrichment'
                }
                
        except Exception as e:
            self.results['checks']['tags'] = {
                'found': False,
                'error': str(e)
            }
    
    async def _check_enrichment(self, post_id: str):
        """Проверка enrichment данных (vision, crawl)."""
        try:
            conn = psycopg2.connect(self.config['database_url'])
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT 
                    kind,
                    data,
                    status,
                    provider,
                    updated_at
                FROM post_enrichment
                WHERE post_id = %s AND kind IN ('vision', 'crawl')
                ORDER BY kind, updated_at DESC
            """, (post_id,))
            
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            
            enrichment = {
                'vision': None,
                'crawl': None
            }
            
            for row in rows:
                if not row:
                    continue
                kind = row.get('kind')
                if not kind:
                    continue
                data = row.get('data')
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except:
                        data = None
                
                enrichment[kind] = {
                    'found': True,
                    'status': row.get('status'),
                    'provider': row.get('provider'),
                    'updated_at': row.get('updated_at').isoformat() if row.get('updated_at') else None,
                    'has_data': bool(data),
                    'data_keys': list(data.keys()) if data and isinstance(data, dict) else []
                }
            
            self.results['checks']['enrichment'] = enrichment
            
        except Exception as e:
            self.results['checks']['enrichment'] = {
                'error': str(e)
            }
    
    async def _check_indexing_status(self, post_id: str):
        """Проверка статуса индексации."""
        try:
            conn = psycopg2.connect(self.config['database_url'])
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT 
                    embedding_status,
                    graph_status,
                    vector_id,
                    error_message,
                    processing_started_at,
                    processing_completed_at
                FROM indexing_status
                WHERE post_id = %s
            """, (post_id,))
            
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if row:
                self.results['checks']['indexing_status'] = {
                    'found': True,
                    'embedding_status': row['embedding_status'],
                    'graph_status': row['graph_status'],
                    'vector_id': row['vector_id'],
                    'error_message': row['error_message'],
                    'processing_started_at': row['processing_started_at'].isoformat() if row['processing_started_at'] else None,
                    'processing_completed_at': row['processing_completed_at'].isoformat() if row['processing_completed_at'] else None,
                    'is_completed': (row['embedding_status'] == 'completed' and row['graph_status'] == 'completed')
                }
            else:
                self.results['checks']['indexing_status'] = {
                    'found': False,
                    'error': 'Indexing status not found'
                }
                
        except Exception as e:
            self.results['checks']['indexing_status'] = {
                'found': False,
                'error': str(e)
            }
    
    async def _check_qdrant(self, post_id: str):
        """
        Context7 best practice: Проверка наличия в Qdrant через scroll с фильтром по post_id.
        
        Используем scroll для поиска точки по payload.post_id, так как это надёжнее,
        чем прямое получение по ID (ID может быть в другом формате).
        """
        try:
            # Получаем tenant_id из результатов проверки БД
            tenant_id = None
            if 'post_in_db' in self.results['checks']:
                post_check = self.results['checks']['post_in_db']
                if post_check.get('found'):
                    tenant_id = post_check.get('tenant_id') or post_check.get('data', {}).get('tenant_id')
            
            # Если tenant_id известен, проверяем конкретную коллекцию
            collections_to_check = []
            if tenant_id:
                collections_to_check.append(f"t{tenant_id}_posts")
            else:
                # Проверяем все коллекции постов
                collections = self.qdrant_client.client.get_collections()
                collections_to_check = [
                    col.name for col in collections.collections
                    if (col.name.startswith('t') and col.name.endswith('_posts')) or
                       (col.name.startswith('user_') and col.name.endswith('_posts'))
                ]
            
            qdrant_found = False
            qdrant_data = None
            collection_name = None
            
            for coll_name in collections_to_check:
                try:
                    # Context7: Используем scroll с фильтром по post_id в payload
                    from qdrant_client.http import models
                    
                    scroll_filter = models.Filter(
                        must=[
                            models.FieldCondition(
                                key="post_id",
                                match=models.MatchValue(value=post_id)
                            )
                        ]
                    )
                    
                    scroll_result = self.qdrant_client.client.scroll(
                        collection_name=coll_name,
                        scroll_filter=scroll_filter,
                        limit=1
                    )
                    
                    points, _ = scroll_result
                    
                    if points:
                        point = points[0]
                        qdrant_found = True
                        collection_name = coll_name
                        qdrant_data = {
                            'vector_id': str(point.id),
                            'payload': point.payload,
                            'has_vector': point.vector is not None,
                            'vector_dim': len(point.vector) if point.vector else 0,
                            'payload_keys': list(point.payload.keys()) if point.payload else [],
                            'collection': coll_name
                        }
                        break
                        
                except Exception as e:
                    logger.debug("Qdrant check failed for collection",
                               collection=coll_name,
                               post_id=post_id,
                               error=str(e))
                    continue
            
            self.results['checks']['qdrant'] = {
                'found': qdrant_found,
                'data': qdrant_data,
                'collections_checked': len(collections_to_check)
            }
            
            if not qdrant_found:
                self.results['checks']['qdrant']['error'] = 'Post not found in Qdrant'
                
        except Exception as e:
            self.results['checks']['qdrant'] = {
                'found': False,
                'error': str(e)
            }
    
    async def _check_neo4j(self, post_id: str):
        """
        Context7 best practice: Проверка наличия в Neo4j через MATCH по post_id property.
        
        Используем параметризованный запрос для безопасности.
        """
        try:
            if not self.neo4j_client or not self.neo4j_client._driver:
                self.results['checks']['neo4j'] = {
                    'found': False,
                    'error': 'Neo4j client not initialized'
                }
                return
            
            async with self.neo4j_client._driver.session() as session:
                # Context7: Параметризованный запрос для безопасности
                result = await session.run(
                    "MATCH (p:Post {post_id: $post_id}) RETURN p, p.post_id as post_id, p.tenant_id as tenant_id, p.channel_id as channel_id, p.indexed_at as indexed_at",
                    post_id=post_id
                )
                
                record = await result.single()
                
                if record:
                    # Проверяем связи с тегами
                    tags_result = await session.run(
                        "MATCH (p:Post {post_id: $post_id})-[:TAGGED_AS]->(t:Tag) RETURN t.name as tag_name ORDER BY tag_name",
                        post_id=post_id
                    )
                    tags = [r['tag_name'] for r in await tags_result.data()]
                    
                    # Проверяем связи с изображениями
                    images_result = await session.run(
                        "MATCH (p:Post {post_id: $post_id})-[:HAS_IMAGE]->(img:Image) RETURN img.sha256 as sha256, img.s3_key as s3_key LIMIT 5",
                        post_id=post_id
                    )
                    images = await images_result.data()
                    
                    # Проверяем связи с веб-страницами
                    webpages_result = await session.run(
                        "MATCH (p:Post {post_id: $post_id})-[:REFERS_TO]->(wp:WebPage) RETURN wp.url as url LIMIT 5",
                        post_id=post_id
                    )
                    webpages = await webpages_result.data()
                    
                    self.results['checks']['neo4j'] = {
                        'found': True,
                        'data': {
                            'post_id': record.get('post_id'),
                            'tenant_id': record.get('tenant_id'),
                            'channel_id': record.get('channel_id'),
                            'indexed_at': record.get('indexed_at'),
                            'tags_count': len(tags),
                            'tags': tags[:10],  # Первые 10 тегов
                            'images_count': len(images),
                            'images': images,
                            'webpages_count': len(webpages),
                            'webpages': [w['url'] for w in webpages[:5]]
                        }
                    }
                else:
                    self.results['checks']['neo4j'] = {
                        'found': False,
                        'error': 'Post not found in Neo4j'
                    }
                    
        except Exception as e:
            self.results['checks']['neo4j'] = {
                'found': False,
                'error': str(e)
            }
    
    async def _check_redis_streams(self, post_id: str):
        """Проверка событий в Redis streams."""
        try:
            from event_bus import STREAMS
            
            streams_to_check = {
                'posts.tagged': STREAMS.get('posts.tagged', 'stream:posts:tagged'),
                'posts.enriched': STREAMS.get('posts.enriched', 'stream:posts:enriched'),
                'posts.indexed': STREAMS.get('posts.indexed', 'stream:posts:indexed')
            }
            
            stream_results = {}
            
            for stream_name, stream_key in streams_to_check.items():
                try:
                    # Ищем сообщения с post_id в данных
                    # Читаем последние 1000 сообщений
                    messages = await self.redis_client.xrevrange(
                        stream_key,
                        count=1000,
                        max='+',
                        min='-'
                    )
                    
                    found_messages = []
                    for msg_id, fields in messages:
                        # Проверяем наличие post_id в полях
                        if 'post_id' in fields and fields['post_id'] == post_id:
                            # Парсим данные если это JSON
                            data = {}
                            if 'data' in fields:
                                try:
                                    data = json.loads(fields['data'])
                                except:
                                    data = {'raw': fields['data']}
                            elif 'payload' in fields:
                                try:
                                    data = json.loads(fields['payload'])
                                except:
                                    data = {'raw': fields['payload']}
                            else:
                                data = fields
                            
                            found_messages.append({
                                'message_id': msg_id,
                                'timestamp': msg_id.split('-')[0] if '-' in msg_id else None,
                                'data': data
                            })
                    
                    stream_results[stream_name] = {
                        'found': len(found_messages) > 0,
                        'messages_count': len(found_messages),
                        'messages': found_messages[:5]  # Первые 5 сообщений
                    }
                    
                except Exception as e:
                    stream_results[stream_name] = {
                        'found': False,
                        'error': str(e)
                    }
            
            self.results['checks']['redis_streams'] = stream_results
            
        except Exception as e:
            self.results['checks']['redis_streams'] = {
                'error': str(e)
            }
    
    def _generate_summary(self) -> Dict[str, Any]:
        """Генерация сводки диагностики."""
        checks = self.results['checks']
        
        summary = {
            'post_exists': checks.get('post_in_db', {}).get('found', False),
            'has_tags': checks.get('tags', {}).get('found', False) and checks.get('tags', {}).get('tags_count', 0) > 0,
            'has_enrichment': (
                checks.get('enrichment', {}).get('vision', {}).get('found', False) or
                checks.get('enrichment', {}).get('crawl', {}).get('found', False)
            ),
            'is_indexed_qdrant': checks.get('qdrant', {}).get('found', False),
            'is_indexed_neo4j': checks.get('neo4j', {}).get('found', False),
            'indexing_completed': checks.get('indexing_status', {}).get('is_completed', False),
            'has_events': any(
                s.get('found', False) 
                for s in checks.get('redis_streams', {}).values() 
                if isinstance(s, dict)
            )
        }
        
        # Определение проблем
        issues = []
        if not summary['post_exists']:
            issues.append('Post not found in database')
        if not summary['has_tags']:
            issues.append('No tags found in post_enrichment')
        if not summary['is_indexed_qdrant']:
            issues.append('Post not indexed in Qdrant')
        if not summary['is_indexed_neo4j']:
            issues.append('Post not indexed in Neo4j')
        if not summary['indexing_completed']:
            indexing_status = checks.get('indexing_status', {})
            if indexing_status.get('found'):
                issues.append(f"Indexing incomplete: embedding={indexing_status.get('embedding_status')}, graph={indexing_status.get('graph_status')}")
            else:
                issues.append('Indexing status not found')
        
        summary['issues'] = issues
        summary['pipeline_status'] = 'complete' if not issues else 'stuck'
        summary['pipeline_stage'] = self._determine_stage()
        
        return summary
    
    def _determine_stage(self) -> str:
        """Определение этапа, на котором застрял пайплайн."""
        checks = self.results['checks']
        
        if not checks.get('post_in_db', {}).get('found'):
            return 'not_started'
        
        if not checks.get('tags', {}).get('found'):
            return 'tagging'
        
        if not checks.get('enrichment', {}).get('crawl', {}).get('found'):
            return 'enrichment'
        
        if not checks.get('indexing_status', {}).get('found'):
            return 'indexing_pending'
        
        indexing_status = checks.get('indexing_status', {})
        if indexing_status.get('embedding_status') != 'completed':
            return 'indexing_embeddings'
        
        if indexing_status.get('graph_status') != 'completed':
            return 'indexing_graph'
        
        if not checks.get('qdrant', {}).get('found'):
            return 'qdrant_indexing'
        
        if not checks.get('neo4j', {}).get('found'):
            return 'neo4j_indexing'
        
        return 'complete'


# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Главная функция."""
    parser = argparse.ArgumentParser(description='Диагностика поста в пайплайне')
    parser.add_argument('post_id', help='ID поста для диагностики')
    parser.add_argument('--json', action='store_true', help='Вывод в JSON формате')
    parser.add_argument('--compact', action='store_true', help='Компактный вывод (только summary)')
    
    args = parser.parse_args()
    
    config = get_env_config()
    diagnostic = PostDiagnostic(config)
    
    try:
        await diagnostic.initialize()
        results = await diagnostic.diagnose(args.post_id)
        
        if args.compact:
            # Только summary
            print(json.dumps(results.get('summary', {}), indent=2, ensure_ascii=False))
        elif args.json:
            # Полный JSON
            print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        else:
            # Человекочитаемый вывод
            _print_human_readable(results)
            
    except Exception as e:
        logger.error("Diagnostic failed", error=str(e))
        print(f"❌ Ошибка диагностики: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await diagnostic.close()


def _print_human_readable(results: Dict[str, Any]):
    """Вывод результатов в читаемом формате."""
    post_id = results.get('post_id', 'unknown')
    checks = results.get('checks', {})
    summary = results.get('summary', {})
    
    if not summary:
        summary = {}
    
    print(f"\n{'='*60}")
    print(f"Диагностика поста: {post_id}")
    print(f"{'='*60}\n")
    
    # Post in DB
    post_check = checks.get('post_in_db', {})
    status = "✅" if post_check.get('found') else "❌"
    print(f"{status} БД: {'Найден' if post_check.get('found') else 'Не найден'}")
    if post_check.get('found'):
        data = post_check.get('data', {})
        print(f"   - Tenant ID: {data.get('tenant_id', 'N/A')}")
        print(f"   - Channel ID: {data.get('channel_id', 'N/A')}")
        print(f"   - Is Processed: {data.get('is_processed', False)}")
        print(f"   - Created: {data.get('created_at', 'N/A')}")
    
    # Tags
    tags_check = checks.get('tags', {})
    status = "✅" if tags_check.get('found') and tags_check.get('tags_count', 0) > 0 else "❌"
    print(f"\n{status} Теги: {'Найдены' if tags_check.get('found') else 'Не найдены'}")
    if tags_check.get('found'):
        print(f"   - Количество: {tags_check.get('tags_count', 0)}")
        tags = tags_check.get('tags', [])
        if tags:
            print(f"   - Примеры: {', '.join(tags[:5])}")
        print(f"   - Статус: {tags_check.get('status', 'N/A')}")
        print(f"   - Provider: {tags_check.get('provider', 'N/A')}")
    
    # Enrichment
    enrichment = checks.get('enrichment', {})
    print(f"\n📊 Enrichment данные:")
    for kind in ['vision', 'crawl']:
        kind_data = enrichment.get(kind) or {}
        if isinstance(kind_data, dict):
            status = "✅" if kind_data.get('found') else "⚪"
            print(f"   {status} {kind.upper()}: {'Найдено' if kind_data.get('found') else 'Не найдено'}")
            if kind_data.get('found'):
                print(f"      - Статус: {kind_data.get('status', 'N/A')}")
                print(f"      - Provider: {kind_data.get('provider', 'N/A')}")
                print(f"      - Ключи данных: {', '.join(kind_data.get('data_keys', [])[:5])}")
        else:
            print(f"   ⚪ {kind.upper()}: Не найдено")
    
    # Indexing Status
    indexing = checks.get('indexing_status', {})
    status = "✅" if indexing.get('is_completed') else "❌"
    print(f"\n{status} Статус индексации: {'Завершена' if indexing.get('is_completed') else 'Не завершена'}")
    if indexing.get('found'):
        print(f"   - Embedding: {indexing.get('embedding_status', 'N/A')}")
        print(f"   - Graph: {indexing.get('graph_status', 'N/A')}")
        if indexing.get('vector_id'):
            print(f"   - Vector ID: {indexing.get('vector_id')}")
        if indexing.get('error_message'):
            print(f"   - Ошибка: {indexing.get('error_message')}")
    
    # Qdrant
    qdrant = checks.get('qdrant', {})
    status = "✅" if qdrant.get('found') else "❌"
    print(f"\n{status} Qdrant: {'Индексирован' if qdrant.get('found') else 'Не индексирован'}")
    if qdrant.get('found'):
        data = qdrant.get('data', {})
        print(f"   - Collection: {data.get('collection', 'N/A')}")
        print(f"   - Vector ID: {data.get('vector_id', 'N/A')}")
        print(f"   - Vector dimension: {data.get('vector_dim', 0)}")
        print(f"   - Payload keys: {', '.join(data.get('payload_keys', [])[:10])}")
    
    # Neo4j
    neo4j = checks.get('neo4j', {})
    status = "✅" if neo4j.get('found') else "❌"
    print(f"\n{status} Neo4j: {'Индексирован' if neo4j.get('found') else 'Не индексирован'}")
    if neo4j.get('found'):
        data = neo4j.get('data', {})
        print(f"   - Tags в графе: {data.get('tags_count', 0)}")
        print(f"   - Images: {data.get('images_count', 0)}")
        print(f"   - WebPages: {data.get('webpages_count', 0)}")
        if data.get('tags'):
            print(f"   - Примеры тегов: {', '.join(data.get('tags', [])[:5])}")
    
    # Redis Streams
    streams = checks.get('redis_streams', {})
    print(f"\n📨 События в Redis streams:")
    for stream_name, stream_data in streams.items():
        if isinstance(stream_data, dict):
            status = "✅" if stream_data.get('found') else "⚪"
            count = stream_data.get('messages_count', 0)
            print(f"   {status} {stream_name}: {count} сообщений")
    
            # Summary
    print(f"\n{'='*60}")
    print("Сводка:")
    print(f"{'='*60}")
    if summary:
        print(f"Статус пайплайна: {summary.get('pipeline_status', 'unknown')}")
        print(f"Этап: {summary.get('pipeline_stage', 'unknown')}")
        
        if summary.get('issues'):
            print(f"\n❌ Обнаруженные проблемы:")
            for issue in summary['issues']:
                print(f"   - {issue}")
        else:
            print(f"\n✅ Все проверки пройдены успешно!")
    else:
        print("⚠️ Summary не сгенерирован из-за ошибок")
    
    print()


if __name__ == "__main__":
    asyncio.run(main())

