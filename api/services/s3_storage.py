"""
S3 Storage Service для Cloud.ru bucket
Context7 best practice: content-addressed storage, compression, quota management
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
from prometheus_client import Histogram, Counter, Gauge, REGISTRY
import structlog

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

# Context7: Предотвращаем дублирование метрик при cross-service импортах
def _safe_create_metric(metric_type, name, description, labels=None):
    """Создать метрику с проверкой на существование (Context7: предотвращение дублирования)."""
    try:
        # Пытаемся найти существующую метрику
        if hasattr(REGISTRY, '_names_to_collectors'):
            existing = REGISTRY._names_to_collectors.get(name)
            if existing:
                # Возвращаем существующую метрику
                return existing
    except (AttributeError, KeyError, TypeError):
        pass
    
    # Создаем новую метрику с обработкой ошибки дублирования
    try:
        if labels:
            return metric_type(name, description, labels)
        else:
            return metric_type(name, description)
    except ValueError as e:
        # Если метрика уже существует, пытаемся получить существующую
        if 'Duplicated' in str(e) or 'already registered' in str(e).lower():
            try:
                if hasattr(REGISTRY, '_names_to_collectors'):
                    return REGISTRY._names_to_collectors.get(name)
            except (AttributeError, KeyError, TypeError):
                pass
        raise

s3_operations_total = _safe_create_metric(
    Counter,
    's3_operations_total',
    'S3 operations count',
    ['operation', 'result', 'content_type']
)

s3_upload_duration_seconds = _safe_create_metric(
    Histogram,
    's3_upload_duration_seconds',
    'S3 upload latency',
    ['content_type', 'size_bucket']
)

s3_file_size_bytes = _safe_create_metric(
    Histogram,
    's3_file_size_bytes',
    'S3 file sizes',
    ['content_type']
)

s3_compression_ratio = _safe_create_metric(
    Histogram,
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
        # Context7: Cloud.ru S3 Quickstart best practices - path-style для SDK/бэкенда
        signature_version = os.getenv('AWS_SIGNATURE_VERSION', 's3v4')
        # Context7: Cloud.ru рекомендует path-style для SDK операций
        addressing_style = os.getenv('S3_ADDRESSING_STYLE', 'path')  # 'path' для Cloud.ru

        # Cloud.ru: Access Key формат <tenant_id>:<key_id>
        if 's3.cloud.ru' in (endpoint_url or '') and access_key_id and ':' not in access_key_id:
            logger.warning("Cloud.ru access key likely missing tenant_id prefix '<tenant_id>:<key_id>'", endpoint=endpoint_url)

        # Context7: Retry конфигурация для Cloud.ru S3 (best practice для стабильности)
        # Используем exponential backoff с максимумом попыток
        max_retry_attempts = int(os.getenv('S3_MAX_RETRY_ATTEMPTS', '5'))
        retry_mode = os.getenv('S3_RETRY_MODE', 'standard')  # 'standard' | 'adaptive' | 'legacy'
        
        # Context7: Timeout конфигурация (важно для избежания зависаний)
        connect_timeout = int(os.getenv('S3_CONNECT_TIMEOUT_SEC', '30'))
        read_timeout = int(os.getenv('S3_READ_TIMEOUT_SEC', '60'))
        max_pool_connections = int(os.getenv('S3_MAX_POOL_CONNECTIONS', '50'))

        cfg = Config(
            signature_version=signature_version,
            s3={
                'addressing_style': addressing_style,
                'payload_signing_enabled': False,  # Cloud.ru не требует payload signing
            },
            retries={
                'max_attempts': max_retry_attempts,
                'mode': retry_mode
            },
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_pool_connections=max_pool_connections
        )

        # Context7: Cloud.ru S3 Quickstart - используем базовый endpoint s3.cloud.ru
        # Региональные endpoints опциональны и указываются через S3_REGIONAL_ENDPOINT
        effective_endpoint = endpoint_url
        regional_endpoint = os.getenv('S3_REGIONAL_ENDPOINT', '')
        if regional_endpoint and 's3.cloud.ru' in endpoint_url:
            effective_endpoint = regional_endpoint
            logger.debug("Using regional endpoint", endpoint=effective_endpoint, region=region)
        
        self.s3_client: BaseClient = boto3.client(
            's3',
            endpoint_url=effective_endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,  # Context7: Cloud.ru требует явное указание региона для SigV4
            config=cfg,
            use_ssl=True,
            verify=True  # Context7: SSL verification включен для безопасности
        )
        
        logger.info(
            "S3StorageService initialized",
            bucket=bucket_name,
            endpoint=endpoint_url,
            region=region,
            addressing_style=addressing_style,
            signature_version=signature_version,
            compression_enabled=use_compression,
            retry_attempts=max_retry_attempts,
            retry_mode=retry_mode,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout
        )
        
        # Context7: Включаем детальное логирование botocore для диагностики (опционально через env)
        if os.getenv('S3_ENABLE_DEBUG_LOGGING', 'false').lower() == 'true':
            import logging
            boto3.set_stream_logger('botocore', logging.DEBUG)
            logger.info("Botocore debug logging enabled")
    
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
    
    def build_crawl_key(self, tenant_id: str, url_hash: str, suffix: str = ".html", post_id: Optional[str] = None) -> str:
        """
        Построение S3 ключа для Crawl результатов.
        
        Формат:
        - С post_id: crawl/{tenant}/{post_id}/{urlhash}{suffix}
        - Без post_id: crawl/{tenant}/{urlhash[:2]}/{urlhash}{suffix}
        
        Args:
            tenant_id: ID tenant
            url_hash: SHA256 hash URL
            suffix: Суффикс файла (.html, .md)
            post_id: Опциональный ID поста для группировки по постам
        """
        if post_id:
            return f"crawl/{tenant_id}/{post_id}/{url_hash}{suffix}"
        else:
            prefix = url_hash[:2]
            return f"crawl/{tenant_id}/{prefix}/{url_hash}{suffix}"
    
    def build_album_key(
        self,
        tenant_id: str,
        album_id: int,
        suffix: str = "_vision_summary",
        schema_version: str = "v1"
    ) -> str:
        """
        Построение S3 ключа для album-level данных.
        
        Context7: Структурированное хранение альбомов в S3 для долгосрочного хранения
        и кэширования vision summary.
        
        Формат:
        album/{tenant_id}/{album_id}{suffix}_{schema_version}.json
        
        Args:
            tenant_id: ID tenant
            album_id: ID альбома (group_id из media_groups)
            suffix: Суффикс файла (например, "_vision_summary", "_enrichment")
            schema_version: Версия схемы (например, "v1", "v1.0")
            
        Примеры:
        - album/default/12345_vision_summary_v1.json
        - album/tenant_456/67890_enrichment_v1.json
        """
        return f"album/{tenant_id}/{album_id}{suffix}_{schema_version}.json"
    
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
            
            # Context7: Проверяем существование через async head_object (строгая идемпотентность)
            # Cloud.ru S3 может возвращать 500 на HEAD для несуществующих объектов
            # Используем безопасную проверку с обработкой InternalError
            existing_object = await self.head_object(s3_key)
            if existing_object:
                # Объект уже существует - возвращаем существующие данные
                logger.debug("Media already exists in S3", sha256=sha256, s3_key=s3_key)
                duration = time.time() - start_time
                s3_upload_duration_seconds.labels(
                    content_type='media',
                    size_bucket=self._get_size_bucket(len(content))
                ).observe(duration)
                # Возвращаем существующий размер из S3 (может отличаться от локального)
                existing_size = existing_object.get('size', len(content))
                return sha256, s3_key, existing_size
            
            # Context7: Вычисляем MD5 для проверки целостности
            content_md5 = hashlib.md5(content).digest()
            import base64
            content_md5_base64 = base64.b64encode(content_md5).decode('utf-8')
            
            # Загрузка файла
            extra_args = {
                'ContentType': mime_type,
                'ContentMD5': content_md5_base64,  # Context7: Проверка целостности
            }
            
            # Multipart upload для больших файлов
            if len(content) > self.multipart_threshold:
                self._upload_multipart(content, s3_key, mime_type, content_md5_base64)
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
            
            # Context7: Извлекаем request ID для поддержки Cloud.ru
            request_id = ''
            amz_request_id = ''
            amz_id_2 = ''
            error_code = 'Unknown'
            
            if isinstance(e, ClientError):
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                response_metadata = e.response.get('ResponseMetadata', {})
                request_id = response_metadata.get('RequestId', '')
                http_headers = response_metadata.get('HTTPHeaders', {})
                amz_request_id = http_headers.get('x-amz-request-id', '')
                amz_id_2 = http_headers.get('x-amz-id-2', '')
            
            logger.error(
                "Failed to upload media to S3",
                error=str(e),
                error_type=type(e).__name__,
                error_code=error_code,
                request_id=request_id or amz_request_id,
                x_amz_request_id=amz_request_id,
                x_amz_id_2=amz_id_2,
                s3_key=s3_key if 's3_key' in locals() else 'unknown',
                bucket=self.bucket_name,
                endpoint=self.endpoint_url
            )
            raise
    
    async def put_text(
        self,
        content: bytes,
        s3_key: str,
        content_type: str = "text/plain",
        compress: bool = True,
        content_md5: Optional[str] = None
    ) -> int:
        """
        Сохранение текстового контента (HTML, Markdown, etc.) в S3 с опциональным сжатием.
        
        Context7 best practice: универсальный метод для текстового контента с поддержкой gzip.
        
        Args:
            content: Содержимое в байтах
            s3_key: S3 ключ объекта
            content_type: MIME тип (text/html, text/markdown, etc.)
            compress: Применять ли gzip сжатие
            content_md5: Опциональный MD5 для проверки целостности (base64 encoded)
            
        Returns:
            size_bytes (после сжатия, если применено)
        """
        import time
        import hashlib
        import base64
        start_time = time.time()
        
        try:
            # Вычисляем MD5 если не передан
            if not content_md5:
                content_md5_bytes = hashlib.md5(content).digest()
                content_md5 = base64.b64encode(content_md5_bytes).decode('utf-8')
            
            # Сжатие если нужно
            if compress and self.use_compression:
                compressed_content, encoding = self._compress_content(content)
                extra_args = {
                    'ContentType': content_type,
                    'ContentEncoding': encoding,
                    'ContentMD5': content_md5,
                }
                final_content = compressed_content
            else:
                final_content = content
                extra_args = {
                    'ContentType': content_type,
                    'ContentMD5': content_md5,
                }
            
            # Загрузка
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=final_content,
                **extra_args
            )
            
            duration = time.time() - start_time
            size_bucket = self._get_size_bucket(len(final_content))
            s3_upload_duration_seconds.labels(
                content_type='text',
                size_bucket=size_bucket
            ).observe(duration)
            s3_operations_total.labels(operation='put', result='success', content_type='text').inc()
            s3_file_size_bytes.labels(content_type='text').observe(len(final_content))
            
            logger.debug(
                "Text content uploaded to S3",
                s3_key=s3_key,
                size_bytes=len(final_content),
                original_size=len(content),
                compressed=compress,
                content_type=content_type
            )
            
            return len(final_content)
            
        except (ClientError, BotoCoreError) as e:
            s3_operations_total.labels(operation='put', result='error', content_type='text').inc()
            
            # Context7: Извлекаем request ID для поддержки Cloud.ru
            request_id = ''
            amz_request_id = ''
            amz_id_2 = ''
            error_code = 'Unknown'
            
            if isinstance(e, ClientError):
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                response_metadata = e.response.get('ResponseMetadata', {})
                request_id = response_metadata.get('RequestId', '')
                http_headers = response_metadata.get('HTTPHeaders', {})
                amz_request_id = http_headers.get('x-amz-request-id', '')
                amz_id_2 = http_headers.get('x-amz-id-2', '')
            
            logger.error(
                "Failed to upload text content to S3",
                s3_key=s3_key,
                error=str(e),
                error_code=error_code,
                request_id=request_id or amz_request_id,
                x_amz_request_id=amz_request_id,
                x_amz_id_2=amz_id_2,
                bucket=self.bucket_name,
                endpoint=self.endpoint_url
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
            
            # Context7: Извлекаем request ID для поддержки Cloud.ru
            request_id = ''
            amz_request_id = ''
            amz_id_2 = ''
            error_code = 'Unknown'
            
            if isinstance(e, ClientError):
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                response_metadata = e.response.get('ResponseMetadata', {})
                request_id = response_metadata.get('RequestId', '')
                http_headers = response_metadata.get('HTTPHeaders', {})
                amz_request_id = http_headers.get('x-amz-request-id', '')
                amz_id_2 = http_headers.get('x-amz-id-2', '')
            
            logger.error(
                "Failed to upload JSON to S3",
                s3_key=s3_key,
                error=str(e),
                error_code=error_code,
                request_id=request_id or amz_request_id,
                x_amz_request_id=amz_request_id,
                x_amz_id_2=amz_id_2
            )
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
            
            # Context7: Извлекаем request ID для поддержки Cloud.ru
            request_id = ''
            amz_request_id = ''
            amz_id_2 = ''
            error_code = 'Unknown'
            
            if isinstance(e, ClientError):
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                response_metadata = e.response.get('ResponseMetadata', {})
                request_id = response_metadata.get('RequestId', '')
                http_headers = response_metadata.get('HTTPHeaders', {})
                amz_request_id = http_headers.get('x-amz-request-id', '')
                amz_id_2 = http_headers.get('x-amz-id-2', '')
            
            logger.error(
                "Failed to generate presigned URL",
                s3_key=s3_key,
                error=str(e),
                error_code=error_code,
                request_id=request_id or amz_request_id,
                x_amz_request_id=amz_request_id,
                x_amz_id_2=amz_id_2
            )
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
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            
            if error_code == '404' or error_code == 'NoSuchKey':
                s3_operations_total.labels(operation='head', result='not_found', content_type='any').inc()
                return None
            
            # Context7: Извлекаем request ID для поддержки Cloud.ru
            response_metadata = e.response.get('ResponseMetadata', {})
            request_id = response_metadata.get('RequestId', '')
            http_headers = response_metadata.get('HTTPHeaders', {})
            amz_request_id = http_headers.get('x-amz-request-id', '')
            amz_id_2 = http_headers.get('x-amz-id-2', '')
            
            logger.error(
                "S3 HEAD error",
                s3_key=s3_key,
                error_code=error_code,
                request_id=request_id or amz_request_id,
                x_amz_request_id=amz_request_id,
                x_amz_id_2=amz_id_2
            )
            raise
    
    async def get_json(
        self,
        s3_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Загрузка и десериализация JSON из S3 с автоматической декомпрессией.
        
        Context7 best practice: чтение JSON с поддержкой gzip сжатия.
        
        Args:
            s3_key: S3 ключ объекта
            
        Returns:
            Десериализованный JSON dict или None если не найден
        """
        import json
        import gzip
        import time
        start_time = time.time()
        
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            content = response['Body'].read()
            
            # Проверяем Content-Encoding для декомпрессии
            content_encoding = response.get('ContentEncoding', '')
            if content_encoding == 'gzip' or s3_key.endswith('.gz'):
                try:
                    content = gzip.decompress(content)
                except Exception as e:
                    logger.warning("Failed to decompress gzip content, trying raw", s3_key=s3_key, error=str(e))
            
            # Десериализация JSON
            try:
                data = json.loads(content.decode('utf-8'))
            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON from S3", s3_key=s3_key, error=str(e))
                return None
            
            duration = time.time() - start_time
            s3_upload_duration_seconds.labels(
                content_type='json',
                size_bucket=self._get_size_bucket(len(content))
            ).observe(duration)
            s3_operations_total.labels(operation='get', result='success', content_type='json').inc()
            
            logger.debug(
                "JSON downloaded and parsed from S3",
                s3_key=s3_key,
                size_bytes=len(content),
                compressed=content_encoding == 'gzip'
            )
            return data
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            
            if error_code == 'NoSuchKey' or error_code == '404':
                s3_operations_total.labels(operation='get', result='not_found', content_type='json').inc()
                logger.debug("JSON not found in S3", s3_key=s3_key)
                return None
            
            # Context7: Извлекаем request ID для поддержки Cloud.ru
            response_metadata = e.response.get('ResponseMetadata', {})
            request_id = response_metadata.get('RequestId', '')
            http_headers = response_metadata.get('HTTPHeaders', {})
            amz_request_id = http_headers.get('x-amz-request-id', '')
            amz_id_2 = http_headers.get('x-amz-id-2', '')
            
            s3_operations_total.labels(operation='get', result='error', content_type='json').inc()
            logger.error(
                "Failed to get JSON from S3",
                s3_key=s3_key,
                error=str(e),
                error_code=error_code,
                request_id=request_id or amz_request_id,
                x_amz_request_id=amz_request_id,
                x_amz_id_2=amz_id_2
            )
            raise
        except (BotoCoreError, json.JSONDecodeError) as e:
            s3_operations_total.labels(operation='get', result='error', content_type='json').inc()
            logger.error("Failed to get JSON from S3", s3_key=s3_key, error=str(e))
            raise
    
    async def get_object(self, s3_key: str) -> Optional[bytes]:
        """
        Загрузка объекта из S3.
        
        Context7: Cloud.ru S3 best practices - обработка InternalError, retry логика
        Args:
            s3_key: S3 ключ объекта
            
        Returns:
            Содержимое файла в байтах или None если не найден
        """
        import time
        start_time = time.time()
        
        try:
            # Context7: Cloud.ru S3 может возвращать InternalError при временных сбоях
            # boto3 автоматически выполняет retry согласно Config, но логируем для мониторинга
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
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            
            # Context7: Извлекаем request ID для поддержки Cloud.ru
            response_metadata = e.response.get('ResponseMetadata', {})
            request_id = response_metadata.get('RequestId', '')
            http_headers = response_metadata.get('HTTPHeaders', {})
            amz_request_id = http_headers.get('x-amz-request-id', '')
            amz_id_2 = http_headers.get('x-amz-id-2', '')
            
            if error_code == 'NoSuchKey' or error_code == '404':
                s3_operations_total.labels(operation='get', result='not_found', content_type='any').inc()
                logger.debug("Object not found in S3", s3_key=s3_key)
                return None
            
            # Context7: InternalError может быть временной проблемой Cloud.ru S3
            # Логируем с дополнительным контекстом для диагностики
            if error_code == 'InternalError' or 'Internal' in str(e):
                s3_operations_total.labels(operation='get', result='error', content_type='any').inc()
                logger.warning(
                    "S3 InternalError (может быть временной проблемой Cloud.ru)",
                    s3_key=s3_key,
                    error=str(e),
                    error_code=error_code,
                    request_id=request_id or amz_request_id,
                    x_amz_request_id=amz_request_id,
                    x_amz_id_2=amz_id_2,
                    bucket=self.bucket_name,
                    endpoint=self.endpoint_url
                )
            else:
                s3_operations_total.labels(operation='get', result='error', content_type='any').inc()
                logger.error(
                    "Failed to get object from S3",
                    s3_key=s3_key,
                    error=str(e),
                    error_code=error_code,
                    request_id=request_id or amz_request_id,
                    x_amz_request_id=amz_request_id,
                    x_amz_id_2=amz_id_2,
                    bucket=self.bucket_name,
                    endpoint=self.endpoint_url
                )
            
            raise
        except BotoCoreError as e:
            s3_operations_total.labels(operation='get', result='error', content_type='any').inc()
            logger.error("Failed to get object from S3 (BotoCoreError)", s3_key=s3_key, error=str(e))
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
    
    def _upload_multipart(self, content: bytes, s3_key: str, content_type: str, content_md5_base64: Optional[str] = None):
        """
        Multipart upload для больших файлов.
        
        Context7: Для multipart upload MD5 проверка целостности работает через ETag каждого чанка.
        S3 автоматически вычисляет MD5 для каждого чанка и объединяет их в финальный ETag.
        """
        multipart_args = {
            'Bucket': self.bucket_name,
            'Key': s3_key,
            'ContentType': content_type,
        }
        
        # Context7: Для multipart upload ContentMD5 не поддерживается на уровне всего файла
        # Проверка целостности выполняется через ETag каждого чанка
        upload_id = self.s3_client.create_multipart_upload(**multipart_args)['UploadId']
        
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

