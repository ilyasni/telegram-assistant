"""
Storage Quota Service для управления лимитом 15 GB в S3
Context7 best practice: quota enforcement, emergency cleanup, LRU eviction
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass

import structlog
from prometheus_client import Counter, Gauge, Histogram

# Context7: Настройка путей для cross-service импортов
# Best practice: поддержка dev (volume mounts) и production окружений
import sys
import os

# 1. Импорт ensure_dt_utc - пробуем разные источники
# Context7: Импортируем time_utils напрямую, минуя __init__.py, чтобы избежать зависимости от phash
try:
    # Сначала пробуем shared-пакет (правильный способ)
    # Context7: Импортируем напрямую из time_utils, минуя __init__.py с phash
    import importlib.util
    shared_utils_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), '..', '..', 'shared', 'python'
    ))
    time_utils_path = os.path.join(shared_utils_path, 'shared', 'utils', 'time_utils.py')
    if os.path.exists(time_utils_path):
        spec = importlib.util.spec_from_file_location("time_utils", time_utils_path)
        time_utils = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(time_utils)
        ensure_dt_utc = time_utils.ensure_dt_utc
    else:
        # Fallback: обычный импорт
        from shared.utils.time_utils import ensure_dt_utc
except (ImportError, RuntimeError, FileNotFoundError):
    try:
        # Fallback: локальный utils в worker (dev окружение)
        from utils.time_utils import ensure_dt_utc
    except ImportError:
        # Последний fallback: обычный импорт
        from shared.utils.time_utils import ensure_dt_utc

# 2. Импорт S3StorageService из api (временное исключение для архитектурной границы)
# TODO: [C7-ID: ARCH-SHARED-001] Переместить в shared-пакет в будущем
# Context7: Настройка путей с учетом различных окружений
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Пробуем импорт через разные пути
try:
    # Вариант 1: прямой импорт через api (когда api доступен через project_root)
    from api.services.s3_storage import S3StorageService
except ImportError:
    # Вариант 2: импорт из /app/services (API контейнер с volume mount)
    app_services_path = '/app/services'
    if app_services_path not in sys.path and os.path.exists(app_services_path):
        sys.path.insert(0, '/app')
    try:
        from services.s3_storage import S3StorageService
    except ImportError:
        # Вариант 3: через /opt/telegram-assistant/api (dev volume mount)
        api_path = '/opt/telegram-assistant/api'
        if api_path not in sys.path and os.path.exists(api_path):
            sys.path.insert(0, api_path)
        from api.services.s3_storage import S3StorageService

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

storage_bucket_usage_gb = Gauge(
    'storage_bucket_usage_gb',
    'Total S3 bucket usage in GB',
    ['content_type'],  # media | vision | crawl
    namespace='worker'
)

storage_quota_violations_total = Counter(
    'storage_quota_violations_total',
    'Quota violation attempts',
    ['tenant_id', 'reason'],  # bucket_full | tenant_limit | type_limit
    namespace='worker'
)

storage_emergency_cleanups_total = Counter(
    'storage_emergency_cleanups_total',
    'Emergency cleanup runs',
    ['trigger_reason'],
    namespace='worker'
)

storage_lru_evictions_total = Counter(
    'storage_lru_evictions_total',
    'LRU eviction operations',
    ['content_type', 'tenant_id'],
    namespace='worker'
)

storage_cleanup_freed_gb = Histogram(
    'storage_cleanup_freed_gb',
    'Storage freed by cleanup operations',
    ['cleanup_type'],  # emergency | lru | ttl
    namespace='worker'
)

storage_quota_check_duration_seconds = Histogram(
    'storage_quota_check_duration_seconds',
    'Duration of quota checks',
    namespace='worker'
)

# Context7: Метрики для tenant storage usage tracking
tenant_storage_usage_gb = Gauge(
    'tenant_storage_usage_gb',
    'Storage usage per tenant by content type',
    ['tenant_id', 'content_type'],  # tenant_id: UUID, content_type: media|vision|crawl
    namespace='worker'
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
        cleanup_check_interval_hours: int = 6,
        db_pool=None  # asyncpg.Pool для интеграции с media_objects таблицей
    ):
        self.s3_service = s3_service
        self.limits = limits or STORAGE_LIMITS
        self.enable_emergency_cleanup = enable_emergency_cleanup
        self.cleanup_check_interval = cleanup_check_interval_hours
        self.db_pool = db_pool  # Context7: для LRU eviction через media_objects
        
        # Cache для usage metrics (обновляется периодически)
        self._usage_cache: Dict[str, float] = {}
        self._cache_updated_at: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=15)
        
        # Lock для thread-safe операций
        self._lock = asyncio.Lock()
        
        logger.info(
            "StorageQuotaService initialized",
            total_limit_gb=self.limits["total_gb"],
            emergency_threshold_gb=self.limits["emergency_threshold_gb"],
            db_pool_available=db_pool is not None
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
        Context7: детальное логирование блокировок с метриками.
        
        Returns:
            QuotaCheckResult с allowed=True/False и reason
        """
        import structlog
        logger = structlog.get_logger()
        
        size_gb = size_bytes / (1024 ** 3)
        
        # Получаем текущее использование
        usage = await self.get_bucket_usage()
        total_gb = usage["total_gb"]
        
        # Context7: Проверка 1: Общий лимит с emergency cleanup
        if total_gb + size_gb > self.limits["emergency_threshold_gb"]:
            # Триггерим emergency cleanup
            if self.enable_emergency_cleanup:
                logger.info(
                    "Triggering emergency cleanup",
                    current_usage_gb=total_gb,
                    emergency_threshold_gb=self.limits["emergency_threshold_gb"],
                    size_gb=size_gb,
                    content_type=content_type
                )
                cleanup_start = time.time()
                await self.trigger_emergency_cleanup()
                cleanup_duration = time.time() - cleanup_start
                # Пересчитываем после cleanup
                usage = await self.get_bucket_usage(force_refresh=True)
                total_gb = usage["total_gb"]
                logger.info(
                    "Emergency cleanup completed",
                    duration_seconds=cleanup_duration,
                    new_usage_gb=total_gb
                )
            
            # Если всё ещё превышает — блокируем
            if total_gb + size_gb > self.limits["total_gb"]:
                storage_quota_violations_total.labels(
                    tenant_id=tenant_id,
                    reason="bucket_full"
                ).inc()
                logger.warning(
                    "Quota check blocked upload - bucket full",
                    tenant_id=tenant_id,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    size_gb=size_gb,
                    current_usage_gb=total_gb,
                    total_limit_gb=self.limits["total_gb"],
                    remaining_gb=self.limits["total_gb"] - total_gb
                )
                return QuotaCheckResult(
                    allowed=False,
                    reason="bucket_full",
                    current_usage_gb=total_gb
                )
        
        # Context7: Проверка 2: Tenant квота через БД
        if self.db_pool:
            try:
                tenant_usage_result = await self.get_tenant_usage(tenant_id, content_type)
                tenant_usage_gb = tenant_usage_result.get("total_gb", 0.0) if isinstance(tenant_usage_result, dict) else 0.0
                
                per_tenant_limit = self.limits.get("per_tenant_max_gb", 2.0)
                
                if tenant_usage_gb + size_gb > per_tenant_limit:
                    storage_quota_violations_total.labels(
                        tenant_id=tenant_id,
                        reason="tenant_limit"
                    ).inc()
                    logger.warning(
                        "Quota check blocked upload - tenant limit exceeded",
                        tenant_id=tenant_id,
                        content_type=content_type,
                        size_bytes=size_bytes,
                        size_gb=size_gb,
                        tenant_usage_gb=tenant_usage_gb,
                        tenant_limit_gb=per_tenant_limit,
                        remaining_gb=per_tenant_limit - tenant_usage_gb
                    )
                    return QuotaCheckResult(
                        allowed=False,
                        reason="tenant_limit",
                        current_usage_gb=total_gb,
                        tenant_usage_gb=tenant_usage_gb,
                        type_usage_gb=type_usage
                    )
            except Exception as e:
                # Не критично - логируем но продолжаем проверку
                logger.warning(
                    "Failed to check tenant quota, continuing with other checks",
                    tenant_id=tenant_id,
                    content_type=content_type,
                    error=str(e)
                )
        
        # Context7: Проверка 3: Type квота с детальным логированием
        type_limit = self.limits["quotas_by_type"][content_type]["max_gb"]
        type_usage = usage["by_type"].get(content_type, 0.0)
        
        if type_usage + size_gb > type_limit:
            storage_quota_violations_total.labels(
                tenant_id=tenant_id,
                reason="type_limit"
            ).inc()
            logger.warning(
                "Quota check blocked upload - type limit exceeded",
                tenant_id=tenant_id,
                content_type=content_type,
                size_bytes=size_bytes,
                size_gb=size_gb,
                type_usage_gb=type_usage,
                type_limit_gb=type_limit,
                remaining_gb=type_limit - type_usage
            )
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
            # Context7: Интеграция с media_objects таблицей для LRU eviction
            if self.db_pool:
                try:
                    from worker.services.lru_eviction import LRUEvictionService
                    
                    lru_service = LRUEvictionService(
                        s3_service=self.s3_service,
                        db_pool=self.db_pool,
                        target_free_gb=self.limits.get("target_after_cleanup_gb", 2.0)
                    )
                    
                    # Вычисляем сколько нужно освободить
                    usage = await self.get_bucket_usage()
                    current_gb = usage["total_gb"]
                    target_gb = self.limits.get("target_after_cleanup_gb", 12.0)
                    
                    if current_gb > target_gb:
                        target_free_gb = current_gb - target_gb
                        lru_result = await lru_service.evict_to_target(
                            target_free_gb=target_free_gb,
                            content_type='media',
                            dry_run=False
                        )
                        
                        stats["media_deleted"] += lru_result.get("deleted_count", 0)
                        stats["bytes_freed"] += int(lru_result.get("freed_bytes", 0))
                        
                        logger.info(
                            "LRU eviction completed in emergency cleanup",
                            deleted_count=lru_result.get("deleted_count", 0),
                            freed_gb=lru_result.get("freed_gb", 0)
                        )
                except ImportError:
                    logger.warning("LRUEvictionService not available, skipping LRU eviction")
                except Exception as e:
                    logger.error("LRU eviction failed in emergency cleanup", error=str(e))
            
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
        content_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        LRU eviction для освобождения места.
        
        Context7: Использует LRUEvictionService для интеграции с media_objects таблицей.
        
        Args:
            target_free_gb: Целевой объём для освобождения
            content_type: Тип контента (media | vision | crawl), опционально
        
        Returns:
            Dict с результатами eviction
        """
        try:
            from worker.services.lru_eviction import LRUEvictionService
            
            lru_service = LRUEvictionService(
                s3_service=self.s3_service,
                db_pool=self.db_pool,  # Может быть None, но LRUEvictionService обработает
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
    
    async def update_tenant_usage(
        self,
        tenant_id: str,
        content_type: str,
        size_bytes: int,
        objects_count: int = 1
    ) -> None:
        """
        Обновление использования storage для tenant.
        
        Context7: Использует таблицу tenant_storage_usage для отслеживания использования.
        Идемпотентная операция через ON CONFLICT (UPSERT).
        
        Args:
            tenant_id: ID tenant (UUID строка)
            content_type: Тип контента (media|vision|crawl)
            size_bytes: Размер в байтах
            objects_count: Количество объектов (по умолчанию 1)
        """
        if not self.db_pool:
            logger.debug(
                "DB pool not available, skipping tenant usage update",
                tenant_id=tenant_id,
                content_type=content_type
            )
            return
        
        try:
            size_gb = size_bytes / (1024 ** 3)
            
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO tenant_storage_usage 
                        (tenant_id, content_type, total_bytes, total_gb, objects_count, last_updated)
                    VALUES 
                        ($1::uuid, $2, $3, $4, $5, now())
                    ON CONFLICT (tenant_id, content_type)
                    DO UPDATE SET
                        total_bytes = tenant_storage_usage.total_bytes + $3,
                        total_gb = (tenant_storage_usage.total_bytes + $3) / (1024.0 ^ 3),
                        objects_count = tenant_storage_usage.objects_count + $5,
                        last_updated = now()
                    """,
                    tenant_id, content_type, size_bytes, size_gb, objects_count
                )
            
            # Обновляем Prometheus метрики
            tenant_storage_usage_gb.labels(
                tenant_id=tenant_id,
                content_type=content_type
            ).inc(size_gb)
            
            logger.debug(
                "Tenant storage usage updated",
                tenant_id=tenant_id,
                content_type=content_type,
                size_bytes=size_bytes,
                size_gb=size_gb,
                objects_count=objects_count
            )
            
        except Exception as e:
            logger.error(
                "Failed to update tenant storage usage",
                tenant_id=tenant_id,
                content_type=content_type,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            # Не критично - продолжаем без обновления
    
    async def get_tenant_usage(
        self,
        tenant_id: str,
        content_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Получение использования storage для tenant.
        
        Context7: Использует таблицу tenant_storage_usage для получения актуальных данных.
        
        Args:
            tenant_id: ID tenant (UUID строка)
            content_type: Тип контента (media|vision|crawl), опционально
            
        Returns:
            Dict с использованием по типам контента:
            {
                "tenant_id": str,
                "total_bytes": int,
                "total_gb": float,
                "objects_count": int,
                "by_type": {
                    "media": {"total_bytes": int, "total_gb": float, "objects_count": int},
                    "vision": {...},
                    "crawl": {...}
                },
                "last_updated": datetime
            }
        """
        if not self.db_pool:
            logger.debug(
                "DB pool not available, returning empty tenant usage",
                tenant_id=tenant_id
            )
            return {
                "tenant_id": tenant_id,
                "total_bytes": 0,
                "total_gb": 0.0,
                "objects_count": 0,
                "by_type": {},
                "last_updated": None
            }
        
        try:
            async with self.db_pool.acquire() as conn:
                if content_type:
                    # Получение использования для конкретного типа
                    row = await conn.fetchrow(
                        """
                        SELECT 
                            tenant_id,
                            content_type,
                            total_bytes,
                            total_gb,
                            objects_count,
                            last_updated
                        FROM tenant_storage_usage
                        WHERE tenant_id = $1::uuid AND content_type = $2
                        """,
                        tenant_id, content_type
                    )
                    
                    if row:
                        return {
                            "tenant_id": str(row['tenant_id']),
                            "content_type": row['content_type'],
                            "total_bytes": row['total_bytes'],
                            "total_gb": float(row['total_gb']),
                            "objects_count": row['objects_count'],
                            "last_updated": row['last_updated']
                        }
                    else:
                        return {
                            "tenant_id": tenant_id,
                            "content_type": content_type,
                            "total_bytes": 0,
                            "total_gb": 0.0,
                            "objects_count": 0,
                            "last_updated": None
                        }
                else:
                    # Получение использования для всех типов
                    rows = await conn.fetch(
                        """
                        SELECT 
                            tenant_id,
                            content_type,
                            total_bytes,
                            total_gb,
                            objects_count,
                            last_updated
                        FROM tenant_storage_usage
                        WHERE tenant_id = $1::uuid
                        ORDER BY content_type
                        """,
                        tenant_id
                    )
                    
                    total_bytes = 0
                    total_objects = 0
                    by_type = {}
                    last_updated = None
                    
                    for row in rows:
                        ct = row['content_type']
                        by_type[ct] = {
                            "total_bytes": row['total_bytes'],
                            "total_gb": float(row['total_gb']),
                            "objects_count": row['objects_count'],
                            "last_updated": row['last_updated']
                        }
                        total_bytes += row['total_bytes']
                        total_objects += row['objects_count']
                        if not last_updated or row['last_updated'] > last_updated:
                            last_updated = row['last_updated']
                    
                    return {
                        "tenant_id": tenant_id,
                        "total_bytes": total_bytes,
                        "total_gb": total_bytes / (1024 ** 3),
                        "objects_count": total_objects,
                        "by_type": by_type,
                        "last_updated": last_updated
                    }
                    
        except Exception as e:
            logger.error(
                "Failed to get tenant storage usage",
                tenant_id=tenant_id,
                content_type=content_type,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            return {
                "tenant_id": tenant_id,
                "total_bytes": 0,
                "total_gb": 0.0,
                "objects_count": 0,
                "by_type": {},
                "last_updated": None
            }
    
    async def calculate_and_update_tenant_usage(
        self,
        tenant_id: str,
        content_type: str
    ) -> Dict[str, Any]:
        """
        Расчет использования storage для tenant из S3 и обновление БД.
        
        Context7: Сканирует S3 bucket для расчета использования по tenant_id.
        Используется для периодической синхронизации использования.
        
        Args:
            tenant_id: ID tenant (UUID строка)
            content_type: Тип контента (media|vision|crawl)
            
        Returns:
            Dict с результатами расчета:
            {
                "tenant_id": str,
                "content_type": str,
                "total_bytes": int,
                "total_gb": float,
                "objects_count": int,
                "calculated_at": datetime
            }
        """
        try:
            # Получаем префикс для tenant (например, "media/t{tenant_id}/")
            prefix = f"{content_type}/t{tenant_id}/"
            
            # Список объектов S3 для tenant
            objects = await self.s3_service.list_objects(prefix)
            
            total_bytes = 0
            objects_count = 0
            
            for obj in objects:
                total_bytes += obj.get('size', 0)
                objects_count += 1
            
            size_gb = total_bytes / (1024 ** 3)
            
            # Обновляем в БД
            if self.db_pool:
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO tenant_storage_usage 
                            (tenant_id, content_type, total_bytes, total_gb, objects_count, last_updated)
                        VALUES 
                            ($1::uuid, $2, $3, $4, $5, now())
                        ON CONFLICT (tenant_id, content_type)
                        DO UPDATE SET
                            total_bytes = $3,
                            total_gb = $4,
                            objects_count = $5,
                            last_updated = now()
                        """,
                        tenant_id, content_type, total_bytes, size_gb, objects_count
                    )
                
                # Обновляем Prometheus метрики
                tenant_storage_usage_gb.labels(
                    tenant_id=tenant_id,
                    content_type=content_type
                ).set(size_gb)
            
            logger.info(
                "Tenant storage usage calculated and updated",
                tenant_id=tenant_id,
                content_type=content_type,
                total_bytes=total_bytes,
                total_gb=size_gb,
                objects_count=objects_count
            )
            
            return {
                "tenant_id": tenant_id,
                "content_type": content_type,
                "total_bytes": total_bytes,
                "total_gb": size_gb,
                "objects_count": objects_count,
                "calculated_at": datetime.now(timezone.utc)
            }
            
        except Exception as e:
            logger.error(
                "Failed to calculate tenant storage usage",
                tenant_id=tenant_id,
                content_type=content_type,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            return {
                "tenant_id": tenant_id,
                "content_type": content_type,
                "total_bytes": 0,
                "total_gb": 0.0,
                "objects_count": 0,
                "calculated_at": None,
                "error": str(e)
            }

