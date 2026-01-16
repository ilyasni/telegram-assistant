#!/usr/bin/env python3
"""
Проверка проблемы с метрикой Channel Processing Time.
Context7: Диагностика падения метрики parser_channel_processing_seconds
"""

import os
import sys
import json
import asyncio
import asyncpg
import aiohttp
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path("/opt/telegram-assistant")
sys.path.insert(0, str(PROJECT_ROOT))

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


class ChannelProcessingTimeChecker:
    """Проверка метрики Channel Processing Time."""
    
    def __init__(self):
        self.db_pool = None
        self.prometheus_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        self.telethon_ingest_url = os.getenv("TELETHON_INGEST_URL", "http://localhost:8011")
        
    async def initialize(self):
        """Инициализация подключений."""
        try:
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/telegram_assistant")
            self.db_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
            print_success("PostgreSQL подключен")
        except Exception as e:
            print_error(f"Ошибка подключения к БД: {e}")
            raise
    
    async def cleanup(self):
        """Закрытие подключений."""
        if self.db_pool:
            await self.db_pool.close()
    
    async def check_metric_in_prometheus(self):
        """Проверка метрики в Prometheus."""
        print_header("1. ПРОВЕРКА МЕТРИКИ В PROMETHEUS")
        
        try:
            async with aiohttp.ClientSession() as session:
                # Проверка наличия метрики
                query = "parser_channel_processing_seconds_bucket"
                url = f"{self.prometheus_url}/api/v1/query"
                params = {"query": query}
                
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data.get("data", {}).get("result", [])
                        
                        if result:
                            print_success(f"Метрика найдена: {len(result)} записей")
                            
                            # Анализ последних значений
                            recent_values = []
                            for item in result[:10]:  # Первые 10
                                value = item.get("value", [None, "0"])[1]
                                metric = item.get("metric", {})
                                mode = metric.get("mode", "unknown")
                                status = metric.get("status", "unknown")
                                le = metric.get("le", "unknown")
                                recent_values.append({
                                    "mode": mode,
                                    "status": status,
                                    "le": le,
                                    "value": value
                                })
                            
                            print_info("Последние значения метрики:")
                            for v in recent_values[:5]:
                                print(f"  - mode={v['mode']}, status={v['status']}, le={v['le']}, value={v['value']}")
                        else:
                            print_warning("Метрика не найдена или пуста")
                            return False
                    else:
                        print_error(f"Ошибка запроса Prometheus: {resp.status}")
                        return False
                
                # Проверка перцентилей
                print_info("\nПроверка перцентилей:")
                for percentile in [0.50, 0.95, 0.99]:
                    query = f"histogram_quantile({percentile}, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))"
                    params = {"query": query}
                    
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            result = data.get("data", {}).get("result", [])
                            
                            if result:
                                for item in result:
                                    value = float(item.get("value", [None, "0"])[1])
                                    metric = item.get("metric", {})
                                    mode = metric.get("mode", "unknown")
                                    status = metric.get("status", "unknown")
                                    
                                    p_name = f"p{int(percentile * 100)}"
                                    if value > 0:
                                        print_success(f"  {p_name} (mode={mode}, status={status}): {value:.2f} сек")
                                    else:
                                        print_warning(f"  {p_name} (mode={mode}, status={status}): {value:.2f} сек (нет данных)")
                            else:
                                print_warning(f"  p{int(percentile * 100)}: нет данных")
                
                return True
                
        except Exception as e:
            print_error(f"Ошибка проверки Prometheus: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def check_telethon_ingest_status(self):
        """Проверка статуса telethon-ingest сервиса."""
        print_header("2. ПРОВЕРКА СТАТУСА TELETHON-INGEST")
        
        try:
            async with aiohttp.ClientSession() as session:
                # Проверка health endpoint
                url = f"{self.telethon_ingest_url}/health"
                
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print_success("Telethon-ingest сервис доступен")
                        
                        # Проверка scheduler
                        scheduler = data.get("scheduler", {})
                        if scheduler:
                            status = scheduler.get("status", "unknown")
                            last_tick = scheduler.get("last_tick_ts", "never")
                            
                            if status == "ok":
                                print_success(f"Scheduler статус: {status}")
                            else:
                                print_warning(f"Scheduler статус: {status}")
                            
                            print_info(f"Последний тик: {last_tick}")
                        else:
                            print_warning("Scheduler информация не найдена")
                        
                        # Проверка parser
                        parser = data.get("parser", {})
                        if parser:
                            initialized = parser.get("initialized", False)
                            if initialized:
                                print_success("Parser инициализирован")
                            else:
                                print_warning("Parser не инициализирован")
                        else:
                            print_warning("Parser информация не найдена")
                        
                        return True
                    else:
                        print_error(f"Health endpoint вернул {resp.status}")
                        return False
                        
        except aiohttp.ClientError as e:
            print_error(f"Не удалось подключиться к telethon-ingest: {e}")
            print_info("Проверьте, запущен ли контейнер telethon-ingest")
            return False
        except Exception as e:
            print_error(f"Ошибка проверки telethon-ingest: {e}")
            return False
    
    async def check_recent_parsing_activity(self):
        """Проверка недавней активности парсинга."""
        print_header("3. ПРОВЕРКА АКТИВНОСТИ ПАРСИНГА")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Проверка последних обновлений last_parsed_at
                recent_updates = await conn.fetch("""
                    SELECT 
                        id,
                        username,
                        title,
                        last_parsed_at,
                        EXTRACT(EPOCH FROM (NOW() - last_parsed_at)) / 3600 as hours_ago
                    FROM channels
                    WHERE last_parsed_at IS NOT NULL
                    ORDER BY last_parsed_at DESC
                    LIMIT 10
                """)
                
                if recent_updates:
                    print_success(f"Найдено {len(recent_updates)} каналов с недавним парсингом:")
                    for ch in recent_updates[:5]:
                        hours_ago = ch['hours_ago']
                        if hours_ago < 1:
                            status = "✅"
                        elif hours_ago < 6:
                            status = "⚠️"
                        else:
                            status = "❌"
                        print(f"  {status} {ch['username'] or ch['id']}: {hours_ago:.1f} часов назад")
                else:
                    print_warning("Нет каналов с недавним парсингом")
                
                # Проверка постов за последние 24 часа
                posts_24h = await conn.fetchval("""
                    SELECT COUNT(*) 
                    FROM posts 
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                """)
                
                if posts_24h and posts_24h > 0:
                    print_success(f"Постов за последние 24 часа: {posts_24h}")
                else:
                    print_warning("Нет постов за последние 24 часа")
                
                # Проверка последних постов
                last_posts = await conn.fetch("""
                    SELECT 
                        p.id,
                        p.channel_id,
                        p.created_at,
                        c.username,
                        EXTRACT(EPOCH FROM (NOW() - p.created_at)) / 3600 as hours_ago
                    FROM posts p
                    LEFT JOIN channels c ON c.id = p.channel_id
                    ORDER BY p.created_at DESC
                    LIMIT 5
                """)
                
                if last_posts:
                    print_info("\nПоследние посты:")
                    for post in last_posts:
                        hours_ago = post['hours_ago']
                        print(f"  - {post['username'] or post['channel_id']}: {hours_ago:.2f} часов назад")
                
                return True
                
        except Exception as e:
            print_error(f"Ошибка проверки активности: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def check_parser_metrics(self):
        """Проверка других метрик парсера."""
        print_header("4. ПРОВЕРКА ДРУГИХ МЕТРИК ПАРСЕРА")
        
        try:
            async with aiohttp.ClientSession() as session:
                metrics_to_check = [
                    "parser_runs_total",
                    "posts_parsed_total",
                    "parser_mode_forced_total"
                ]
                
                url = f"{self.prometheus_url}/api/v1/query"
                
                for metric_name in metrics_to_check:
                    params = {"query": metric_name}
                    
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            result = data.get("data", {}).get("result", [])
                            
                            if result:
                                total = sum(float(item.get("value", [None, "0"])[1]) for item in result)
                                print_success(f"{metric_name}: {total}")
                            else:
                                print_warning(f"{metric_name}: нет данных")
                        else:
                            print_warning(f"{metric_name}: ошибка запроса ({resp.status})")
                
                return True
                
        except Exception as e:
            print_error(f"Ошибка проверки метрик: {e}")
            return False
    
    async def generate_report(self):
        """Генерация отчета."""
        print_header("ИТОГОВЫЙ ОТЧЕТ")
        
        # Проверки
        metric_ok = await self.check_metric_in_prometheus()
        service_ok = await self.check_telethon_ingest_status()
        activity_ok = await self.check_recent_parsing_activity()
        metrics_ok = await self.check_parser_metrics()
        
        # Итоги
        print("\n" + "="*80)
        print("ИТОГИ ПРОВЕРКИ")
        print("="*80)
        
        if not metric_ok:
            print_error("Метрика parser_channel_processing_seconds не найдена или пуста")
            print_info("Возможные причины:")
            print_info("  1. Парсер не запущен")
            print_info("  2. Парсер не обрабатывает каналы")
            print_info("  3. Метрика не экспортируется в Prometheus")
        
        if not service_ok:
            print_error("Telethon-ingest сервис недоступен")
            print_info("Проверьте статус контейнера: docker-compose ps telethon-ingest")
        
        if not activity_ok:
            print_warning("Нет недавней активности парсинга")
            print_info("Проверьте логи: docker-compose logs telethon-ingest")
        
        if metric_ok and service_ok and activity_ok:
            print_success("Все проверки пройдены - проблема может быть в отсутствии новых данных")
            print_info("Если метрика 'упала', это может означать:")
            print_info("  1. Нет новых каналов для парсинга")
            print_info("  2. Все каналы уже обработаны")
            print_info("  3. Парсер работает, но не обновляет метрику")


async def main():
    """Главная функция."""
    checker = ChannelProcessingTimeChecker()
    
    try:
        await checker.initialize()
        await checker.generate_report()
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await checker.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
