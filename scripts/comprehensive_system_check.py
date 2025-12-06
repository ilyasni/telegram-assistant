#!/usr/bin/env python3
"""
Комплексная проверка стабильности системы Telegram Assistant.
Проверяет:
1. Стабильность сборки (Docker контейнеры, health checks)
2. System Overview (health endpoints, периодические отказы)
3. Scheduler статус и режим работы
4. Весь пайплайн: парсинг → vision → тегирование → обогащение → Qdrant + Neo4j
5. Context7 best practices

Context7: Использует best practices для диагностики production систем.
"""

import asyncio
import json
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import aiohttp

# Настройка логирования
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CheckStatus(Enum):
    PASS = "✅"
    FAIL = "❌"
    WARN = "⚠️"
    SKIP = "⏭️"

@dataclass
class CheckResult:
    category: str
    name: str
    status: CheckStatus
    message: str
    details: Optional[Dict[str, Any]] = None

class ComprehensiveSystemChecker:
    """Комплексная проверка системы."""
    
    def __init__(self):
        self.results: List[CheckResult] = []
        self.base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    def add_result(self, category: str, name: str, status: CheckStatus, 
                   message: str, details: Optional[Dict] = None):
        """Добавление результата проверки."""
        self.results.append(CheckResult(category, name, status, message, details))
        
    async def check_docker_containers(self):
        """1. Проверка стабильности сборки - Docker контейнеры."""
        logger.info("Checking Docker containers...")
        
        try:
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                cwd="/opt/telegram-assistant",
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                self.add_result(
                    "Docker", "Container Status",
                    CheckStatus.FAIL,
                    f"Cannot check containers: {result.stderr}"
                )
                return
            
            containers = []
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            
            unhealthy = [c for c in containers if 'unhealthy' in c.get('Status', '').lower()]
            restarting = [c for c in containers if 'Restarting' in c.get('Status', '')]
            healthy = [c for c in containers if 'healthy' in c.get('Status', '').lower()]
            
            # Проверка критичных сервисов
            critical_services = ['api', 'worker', 'telethon-ingest', 'supabase-db', 'redis', 'qdrant', 'neo4j']
            missing = []
            for service in critical_services:
                found = any(service in c.get('Service', '') for c in containers)
                if not found:
                    missing.append(service)
            
            if missing:
                self.add_result(
                    "Docker", "Critical Services",
                    CheckStatus.FAIL,
                    f"Missing services: {', '.join(missing)}"
                )
            else:
                self.add_result(
                    "Docker", "Critical Services",
                    CheckStatus.PASS,
                    f"All {len(critical_services)} critical services running"
                )
            
            if unhealthy:
                self.add_result(
                    "Docker", "Unhealthy Containers",
                    CheckStatus.WARN,
                    f"{len(unhealthy)} unhealthy containers",
                    {"containers": [c.get('Service') for c in unhealthy]}
                )
            else:
                self.add_result(
                    "Docker", "Container Health",
                    CheckStatus.PASS,
                    f"{len(healthy)} healthy containers"
                )
            
            if restarting:
                self.add_result(
                    "Docker", "Restarting Containers",
                    CheckStatus.WARN,
                    f"{len(restarting)} containers restarting",
                    {"containers": [c.get('Service') for c in restarting]}
                )
            
            # Проверка недавних перезапусков (признак нестабильности)
            recent_restarts = []
            for c in containers:
                status = c.get('Status', '')
                if 'Up' in status:
                    # Проверяем время работы (если менее 5 минут - возможно недавний перезапуск)
                    if 'minute' in status.lower():
                        minutes = int(''.join(filter(str.isdigit, status.split('minute')[0].split()[-1])))
                        if minutes < 5:
                            recent_restarts.append(c.get('Service'))
            
            if recent_restarts:
                self.add_result(
                    "Docker", "Recent Restarts",
                    CheckStatus.WARN,
                    f"{len(recent_restarts)} containers restarted recently",
                    {"containers": recent_restarts}
                )
                
        except Exception as e:
            self.add_result(
                "Docker", "Container Check",
                CheckStatus.FAIL,
                f"Error: {str(e)}"
            )
    
    async def check_system_overview(self):
        """2. Проверка System Overview - health endpoints."""
        logger.info("Checking System Overview...")
        
        endpoints = [
            ("API Health", f"{self.base_url}/health"),
            ("API Health Detailed", f"{self.base_url}/api/health"),
            ("Telethon Health", "http://localhost:8011/health"),
            ("Telethon Health Details", "http://localhost:8011/health/details"),
            ("Worker Health", "http://localhost:8000/worker/health"),
            ("Qdrant Health", "http://localhost:6333/health"),
            ("Neo4j Health", "http://localhost:7475/health"),
        ]
        
        for name, url in endpoints:
            try:
                async with self.session.get(url) as resp:
                    status = resp.status
                    if status == 200:
                        try:
                            data = await resp.json()
                            self.add_result(
                                "System Overview", name,
                                CheckStatus.PASS,
                                f"Status: {status}",
                                {"response": data}
                            )
                        except:
                            text = await resp.text()
                            self.add_result(
                                "System Overview", name,
                                CheckStatus.PASS,
                                f"Status: {status}",
                                {"response_preview": text[:200]}
                            )
                    else:
                        self.add_result(
                            "System Overview", name,
                            CheckStatus.WARN,
                            f"Status: {status} (expected 200)"
                        )
            except asyncio.TimeoutError:
                self.add_result(
                    "System Overview", name,
                    CheckStatus.FAIL,
                    "Timeout (endpoint недоступен)"
                )
            except Exception as e:
                self.add_result(
                    "System Overview", name,
                    CheckStatus.FAIL,
                    f"Error: {str(e)}"
                )
    
    async def check_scheduler(self):
        """3. Проверка Scheduler - статус и режим."""
        logger.info("Checking Scheduler...")
        
        # Проверка через API endpoint (если есть)
        try:
            async with self.session.get(f"{self.base_url}/api/health") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    scheduler_info = data.get('scheduler', {})
                    if scheduler_info:
                        running = scheduler_info.get('running', False)
                        if running:
                            self.add_result(
                                "Scheduler", "Status",
                                CheckStatus.PASS,
                                "Scheduler is running",
                                scheduler_info
                            )
                        else:
                            self.add_result(
                                "Scheduler", "Status",
                                CheckStatus.FAIL,
                                "Scheduler is NOT running",
                                scheduler_info
                            )
                    else:
                        self.add_result(
                            "Scheduler", "Status",
                            CheckStatus.WARN,
                            "Scheduler info not available in health endpoint"
                        )
        except Exception as e:
            logger.warning(f"Cannot check scheduler via API: {e}")
        
        # Проверка через telethon-ingest health/details
        try:
            async with self.session.get("http://localhost:8011/health/details") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    scheduler_info = data.get('scheduler', {})
                    if scheduler_info:
                        status = scheduler_info.get('status', 'unknown')
                        last_tick = scheduler_info.get('last_tick_ts')
                        
                        if status == 'ok':
                            self.add_result(
                                "Scheduler", "Telethon Scheduler",
                                CheckStatus.PASS,
                                f"Status: {status}, Last tick: {last_tick}",
                                scheduler_info
                            )
                        elif status == 'stale':
                            self.add_result(
                                "Scheduler", "Telethon Scheduler",
                                CheckStatus.WARN,
                                f"Status: {status} (last tick too old)",
                                scheduler_info
                            )
                        else:
                            self.add_result(
                                "Scheduler", "Telethon Scheduler",
                                CheckStatus.FAIL,
                                f"Status: {status}",
                                scheduler_info
                            )
        except Exception as e:
            logger.warning(f"Cannot check telethon scheduler: {e}")
        
        # Проверка через логи API
        try:
            result = subprocess.run(
                ["docker", "logs", "telegram-assistant-api-1", "--tail", "100"],
                capture_output=True,
                text=True,
                timeout=10
            )
            logs = result.stdout.lower()
            if 'scheduler started' in logs:
                self.add_result(
                    "Scheduler", "API Scheduler Logs",
                    CheckStatus.PASS,
                    "Found 'scheduler started' in logs"
                )
            elif 'scheduler' in logs and 'error' in logs:
                self.add_result(
                    "Scheduler", "API Scheduler Logs",
                    CheckStatus.FAIL,
                    "Found scheduler errors in logs"
                )
            else:
                self.add_result(
                    "Scheduler", "API Scheduler Logs",
                    CheckStatus.WARN,
                    "No clear scheduler status in logs"
                )
        except Exception as e:
            logger.warning(f"Cannot check API logs: {e}")
    
    async def check_pipeline(self):
        """4. Проверка пайплайна: парсинг → vision → тегирование → обогащение → Qdrant + Neo4j."""
        logger.info("Checking Pipeline...")
        
        # Проверка Redis Streams
        try:
            result = subprocess.run(
                ["docker", "exec", "telegram-assistant-redis-1", 
                 "redis-cli", "--scan", "--pattern", "stream:*"],
                capture_output=True,
                text=True,
                timeout=10
            )
            streams = [s.strip() for s in result.stdout.strip().split('\n') if s.strip()]
            
            required_streams = [
                'stream:posts:parsed',
                'stream:posts:tagged',
                'stream:posts:vision:analyzed',
                'stream:posts:enriched',
                'stream:posts:indexed',
            ]
            
            missing_streams = [s for s in required_streams if s not in streams]
            if missing_streams:
                self.add_result(
                    "Pipeline", "Redis Streams",
                    CheckStatus.WARN,
                    f"Missing streams: {', '.join(missing_streams)}"
                )
            else:
                self.add_result(
                    "Pipeline", "Redis Streams",
                    CheckStatus.PASS,
                    f"All {len(required_streams)} required streams exist"
                )
            
            # Проверка pending сообщений в группах
            for stream in required_streams:
                try:
                    result = subprocess.run(
                        ["docker", "exec", "telegram-assistant-redis-1",
                         "redis-cli", "XINFO", "GROUPS", stream],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        output = result.stdout
                        if 'pending' in output.lower():
                            # Парсим pending count
                            lines = output.split('\n')
                            for line in lines:
                                if 'pending' in line.lower() and ':' in line:
                                    pending = int(''.join(filter(str.isdigit, line.split(':')[-1])))
                                    if pending > 0:
                                        self.add_result(
                                            "Pipeline", f"{stream} Pending",
                                            CheckStatus.WARN,
                                            f"{pending} pending messages"
                                        )
                except Exception as e:
                    logger.debug(f"Cannot check {stream} pending: {e}")
        except Exception as e:
            self.add_result(
                "Pipeline", "Redis Streams",
                CheckStatus.FAIL,
                f"Error checking streams: {str(e)}"
            )
        
        # Проверка Qdrant
        try:
            async with self.session.get("http://localhost:6333/collections") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    collections = data.get('result', {}).get('collections', [])
                    if collections:
                        self.add_result(
                            "Pipeline", "Qdrant Collections",
                            CheckStatus.PASS,
                            f"{len(collections)} collections found",
                            {"collections": [c.get('name') for c in collections]}
                        )
                    else:
                        self.add_result(
                            "Pipeline", "Qdrant Collections",
                            CheckStatus.WARN,
                            "No collections found"
                        )
        except Exception as e:
            self.add_result(
                "Pipeline", "Qdrant",
                CheckStatus.FAIL,
                f"Error: {str(e)}"
            )
        
        # Проверка Neo4j
        try:
            async with self.session.get("http://localhost:7475/health") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.add_result(
                        "Pipeline", "Neo4j Health",
                        CheckStatus.PASS,
                        "Neo4j is healthy",
                        data
                    )
                else:
                    self.add_result(
                        "Pipeline", "Neo4j Health",
                        CheckStatus.WARN,
                        f"Status: {resp.status}"
                    )
        except Exception as e:
            self.add_result(
                "Pipeline", "Neo4j",
                CheckStatus.FAIL,
                f"Error: {str(e)}"
            )
    
    async def run_all_checks(self):
        """Запуск всех проверок."""
        logger.info("Starting comprehensive system check...")
        
        await self.check_docker_containers()
        await self.check_system_overview()
        await self.check_scheduler()
        await self.check_pipeline()
        
        logger.info(f"Completed {len(self.results)} checks")
    
    def generate_report(self) -> str:
        """Генерация отчета."""
        report = []
        report.append("# Комплексная проверка системы Telegram Assistant")
        report.append(f"**Дата**: {datetime.now(timezone.utc).isoformat()}")
        report.append("")
        
        # Группировка по категориям
        categories = {}
        for result in self.results:
            if result.category not in categories:
                categories[result.category] = []
            categories[result.category].append(result)
        
        # Подсчет статистики
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == CheckStatus.PASS)
        failed = sum(1 for r in self.results if r.status == CheckStatus.FAIL)
        warnings = sum(1 for r in self.results if r.status == CheckStatus.WARN)
        
        report.append("## Общая статистика")
        report.append(f"- **Всего проверок**: {total}")
        report.append(f"- **✅ Успешно**: {passed}")
        report.append(f"- **❌ Ошибки**: {failed}")
        report.append(f"- **⚠️ Предупреждения**: {warnings}")
        report.append("")
        
        # Детальные результаты по категориям
        for category, results in sorted(categories.items()):
            report.append(f"## {category}")
            report.append("")
            
            for result in results:
                status_icon = result.status.value
                report.append(f"### {status_icon} {result.name}")
                report.append(f"**Статус**: {result.message}")
                if result.details:
                    report.append(f"**Детали**:")
                    report.append("```json")
                    report.append(json.dumps(result.details, indent=2, ensure_ascii=False))
                    report.append("```")
                report.append("")
        
        # Рекомендации
        if failed > 0 or warnings > 0:
            report.append("## Рекомендации")
            report.append("")
            
            if failed > 0:
                report.append("### Критичные проблемы")
                failed_results = [r for r in self.results if r.status == CheckStatus.FAIL]
                for result in failed_results:
                    report.append(f"- **{result.category} / {result.name}**: {result.message}")
                report.append("")
            
            if warnings > 0:
                report.append("### Предупреждения")
                warn_results = [r for r in self.results if r.status == CheckStatus.WARN]
                for result in warn_results:
                    report.append(f"- **{result.category} / {result.name}**: {result.message}")
                report.append("")
        
        return "\n".join(report)


async def main():
    """Главная функция."""
    async with ComprehensiveSystemChecker() as checker:
        await checker.run_all_checks()
        report = checker.generate_report()
        
        print(report)
        
        # Сохранение отчета
        report_file = f"/opt/telegram-assistant/reports/comprehensive_check_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
        os.makedirs(os.path.dirname(report_file), exist_ok=True)
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"\nОтчет сохранен: {report_file}")
        
        # Exit code based on results
        failed = sum(1 for r in checker.results if r.status == CheckStatus.FAIL)
        if failed > 0:
            sys.exit(1)
        else:
            sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())

