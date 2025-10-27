#!/usr/bin/env python3
"""
Система полной проверки Telegram Assistant
Проверяет все endpoints, метрики, конфигурацию и выявляет проблемы
"""

import asyncio
import aiohttp
import json
import time
import sys
import os
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging
from pathlib import Path

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CheckStatus(Enum):
    PASS = "✅ PASS"
    FAIL = "❌ FAIL"
    WARN = "⚠️  WARN"
    SKIP = "⏭️  SKIP"

@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    details: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None

class SystemHealthChecker:
    """Основной класс для проверки системы."""
    
    def __init__(self, base_url: str = "http://localhost:8000", supabase_host: str = None):
        self.base_url = base_url
        self.supabase_host = supabase_host or os.getenv("SUPABASE_HOST", "localhost")
        self.results: List[CheckResult] = []
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "SystemHealthChecker/1.0"}
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def check_endpoint(self, name: str, url: str, expected_status: int = 200, 
                           method: str = "GET", data: Optional[Dict] = None) -> CheckResult:
        """Проверка endpoint'а."""
        start_time = time.time()
        
        try:
            if method.upper() == "GET":
                async with self.session.get(url) as response:
                    status = response.status
                    content = await response.text()
            elif method.upper() == "POST":
                async with self.session.post(url, json=data) as response:
                    status = response.status
                    content = await response.text()
            else:
                return CheckResult(name, CheckStatus.FAIL, f"Unsupported method: {method}")
            
            duration_ms = (time.time() - start_time) * 1000
            
            if status == expected_status:
                return CheckResult(
                    name, CheckStatus.PASS, 
                    f"Status {status} (expected {expected_status})",
                    {"status": status, "response_size": len(content)},
                    duration_ms
                )
            else:
                return CheckResult(
                    name, CheckStatus.FAIL,
                    f"Status {status} (expected {expected_status})",
                    {"status": status, "response": content[:200]},
                    duration_ms
                )
                
        except asyncio.TimeoutError:
            return CheckResult(name, CheckStatus.FAIL, "Timeout", duration_ms=(time.time() - start_time) * 1000)
        except Exception as e:
            return CheckResult(name, CheckStatus.FAIL, f"Error: {str(e)}", duration_ms=(time.time() - start_time) * 1000)
    
    async def check_health_endpoints(self) -> List[CheckResult]:
        """Проверка health endpoints."""
        results = []
        
        # Основные health checks
        health_endpoints = [
            ("API Health", f"{self.base_url}/health"),
            ("API Ready", f"{self.base_url}/api/health"),
            ("API Metrics", f"{self.base_url}/metrics"),
            ("API Root", f"{self.base_url}/"),
        ]
        
        for name, url in health_endpoints:
            result = await self.check_endpoint(name, url)
            results.append(result)
        
        return results
    
    async def check_api_endpoints(self) -> List[CheckResult]:
        """Проверка API endpoints."""
        results = []
        
        # API endpoints (без авторизации)
        api_endpoints = [
            ("Channels List", f"{self.base_url}/api/channels/users/123456789/list"),
            ("Users Get", f"{self.base_url}/api/users/123456789"),
            ("Sessions List", f"{self.base_url}/api/sessions/"),
            ("Posts List", f"{self.base_url}/api/posts/?tenant_id=default-tenant"),
            ("RAG Status", f"{self.base_url}/api/rag/status/12345678-1234-1234-1234-123456789012"),
        ]
        
        for name, url in api_endpoints:
            # Ожидаем 404 для несуществующих пользователей или 200 для общих endpoints
            expected_status = 200 if "sessions" in url or "posts" in url else 404
            result = await self.check_endpoint(name, url, expected_status)
            results.append(result)
        
        return results
    
    async def check_supabase_endpoints(self) -> List[CheckResult]:
        """Проверка Supabase endpoints."""
        if not self.supabase_host:
            return [CheckResult("Supabase", CheckStatus.SKIP, "SUPABASE_HOST not configured")]
        
        results = []
        
        supabase_endpoints = [
            ("Supabase REST", f"https://{self.supabase_host}/rest/v1/"),
            ("Supabase Auth", f"https://{self.supabase_host}/auth/v1/"),
            ("Supabase Storage", f"https://{self.supabase_host}/storage/v1/"),
            ("Supabase Realtime", f"https://{self.supabase_host}/realtime/v1/"),
        ]
        
        for name, url in supabase_endpoints:
            # Supabase endpoints могут возвращать 200, 401, 404 - все это нормально
            result = await self.check_endpoint(name, url, expected_status=None)
            if result.status == CheckStatus.FAIL and "401" in result.message:
                result.status = CheckStatus.PASS
                result.message = "Auth required (expected)"
            results.append(result)
        
        return results
    
    async def check_metrics(self) -> List[CheckResult]:
        """Проверка Prometheus метрик."""
        results = []
        
        try:
            async with self.session.get(f"{self.base_url}/metrics") as response:
                if response.status == 200:
                    content = await response.text()
                    
                    # Проверяем наличие ключевых метрик
                    required_metrics = [
                        "http_requests_total",
                        "http_request_duration_seconds",
                        "posts_processed_total",
                        "posts_in_queue_total",
                        "tagging_requests_total",
                        "embedding_requests_total",
                        "enrichment_requests_total",
                        "qdrant_operations_total",
                        "neo4j_operations_total",
                        "cleanup_operations_total",
                    ]
                    
                    found_metrics = []
                    missing_metrics = []
                    
                    for metric in required_metrics:
                        if metric in content:
                            found_metrics.append(metric)
                        else:
                            missing_metrics.append(metric)
                    
                    if missing_metrics:
                        results.append(CheckResult(
                            "Metrics Coverage", CheckStatus.WARN,
                            f"Missing metrics: {', '.join(missing_metrics)}",
                            {"found": len(found_metrics), "missing": len(missing_metrics)}
                        ))
                    else:
                        results.append(CheckResult(
                            "Metrics Coverage", CheckStatus.PASS,
                            f"All {len(found_metrics)} required metrics found"
                        ))
                    
                    # Проверяем формат метрик
                    lines = content.split('\n')
                    metric_lines = [line for line in lines if not line.startswith('#') and line.strip()]
                    
                    results.append(CheckResult(
                        "Metrics Format", CheckStatus.PASS,
                        f"Found {len(metric_lines)} metric lines",
                        {"total_lines": len(lines), "metric_lines": len(metric_lines)}
                    ))
                    
                else:
                    results.append(CheckResult(
                        "Metrics Endpoint", CheckStatus.FAIL,
                        f"Status {response.status}"
                    ))
                    
        except Exception as e:
            results.append(CheckResult(
                "Metrics Endpoint", CheckStatus.FAIL,
                f"Error: {str(e)}"
            ))
        
        return results
    
    async def check_docker_services(self) -> List[CheckResult]:
        """Проверка Docker сервисов."""
        results = []
        
        try:
            import subprocess
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0:
                services = json.loads(result.stdout)
                running_services = [s for s in services if s.get('State') == 'running']
                
                results.append(CheckResult(
                    "Docker Services", CheckStatus.PASS,
                    f"{len(running_services)}/{len(services)} services running",
                    {"services": [s['Name'] for s in running_services]}
                ))
            else:
                results.append(CheckResult(
                    "Docker Services", CheckStatus.FAIL,
                    f"docker compose ps failed: {result.stderr}"
                ))
                
        except Exception as e:
            results.append(CheckResult(
                "Docker Services", CheckStatus.FAIL,
                f"Error checking Docker: {str(e)}"
            ))
        
        return results
    
    async def check_configuration(self) -> List[CheckResult]:
        """Проверка конфигурации."""
        results = []
        
        # Проверка .env файла
        env_file = Path(".env")
        if env_file.exists():
            results.append(CheckResult(
                "Environment File", CheckStatus.PASS,
                ".env file exists"
            ))
        else:
            results.append(CheckResult(
                "Environment File", CheckStatus.WARN,
                ".env file not found, using defaults"
            ))
        
        # Проверка docker-compose.yml
        compose_file = Path("docker-compose.yml")
        if compose_file.exists():
            results.append(CheckResult(
                "Docker Compose", CheckStatus.PASS,
                "docker-compose.yml exists"
            ))
        else:
            results.append(CheckResult(
                "Docker Compose", CheckStatus.FAIL,
                "docker-compose.yml not found"
            ))
        
        # Проверка переменных окружения
        required_env_vars = [
            "JWT_SECRET", "ANON_KEY", "SERVICE_KEY", "DATABASE_URL", "REDIS_URL"
        ]
        
        missing_vars = []
        for var in required_env_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            results.append(CheckResult(
                "Environment Variables", CheckStatus.WARN,
                f"Missing variables: {', '.join(missing_vars)}"
            ))
        else:
            results.append(CheckResult(
                "Environment Variables", CheckStatus.PASS,
                "All required variables set"
            ))
        
        return results
    
    async def run_all_checks(self) -> Dict[str, List[CheckResult]]:
        """Запуск всех проверок."""
        logger.info("Starting system health check...")
        
        all_results = {}
        
        # Health endpoints
        logger.info("Checking health endpoints...")
        all_results["health"] = await self.check_health_endpoints()
        
        # API endpoints
        logger.info("Checking API endpoints...")
        all_results["api"] = await self.check_api_endpoints()
        
        # Supabase endpoints
        logger.info("Checking Supabase endpoints...")
        all_results["supabase"] = await self.check_supabase_endpoints()
        
        # Metrics
        logger.info("Checking metrics...")
        all_results["metrics"] = await self.check_metrics()
        
        # Docker services
        logger.info("Checking Docker services...")
        all_results["docker"] = await self.check_docker_services()
        
        # Configuration
        logger.info("Checking configuration...")
        all_results["config"] = await self.check_configuration()
        
        return all_results
    
    def generate_report(self, results: Dict[str, List[CheckResult]]) -> str:
        """Генерация отчета."""
        report = []
        report.append("# System Health Check Report")
        report.append(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        total_checks = 0
        passed_checks = 0
        failed_checks = 0
        warned_checks = 0
        
        for category, checks in results.items():
            report.append(f"## {category.title()}")
            report.append("")
            
            for check in checks:
                total_checks += 1
                if check.status == CheckStatus.PASS:
                    passed_checks += 1
                elif check.status == CheckStatus.FAIL:
                    failed_checks += 1
                elif check.status == CheckStatus.WARN:
                    warned_checks += 1
                
                status_icon = check.status.value
                report.append(f"- {status_icon} **{check.name}**: {check.message}")
                
                if check.duration_ms:
                    report.append(f"  - Duration: {check.duration_ms:.2f}ms")
                
                if check.details:
                    report.append(f"  - Details: {json.dumps(check.details, indent=2)}")
                
                report.append("")
        
        # Summary
        report.append("## Summary")
        report.append("")
        report.append(f"- **Total Checks**: {total_checks}")
        report.append(f"- **✅ Passed**: {passed_checks}")
        report.append(f"- **❌ Failed**: {failed_checks}")
        report.append(f"- **⚠️  Warnings**: {warned_checks}")
        report.append("")
        
        if failed_checks > 0:
            report.append("## Critical Issues")
            report.append("")
            for category, checks in results.items():
                for check in checks:
                    if check.status == CheckStatus.FAIL:
                        report.append(f"- **{check.name}**: {check.message}")
            report.append("")
        
        if warned_checks > 0:
            report.append("## Warnings")
            report.append("")
            for category, checks in results.items():
                for check in checks:
                    if check.status == CheckStatus.WARN:
                        report.append(f"- **{check.name}**: {check.message}")
            report.append("")
        
        return "\n".join(report)

async def main():
    """Основная функция."""
    base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
    supabase_host = os.getenv("SUPABASE_HOST")
    
    async with SystemHealthChecker(base_url, supabase_host) as checker:
        results = await checker.run_all_checks()
        report = checker.generate_report(results)
        
        print(report)
        
        # Сохранение отчета
        with open("system_health_report.md", "w", encoding="utf-8") as f:
            f.write(report)
        
        logger.info("Report saved to system_health_report.md")
        
        # Возвращаем код выхода
        failed_checks = sum(1 for category_results in results.values() 
                           for check in category_results 
                           if check.status == CheckStatus.FAIL)
        
        if failed_checks > 0:
            logger.error(f"Health check failed with {failed_checks} critical issues")
            sys.exit(1)
        else:
            logger.info("All health checks passed")
            sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
