"""
Budget Gate Service для контроля Vision API токенов
Context7 best practice: quota tracking, enforcement, tenant isolation
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from dataclasses import dataclass

import redis.asyncio as redis
import structlog
from prometheus_client import Counter, Gauge, Histogram

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

vision_budget_gate_blocks_total = Counter(
    'vision_budget_gate_blocks_total',
    'Budget gate blocks',
    ['tenant_id', 'reason']  # quota_exceeded | rate_limited | daily_limit
)

# Метрика vision_tokens_used_total определена в ai_adapters/gigachat_vision.py
# для избежания дублирования в CollectorRegistry
# Импортируем при необходимости:
# from ai_adapters.gigachat_vision import vision_tokens_used_total

vision_budget_usage_gauge = Gauge(
    'vision_budget_usage_tokens',
    'Current budget usage per tenant',
    ['tenant_id', 'period']  # hour | day
)


@dataclass
class BudgetCheckResult:
    """Результат проверки бюджета."""
    allowed: bool
    reason: Optional[str] = None
    current_usage: int = 0
    limit: int = 0
    reset_at: Optional[datetime] = None


class BudgetGateService:
    """
    Budget Gate Service для контроля использования Vision API токенов.
    
    Features:
    - Per-tenant daily/hourly quotas
    - Token usage tracking в Redis
    - Automatic reset по времени
    - Rate limiting по concurrency
    """
    
    def __init__(
        self,
        redis_url: str,
        max_daily_tokens_per_tenant: int = 250000,
        max_concurrent_requests: int = 3,
        redis_ttl_hours: int = 25  # Немного больше суток для безопасности
    ):
        self.redis_url = redis_url
        self.max_daily_tokens = max_daily_tokens_per_tenant
        self.max_concurrent = max_concurrent_requests
        self.redis_ttl = timedelta(hours=redis_ttl_hours)
        
        self.redis_client: Optional[redis.Redis] = None
        
        # Concurrency tracking (in-memory + Redis для распределённых воркеров)
        self._concurrent_requests: Dict[str, int] = {}
        self._semaphore: Dict[str, asyncio.Semaphore] = {}
        
        logger.info(
            "BudgetGateService initialized",
            max_daily_tokens=max_daily_tokens_per_tenant,
            max_concurrent=max_concurrent_requests
        )
    
    async def start(self):
        """Инициализация Redis подключения."""
        try:
            self.redis_client = redis.from_url(self.redis_url, decode_responses=True)
            await self.redis_client.ping()
            logger.info("BudgetGateService Redis connected")
        except Exception as e:
            logger.error("Failed to connect BudgetGateService to Redis", error=str(e))
            raise
    
    async def stop(self):
        """Закрытие подключений."""
        if self.redis_client:
            await self.redis_client.close()
    
    def _get_daily_key(self, tenant_id: str) -> str:
        """Ключ для daily quota tracking."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"vision_budget:tenant:{tenant_id}:day:{today}"
    
    def _get_concurrent_key(self, tenant_id: str) -> str:
        """Ключ для concurrent requests tracking."""
        return f"vision_concurrent:tenant:{tenant_id}"
    
    async def check_budget(
        self,
        tenant_id: str,
        estimated_tokens: int = 0
    ) -> BudgetCheckResult:
        """
        Проверка бюджета перед Vision API вызовом.
        
        Args:
            tenant_id: ID tenant
            estimated_tokens: Оценочное количество токенов (0 для проверки без учёта)
            
        Returns:
            BudgetCheckResult
        """
        if not self.redis_client:
            # Если Redis недоступен, разрешаем (graceful degradation)
            logger.warning("BudgetGate Redis unavailable, allowing request")
            return BudgetCheckResult(allowed=True, reason="redis_unavailable")
        
        try:
            # Получаем текущее использование за день
            daily_key = self._get_daily_key(tenant_id)
            current_usage = await self.redis_client.get(daily_key)
            current_usage = int(current_usage) if current_usage else 0
            
            # Проверка daily limit
            if current_usage + estimated_tokens > self.max_daily_tokens:
                vision_budget_gate_blocks_total.labels(
                    tenant_id=tenant_id,
                    reason="daily_limit"
                ).inc()
                return BudgetCheckResult(
                    allowed=False,
                    reason="daily_limit",
                    current_usage=current_usage,
                    limit=self.max_daily_tokens,
                    reset_at=self._get_next_reset_time()
                )
            
            # Проверка concurrent requests
            concurrent_key = self._get_concurrent_key(tenant_id)
            concurrent_count = await self.redis_client.get(concurrent_key)
            concurrent_count = int(concurrent_count) if concurrent_count else 0
            
            if concurrent_count >= self.max_concurrent:
                vision_budget_gate_blocks_total.labels(
                    tenant_id=tenant_id,
                    reason="rate_limited"
                ).inc()
                return BudgetCheckResult(
                    allowed=False,
                    reason="rate_limited",
                    current_usage=current_usage
                )
            
            # Все проверки пройдены
            return BudgetCheckResult(
                allowed=True,
                current_usage=current_usage,
                limit=self.max_daily_tokens
            )
            
        except Exception as e:
            logger.error("Budget check failed", tenant_id=tenant_id, error=str(e))
            # При ошибке разрешаем (graceful degradation)
            return BudgetCheckResult(allowed=True, reason="check_error")
    
    async def record_token_usage(
        self,
        tenant_id: str,
        tokens_used: int,
        provider: str,
        model: str
    ):
        """
        Учёт использованных токенов.
        
        Args:
            tenant_id: ID tenant
            tokens_used: Количество использованных токенов
            provider: Провайдер (gigachat, ocr_fallback)
            model: Модель
        """
        if not self.redis_client:
            return
        
        try:
            daily_key = self._get_daily_key(tenant_id)
            
            # Atomic increment
            new_usage = await self.redis_client.incrby(daily_key, tokens_used)
            
            # Устанавливаем TTL на следующий день
            if new_usage == tokens_used:  # Первое использование сегодня
                await self.redis_client.expire(daily_key, int(self.redis_ttl.total_seconds()))
            
            # Обновляем метрики
            # Метрика vision_tokens_used_total перенесена в gigachat_vision.py
            # для избежания дублирования в CollectorRegistry
            
            vision_budget_usage_gauge.labels(
                tenant_id=tenant_id,
                period="day"
            ).set(new_usage)
            
            logger.debug(
                "Token usage recorded",
                tenant_id=tenant_id,
                tokens_used=tokens_used,
                total_usage=new_usage,
                limit=self.max_daily_tokens
            )
            
        except Exception as e:
            logger.error("Failed to record token usage", tenant_id=tenant_id, error=str(e))
    
    async def acquire_concurrent_slot(self, tenant_id: str) -> bool:
        """
        Получение слота для concurrent request.
        
        Returns:
            True если слот получен
        """
        if not self.redis_client:
            return True  # Graceful degradation
        
        try:
            concurrent_key = self._get_concurrent_key(tenant_id)
            current = await self.redis_client.incr(concurrent_key)
            
            if current == 1:
                # Устанавливаем TTL (1 час)
                await self.redis_client.expire(concurrent_key, 3600)
            
            if current > self.max_concurrent:
                # Откат инкремента
                await self.redis_client.decr(concurrent_key)
                return False
            
            return True
            
        except Exception as e:
            logger.error("Failed to acquire concurrent slot", tenant_id=tenant_id, error=str(e))
            return True  # Graceful degradation
    
    async def release_concurrent_slot(self, tenant_id: str):
        """Освобождение слота concurrent request."""
        if not self.redis_client:
            return
        
        try:
            concurrent_key = self._get_concurrent_key(tenant_id)
            await self.redis_client.decr(concurrent_key)
        except Exception as e:
            logger.error("Failed to release concurrent slot", tenant_id=tenant_id, error=str(e))
    
    def _get_next_reset_time(self) -> datetime:
        """Время следующего сброса квоты (начало следующего дня UTC)."""
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    
    async def get_usage(self, tenant_id: str) -> Dict[str, int]:
        """Получение текущего использования квот."""
        if not self.redis_client:
            return {"daily_tokens": 0, "concurrent": 0}
        
        try:
            daily_key = self._get_daily_key(tenant_id)
            concurrent_key = self._get_concurrent_key(tenant_id)
            
            daily_tokens = await self.redis_client.get(daily_key)
            concurrent = await self.redis_client.get(concurrent_key)
            
            return {
                "daily_tokens": int(daily_tokens) if daily_tokens else 0,
                "concurrent": int(concurrent) if concurrent else 0,
                "limit_daily": self.max_daily_tokens,
                "limit_concurrent": self.max_concurrent,
            }
        except Exception as e:
            logger.error("Failed to get usage", tenant_id=tenant_id, error=str(e))
            return {"daily_tokens": 0, "concurrent": 0}

