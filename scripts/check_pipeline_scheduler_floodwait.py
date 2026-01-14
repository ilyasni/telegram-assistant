#!/usr/bin/env python3
"""
Комплексная проверка пайплайна постов и альбомов, Scheduler и FloodWait.
Context7: Проверка всех этапов от парсинга до Qdrant и Neo4j, статус scheduler'ов и FloodWait.
"""

import os
import sys
import json
import asyncio
import asyncpg
import redis.asyncio as redis
import aiohttp
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

# Добавляем пути
PROJECT_ROOT = Path("/opt/telegram-assistant")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "api"))

import structlog
logger = structlog.get_logger()

# Цвета для вывода
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


class ComprehensiveChecker:
    """Комплексная проверка пайплайна, scheduler и FloodWait."""
    
    def __init__(self):
        self.db_pool: Optional[asyncpg.Pool] = None
        self.redis_client: Optional[redis.Redis] = None
        self.results: Dict[str, Any] = {}
        # Context7: Определяем URL в зависимости от окружения
        # API слушает на 8000 внутри контейнера, но проксируется на 8001 снаружи
        # Проверяем, запущен ли скрипт внутри контейнера
        if os.path.exists('/.dockerenv') or os.getenv('HOSTNAME', '').startswith('telegram-assistant-'):
            # Внутри Docker контейнера - используем имена сервисов и внутренние порты
            self.api_url = os.getenv("API_URL", "http://api:8000")
            self.telethon_ingest_url = os.getenv("TELETHON_INGEST_URL", "http://telethon-ingest:8011")
            self.prometheus_url = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
        else:
            # Снаружи Docker - используем localhost и внешние порты
            self.api_url = os.getenv("API_URL", "http://localhost:8001")
            self.telethon_ingest_url = os.getenv("TELETHON_INGEST_URL", "http://localhost:8011")
            self.prometheus_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        
    async def initialize(self):
        """Инициализация подключений."""
        try:
            # PostgreSQL
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/telegram_assistant")
            self.db_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
            print_success("PostgreSQL подключен")
            
            # Redis
            redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
            self.redis_client = redis.from_url(redis_url, decode_responses=True)
            await self.redis_client.ping()
            print_success("Redis подключен")
            
        except Exception as e:
            print_error(f"Ошибка инициализации: {e}")
            raise
    
    async def cleanup(self):
        """Закрытие подключений."""
        if self.db_pool:
            await self.db_pool.close()
        if self.redis_client:
            await self.redis_client.aclose()
    
    # ========== SCHEDULER CHECKS ==========
    
    async def check_telethon_ingest_scheduler(self) -> Dict[str, Any]:
        """Проверка scheduler'а telethon-ingest."""
        print_header("1. TELETHON-INGEST SCHEDULER (ParseAllChannelsTask)")
        
        result = {
            "status": "unknown",
            "running": False,
            "last_tick": None,
            "interval_sec": None,
            "lock_owner": None,
            "issues": []
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.telethon_ingest_url}/health/details", timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        scheduler_info = data.get("scheduler", {})
                        
                        result["status"] = scheduler_info.get("status", "unknown")
                        result["last_tick"] = scheduler_info.get("last_tick_ts")
                        result["interval_sec"] = scheduler_info.get("interval_sec", 300)
                        result["lock_owner"] = scheduler_info.get("lock_owner")
                        
                        if result["status"] == "ok":
                            result["running"] = True
                            print_success(f"Scheduler работает (status: {result['status']})")
                        elif result["status"] == "stale":
                            result["running"] = True
                            result["issues"].append("Scheduler stale (последний тик давно)")
                            print_warning(f"Scheduler stale (status: {result['status']})")
                        elif result["status"] == "down":
                            result["issues"].append("Scheduler down")
                            print_error(f"Scheduler down (status: {result['status']})")
                        else:
                            result["issues"].append(f"Unknown status: {result['status']}")
                            print_warning(f"Unknown status: {result['status']}")
                        
                        print_info(f"Последний тик: {result['last_tick']}")
                        print_info(f"Интервал: {result['interval_sec']} секунд ({result['interval_sec'] // 60} минут)")
                        
                        if result["lock_owner"]:
                            print_info(f"Lock owner: {result['lock_owner']}")
                        else:
                            print_info("Lock owner: нет (lock не установлен)")
                        
                        # Проверка времени последнего тика
                        if result["last_tick"]:
                            try:
                                if result["last_tick"].endswith('Z'):
                                    last_tick_dt = datetime.fromisoformat(result["last_tick"].replace('Z', '+00:00'))
                                else:
                                    last_tick_dt = datetime.fromisoformat(result["last_tick"])
                                
                                now = datetime.now(timezone.utc) if last_tick_dt.tzinfo else datetime.utcnow()
                                age_seconds = (now - last_tick_dt).total_seconds()
                                
                                if age_seconds < result["interval_sec"] * 2:
                                    print_success(f"Последний тик был {age_seconds:.0f} секунд назад (свежий)")
                                elif age_seconds < result["interval_sec"] * 4:
                                    print_warning(f"Последний тик был {age_seconds:.0f} секунд назад (stale)")
                                    result["issues"].append(f"Последний тик был {age_seconds:.0f} секунд назад")
                                else:
                                    print_error(f"Последний тик был {age_seconds:.0f} секунд назад (down)")
                                    result["issues"].append(f"Последний тик был {age_seconds:.0f} секунд назад")
                            except Exception as e:
                                result["issues"].append(f"Ошибка парсинга времени: {e}")
                                print_warning(f"Не удалось распарсить время последнего тика: {e}")
                    else:
                        result["issues"].append(f"Health endpoint вернул {resp.status}")
                        print_error(f"Health endpoint вернул {resp.status}")
                        
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки: {e}")
            print_error(f"Ошибка проверки telethon-ingest scheduler: {e}")
        
        self.results["telethon_ingest_scheduler"] = result
        return result
    
    async def check_api_scheduler(self) -> Dict[str, Any]:
        """Проверка scheduler'а api."""
        print_header("2. API SCHEDULER (SchedulerTasks)")
        
        result = {
            "status": "unknown",
            "running": False,
            "jobs_count": 0,
            "jobs": [],
            "issues": []
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/health", timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Context7: Проверяем scheduler в checks.scheduler (новый формат)
                        checks = data.get("checks", {})
                        scheduler_info = checks.get("scheduler", {})
                        # Fallback на старый формат
                        if not scheduler_info:
                            scheduler_info = data.get("scheduler", {})
                        
                        result["running"] = scheduler_info.get("running", False)
                        result["jobs_count"] = scheduler_info.get("jobs_count", 0)
                        result["jobs"] = scheduler_info.get("job_ids", [])
                        
                        if result["running"]:
                            result["status"] = "ok"
                            print_success(f"Scheduler работает ({result['jobs_count']} задач)")
                            
                            if result["jobs"]:
                                print_info("Активные задачи:")
                                for job_id in result["jobs"]:
                                    print(f"  - {job_id}")
                            else:
                                print_warning("Нет активных задач")
                        else:
                            result["status"] = "stopped"
                            result["issues"].append("Scheduler не запущен")
                            print_error("Scheduler не запущен")
                    else:
                        result["issues"].append(f"Health endpoint вернул {resp.status}")
                        print_error(f"Health endpoint вернул {resp.status}")
                        
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки: {e}")
            print_error(f"Ошибка проверки API scheduler: {e}")
        
        self.results["api_scheduler"] = result
        return result
    
    # ========== FLOODWAIT CHECKS ==========
    
    async def check_floodwait(self) -> Dict[str, Any]:
        """Проверка FloodWait."""
        print_header("3. ПРОВЕРКА FLOODWAIT")
        
        result = {
            "status": "unknown",
            "active_floodwaits": [],
            "channels_in_cooldown": [],
            "prometheus_metrics": {},
            "issues": []
        }
        
        try:
            # Проверка активных FloodWait в Redis
            try:
                # Ищем все ключи floodwait:*
                keys = await self.redis_client.keys("floodwait:*")
                if keys:
                    print_warning(f"Найдено {len(keys)} активных FloodWait ключей в Redis")
                    for key in keys[:10]:  # Показываем первые 10
                        try:
                            unlock_time_str = await self.redis_client.get(key)
                            if unlock_time_str:
                                unlock_time = float(unlock_time_str)
                                wait_seconds = max(0, unlock_time - time.time())
                                if wait_seconds > 0:
                                    result["active_floodwaits"].append({
                                        "key": key,
                                        "wait_seconds": wait_seconds,
                                        "unlock_time": datetime.fromtimestamp(unlock_time, tz=timezone.utc).isoformat()
                                    })
                                    print_warning(f"  - {key}: ждать {wait_seconds:.0f} секунд")
                        except Exception as e:
                            print_warning(f"  - {key}: ошибка чтения ({e})")
                else:
                    print_success("Нет активных FloodWait ключей в Redis")
            except Exception as e:
                result["issues"].append(f"Ошибка проверки Redis FloodWait: {e}")
                print_warning(f"Не удалось проверить FloodWait в Redis: {e}")
            
            # Проверка каналов в cooldown из-за FloodWait
            try:
                async with self.db_pool.acquire() as conn:
                    channels_in_cooldown = await conn.fetch("""
                        SELECT id, title, blocked_until, 
                               EXTRACT(EPOCH FROM (blocked_until - NOW()))::INTEGER as seconds_remaining
                        FROM channels
                        WHERE blocked_until IS NOT NULL 
                        AND blocked_until > NOW()
                        ORDER BY blocked_until
                        LIMIT 20
                    """)
                    
                    if channels_in_cooldown:
                        print_warning(f"Найдено {len(channels_in_cooldown)} каналов в cooldown")
                        for channel in channels_in_cooldown[:10]:  # Показываем первые 10
                            result["channels_in_cooldown"].append({
                                "channel_id": channel["id"],
                                "title": channel["title"],
                                "blocked_until": channel["blocked_until"].isoformat() if channel["blocked_until"] else None,
                                "seconds_remaining": channel["seconds_remaining"]
                            })
                            print_warning(f"  - {channel['title']} (ID: {channel['id']}): ждать {channel['seconds_remaining']:.0f} секунд")
                    else:
                        print_success("Нет каналов в cooldown")
            except Exception as e:
                result["issues"].append(f"Ошибка проверки каналов в cooldown: {e}")
                print_warning(f"Не удалось проверить каналы в cooldown: {e}")
            
            # Проверка метрик Prometheus
            try:
                async with aiohttp.ClientSession() as session:
                    # telethon_floodwait_total
                    query = "sum(rate(telethon_floodwait_total[5m]))"
                    url = f"{self.prometheus_url}/api/v1/query"
                    params = {"query": query}
                    
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            prom_result = data.get("data", {}).get("result", [])
                            
                            if prom_result:
                                rate = float(prom_result[0].get("value", [None, "0"])[1])
                                result["prometheus_metrics"]["floodwait_rate_5m"] = rate
                                if rate > 0.1:
                                    print_warning(f"Высокий FloodWait rate: {rate:.3f} событий/сек")
                                    result["issues"].append(f"Высокий FloodWait rate: {rate:.3f} событий/сек")
                                else:
                                    print_success(f"FloodWait rate: {rate:.3f} событий/сек (нормально)")
                            
                    # parser_floodwait_seconds_total
                    query = "sum(parser_floodwait_seconds_total)"
                    params = {"query": query}
                    
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            prom_result = data.get("data", {}).get("result", [])
                            
                            if prom_result:
                                total_seconds = float(prom_result[0].get("value", [None, "0"])[1])
                                result["prometheus_metrics"]["total_floodwait_seconds"] = total_seconds
                                print_info(f"Всего времени FloodWait: {total_seconds:.0f} секунд")
                                
            except Exception as e:
                result["issues"].append(f"Ошибка проверки Prometheus: {e}")
                print_warning(f"Не удалось проверить метрики Prometheus: {e}")
            
            result["status"] = "ok" if not result["active_floodwaits"] and not result["channels_in_cooldown"] else "warning"
            
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки FloodWait: {e}")
            print_error(f"Ошибка проверки FloodWait: {e}")
        
        self.results["floodwait"] = result
        return result
    
    # ========== PIPELINE CHECKS ==========
    
    async def check_parsing(self) -> Dict[str, Any]:
        """Проверка парсинга постов и альбомов."""
        print_header("4. ПРОВЕРКА ПАРСИНГА")
        
        result = {
            "status": "unknown",
            "recent_posts": 0,
            "recent_albums": 0,
            "stream_length": 0,
            "pending": 0,
            "issues": []
        }
        
        try:
            # Проверка недавних постов (последние 24 часа)
            async with self.db_pool.acquire() as conn:
                recent_posts = await conn.fetchval("""
                    SELECT COUNT(*) 
                    FROM posts 
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                """)
                
                recent_albums = await conn.fetchval("""
                    SELECT COUNT(DISTINCT grouped_id) 
                    FROM posts 
                    WHERE grouped_id IS NOT NULL 
                    AND created_at > NOW() - INTERVAL '24 hours'
                """)
                
                result["recent_posts"] = recent_posts
                result["recent_albums"] = recent_albums
                
                if recent_posts > 0:
                    print_success(f"Найдено {recent_posts} постов за последние 24 часа")
                else:
                    result["issues"].append("Нет новых постов за последние 24 часа")
                    print_warning("Нет новых постов за последние 24 часа")
                
                if recent_albums > 0:
                    print_success(f"Найдено {recent_albums} альбомов за последние 24 часа")
                else:
                    print_info("Нет новых альбомов за последние 24 часа")
            
            # Проверка Redis Streams для парсинга
            try:
                stream_name = "stream:posts:parsed"
                stream_info = await self.redis_client.xinfo_stream(stream_name)
                length = stream_info.get("length", 0)
                result["stream_length"] = length
                
                if length > 0:
                    print_success(f"Stream {stream_name} содержит {length} сообщений")
                else:
                    print_info(f"Stream {stream_name} пуст")
                
                # Проверка pending
                try:
                    groups_info = await self.redis_client.xinfo_groups(stream_name)
                    pending_total = 0
                    for group in groups_info:
                        if isinstance(group, dict):
                            pending = group.get(b'pending', 0) or group.get('pending', 0)
                        elif isinstance(group, list):
                            g_dict = dict(zip(group[::2], group[1::2]))
                            pending = g_dict.get(b'pending', 0) or g_dict.get('pending', 0)
                        else:
                            pending = 0
                        pending = int(pending) if pending else 0
                        pending_total += pending
                    
                    result["pending"] = pending_total
                    if pending_total > 0:
                        print_warning(f"Pending сообщений: {pending_total}")
                        result["issues"].append(f"Pending сообщений: {pending_total}")
                    else:
                        print_success(f"Pending сообщений: {pending_total}")
                except Exception as e:
                    print_warning(f"Не удалось проверить pending: {e}")
                    
            except Exception as e:
                result["issues"].append(f"Не удалось проверить stream {stream_name}: {e}")
                print_warning(f"Не удалось проверить stream {stream_name}: {e}")
            
            result["status"] = "ok" if recent_posts > 0 else "warning"
            
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки парсинга: {e}")
            print_error(f"Ошибка проверки парсинга: {e}")
        
        self.results["parsing"] = result
        return result
    
    async def check_vision(self) -> Dict[str, Any]:
        """Проверка Vision анализа."""
        print_header("5. ПРОВЕРКА VISION АНАЛИЗА")
        
        result = {
            "status": "unknown",
            "recent_analyses": 0,
            "stream_length": 0,
            "pending": 0,
            "issues": []
        }
        
        try:
            # Проверка недавних vision анализов
            async with self.db_pool.acquire() as conn:
                recent_analyses = await conn.fetchval("""
                    SELECT COUNT(*) 
                    FROM post_enrichment 
                    WHERE kind = 'vision' 
                    AND updated_at > NOW() - INTERVAL '24 hours'
                """)
                
                result["recent_analyses"] = recent_analyses
                
                if recent_analyses > 0:
                    print_success(f"Найдено {recent_analyses} vision анализов за последние 24 часа")
                else:
                    result["issues"].append("Нет vision анализов за последние 24 часа")
                    print_warning("Нет vision анализов за последние 24 часа")
            
            # Проверка Redis Streams
            try:
                stream_name = "stream:posts:vision:analyzed"
                stream_info = await self.redis_client.xinfo_stream(stream_name)
                length = stream_info.get("length", 0)
                result["stream_length"] = length
                
                if length > 0:
                    print_info(f"Stream {stream_name} содержит {length} сообщений")
                else:
                    print_info(f"Stream {stream_name} пуст")
                
                # Проверка pending
                try:
                    groups_info = await self.redis_client.xinfo_groups(stream_name)
                    pending_total = 0
                    for group in groups_info:
                        if isinstance(group, dict):
                            pending = group.get(b'pending', 0) or group.get('pending', 0)
                        elif isinstance(group, list):
                            g_dict = dict(zip(group[::2], group[1::2]))
                            pending = g_dict.get(b'pending', 0) or g_dict.get('pending', 0)
                        else:
                            pending = 0
                        pending = int(pending) if pending else 0
                        pending_total += pending
                    
                    result["pending"] = pending_total
                    if pending_total > 0:
                        print_warning(f"Pending сообщений: {pending_total}")
                        result["issues"].append(f"Pending сообщений: {pending_total}")
                    else:
                        print_success(f"Pending сообщений: {pending_total}")
                except Exception as e:
                    print_warning(f"Не удалось проверить pending: {e}")
                    
            except Exception as e:
                result["issues"].append(f"Не удалось проверить stream {stream_name}: {e}")
                print_warning(f"Не удалось проверить stream {stream_name}: {e}")
            
            result["status"] = "ok" if recent_analyses > 0 else "warning"
            
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки Vision: {e}")
            print_error(f"Ошибка проверки Vision: {e}")
        
        self.results["vision"] = result
        return result
    
    async def check_tagging(self) -> Dict[str, Any]:
        """Проверка тегирования."""
        print_header("6. ПРОВЕРКА ТЕГИРОВАНИЯ")
        
        result = {
            "status": "unknown",
            "recent_tags": 0,
            "stream_length": 0,
            "pending": 0,
            "issues": []
        }
        
        try:
            # Проверка недавних тегов
            async with self.db_pool.acquire() as conn:
                recent_tags = await conn.fetchval("""
                    SELECT COUNT(*) 
                    FROM post_enrichment 
                    WHERE kind = 'tags' 
                    AND updated_at > NOW() - INTERVAL '24 hours'
                """)
                
                result["recent_tags"] = recent_tags
                
                if recent_tags > 0:
                    print_success(f"Найдено {recent_tags} тегирований за последние 24 часа")
                else:
                    result["issues"].append("Нет тегирований за последние 24 часа")
                    print_warning("Нет тегирований за последние 24 часа")
            
            # Проверка Redis Streams
            try:
                stream_name = "stream:posts:tagged"
                stream_info = await self.redis_client.xinfo_stream(stream_name)
                length = stream_info.get("length", 0)
                result["stream_length"] = length
                
                if length > 0:
                    print_info(f"Stream {stream_name} содержит {length} сообщений")
                else:
                    print_info(f"Stream {stream_name} пуст")
                
                # Проверка pending
                try:
                    groups_info = await self.redis_client.xinfo_groups(stream_name)
                    pending_total = 0
                    for group in groups_info:
                        if isinstance(group, dict):
                            pending = group.get(b'pending', 0) or group.get('pending', 0)
                        elif isinstance(group, list):
                            g_dict = dict(zip(group[::2], group[1::2]))
                            pending = g_dict.get(b'pending', 0) or g_dict.get('pending', 0)
                        else:
                            pending = 0
                        pending = int(pending) if pending else 0
                        pending_total += pending
                    
                    result["pending"] = pending_total
                    if pending_total > 0:
                        print_warning(f"Pending сообщений: {pending_total}")
                        result["issues"].append(f"Pending сообщений: {pending_total}")
                    else:
                        print_success(f"Pending сообщений: {pending_total}")
                except Exception as e:
                    print_warning(f"Не удалось проверить pending: {e}")
                    
            except Exception as e:
                result["issues"].append(f"Не удалось проверить stream {stream_name}: {e}")
                print_warning(f"Не удалось проверить stream {stream_name}: {e}")
            
            result["status"] = "ok" if recent_tags > 0 else "warning"
            
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки тегирования: {e}")
            print_error(f"Ошибка проверки тегирования: {e}")
        
        self.results["tagging"] = result
        return result
    
    async def check_enrichment(self) -> Dict[str, Any]:
        """Проверка обогащения с Crawl4AI."""
        print_header("7. ПРОВЕРКА ОБОГАЩЕНИЯ (CRAWL4AI)")
        
        result = {
            "status": "unknown",
            "recent_enrichments": 0,
            "stream_length": 0,
            "pending": 0,
            "issues": []
        }
        
        try:
            # Проверка недавних обогащений
            async with self.db_pool.acquire() as conn:
                recent_enrichments = await conn.fetchval("""
                    SELECT COUNT(*) 
                    FROM post_enrichment 
                    WHERE kind = 'crawl' 
                    AND updated_at > NOW() - INTERVAL '24 hours'
                """)
                
                result["recent_enrichments"] = recent_enrichments
                
                if recent_enrichments > 0:
                    print_success(f"Найдено {recent_enrichments} обогащений за последние 24 часа")
                else:
                    print_info("Нет обогащений за последние 24 часа (может быть нормально)")
            
            # Проверка Redis Streams
            try:
                stream_name = "stream:posts:enriched"
                stream_info = await self.redis_client.xinfo_stream(stream_name)
                length = stream_info.get("length", 0)
                result["stream_length"] = length
                
                if length > 0:
                    print_info(f"Stream {stream_name} содержит {length} сообщений")
                else:
                    print_info(f"Stream {stream_name} пуст")
                
                # Проверка pending
                try:
                    groups_info = await self.redis_client.xinfo_groups(stream_name)
                    pending_total = 0
                    for group in groups_info:
                        if isinstance(group, dict):
                            pending = group.get(b'pending', 0) or group.get('pending', 0)
                        elif isinstance(group, list):
                            g_dict = dict(zip(group[::2], group[1::2]))
                            pending = g_dict.get(b'pending', 0) or g_dict.get('pending', 0)
                        else:
                            pending = 0
                        pending = int(pending) if pending else 0
                        pending_total += pending
                    
                    result["pending"] = pending_total
                    if pending_total > 0:
                        print_warning(f"Pending сообщений: {pending_total}")
                        result["issues"].append(f"Pending сообщений: {pending_total}")
                    else:
                        print_success(f"Pending сообщений: {pending_total}")
                except Exception as e:
                    print_warning(f"Не удалось проверить pending: {e}")
                    
            except Exception as e:
                result["issues"].append(f"Не удалось проверить stream {stream_name}: {e}")
                print_warning(f"Не удалось проверить stream {stream_name}: {e}")
            
            result["status"] = "ok"
            
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки обогащения: {e}")
            print_error(f"Ошибка проверки обогащения: {e}")
        
        self.results["enrichment"] = result
        return result
    
    async def check_qdrant(self) -> Dict[str, Any]:
        """Проверка индексации в Qdrant."""
        print_header("8. ПРОВЕРКА QDRANT")
        
        result = {
            "status": "unknown",
            "collections": [],
            "total_points": 0,
            "issues": []
        }
        
        try:
            from api.worker.integrations.qdrant_client import QdrantClient
            
            qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
            qdrant_client = QdrantClient(qdrant_url)
            await qdrant_client.connect()
            
            # Получение списка коллекций (синхронный метод)
            collections = qdrant_client.client.get_collections()
            result["collections"] = [c.name for c in collections.collections]
            
            if result["collections"]:
                print_success(f"Найдено {len(result['collections'])} коллекций:")
                for coll_name in result["collections"]:
                    try:
                        collection_info = qdrant_client.client.get_collection(coll_name)
                        points_count = collection_info.points_count
                        result["total_points"] += points_count
                        print_info(f"  - {coll_name}: {points_count} точек")
                    except Exception as e:
                        result["issues"].append(f"Не удалось получить информацию о коллекции {coll_name}: {e}")
                        print_warning(f"  - {coll_name}: ошибка получения информации")
            else:
                result["issues"].append("Нет коллекций в Qdrant")
                print_warning("Нет коллекций в Qdrant")
            
            result["status"] = "ok" if result["collections"] else "warning"
            
        except ImportError:
            result["issues"].append("Не удалось импортировать QdrantClient")
            print_warning("Не удалось импортировать QdrantClient (может быть нормально)")
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки Qdrant: {e}")
            print_error(f"Ошибка проверки Qdrant: {e}")
        
        self.results["qdrant"] = result
        return result
    
    async def check_neo4j(self) -> Dict[str, Any]:
        """Проверка индексации в Neo4j."""
        print_header("9. ПРОВЕРКА NEO4J")
        
        result = {
            "status": "unknown",
            "post_nodes": 0,
            "album_nodes": 0,
            "tag_nodes": 0,
            "issues": []
        }
        
        try:
            from api.worker.integrations.neo4j_client import Neo4jClient
            
            neo4j_url = os.getenv("NEO4J_URL", "bolt://neo4j:7687")
            neo4j_user = os.getenv("NEO4J_USER", "neo4j")
            neo4j_password = os.getenv("NEO4J_PASSWORD", "neo4j123")
            
            neo4j_client = Neo4jClient(neo4j_url, neo4j_user, neo4j_password)
            await neo4j_client.connect()
            
            # Подсчет узлов через прямой запрос
            async with neo4j_client._driver.session() as session:
                post_result = await session.run("MATCH (n:Post) RETURN count(n) as count")
                post_record = await post_result.single()
                post_count = post_record["count"] if post_record else 0
                
                album_result = await session.run("MATCH (n:Album) RETURN count(n) as count")
                album_record = await album_result.single()
                album_count = album_record["count"] if album_record else 0
                
                tag_result = await session.run("MATCH (n:Tag) RETURN count(n) as count")
                tag_record = await tag_result.single()
                tag_count = tag_record["count"] if tag_record else 0
            
            result["post_nodes"] = post_count
            result["album_nodes"] = album_count
            result["tag_nodes"] = tag_count
            
            print_success(f"Найдено узлов:")
            print_info(f"  - Post: {post_count}")
            print_info(f"  - Album: {album_count}")
            print_info(f"  - Tag: {tag_count}")
            
            result["status"] = "ok"
            
        except ImportError:
            result["issues"].append("Не удалось импортировать Neo4jClient")
            print_warning("Не удалось импортировать Neo4jClient (может быть нормально)")
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки Neo4j: {e}")
            print_error(f"Ошибка проверки Neo4j: {e}")
        
        self.results["neo4j"] = result
        return result
    
    async def check_indexing(self) -> Dict[str, Any]:
        """Проверка индексации (общая)."""
        print_header("10. ПРОВЕРКА ИНДЕКСАЦИИ")
        
        result = {
            "status": "unknown",
            "recent_indexed": 0,
            "stream_length": 0,
            "pending": 0,
            "issues": []
        }
        
        try:
            # Проверка недавно проиндексированных постов
            async with self.db_pool.acquire() as conn:
                recent_indexed = await conn.fetchval("""
                    SELECT COUNT(DISTINCT p.id)
                    FROM posts p
                    INNER JOIN indexing_status idx ON idx.post_id = p.id
                    WHERE (idx.embedding_status = 'completed' OR idx.graph_status = 'completed')
                    AND idx.processing_completed_at > NOW() - INTERVAL '24 hours'
                """)
                
                result["recent_indexed"] = recent_indexed
                
                if recent_indexed > 0:
                    print_success(f"Найдено {recent_indexed} проиндексированных постов за последние 24 часа")
                else:
                    result["issues"].append("Нет проиндексированных постов за последние 24 часа")
                    print_warning("Нет проиндексированных постов за последние 24 часа")
            
            # Проверка Redis Streams
            try:
                stream_name = "stream:posts:indexed"
                stream_info = await self.redis_client.xinfo_stream(stream_name)
                length = stream_info.get("length", 0)
                result["stream_length"] = length
                
                if length > 0:
                    print_info(f"Stream {stream_name} содержит {length} сообщений")
                else:
                    print_info(f"Stream {stream_name} пуст")
                
                # Проверка pending
                try:
                    groups_info = await self.redis_client.xinfo_groups(stream_name)
                    pending_total = 0
                    for group in groups_info:
                        if isinstance(group, dict):
                            pending = group.get(b'pending', 0) or group.get('pending', 0)
                        elif isinstance(group, list):
                            g_dict = dict(zip(group[::2], group[1::2]))
                            pending = g_dict.get(b'pending', 0) or g_dict.get('pending', 0)
                        else:
                            pending = 0
                        pending = int(pending) if pending else 0
                        pending_total += pending
                    
                    result["pending"] = pending_total
                    if pending_total > 0:
                        print_warning(f"Pending сообщений: {pending_total}")
                        result["issues"].append(f"Pending сообщений: {pending_total}")
                    else:
                        print_success(f"Pending сообщений: {pending_total}")
                except Exception as e:
                    print_warning(f"Не удалось проверить pending: {e}")
                    
            except Exception as e:
                result["issues"].append(f"Не удалось проверить stream {stream_name}: {e}")
                print_warning(f"Не удалось проверить stream {stream_name}: {e}")
            
            result["status"] = "ok" if recent_indexed > 0 else "warning"
            
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка проверки индексации: {e}")
            print_error(f"Ошибка проверки индексации: {e}")
        
        self.results["indexing"] = result
        return result
    
    def generate_report(self) -> str:
        """Генерация итогового отчета."""
        print_header("ИТОГОВЫЙ ОТЧЕТ")
        
        report_lines = []
        report_lines.append("# Комплексная проверка пайплайна, Scheduler и FloodWait\n\n")
        report_lines.append(f"**Дата**: {datetime.now(timezone.utc).isoformat()}\n\n")
        report_lines.append("---\n\n")
        
        # Сводка по каждому этапу
        stages = [
            ("telethon_ingest_scheduler", "Telethon-ingest Scheduler"),
            ("api_scheduler", "API Scheduler"),
            ("floodwait", "FloodWait"),
            ("parsing", "Парсинг"),
            ("vision", "Vision анализ"),
            ("tagging", "Тегирование"),
            ("enrichment", "Обогащение (Crawl4AI)"),
            ("qdrant", "Qdrant"),
            ("neo4j", "Neo4j"),
            ("indexing", "Индексация")
        ]
        
        report_lines.append("## Сводка по этапам\n\n")
        report_lines.append("| Этап | Статус | Детали |\n")
        report_lines.append("|------|--------|--------|\n")
        
        for stage_key, stage_name in stages:
            stage_result = self.results.get(stage_key, {})
            status = stage_result.get("status", "unknown")
            
            # Эмодзи для статуса
            if status == "ok":
                status_emoji = "✅"
            elif status == "warning":
                status_emoji = "⚠️"
            elif status == "error":
                status_emoji = "❌"
            else:
                status_emoji = "❓"
            
            # Детали
            details = []
            if stage_key == "telethon_ingest_scheduler":
                if stage_result.get("running"):
                    details.append(f"Запущен (status: {stage_result.get('status', 'unknown')})")
                else:
                    details.append("Не запущен")
            elif stage_key == "api_scheduler":
                if stage_result.get("running"):
                    details.append(f"Запущен ({stage_result.get('jobs_count', 0)} задач)")
                else:
                    details.append("Не запущен")
            elif stage_key == "floodwait":
                active_count = len(stage_result.get("active_floodwaits", []))
                cooldown_count = len(stage_result.get("channels_in_cooldown", []))
                if active_count > 0 or cooldown_count > 0:
                    details.append(f"{active_count} активных, {cooldown_count} каналов в cooldown")
                else:
                    details.append("Нет активных FloodWait")
            elif stage_key == "parsing":
                details.append(f"{stage_result.get('recent_posts', 0)} постов за 24ч")
            elif stage_key == "vision":
                details.append(f"{stage_result.get('recent_analyses', 0)} анализов за 24ч")
            elif stage_key == "tagging":
                details.append(f"{stage_result.get('recent_tags', 0)} тегирований за 24ч")
            elif stage_key == "enrichment":
                details.append(f"{stage_result.get('recent_enrichments', 0)} обогащений за 24ч")
            elif stage_key == "qdrant":
                details.append(f"{len(stage_result.get('collections', []))} коллекций")
            elif stage_key == "neo4j":
                details.append(f"{stage_result.get('post_nodes', 0)} постов, {stage_result.get('album_nodes', 0)} альбомов")
            elif stage_key == "indexing":
                details.append(f"{stage_result.get('recent_indexed', 0)} проиндексировано за 24ч")
            
            details_str = ", ".join(details) if details else "-"
            
            report_lines.append(f"| {stage_name} | {status_emoji} {status} | {details_str} |\n")
        
        # Проблемы
        all_issues = []
        for stage_key, stage_name in stages:
            stage_result = self.results.get(stage_key, {})
            issues = stage_result.get("issues", [])
            if issues:
                all_issues.extend([f"**{stage_name}**: {issue}" for issue in issues])
        
        if all_issues:
            report_lines.append("\n## Проблемы\n\n")
            for issue in all_issues:
                report_lines.append(f"- {issue}\n")
        
        return "".join(report_lines)


async def main():
    """Главная функция."""
    import time
    checker = ComprehensiveChecker()
    
    try:
        await checker.initialize()
        
        # Проверка всех этапов
        await checker.check_telethon_ingest_scheduler()
        await checker.check_api_scheduler()
        await checker.check_floodwait()
        await checker.check_parsing()
        await checker.check_vision()
        await checker.check_tagging()
        await checker.check_enrichment()
        await checker.check_qdrant()
        await checker.check_neo4j()
        await checker.check_indexing()
        
        # Генерация отчета
        report = checker.generate_report()
        
        # Сохранение отчета
        report_path = PROJECT_ROOT / "reports" / f"PIPELINE_SCHEDULER_FLOODWAIT_CHECK_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        
        print_success(f"Отчет сохранен: {report_path}")
        
        # Вывод отчета
        print(report)
        
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await checker.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
