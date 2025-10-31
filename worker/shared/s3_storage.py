"""
⚠️ DEPRECATED ⚠️

@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Точный дубликат api/services/s3_storage.py
Replacement: from api.services.s3_storage import S3StorageService

Этот файл перемещён в legacy/deprecated_2025-01-30/
[C7-ID: CODE-CLEANUP-025] Context7 best practice: карантин deprecated кода
"""

import warnings
import os

# Runtime guard: блокируем импорт в production
if os.getenv("ENV") == "production":
    raise ImportError(
        "worker/shared/s3_storage.py is deprecated (duplicate of api/services/s3_storage.py). "
        "Use 'from api.services.s3_storage import S3StorageService' instead. "
        "See legacy/deprecated_2025-01-30/README.md"
    )

warnings.warn(
    "worker/shared/s3_storage.py is DEPRECATED (duplicate). "
    "Use api/services/s3_storage.py instead. "
    "See legacy/deprecated_2025-01-30/README.md",
    DeprecationWarning,
    stacklevel=2
)

# Re-export из api.services для обратной совместимости
from api.services.s3_storage import *

"""
S3 Storage Service для Cloud.ru bucket
Context7 best practice: content-addressed storage, compression, quota management

⚠️ DEPRECATED: This file is a duplicate of api/services/s3_storage.py
"""

import gzip
import hashlib
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, BinaryIO, Dict, Any
from urllib.parse import urlparse

import boto3
import os
from botocore.client import BaseClient
from botocore.exceptions import ClientError, BotoCoreError
from botocore.config import Config
from prometheus_client import Histogram, Counter, Gauge
import structlog

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

s3_operations_total = Counter(
    's3_operations_total',
    'S3 operations count',
    ['operation', 'result', 'content_type']
)

s3_upload_duration_seconds = Histogram(
    's3_upload_duration_seconds',
    'S3 upload latency',
    ['content_type', 'size_bucket']
)

s3_file_size_bytes = Histogram(
    's3_file_size_bytes',
    'S3 file sizes',
    ['content_type']
)

s3_compression_ratio = Histogram(
    's3_compression_ratio',
    'Compression ratio (original/compressed)',
    ['content_type']
)


class S3StorageService:
    """
    S3 Storage Service для Cloud.ru bucket с content-addressed storage.
    
    Features:
    - Content-addressed keys (SHA256-based)
    - Automatic gzip compression для JSON/HTML
    - Presigned URLs on-demand
    - Multipart upload для больших файлов
    - Quota-aware (integration с StorageQuotaService)
    """
    
    def __init__(
        self,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        region: str = "ru-central-1",
        use_compression: bool = True,
        compression_level: int = 6,
        multipart_threshold_mb: int = 5,
        presigned_ttl_seconds: int = 3600,
    ):
        self.endpoint_url = endpoint_url
        self.bucket_name = bucket_name
        self.region = region
        self.use_compression = use_compression
        self.compression_level = compression_level
        self.multipart_threshold = multipart_threshold_mb * 1024 * 1024
        self.presigned_ttl_seconds = presigned_ttl_seconds
        
        # Initialize S3 client (SigV4 + configurable addressing style)
        signature_version = os.getenv('AWS_SIGNATURE_VERSION', 's3v4')
        addressing_style = os.getenv('S3_ADDRESSING_STYLE', 'virtual')  # 'virtual' | 'path'

        # Cloud.ru: Access Key формат <tenant_id>:<key_id>
        if 's3.cloud.ru' in (endpoint_url or '') and access_key_id and ':' not in access_key_id:
            logger.warning("Cloud.ru access key likely missing tenant_id prefix '<tenant_id>:<key_id>'", endpoint=endpoint_url)

        cfg = Config(signature_version=signature_version, s3={'addressing_style': addressing_style})

        self.s3_client: BaseClient = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=cfg,
        )
        
        logger.info(
            "S3StorageService initialized",
            bucket=bucket_name,
            endpoint=endpoint_url,
            region=region,
            addressing_style=addressing_style,
            signature_version=signature_version,
            compression_enabled=use_compression
        )
    
    def compute_sha256(self, content: bytes) -> str:
        """Вычисление SHA256 хеша контента."""
        return hashlib.sha256(content).hexdigest()
    
    def _should_compress(self, content_type: str, content: bytes) -> bool:
        """Определение необходимости сжатия."""
        if not self.use_compression:
            return False
        
        compressible_types = [
            'application/json',
            'text/html',
            'text/plain',
            'application/xml',
            'text/xml',
        ]
        
        return any(content_type.startswith(ct) for ct in compressible_types)
    
    def _compress_content(self, content: bytes) -> tuple[bytes, str]:
        """Сжатие контента gzip."""
        compressed = gzip.compress(content, compresslevel=self.compression_level)
        
        original_size = len(content)
        compressed_size = len(compressed)
        
        if compressed_size > 0:
            ratio = original_size / compressed_size
            s3_compression_ratio.labels(content_type='json').observe(ratio)
        
        return compressed, 'gzip'
    
    def _get_size_bucket(self, size_bytes: int) -> str:
        """Определение размера для метрик."""
        if size_bytes < 1024 * 1024:
            return '<1mb'
        elif size_bytes < 5 * 1024 * 1024:
            return '1-5mb'
        elif size_bytes < 15 * 1024 * 1024:
            return '5-15mb'
        else:
            return '>15mb'
    
    def build_media_key(self, tenant_id: str, sha256: str, extension: str) -> str:
        """
        Построение S3 ключа для медиа: media/{tenant}/{sha256[:2]}/{sha256}.{ext}
        """
        prefix = sha256[:2]
        return f"media/{tenant_id}/{prefix}/{sha256}.{extension}"
    
    def build_vision_key(
        self,
        tenant_id: str,
        sha256: str,
        provider: str,
        model: str,
        schema_version: str
    ) -> str:
        """
        Построение S3 ключа для Vision результатов.
        vision/{tenant}/{sha256}_{provider}_{model}_v{schema}.json
        """
        return f"vision/{tenant_id}/{sha256}_{provider}_{model}_v{schema_version}.json"
    
    def build_crawl_key(self, tenant_id: str, url_hash: str, suffix: str = ".html") -> str:
        """
        Построение S3 ключа для Crawl результатов.
        crawl/{tenant}/{urlhash[:2]}/{urlhash}{suffix}
        """
        prefix = url_hash[:2]
        return f"crawl/{tenant_id}/{prefix}/{url_hash}{suffix}"
    
    async def put_media(
        self,
        content: bytes,
        mime_type: str,
        tenant_id: str,
        extension: Optional[str] = None
    ) -> tuple[str, str, int]:
        """
        Идемпотентная загрузка медиа в S3.
        
        Returns:
            (sha256, s3_key, size_bytes)
        """
        import time
        start_time = time.time()
        
        try:
            # Вычисляем SHA256
            sha256 = self.compute_sha256(content)
            
            # Определяем extension из mime_type или используем предоставленный
            if not extension:
                extension = self._guess_extension(mime_type)
            
            # Строим ключ
            s3_key = self.build_media_key(tenant_id, sha256, extension)
            
            # Проверяем существование (HEAD request)
            try:
                self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
                logger.debug("Media already exists in S3", sha256=sha256, s3_key=s3_key)
                s3_operations_total.labels(operation='head', result='exists', content_type='media').inc()
                duration = time.time() - start_time
                s3_upload_duration_seconds.labels(
                    content_type='media',
                    size_bucket=self._get_size_bucket(len(content))
                ).observe(duration)
                return sha256, s3_key, len(content)
            except ClientError as e:
                if e.response['Error']['Code'] != '404':
                    raise
            
            # Загрузка файла
            extra_args = {
                'ContentType': mime_type,
            }
            
            # Multipart upload для больших файлов
            if len(content) > self.multipart_threshold:
                self._upload_multipart(content, s3_key, mime_type)
            else:
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=content,
                    **extra_args
                )
            
            duration = time.time() - start_time
            s3_upload_duration_seconds.labels(
                content_type='media',
                size_bucket=self._get_size_bucket(len(content))
            ).observe(duration)
            s3_operations_total.labels(operation='put', result='success', content_type='media').inc()
            s3_file_size_bytes.labels(content_type='media').observe(len(content))
            
            logger.info(
                "Media uploaded to S3",
                sha256=sha256,
                s3_key=s3_key,
                size_bytes=len(content),
                duration_ms=int(duration * 1000)
            )
            
            return sha256, s3_key, len(content)
            
        except (ClientError, BotoCoreError) as e:
            s3_operations_total.labels(operation='put', result='error', content_type='media').inc()
            logger.error(
                "Failed to upload media to S3",
                error=str(e),
                error_type=type(e).__name__
            )
            raise
    
    async def put_json(
        self,
        data: Dict[str, Any],
        s3_key: str,
        compress: bool = True
    ) -> int:
        """
        Сохранение JSON в S3 с опциональным сжатием.
        
        Returns:
            size_bytes (после сжатия, если применено)
        """
        import time
        import json
        start_time = time.time()
        
        try:
            # Сериализация JSON
            json_bytes = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
            
            # Сжатие если нужно
            if compress and self.use_compression:
                content, encoding = self._compress_content(json_bytes)
                content_type = 'application/json'
                extra_args = {
                    'ContentType': content_type,
                    'ContentEncoding': encoding,
                }
            else:
                content = json_bytes
                content_type = 'application/json'
                extra_args = {'ContentType': content_type}
            
            # Загрузка
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=content,
                **extra_args
            )
            
            duration = time.time() - start_time
            size_bucket = self._get_size_bucket(len(content))
            s3_upload_duration_seconds.labels(
                content_type='json',
                size_bucket=size_bucket
            ).observe(duration)
            s3_operations_total.labels(operation='put', result='success', content_type='json').inc()
            
            logger.debug(
                "JSON uploaded to S3",
                s3_key=s3_key,
                size_bytes=len(content),
                compressed=compress
            )
            
            return len(content)
            
        except (ClientError, BotoCoreError) as e:
            s3_operations_total.labels(operation='put', result='error', content_type='json').inc()
            logger.error("Failed to upload JSON to S3", s3_key=s3_key, error=str(e))
            raise
    
    async def get_presigned_url(
        self,
        s3_key: str,
        expiration_seconds: Optional[int] = None,
        response_content_disposition: Optional[str] = None
    ) -> str:
        """
        Генерация presigned URL on-demand.
        
        Args:
            s3_key: S3 ключ объекта
            expiration_seconds: TTL (по умолчанию из конфига)
            response_content_disposition: Опциональный Content-Disposition заголовок
        """
        try:
            ttl = expiration_seconds or self.presigned_ttl_seconds
            
            params = {
                'Bucket': self.bucket_name,
                'Key': s3_key,
            }
            
            if response_content_disposition:
                params['ResponseContentDisposition'] = response_content_disposition
            
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params=params,
                ExpiresIn=ttl
            )
            
            s3_operations_total.labels(operation='presigned', result='success', content_type='url').inc()
            return url
            
        except (ClientError, BotoCoreError) as e:
            s3_operations_total.labels(operation='presigned', result='error', content_type='url').inc()
            logger.error("Failed to generate presigned URL", s3_key=s3_key, error=str(e))
            raise
    
    async def head_object(self, s3_key: str) -> Optional[Dict[str, Any]]:
        """Проверка существования объекта в S3."""
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            s3_operations_total.labels(operation='head', result='exists', content_type='any').inc()
            return {
                'size': response.get('ContentLength', 0),
                'content_type': response.get('ContentType'),
                'last_modified': response.get('LastModified'),
                'etag': response.get('ETag', '').strip('"'),
            }
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                s3_operations_total.labels(operation='head', result='not_found', content_type='any').inc()
                return None
            raise
    
    async def get_object(self, s3_key: str) -> Optional[bytes]:
        """
        Загрузка объекта из S3.
        
        Args:
            s3_key: S3 ключ объекта
            
        Returns:
            Содержимое файла в байтах или None если не найден
        """
        import time
        start_time = time.time()
        
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            content = response['Body'].read()
            
            duration = time.time() - start_time
            s3_upload_duration_seconds.labels(
                content_type='download',
                size_bucket=self._get_size_bucket(len(content))
            ).observe(duration)
            s3_operations_total.labels(operation='get', result='success', content_type='any').inc()
            
            logger.debug("Object downloaded from S3", s3_key=s3_key, size_bytes=len(content))
            return content
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                s3_operations_total.labels(operation='get', result='not_found', content_type='any').inc()
                return None
            s3_operations_total.labels(operation='get', result='error', content_type='any').inc()
            logger.error("Failed to get object from S3", s3_key=s3_key, error=str(e))
            raise
        except BotoCoreError as e:
            s3_operations_total.labels(operation='get', result='error', content_type='any').inc()
            logger.error("Failed to get object from S3", s3_key=s3_key, error=str(e))
            raise
    
    async def delete_object(self, s3_key: str) -> bool:
        """Удаление объекта из S3."""
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            s3_operations_total.labels(operation='delete', result='success', content_type='any').inc()
            logger.debug("Object deleted from S3", s3_key=s3_key)
            return True
        except (ClientError, BotoCoreError) as e:
            s3_operations_total.labels(operation='delete', result='error', content_type='any').inc()
            logger.error("Failed to delete object from S3", s3_key=s3_key, error=str(e))
            return False
    
    def _upload_multipart(self, content: bytes, s3_key: str, content_type: str):
        """Multipart upload для больших файлов."""
        upload_id = self.s3_client.create_multipart_upload(
            Bucket=self.bucket_name,
            Key=s3_key,
            ContentType=content_type,
        )['UploadId']
        
        try:
            chunk_size = 5 * 1024 * 1024  # 5MB chunks
            parts = []
            
            for i, chunk_start in enumerate(range(0, len(content), chunk_size)):
                chunk = content[chunk_start:chunk_start + chunk_size]
                
                part = self.s3_client.upload_part(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    PartNumber=i + 1,
                    UploadId=upload_id,
                    Body=chunk,
                )
                parts.append({'PartNumber': i + 1, 'ETag': part['ETag']})
            
            self.s3_client.complete_multipart_upload(
                Bucket=self.bucket_name,
                Key=s3_key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )
        except Exception as e:
            # Abort multipart upload при ошибке
            self.s3_client.abort_multipart_upload(
                Bucket=self.bucket_name,
                Key=s3_key,
                UploadId=upload_id
            )
            raise
    
    def _guess_extension(self, mime_type: str) -> str:
        """Определение расширения по MIME типу."""
        mime_to_ext = {
            'image/jpeg': 'jpg',
            'image/png': 'png',
            'image/gif': 'gif',
            'image/webp': 'webp',
            'image/tiff': 'tiff',
            'image/bmp': 'bmp',
            'application/pdf': 'pdf',
            'application/msword': 'doc',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
            'text/plain': 'txt',
            'application/epub+zip': 'epub',
            'video/mp4': 'mp4',
            'audio/mpeg': 'mp3',
            'audio/wav': 'wav',
        }
        return mime_to_ext.get(mime_type, 'bin')
    
    async def list_objects(
        self,
        prefix: str,
        max_keys: int = 1000
    ) -> list[Dict[str, Any]]:
        """Список объектов с префиксом (для quota calculations)."""
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            
            objects = []
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects.append({
                            'key': obj['Key'],
                            'size': obj['Size'],
                            'last_modified': obj['LastModified'].isoformat(),
                        })
            
            return objects
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to list objects", prefix=prefix, error=str(e))
            return []

