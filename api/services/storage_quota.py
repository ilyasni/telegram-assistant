"""
Storage Quota Service для API (sync версия)
Context7 best practice: quota enforcement, emergency cleanup

⚠️ ВАЖНО: API использует только sync операции (psycopg2)
Эта sync версия использует asyncio.run() для вызова async методов S3StorageService
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

import structlog
from prometheus_client import Counter, Gauge, Histogram

from services.s3_storage import S3StorageService

logger = structlog.get_logger()

# ============================================================================
# METRICS (совместные с worker версией)
# ============================================================================

storage_bucket_usage_gb = Gauge(
    'storage_bucket_usage_gb',
    'Total S3 bucket usage in GB',
    ['content_type'],  # media | vision | crawl
    namespace='api'
)

storage_quota_violations_total = Counter(
    'storage_quota_violations_total',
    'Quota violation attempts',
    ['tenant_id', 'reason'],  # bucket_full | tenant_limit | type_limit
    namespace='api'
)

storage_emergency_cleanups_total = Counter(
    'storage_emergency_cleanups_total',
    'Emergency cleanup runs',
    ['trigger_reason'],
    namespace='api'
)

storage_cleanup_freed_gb = Histogram(
    'storage_cleanup_freed_gb',
    'Storage freed by cleanup operations',
    ['cleanup_type'],  # emergency | lru | ttl
    namespace='api'
)

# ============================================================================
# CONFIGURATION
# ============================================================================

STORAGE_LIMITS = {
    "total_gb": 15.0,
    "emergency_threshold_gb": 14.0,
    "target_after_cleanup_gb": 12.0,
    "per_tenant_max_gb": 2.0,
    "reserved_system_gb": 1.0,
    
    "quotas_by_type": {
        "media": {
            "max_gb": 10.0,
            "max_file_mb": 15,
            "compression_required": True,
            "ttl_days": 30
        },
        "vision": {
            "max_gb": 2.0,
            "compression_required": True,
            "prefer_db_storage": True,
            "ttl_days": 14
        },
        "crawl": {
            "max_gb": 2.0,
            "compression_required": True,
            "ttl_days": 7
        }
    }
}


class QuotaCheckResult:
    """Результат проверки квоты."""
    def __init__(self, allowed: bool, reason: Optional[str] = None, 
                 current_usage_gb: float = 0.0, type_usage_gb: float = 0.0):
        self.allowed = allowed
        self.reason = reason
        self.current_usage_gb = current_usage_gb
        self.type_usage_gb = type_usage_gb


class StorageQuotaService:
    """
    Storage Quota Service для API (sync версия).
    
    Context7: API использует только sync операции (psycopg2).
    Для работы с async S3StorageService используем asyncio.run().
    
    Features:
    - Pre-upload quota checks
    - Usage tracking per tenant и content type
    - Emergency cleanup при приближении к лимиту
    """
    
    def __init__(
        self,
        s3_service: S3StorageService,
        limits: Dict[str, Any] = None,
        enable_emergency_cleanup: bool = True
    ):
        self.s3_service = s3_service
        self.limits = limits or STORAGE_LIMITS
        self.enable_emergency_cleanup = enable_emergency_cleanup
        
        # Cache для usage metrics (обновляется периодически)
        self._usage_cache: Dict[str, float] = {}
        self._cache_updated_at: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=15)
        
        logger.info(
            "StorageQuotaService (sync) initialized",
            total_limit_gb=self.limits["total_gb"],
            emergency_threshold_gb=self.limits["emergency_threshold_gb"]
        )
    
    def get_bucket_usage(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Получение текущего использования bucket по типам контента (sync обёртка).
        
        Returns:
            {
                "total_gb": float,
                "by_type": {"media": float, "vision": float, "crawl": float},
                "emergency_threshold": float,
                "limit": float
            }
        """
        # Проверка кэша
        if not force_refresh and self._usage_cache:
            if self._cache_updated_at:
                age = datetime.now(timezone.utc) - self._cache_updated_at
                if age < self._cache_ttl:
                    return self._usage_cache
        
        # Вызываем async версию через asyncio.run()
        usage = asyncio.run(self._get_bucket_usage_async(force_refresh))
        
        # Обновляем кэш
        self._usage_cache = usage
        self._cache_updated_at = datetime.now(timezone.utc)
        
        # Обновляем метрики
        for content_type, gb in usage.get("by_type", {}).items():
            storage_bucket_usage_gb.labels(content_type=content_type).set(gb)
        
        return usage
    
    async def _get_bucket_usage_async(self, force_refresh: bool) -> Dict[str, Any]:
        """Async реализация получения usage."""
        usage = await self._calculate_usage_async()
        return usage
    
    async def _calculate_usage_async(self) -> Dict[str, Any]:
        """Расчёт использования bucket (async)."""
        total_bytes = 0
        by_type = {"media": 0, "vision": 0, "crawl": 0}
        
        # Считаем media
        media_objects = await self.s3_service.list_objects("media/")
        for obj in media_objects:
            total_bytes += obj['size']
            by_type["media"] += obj['size']
        
        # Считаем vision
        vision_objects = await self.s3_service.list_objects("vision/")
        for obj in vision_objects:
            total_bytes += obj['size']
            by_type["vision"] += obj['size']
        
        # Считаем crawl
        crawl_objects = await self.s3_service.list_objects("crawl/")
        for obj in crawl_objects:
            total_bytes += obj['size']
            by_type["crawl"] += obj['size']
        
        # Конвертируем в GB
        total_gb = total_bytes / (1024 ** 3)
        by_type_gb = {
            k: v / (1024 ** 3)
            for k, v in by_type.items()
        }
        
        return {
            "total_gb": total_gb,
            "total_bytes": total_bytes,
            "by_type": by_type_gb,
            "emergency_threshold_gb": self.limits["emergency_threshold_gb"],
            "limit_gb": self.limits["total_gb"],
            "usage_percent": (total_gb / self.limits["total_gb"]) * 100
        }
    
    def check_quota_before_upload(
        self,
        tenant_id: str,
        size_bytes: int,
        content_type: str  # media | vision | crawl
    ) -> QuotaCheckResult:
        """
        Проверка квоты перед загрузкой (sync обёртка).
        
        Returns:
            QuotaCheckResult с allowed=True/False и reason
        """
        return asyncio.run(self.check_quota_before_upload_async(
            tenant_id=tenant_id,
            size_bytes=size_bytes,
            content_type=content_type
        ))
    
    async def check_quota_before_upload_async(
        self,
        tenant_id: str,
        size_bytes: int,
        content_type: str  # media | vision | crawl
    ) -> QuotaCheckResult:
        """
        Проверка квоты перед загрузкой (async).
        
        Returns:
            QuotaCheckResult с allowed=True/False и reason
        """
        size_gb = size_bytes / (1024 ** 3)
        
        # Получаем текущее использование
        usage = await self._get_bucket_usage_async(force_refresh=False)
        total_gb = usage["total_gb"]
        
        # Проверка 1: Общий лимит
        if total_gb + size_gb > self.limits["emergency_threshold_gb"]:
            # Триггерим emergency cleanup
            if self.enable_emergency_cleanup:
                await self._trigger_emergency_cleanup_async()
                # Пересчитываем после cleanup
                usage = await self._get_bucket_usage_async(force_refresh=True)
                total_gb = usage["total_gb"]
            
            # Если всё ещё превышает — блокируем
            if total_gb + size_gb > self.limits["total_gb"]:
                storage_quota_violations_total.labels(
                    tenant_id=tenant_id,
                    reason="bucket_full"
                ).inc()
                return QuotaCheckResult(
                    allowed=False,
                    reason="bucket_full",
                    current_usage_gb=total_gb
                )
        
        # Проверка 2: Type квота
        type_limit = self.limits["quotas_by_type"][content_type]["max_gb"]
        type_usage = usage["by_type"].get(content_type, 0.0)
        
        if type_usage + size_gb > type_limit:
            storage_quota_violations_total.labels(
                tenant_id=tenant_id,
                reason="type_limit"
            ).inc()
            return QuotaCheckResult(
                allowed=False,
                reason=f"{content_type}_limit",
                current_usage_gb=total_gb,
                type_usage_gb=type_usage
            )
        
        # Все проверки пройдены
        return QuotaCheckResult(
            allowed=True,
            current_usage_gb=total_gb,
            type_usage_gb=type_usage
        )
    
    async def _trigger_emergency_cleanup_async(self) -> Dict[str, Any]:
        """
        Emergency cleanup при приближении к лимиту (async).
        
        Strategy:
        1. Crawl cache > 3 days
        2. Vision results > 7 days (дубли БД)
        """
        logger.warning("Triggering emergency cleanup", reason="storage_quota")
        
        stats = {
            "crawl_deleted": 0,
            "vision_deleted": 0,
            "bytes_freed": 0
        }
        
        try:
            # 1. Удаляем старый crawl cache (>3 days)
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=3)
            crawl_objects = await self.s3_service.list_objects("crawl/")
            
            for obj in crawl_objects:
                last_modified_str = obj['last_modified']
                if isinstance(last_modified_str, str):
                    last_modified = datetime.fromisoformat(last_modified_str.replace('Z', '+00:00'))
                else:
                    last_modified = last_modified_str
                
                if last_modified < cutoff_date:
                    await self.s3_service.delete_object(obj['key'])
                    stats["crawl_deleted"] += 1
                    stats["bytes_freed"] += obj['size']
            
            # 2. Удаляем старые vision results (>7 days)
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)
            vision_objects = await self.s3_service.list_objects("vision/")
            
            for obj in vision_objects:
                last_modified_str = obj['last_modified']
                if isinstance(last_modified_str, str):
                    last_modified = datetime.fromisoformat(last_modified_str.replace('Z', '+00:00'))
                else:
                    last_modified = last_modified_str
                
                if last_modified < cutoff_date:
                    await self.s3_service.delete_object(obj['key'])
                    stats["vision_deleted"] += 1
                    stats["bytes_freed"] += obj['size']
            
            # Обновляем метрики
            freed_gb = stats["bytes_freed"] / (1024 ** 3)
            storage_cleanup_freed_gb.labels(cleanup_type="emergency").observe(freed_gb)
            storage_emergency_cleanups_total.labels(trigger_reason="storage_quota").inc()
            
            logger.info(
                "Emergency cleanup completed",
                crawl_deleted=stats["crawl_deleted"],
                vision_deleted=stats["vision_deleted"],
                bytes_freed=stats["bytes_freed"],
                freed_gb=freed_gb
            )
            
            return stats
            
        except Exception as e:
            logger.error("Emergency cleanup failed", error=str(e))
            raise
    
    async def trigger_emergency_cleanup(self) -> Dict[str, Any]:
        """
        Emergency cleanup (sync обёртка для совместимости с async endpoints).
        """
        return await self._trigger_emergency_cleanup_async()

