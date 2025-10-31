"""
Storage Quota Service для управления лимитом 15 GB в S3
Context7 best practice: quota enforcement, emergency cleanup, LRU eviction
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass

import structlog
from prometheus_client import Counter, Gauge, Histogram

# Context7: Импорт ensure_dt_utc для безопасного парсинга дат
from utils.time_utils import ensure_dt_utc

# Context7: Импорт S3StorageService из shared модуля worker
try:
    from shared.s3_storage import S3StorageService
except ImportError:
    # Fallback для обратной совместимости
    import sys
    import os
    parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    try:
        from api.services.s3_storage import S3StorageService
    except ImportError:
        from shared.s3_storage import S3StorageService

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

storage_bucket_usage_gb = Gauge(
    'storage_bucket_usage_gb',
    'Total S3 bucket usage in GB',
    ['content_type']  # media | vision | crawl
)

storage_quota_violations_total = Counter(
    'storage_quota_violations_total',
    'Quota violation attempts',
    ['tenant_id', 'reason']  # bucket_full | tenant_limit | type_limit
)

storage_emergency_cleanups_total = Counter(
    'storage_emergency_cleanups_total',
    'Emergency cleanup runs',
    ['trigger_reason']
)

storage_lru_evictions_total = Counter(
    'storage_lru_evictions_total',
    'LRU eviction operations',
    ['content_type', 'tenant_id']
)

storage_cleanup_freed_gb = Histogram(
    'storage_cleanup_freed_gb',
    'Storage freed by cleanup operations',
    ['cleanup_type']  # emergency | lru | ttl
)

storage_quota_check_duration_seconds = Histogram(
    'storage_quota_check_duration_seconds',
    'Duration of quota checks'
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
    },
    
    "eviction_policy": {
        "strategy": "lru",
        "check_interval_hours": 6,
        "cleanup_batch_size": 100
    }
}


@dataclass
class QuotaCheckResult:
    """Результат проверки квоты."""
    allowed: bool
    reason: Optional[str] = None
    current_usage_gb: float = 0.0
    tenant_usage_gb: float = 0.0
    type_usage_gb: float = 0.0


class StorageQuotaService:
    """
    Storage Quota Service для управления лимитом 15 GB.
    
    Features:
    - Pre-upload quota checks
    - Usage tracking per tenant и content type
    - Emergency cleanup при приближении к лимиту
    - LRU eviction для освобождения места
    """
    
    def __init__(
        self,
        s3_service: S3StorageService,
        limits: Dict[str, Any] = None,
        enable_emergency_cleanup: bool = True,
        cleanup_check_interval_hours: int = 6
    ):
        self.s3_service = s3_service
        self.limits = limits or STORAGE_LIMITS
        self.enable_emergency_cleanup = enable_emergency_cleanup
        self.cleanup_check_interval = cleanup_check_interval_hours
        
        # Cache для usage metrics (обновляется периодически)
        self._usage_cache: Dict[str, float] = {}
        self._cache_updated_at: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=15)
        
        # Lock для thread-safe операций
        self._lock = asyncio.Lock()
        
        logger.info(
            "StorageQuotaService initialized",
            total_limit_gb=self.limits["total_gb"],
            emergency_threshold_gb=self.limits["emergency_threshold_gb"]
        )
    
    async def get_bucket_usage(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Получение текущего использования bucket по типам контента.
        
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
        
        async with self._lock:
            # Пересчитываем usage
            usage = await self._calculate_usage()
            
            # Обновляем кэш
            self._usage_cache = usage
            self._cache_updated_at = datetime.now(timezone.utc)
            
            # Обновляем метрики
            for content_type, gb in usage.get("by_type", {}).items():
                storage_bucket_usage_gb.labels(content_type=content_type).set(gb)
            
            return usage
    
    async def _calculate_usage(self) -> Dict[str, Any]:
        """Расчёт использования bucket."""
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
    
    async def check_quota_before_upload(
        self,
        tenant_id: str,
        size_bytes: int,
        content_type: str  # media | vision | crawl
    ) -> QuotaCheckResult:
        """
        Проверка квоты перед загрузкой.
        
        Returns:
            QuotaCheckResult с allowed=True/False и reason
        """
        size_gb = size_bytes / (1024 ** 3)
        
        # Получаем текущее использование
        usage = await self.get_bucket_usage()
        total_gb = usage["total_gb"]
        
        # Проверка 1: Общий лимит
        if total_gb + size_gb > self.limits["emergency_threshold_gb"]:
            # Триггерим emergency cleanup
            if self.enable_emergency_cleanup:
                await self.trigger_emergency_cleanup()
                # Пересчитываем после cleanup
                usage = await self.get_bucket_usage(force_refresh=True)
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
        
        # Проверка 2: Tenant квота (упрощённая, нужна БД для точного tracking)
        # TODO: Реализовать через БД при наличии tenant usage tracking
        
        # Проверка 3: Type квота
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
    
    async def evict_lru_media(
        self,
        target_free_gb: float,
        content_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        LRU eviction для освобождения места.
        
        Использует LRUEvictionService для поиска и удаления неиспользуемых файлов.
        """
        try:
            from worker.services.lru_eviction import LRUEvictionService
            
            lru_service = LRUEvictionService(
                s3_service=self.s3_service,
                db_pool=None,  # TODO: добавить db_pool если нужен refs_count
                target_free_gb=target_free_gb
            )
            
            result = await lru_service.evict_to_target(
                target_free_gb=target_free_gb,
                content_type=content_type,
                dry_run=False
            )
            
            # Обновляем метрики
            for ct, freed_gb in result.get('by_content_type', {}).items():
                storage_lru_evictions_total.labels(content_type=ct, tenant_id='all').inc()
            
            storage_cleanup_freed_gb.labels(cleanup_type='lru').observe(
                result.get('freed_gb', 0)
            )
            
            logger.info(
                "LRU eviction completed",
                freed_gb=result.get('freed_gb', 0),
                deleted_count=result.get('deleted_count', 0)
            )
            
            return result
            
        except ImportError:
            logger.warning("LRUEvictionService not available, skipping LRU eviction")
            return {
                "status": "service_unavailable",
                "deleted_count": 0,
                "freed_gb": 0.0
            }
        except Exception as e:
            logger.error("LRU eviction failed", error=str(e))
            return {
                "status": "error",
                "error": str(e),
                "deleted_count": 0,
                "freed_gb": 0.0
            }
    
    async def trigger_emergency_cleanup(self) -> Dict[str, Any]:
        """
        Emergency cleanup при приближении к лимиту.
        
        Strategy:
        1. Crawl cache > 3 days
        2. Vision results > 7 days (дубли БД)
        3. LRU media с refs_count=0 (требует БД интеграцию)
        4. Orphaned multipart uploads
        """
        logger.warning("Triggering emergency cleanup", reason="storage_quota")
        
        stats = {
            "crawl_deleted": 0,
            "vision_deleted": 0,
            "media_deleted": 0,
            "bytes_freed": 0
        }
        
        try:
            # 1. Удаляем старый crawl cache (>3 days)
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=3)
            crawl_objects = await self.s3_service.list_objects("crawl/")
            
            for obj in crawl_objects:
                last_modified = ensure_dt_utc(obj['last_modified'])
                if last_modified < cutoff_date:
                    await self.s3_service.delete_object(obj['key'])
                    stats["crawl_deleted"] += 1
                    stats["bytes_freed"] += obj['size']
            
            # 2. Удаляем старые vision results (>7 days)
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)
            vision_objects = await self.s3_service.list_objects("vision/")
            
            for obj in vision_objects:
                last_modified = ensure_dt_utc(obj['last_modified'])
                if last_modified < cutoff_date:
                    await self.s3_service.delete_object(obj['key'])
                    stats["vision_deleted"] += 1
                    stats["bytes_freed"] += obj['size']
            
            # 3. LRU media eviction (требует БД для refs_count)
            # TODO: Реализовать через БД при наличии media_objects таблицы
            
            # Обновляем кэш usage
            await self.get_bucket_usage(force_refresh=True)
            
            storage_emergency_cleanups_total.labels(trigger_reason="storage_quota").inc()
            
            logger.info(
                "Emergency cleanup completed",
                **stats,
                freed_gb=stats["bytes_freed"] / (1024 ** 3)
            )
            
            return stats
            
        except Exception as e:
            logger.error("Emergency cleanup failed", error=str(e))
            raise
    
    async def evict_lru_media(
        self,
        target_free_gb: float,
        refs_count_zero_only: bool = True
    ) -> Dict[str, Any]:
        """
        LRU eviction для освобождения места.
        
        Requires: БД таблица media_objects с refs_count и last_seen_at
        
        Args:
            target_free_gb: Целевой объём для освобождения
            refs_count_zero_only: Удалять только медиа с refs_count=0
        """
        # TODO: Реализовать после создания media_objects таблицы
        # Нужно:
        # 1. SELECT из media_objects WHERE refs_count=0 ORDER BY last_seen_at ASC
        # 2. Удалить файлы из S3
        # 3. Удалить записи из БД
        
        logger.warning(
            "LRU eviction not yet implemented",
            reason="requires_media_objects_table"
        )
        
        return {
            "evicted_count": 0,
            "bytes_freed": 0
        }

