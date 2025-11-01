"""
Storage Quota Management API
Context7 best practice: async endpoints, error handling, dependency injection
"""

import asyncio
import os
from typing import Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from services.storage_quota import StorageQuotaService

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import structlog

try:
    from config import settings
except ImportError:
    import os
    # Fallback для случаев когда settings недоступен
    class Settings:
        s3_endpoint_url = os.getenv("S3_ENDPOINT_URL", "https://s3.cloud.ru")
        s3_bucket_name = os.getenv("S3_BUCKET_NAME", "test-467940")
        s3_region = os.getenv("S3_REGION", "ru-central-1")
        s3_access_key_id = os.getenv("S3_ACCESS_KEY_ID", "")
        s3_secret_access_key = os.getenv("S3_SECRET_ACCESS_KEY", "")
        s3_use_compression = os.getenv("S3_USE_COMPRESSION", "true").lower() == "true"
        s3_compression_level = int(os.getenv("S3_COMPRESSION_LEVEL", "6"))
        s3_multipart_threshold_mb = int(os.getenv("S3_MULTIPART_THRESHOLD_MB", "5"))
    settings = Settings()

try:
    from middleware.tracing import get_trace_id
except ImportError:
    import uuid
    def get_trace_id() -> str:
        """Fallback для trace_id если middleware недоступен."""
        return str(uuid.uuid4())

logger = structlog.get_logger()

router = APIRouter(prefix="/storage", tags=["storage"])

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class StorageUsageResponse(BaseModel):
    """Текущее использование storage."""
    total_gb: float = Field(..., description="Общее использование в GB")
    limit_gb: float = Field(..., description="Лимит bucket в GB")
    usage_percent: float = Field(..., description="Процент использования")
    by_type: Dict[str, float] = Field(..., description="Использование по типам контента")
    emergency_threshold_gb: float = Field(..., description="Порог emergency cleanup")
    last_updated: datetime = Field(..., description="Время последнего обновления")


class QuotaCheckRequest(BaseModel):
    """Запрос на проверку квоты перед загрузкой."""
    tenant_id: str = Field(..., description="ID tenant")
    size_bytes: int = Field(..., description="Размер файла в байтах", gt=0)
    content_type: str = Field(..., description="Тип контента: media | vision | crawl")


class QuotaCheckResponse(BaseModel):
    """Результат проверки квоты."""
    allowed: bool = Field(..., description="Разрешена ли загрузка")
    reason: Optional[str] = Field(None, description="Причина отказа если не разрешено")
    current_usage_gb: float = Field(..., description="Текущее использование tenant")
    tenant_limit_gb: float = Field(..., description="Лимит tenant")
    bucket_usage_gb: float = Field(..., description="Текущее использование bucket")


class CleanupRequest(BaseModel):
    """Запрос на ручную очистку."""
    target_free_gb: Optional[float] = Field(None, description="Целевой свободный объём в GB")
    content_type: Optional[str] = Field(None, description="Тип контента для очистки")


class CleanupResponse(BaseModel):
    """Результат cleanup операции."""
    deleted_count: int = Field(..., description="Количество удалённых объектов")
    freed_gb: float = Field(..., description="Освобождено GB")
    duration_seconds: float = Field(..., description="Длительность операции")


# ============================================================================
# DEPENDENCIES
# ============================================================================

async def get_storage_quota_service():
    """
    Context7: Dependency для получения StorageQuotaService.
    
    Best practice: правильная настройка путей для cross-service импортов
    с поддержкой dev (volume mounts) и production окружений.
    """
    try:
        import sys
        import os
        
        # Context7: Настройка путей для импортов с учетом архитектурных границ
        # Порядок важен: сначала добавляем основные пути, затем специфичные
        
        # 1. Добавляем корень проекта для абсолютных импортов
        project_root = '/opt/telegram-assistant'
        if project_root not in sys.path and os.path.exists(project_root):
            sys.path.insert(0, project_root)
        
        # 2. Добавляем /app для локальных импортов в API контейнере
        app_root = '/app'
        if app_root not in sys.path and os.path.exists(app_root):
            sys.path.insert(0, app_root)
        
        # 3. Импорт S3StorageService - из локального services (API контейнер)
        # В dev: /app/services/s3_storage.py (volume mount)
        # В production: должен быть в установленном пакете
        try:
            from services.s3_storage import S3StorageService
        except ImportError:
            # Fallback: пробуем через api.services (production образ)
            from api.services.s3_storage import S3StorageService
        
        # 4. Импорт StorageQuotaService из api/services (sync версия для API)
        # Context7: API использует только sync операции, не импортирует из worker
        from services.storage_quota import StorageQuotaService
        
        # Инициализация S3 сервиса
        s3_service = S3StorageService(
            endpoint_url=getattr(settings, 's3_endpoint_url', os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru')),
            access_key_id=getattr(settings, 's3_access_key_id', os.getenv('S3_ACCESS_KEY_ID', '')),
            secret_access_key=getattr(settings, 's3_secret_access_key', os.getenv('S3_SECRET_ACCESS_KEY', '')),
            bucket_name=getattr(settings, 's3_bucket_name', os.getenv('S3_BUCKET_NAME', 'test-467940')),
            region=getattr(settings, 's3_region', os.getenv('S3_REGION', 'ru-central-1')),
            use_compression=getattr(settings, 's3_use_compression', os.getenv('S3_USE_COMPRESSION', 'true').lower() == 'true'),
            compression_level=getattr(settings, 's3_compression_level', int(os.getenv('S3_COMPRESSION_LEVEL', '6'))),
            multipart_threshold_mb=getattr(settings, 's3_multipart_threshold_mb', int(os.getenv('S3_MULTIPART_THRESHOLD_MB', '5'))),
        )
        
        # Storage Quota Service
        storage_quota = StorageQuotaService(s3_service=s3_service)
        
        yield storage_quota
        
    except Exception as e:
        logger.error("Failed to initialize StorageQuotaService", error=str(e))
        raise HTTPException(
            status_code=503,
            detail=f"Storage quota service unavailable: {str(e)}"
        )


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/quota", response_model=StorageUsageResponse)
async def get_storage_quota(
    quota_service: "StorageQuotaService" = Depends(get_storage_quota_service),
    trace_id: str = Depends(get_trace_id)
):
    """
    Получение текущего использования storage и квот.
    
    Returns:
        Информация о текущем использовании и лимитах
    """
    try:
        # Context7: API использует sync версию StorageQuotaService
        usage = quota_service.get_bucket_usage()
        
        return StorageUsageResponse(
            total_gb=usage.get("total_gb", 0.0),
            limit_gb=usage.get("limit_gb", 15.0),
            usage_percent=usage.get("usage_percent", 0.0),
            by_type=usage.get("by_type", {}),
            emergency_threshold_gb=usage.get("emergency_threshold_gb", 14.0),
            last_updated=datetime.now()
        )
        
    except Exception as e:
        logger.error("Failed to get storage quota", error=str(e), trace_id=trace_id)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve storage quota: {str(e)}")


@router.get("/usage/{tenant_id}")
async def get_tenant_usage(
    tenant_id: str,
    quota_service: "StorageQuotaService" = Depends(get_storage_quota_service),
    trace_id: str = Depends(get_trace_id)
):
    """
    Получение использования storage для конкретного tenant.
    
    Args:
        tenant_id: ID tenant
        
    Returns:
        Использование tenant в GB и процент от лимита
    """
    try:
        # Context7: API использует sync версию StorageQuotaService
        usage = quota_service.get_bucket_usage()
        tenant_usage = usage.get("by_tenant", {}).get(tenant_id, {})
        
        return {
            "tenant_id": tenant_id,
            "usage_gb": tenant_usage.get("usage_gb", 0.0),
            "limit_gb": tenant_usage.get("limit_gb", 2.0),
            "usage_percent": tenant_usage.get("usage_percent", 0.0),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to get tenant usage", tenant_id=tenant_id, error=str(e), trace_id=trace_id)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve tenant usage: {str(e)}")


@router.post("/quota/check", response_model=QuotaCheckResponse)
async def check_quota_before_upload(
    request: QuotaCheckRequest,
    quota_service: "StorageQuotaService" = Depends(get_storage_quota_service),
    trace_id: str = Depends(get_trace_id)
):
    """
    Проверка квоты перед загрузкой файла.
    
    Args:
        request: Запрос на проверку квоты
        
    Returns:
        Результат проверки с рекомендациями
    """
    try:
        # Context7: API использует sync версию StorageQuotaService
        # check_quota_before_upload - async метод, вызываем напрямую
        result = await quota_service.check_quota_before_upload_async(
            tenant_id=request.tenant_id,
            size_bytes=request.size_bytes,
            content_type=request.content_type
        )
        
        # Получаем текущее использование для ответа (sync метод)
        usage = quota_service.get_bucket_usage()
        tenant_usage = usage.get("by_tenant", {}).get(request.tenant_id, {})
        
        return QuotaCheckResponse(
            allowed=result.allowed,
            reason=result.reason,
            current_usage_gb=result.tenant_usage_gb,
            tenant_limit_gb=getattr(settings, 's3_per_tenant_limit_gb', 2.0),
            bucket_usage_gb=usage.get("total_gb", 0.0)
        )
        
    except Exception as e:
        logger.error(
            "Failed to check quota",
            tenant_id=request.tenant_id,
            size_bytes=request.size_bytes,
            error=str(e),
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail=f"Failed to check quota: {str(e)}")


@router.post("/cleanup", response_model=CleanupResponse)
async def trigger_cleanup(
    request: CleanupRequest,
    background_tasks: BackgroundTasks,
    quota_service: "StorageQuotaService" = Depends(get_storage_quota_service),
    trace_id: str = Depends(get_trace_id)
):
    """
    Ручной запуск cleanup для освобождения места.
    
    Args:
        request: Параметры cleanup
        background_tasks: FastAPI BackgroundTasks для асинхронной очистки
        
    Returns:
        Результат cleanup операции
    """
    import time
    start_time = time.time()
    
    try:
        # Определяем целевой объём
        target_gb = request.target_free_gb or 12.0  # Default: target_after_cleanup
        
        # Запускаем cleanup в фоне (async метод)
        cleanup_result = await quota_service.trigger_emergency_cleanup()
        
        duration = time.time() - start_time
        
        return CleanupResponse(
            deleted_count=cleanup_result.get("deleted_count", 0),
            freed_gb=cleanup_result.get("freed_gb", 0.0),
            duration_seconds=duration
        )
        
    except Exception as e:
        logger.error("Failed to trigger cleanup", error=str(e), trace_id=trace_id)
        raise HTTPException(status_code=500, detail=f"Failed to trigger cleanup: {str(e)}")


@router.get("/stats")
async def get_storage_stats(
    quota_service: "StorageQuotaService" = Depends(get_storage_quota_service),
    trace_id: str = Depends(get_trace_id)
):
    """
    Получение статистики storage: метрики, история, тренды.
    
    Returns:
        Детальная статистика использования storage
    """
    try:
        # Context7: API использует sync версию StorageQuotaService
        usage = quota_service.get_bucket_usage()
        
        return {
            "current": {
                "total_gb": usage.get("total_gb", 0.0),
                "limit_gb": usage.get("limit_gb", 15.0),
                "usage_percent": usage.get("usage_percent", 0.0),
                "by_type": usage.get("by_type", {}),
            },
            "limits": {
                "total_bucket_gb": 15.0,
                "emergency_threshold_gb": 14.0,
                "per_tenant_max_gb": 2.0,
                "media_max_gb": 10.0,
                "vision_max_gb": 2.0,
                "crawl_max_gb": 2.0,
            },
            "policies": {
                "media_ttl_days": 30,
                "vision_ttl_days": 14,
                "crawl_ttl_days": 7,
                "compression_required": True,
            },
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to get storage stats", error=str(e), trace_id=trace_id)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve storage stats: {str(e)}")

