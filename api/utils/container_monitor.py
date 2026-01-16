"""
Утилита для мониторинга контейнеров Docker.

Context7: Мониторинг перезапусков, uptime и health status контейнеров.
"""

import subprocess
import json
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
import structlog
from prometheus_client import Counter, Gauge

logger = structlog.get_logger()

# Context7: Метрики Prometheus для мониторинга контейнеров
container_restart_count_total = Counter(
    'container_restart_count_total',
    'Total number of container restarts',
    ['service_name']
)

container_uptime_seconds = Gauge(
    'container_uptime_seconds',
    'Container uptime in seconds',
    ['service_name']
)

container_health_status = Gauge(
    'container_health_status',
    'Container health status (1=healthy, 0=unhealthy)',
    ['service_name']
)


class ContainerMonitor:
    """
    Мониторинг статуса Docker контейнеров.
    
    Context7: Отслеживание перезапусков, uptime и причин нестабильности.
    """
    
    def __init__(self):
        """Инициализация мониторинга контейнеров."""
        self.critical_services = [
            'api', 'worker', 'telethon-ingest', 'supabase-db',
            'redis', 'qdrant', 'neo4j'
        ]
    
    async def get_container_status(self, service_name: str) -> Optional[Dict[str, Any]]:
        """
        Получить статус контейнера по имени сервиса.
        
        Args:
            service_name: Имя сервиса (например, 'api')
        
        Returns:
            Словарь со статусом контейнера или None
        """
        try:
            # Получаем информацию о контейнере через docker compose
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json", "--filter", f"name={service_name}"],
                cwd="/opt/telegram-assistant",
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                logger.warning("Failed to get container status", service=service_name, error=result.stderr)
                return None
            
            containers = []
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            
            if not containers:
                return None
            
            # Берем первый контейнер (должен быть один)
            container = containers[0]
            status = container.get('Status', '')
            
            # Парсим время работы из статуса
            uptime_seconds = self._parse_uptime(status)
            
            # Определяем health status
            is_healthy = 'healthy' in status.lower()
            is_unhealthy = 'unhealthy' in status.lower()
            is_restarting = 'Restarting' in status
            
            return {
                'service': service_name,
                'status': status,
                'uptime_seconds': uptime_seconds,
                'healthy': is_healthy,
                'unhealthy': is_unhealthy,
                'restarting': is_restarting,
                'container_id': container.get('ID', '')[:12]  # Короткий ID
            }
            
        except subprocess.TimeoutExpired:
            logger.error("Timeout getting container status", service=service_name)
            return None
        except Exception as e:
            logger.error("Error getting container status", service=service_name, error=str(e))
            return None
    
    def _parse_uptime(self, status: str) -> Optional[int]:
        """
        Парсит время работы контейнера из статуса.
        
        Args:
            status: Строка статуса контейнера
        
        Returns:
            Время работы в секундах или None
        """
        try:
            # Формат: "Up 3 days" или "Up 3 minutes" или "Up 19 hours"
            if 'Up' not in status:
                return None
            
            # Извлекаем часть после "Up"
            up_part = status.split('Up')[1].split('(')[0].strip()
            
            # Парсим количество и единицу времени
            parts = up_part.split()
            if len(parts) < 2:
                return None
            
            amount = int(parts[0])
            unit = parts[1].lower()
            
            # Конвертируем в секунды
            multipliers = {
                'second': 1,
                'seconds': 1,
                'minute': 60,
                'minutes': 60,
                'hour': 3600,
                'hours': 3600,
                'day': 86400,
                'days': 86400,
                'week': 604800,
                'weeks': 604800
            }
            
            multiplier = multipliers.get(unit, 1)
            return amount * multiplier
            
        except Exception as e:
            logger.debug("Failed to parse uptime", status=status, error=str(e))
            return None
    
    async def get_all_containers_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Получить статус всех критичных контейнеров.
        
        Returns:
            Словарь: service_name -> status_dict
        """
        results = {}
        
        for service in self.critical_services:
            status = await self.get_container_status(service)
            if status:
                results[service] = status
        
        return results
    
    async def get_recent_restarts(self, minutes: int = 30) -> List[Dict[str, Any]]:
        """
        Получить контейнеры, которые перезапустились недавно.
        
        Args:
            minutes: Интервал в минутах для проверки
        
        Returns:
            Список контейнеров с недавними перезапусками
        """
        recent_restarts = []
        all_status = await self.get_all_containers_status()
        
        threshold_seconds = minutes * 60
        
        for service, status in all_status.items():
            uptime = status.get('uptime_seconds')
            if uptime is not None and uptime < threshold_seconds:
                recent_restarts.append({
                    'service': service,
                    'uptime_seconds': uptime,
                    'uptime_minutes': round(uptime / 60, 1),
                    'status': status.get('status'),
                    'container_id': status.get('container_id')
                })
        
        return recent_restarts

