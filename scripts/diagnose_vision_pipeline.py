#!/usr/bin/env python3
"""
Диагностика Vision пайплайна для постов с медиа.
Context7: Проверка всех этапов от парсинга до Vision анализа.
"""

import os
import sys
import asyncio
import asyncpg
import redis.asyncio as redis
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
import json

PROJECT_ROOT = Path("/opt/telegram-assistant")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "api"))

import structlog
logger = structlog.get_logger()

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.RESET}\n")

def print_success(text: str):
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")

def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")

def print_error(text: str):
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")

def print_info(text: str):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.RESET}")


class VisionPipelineDiagnostic:
    """Диагностика Vision пайплайна."""
    
    def __init__(self):
        self.db_pool: Optional[asyncpg.Pool] = None
        self.redis_client: Optional[redis.Redis] = None
        
    async def initialize(self):
        """Инициализация подключений."""
        db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/postgres")
        self.db_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
        
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        await self.redis_client.ping()
        
    async def cleanup(self):
        """Закрытие подключений."""
        if self.db_pool:
            await self.db_pool.close()
        if self.redis_client:
            await self.redis_client.aclose()
    
    async def check_posts_with_media(self, channel_id: str = None, hours: int = 24) -> List[Dict[str, Any]]:
        """Проверка постов с медиа за последние N часов."""
        print_header(f"1. ПОСТЫ С МЕДИА ЗА ПОСЛЕДНИЕ {hours} ЧАСОВ")
        
        query = """
            SELECT 
                p.id,
                p.channel_id,
                p.telegram_message_id,
                p.has_media,
                p.posted_at,
                p.created_at,
                p.media_urls,
                (SELECT COUNT(*) FROM post_media_map pm WHERE pm.post_id = p.id) as media_count
            FROM posts p
            WHERE p.has_media = true
            AND p.posted_at > NOW() - INTERVAL '%s hours'
        """ % hours
        
        if channel_id:
            query += " AND p.channel_id = $1"
            params = [channel_id]
        else:
            params = []
        
        query += " ORDER BY p.posted_at DESC LIMIT 20"
        
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        
        posts = []
        for row in rows:
            posts.append({
                'id': str(row['id']),
                'channel_id': str(row['channel_id']),
                'telegram_message_id': row['telegram_message_id'],
                'has_media': row['has_media'],
                'posted_at': row['posted_at'].isoformat() if row['posted_at'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'media_urls': row['media_urls'],
                'media_count': row['media_count']
            })
        
        print_info(f"Найдено {len(posts)} постов с медиа")
        for post in posts[:10]:
            status = "✅" if post['media_count'] > 0 else "⚠️"
            print(f"{status} Post {post['telegram_message_id']}: has_media={post['has_media']}, media_count={post['media_count']}, posted_at={post['posted_at']}")
        
        return posts
    
    async def check_vision_enrichments(self, post_ids: List[str]) -> Dict[str, Any]:
        """Проверка vision обогащений для постов."""
        print_header("2. VISION ОБОГАЩЕНИЯ")
        
        if not post_ids:
            print_warning("Нет постов для проверки")
            return {}
        
        query = """
            SELECT 
                pe.post_id,
                pe.kind,
                pe.updated_at,
                pe.data
            FROM post_enrichment pe
            WHERE pe.post_id = ANY($1::uuid[])
            AND pe.kind = 'vision'
            ORDER BY pe.updated_at DESC
        """
        
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, post_ids)
        
        enrichments = {}
        for row in rows:
            enrichments[str(row['post_id'])] = {
                'kind': row['kind'],
                'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
                'has_data': bool(row['data'])
            }
        
        print_info(f"Найдено {len(enrichments)} vision обогащений из {len(post_ids)} постов")
        
        missing = [pid for pid in post_ids if pid not in enrichments]
        if missing:
            print_warning(f"Нет vision обогащений для {len(missing)} постов")
            for pid in missing[:5]:
                print(f"  - {pid[:8]}...")
        
        return enrichments
    
    async def check_vision_stream_events(self, post_ids: List[str]) -> Dict[str, Any]:
        """Проверка событий в stream:posts:vision для постов."""
        print_header("3. СОБЫТИЯ В STREAM:POSTS:VISION")
        
        stream_name = "stream:posts:vision"
        
        # Получаем последние события
        events = await self.redis_client.xrevrange(stream_name, count=100)
        
        print_info(f"Последние {len(events)} событий в stream:posts:vision")
        
        # Проверяем, есть ли события для наших постов
        found_events = {}
        for event_id, fields in events[:20]:
            try:
                data_str = fields.get('data', '{}')
                if isinstance(data_str, str):
                    event_data = json.loads(data_str)
                    post_id = event_data.get('post_id')
                    if post_id in post_ids:
                        found_events[post_id] = {
                            'event_id': event_id,
                            'occurred_at': event_data.get('occurred_at'),
                            'media_count': len(event_data.get('media_files', []))
                        }
            except Exception as e:
                continue
        
        print_info(f"Найдено {len(found_events)} событий для проверяемых постов")
        
        missing = [pid for pid in post_ids if pid not in found_events]
        if missing:
            print_warning(f"Нет событий в stream для {len(missing)} постов")
        
        # Проверяем последнее событие
        if events:
            last_event_id, last_fields = events[0]
            try:
                last_data = json.loads(last_fields.get('data', '{}'))
                last_occurred = last_data.get('occurred_at', 'unknown')
                print_info(f"Последнее событие: {last_occurred}")
            except:
                pass
        
        return found_events
    
    async def check_vision_analyzed_stream(self, post_ids: List[str]) -> Dict[str, Any]:
        """Проверка событий в stream:posts:vision:analyzed."""
        print_header("4. СОБЫТИЯ В STREAM:POSTS:VISION:ANALYZED")
        
        stream_name = "stream:posts:vision:analyzed"
        
        # Получаем последние события
        events = await self.redis_client.xrevrange(stream_name, count=100)
        
        print_info(f"Последние {len(events)} событий в stream:posts:vision:analyzed")
        
        found_events = {}
        for event_id, fields in events[:20]:
            try:
                data_str = fields.get('data', '{}')
                if isinstance(data_str, str):
                    event_data = json.loads(data_str)
                    post_id = event_data.get('post_id')
                    if post_id in post_ids:
                        found_events[post_id] = {
                            'event_id': event_id,
                            'occurred_at': event_data.get('occurred_at'),
                            'event_type': event_data.get('event_type')
                        }
            except Exception as e:
                continue
        
        print_info(f"Найдено {len(found_events)} событий для проверяемых постов")
        
        return found_events
    
    async def check_media_processing(self, post_ids: List[str]) -> Dict[str, Any]:
        """Проверка обработки медиа файлов."""
        print_header("5. ОБРАБОТКА МЕДИА ФАЙЛОВ")
        
        query = """
            SELECT 
                p.id as post_id,
                COUNT(pm.file_sha256) as media_count,
                COUNT(mo.file_sha256) as media_objects_count
            FROM posts p
            LEFT JOIN post_media_map pm ON pm.post_id = p.id
            LEFT JOIN media_objects mo ON mo.file_sha256 = pm.file_sha256
            WHERE p.id = ANY($1::uuid[])
            GROUP BY p.id
        """
        
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, post_ids)
        
        results = {}
        for row in rows:
            results[str(row['post_id'])] = {
                'media_count': row['media_count'],
                'media_objects_count': row['media_objects_count']
            }
        
        print_info(f"Проверено {len(results)} постов")
        for post_id, data in list(results.items())[:10]:
            status = "✅" if data['media_count'] > 0 else "❌"
            print(f"{status} Post {post_id[:8]}...: media_map={data['media_count']}, media_objects={data['media_objects_count']}")
        
        return results
    
    async def generate_report(self, posts: List[Dict], enrichments: Dict, vision_events: Dict, analyzed_events: Dict, media_results: Dict):
        """Генерация отчета."""
        print_header("ИТОГОВЫЙ ОТЧЕТ")
        
        print("="*80)
        print("ДИАГНОСТИКА VISION ПАЙПЛАЙНА")
        print("="*80)
        print()
        
        print(f"Постов с медиа за 24ч: {len(posts)}")
        print(f"Vision обогащений: {len(enrichments)}")
        print(f"Событий в stream:posts:vision: {len(vision_events)}")
        print(f"Событий в stream:posts:vision:analyzed: {len(analyzed_events)}")
        print()
        
        # Анализ проблем
        issues = []
        
        posts_without_media_map = [p for p in posts if p['media_count'] == 0]
        if posts_without_media_map:
            issues.append(f"❌ {len(posts_without_media_map)} постов с has_media=true, но без медиа в post_media_map")
        
        posts_without_enrichment = [p['id'] for p in posts if p['id'] not in enrichments]
        if posts_without_enrichment:
            issues.append(f"❌ {len(posts_without_enrichment)} постов без vision обогащений")
        
        posts_without_vision_event = [p['id'] for p in posts if p['id'] not in vision_events]
        if posts_without_vision_event:
            issues.append(f"⚠️  {len(posts_without_vision_event)} постов без событий в stream:posts:vision")
        
        if issues:
            print("ПРОБЛЕМЫ:")
            for issue in issues:
                print(f"  {issue}")
        else:
            print_success("Проблем не обнаружено")
        
        print()


async def main():
    """Главная функция."""
    diagnostic = VisionPipelineDiagnostic()
    
    try:
        await diagnostic.initialize()
        
        # Проверка постов с медиа (канал autopotoknews)
        channel_id = '98d8e584-931d-45b1-aa28-4a4124f28505'
        posts = await diagnostic.check_posts_with_media(channel_id=channel_id, hours=24)
        
        if not posts:
            print_warning("Нет постов с медиа за последние 24 часа")
            return
        
        post_ids = [p['id'] for p in posts]
        
        # Проверка vision обогащений
        enrichments = await diagnostic.check_vision_enrichments(post_ids)
        
        # Проверка событий в stream:posts:vision
        vision_events = await diagnostic.check_vision_stream_events(post_ids)
        
        # Проверка событий в stream:posts:vision:analyzed
        analyzed_events = await diagnostic.check_vision_analyzed_stream(post_ids)
        
        # Проверка обработки медиа
        media_results = await diagnostic.check_media_processing(post_ids)
        
        # Генерация отчета
        await diagnostic.generate_report(posts, enrichments, vision_events, analyzed_events, media_results)
        
    except Exception as e:
        print_error(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await diagnostic.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
