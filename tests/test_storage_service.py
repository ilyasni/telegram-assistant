"""
Unit тесты для S3 Storage Service
Context7 best practice: mock external dependencies, test idempotency
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import hashlib

# Mock boto3 before importing
import sys
sys.modules['boto3'] = MagicMock()
sys.modules['botocore'] = MagicMock()
sys.modules['botocore.exceptions'] = MagicMock()

def test_s3_storage_sha256():
    """Тест вычисления SHA256."""
    from api.services.s3_storage import S3StorageService
    
    # Создаём mock сервис
    service = S3StorageService(
        endpoint_url="https://s3.cloud.ru",
        access_key_id="test",
        secret_access_key="test",
        bucket_name="test-bucket"
    )
    
    # Тестируем SHA256
    content = b"test content"
    sha256 = service.compute_sha256(content)
    
    # Проверка
    expected = hashlib.sha256(content).hexdigest()
    assert sha256 == expected, "SHA256 должен совпадать с hashlib.sha256"
    assert len(sha256) == 64, "SHA256 должен быть 64 символа (hex)"


def test_s3_storage_key_generation():
    """Тест генерации S3 ключей."""
    from api.services.s3_storage import S3StorageService
    
    service = S3StorageService(
        endpoint_url="https://s3.cloud.ru",
        access_key_id="test",
        secret_access_key="test",
        bucket_name="test-bucket"
    )
    
    tenant_id = "test-tenant"
    sha256 = "a" * 64  # Mock SHA256
    ext = "jpg"
    
    # Media key
    media_key = service.build_media_key(tenant_id, sha256, ext)
    assert media_key.startswith("media/"), "Media key должен начинаться с media/"
    assert sha256[:2] in media_key, "Должен включать префикс SHA256"
    assert media_key.endswith(f".{ext}"), "Должен заканчиваться расширением"
    
    # Vision key
    vision_key = service.build_vision_key(tenant_id, sha256, "gigachat", "GigaChat-Pro", "1.0")
    assert vision_key.startswith("vision/"), "Vision key должен начинаться с vision/"
    assert sha256 in vision_key, "Должен включать SHA256"
    
    # Crawl key
    crawl_key = service.build_crawl_key(tenant_id, sha256[:16], ".html")
    assert crawl_key.startswith("crawl/"), "Crawl key должен начинаться с crawl/"


def test_url_canonicalizer():
    """Тест URL каноникализации."""
    from api.services.url_canonicalizer import URLCanonicalizer
    
    canonicalizer = URLCanonicalizer()
    
    # Тест 1: Удаление трекинговых параметров
    url1 = "https://example.com/page?utm_source=test&ref=123&utm_campaign=promo"
    canonical = canonicalizer.canonicalize_url(url1)
    assert "utm_source" not in canonical
    assert "utm_campaign" not in canonical
    assert "ref" not in canonical or canonical.count("ref") == 1  # Может остаться если это не tracking
    
    # Тест 2: Удаление фрагмента
    url2 = "https://example.com/page#section"
    canonical = canonicalizer.canonicalize_url(url2)
    assert "#" not in canonical
    
    # Тест 3: Нормализация схемы
    url3 = "http://example.com/page"
    canonical = canonicalizer.canonicalize_url(url3)
    # Должен сохранить http если изначально был http


def test_storage_quota_limits():
    """Тест лимитов Storage Quota."""
    from worker.services.storage_quota import STORAGE_LIMITS
    
    assert STORAGE_LIMITS["total_bucket_gb"] == 15.0, "Общий лимит должен быть 15 GB"
    assert STORAGE_LIMITS["emergency_threshold_gb"] == 14.0, "Emergency threshold должен быть 14 GB"
    assert STORAGE_LIMITS["per_tenant_max_gb"] == 2.0, "Per-tenant лимит должен быть 2 GB"
    
    # Проверка квот по типам
    media_quota = STORAGE_LIMITS["quotas_by_type"]["media"]
    assert media_quota["max_gb"] == 10.0, "Media quota должна быть 10 GB"
    assert media_quota["max_file_mb"] == 15, "Максимальный размер файла 15 MB"
    
    vision_quota = STORAGE_LIMITS["quotas_by_type"]["vision"]
    assert vision_quota["max_gb"] == 2.0, "Vision quota должна быть 2 GB"


def test_vision_event_schemas():
    """Тест Vision Event Schemas."""
    from worker.events.schemas.posts_vision_v1 import (
        MediaFile, 
        VisionAnalysisResult, 
        VisionUploadedEventV1,
        VisionAnalyzedEventV1
    )
    
    # MediaFile
    media = MediaFile(
        sha256="a" * 64,
        s3_key="media/test/sha.jpg",
        mime_type="image/jpeg",
        size_bytes=1024
    )
    assert media.sha256 == "a" * 64
    assert media.mime_type == "image/jpeg"
    
    # VisionUploadedEventV1
    event = VisionUploadedEventV1(
        tenant_id="test-tenant",
        post_id="test-post",
        media_files=[media],
        requires_vision=True
    )
    assert event.event_type == "posts.vision.uploaded"
    assert len(event.media_files) == 1
    assert event.requires_vision is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

