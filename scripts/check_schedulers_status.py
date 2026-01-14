#!/usr/bin/env python3
"""
Проверка статуса всех scheduler'ов в системе.
Context7: Проверка telethon-ingest и api scheduler'ов
"""

import os
import sys
import json
import asyncio
import aiohttp
from datetime import datetime, timezone
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


class SchedulerStatusChecker:
    """Проверка статуса всех scheduler'ов."""
    
    def __init__(self):
        self.telethon_ingest_url = os.getenv("TELETHON_INGEST_URL", "http://localhost:8011")
        self.api_url = os.getenv("API_URL", "http://localhost:8001")
        self.prometheus_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        
    async def check_telethon_ingest_scheduler(self):
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
            # Проверка health/details endpoint
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
            
            # Проверка метрик Prometheus
            try:
                async with aiohttp.ClientSession() as session:
                    query = "scheduler_last_tick_ts_seconds"
                    url = f"{self.prometheus_url}/api/v1/query"
                    params = {"query": query}
                    
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            prom_result = data.get("data", {}).get("result", [])
                            
                            if prom_result:
                                timestamp = float(prom_result[0].get("value", [None, "0"])[1])
                                if timestamp > 0:
                                    tick_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                                    print_success(f"Метрика Prometheus: последний тик {tick_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                                else:
                                    print_warning("Метрика Prometheus: timestamp = 0 (scheduler не обновлял метрику)")
                            else:
                                print_warning("Метрика scheduler_last_tick_ts_seconds не найдена в Prometheus")
            except Exception as e:
                result["issues"].append(f"Ошибка проверки Prometheus: {e}")
                print_warning(f"Не удалось проверить метрики Prometheus: {e}")
                
        except aiohttp.ClientError as e:
            result["status"] = "error"
            result["issues"].append(f"Не удалось подключиться: {e}")
            print_error(f"Не удалось подключиться к telethon-ingest: {e}")
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка: {e}")
            print_error(f"Ошибка проверки: {e}")
            import traceback
            traceback.print_exc()
        
        return result
    
    async def check_api_scheduler(self):
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
            # Проверка health endpoint
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/health", timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
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
            
            # Проверка метрик Prometheus
            try:
                async with aiohttp.ClientSession() as session:
                    # Проверка scheduler_running
                    query = "scheduler_running"
                    url = f"{self.prometheus_url}/api/v1/query"
                    params = {"query": query}
                    
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            prom_result = data.get("data", {}).get("result", [])
                            
                            if prom_result:
                                running_value = float(prom_result[0].get("value", [None, "0"])[1])
                                if running_value == 1:
                                    print_success("Метрика Prometheus: scheduler_running = 1")
                                else:
                                    print_warning(f"Метрика Prometheus: scheduler_running = {running_value}")
                            
                    # Проверка scheduler_jobs_total
                    query = "scheduler_jobs_total"
                    params = {"query": query}
                    
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            prom_result = data.get("data", {}).get("result", [])
                            
                            if prom_result:
                                jobs_count = float(prom_result[0].get("value", [None, "0"])[1])
                                print_info(f"Метрика Prometheus: scheduler_jobs_total = {jobs_count}")
            except Exception as e:
                result["issues"].append(f"Ошибка проверки Prometheus: {e}")
                print_warning(f"Не удалось проверить метрики Prometheus: {e}")
                
        except aiohttp.ClientError as e:
            result["status"] = "error"
            result["issues"].append(f"Не удалось подключиться: {e}")
            print_error(f"Не удалось подключиться к api: {e}")
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(f"Ошибка: {e}")
            print_error(f"Ошибка проверки: {e}")
            import traceback
            traceback.print_exc()
        
        return result
    
    async def check_parser_activity(self):
        """Проверка активности парсера."""
        print_header("3. АКТИВНОСТЬ ПАРСЕРА")
        
        try:
            async with aiohttp.ClientSession() as session:
                # Проверка parser_runs_total
                query = "parser_runs_total"
                url = f"{self.prometheus_url}/api/v1/query"
                params = {"query": query}
                
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        prom_result = data.get("data", {}).get("result", [])
                        
                        if prom_result:
                            total = sum(float(item.get("value", [None, "0"])[1]) for item in prom_result)
                            print_success(f"Всего запусков парсера: {total}")
                            
                            for item in prom_result:
                                mode = item.get("metric", {}).get("mode", "unknown")
                                status = item.get("metric", {}).get("status", "unknown")
                                value = float(item.get("value", [None, "0"])[1])
                                if value > 0:
                                    print_info(f"  - mode={mode}, status={status}: {value}")
                        else:
                            print_warning("Метрика parser_runs_total не найдена")
                            
                # Проверка posts_parsed_total
                query = "posts_parsed_total"
                params = {"query": query}
                
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        prom_result = data.get("data", {}).get("result", [])
                        
                        if prom_result:
                            total = sum(float(item.get("value", [None, "0"])[1]) for item in prom_result)
                            print_success(f"Всего распарсено постов: {total}")
                            
                            for item in prom_result:
                                mode = item.get("metric", {}).get("mode", "unknown")
                                status = item.get("metric", {}).get("status", "unknown")
                                value = float(item.get("value", [None, "0"])[1])
                                if value > 0:
                                    print_info(f"  - mode={mode}, status={status}: {value}")
                        else:
                            print_warning("Метрика posts_parsed_total не найдена")
                            
        except Exception as e:
            print_warning(f"Не удалось проверить активность парсера: {e}")
    
    async def generate_summary(self, telethon_result, api_result):
        """Генерация итогового отчета."""
        print_header("ИТОГОВЫЙ ОТЧЕТ")
        
        print("="*80)
        print("СТАТУС SCHEDULER'ОВ")
        print("="*80)
        
        # Telethon-ingest scheduler
        print("\n📡 Telethon-ingest Scheduler (ParseAllChannelsTask):")
        if telethon_result["running"]:
            print_success(f"  Статус: {telethon_result['status']}")
            print_info(f"  Последний тик: {telethon_result['last_tick']}")
            print_info(f"  Интервал: {telethon_result['interval_sec']} секунд ({telethon_result['interval_sec'] // 60} минут)")
            if telethon_result["lock_owner"]:
                print_info(f"  Lock owner: {telethon_result['lock_owner']}")
        else:
            print_error(f"  Статус: {telethon_result['status']}")
            if telethon_result["issues"]:
                for issue in telethon_result["issues"]:
                    print(f"    - {issue}")
        
        # API scheduler
        print("\n⚙️  API Scheduler (SchedulerTasks):")
        if api_result["running"]:
            print_success(f"  Статус: работает")
            print_info(f"  Задач: {api_result['jobs_count']}")
            if api_result["jobs"]:
                print_info("  Задачи:")
                for job_id in api_result["jobs"]:
                    print(f"    - {job_id}")
        else:
            print_error(f"  Статус: не работает")
            if api_result["issues"]:
                for issue in api_result["issues"]:
                    print(f"    - {issue}")
        
        # Общий статус
        print("\n" + "="*80)
        print("ОБЩИЙ СТАТУС")
        print("="*80)
        
        if telethon_result["running"] and api_result["running"]:
            print_success("✅ Все scheduler'ы работают")
        elif telethon_result["running"]:
            print_warning("⚠️  Telethon-ingest scheduler работает, API scheduler не работает")
        elif api_result["running"]:
            print_warning("⚠️  API scheduler работает, telethon-ingest scheduler не работает")
        else:
            print_error("❌ Оба scheduler'а не работают")
        
        # Рекомендации
        print("\n" + "="*80)
        print("РЕКОМЕНДАЦИИ")
        print("="*80)
        
        if not telethon_result["running"]:
            print("1. Telethon-ingest scheduler:")
            print("   - Проверьте логи: docker compose logs telethon-ingest --tail=100")
            print("   - Проверьте переменные окружения: PARSER_SCHEDULER_INTERVAL_SEC")
            print("   - Проверьте Redis: redis-cli GET parse_all_channels:lock")
        
        if not api_result["running"]:
            print("2. API scheduler:")
            print("   - Проверьте логи: docker compose logs api --tail=100")
            print("   - Проверьте health endpoint: curl http://localhost:8001/health")
            print("   - Перезапустите сервис: docker compose restart api")


async def main():
    """Главная функция."""
    checker = SchedulerStatusChecker()
    
    try:
        telethon_result = await checker.check_telethon_ingest_scheduler()
        api_result = await checker.check_api_scheduler()
        await checker.check_parser_activity()
        await checker.generate_summary(telethon_result, api_result)
        
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
