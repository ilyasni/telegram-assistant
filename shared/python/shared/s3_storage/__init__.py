"""
S3 Storage client for shared use across services.

[C7-ID: ARCH-SHARED-001] Context7 best practice: shared S3 client для соблюдения архитектурных границ

Использование:
    from shared.s3_storage import S3StorageService
    
    s3_service = S3StorageService(
        endpoint_url="https://s3.cloud.ru",
        access_key_id="...",
        secret_access_key="...",
        bucket_name="...",
        region="ru-central-1"
    )
"""

from shared.s3_storage.service import S3StorageService

__all__ = ['S3StorageService']
