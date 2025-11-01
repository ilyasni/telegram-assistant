"""
S3 Lifecycle Setup for Cloud.ru (Context7 best practices)

- media/: expire after MEDIA_TTL_DAYS (default 30)
- vision/: expire after VISION_TTL_DAYS (default 14)
- crawl/:  expire after CRAWL_TTL_DAYS (default 7)
- Abort incomplete multipart uploads after 1 day

Idempotent: put_bucket_lifecycle_configuration is replace-by-default.

Usage:
    python -m worker.scripts.setup_s3_lifecycle
    # или
    docker compose exec worker python -m worker.scripts.setup_s3_lifecycle
"""

import os
import sys
import json
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError


def main():
    """Установка S3 lifecycle правил для Cloud.ru bucket."""
    endpoint_url = os.getenv("S3_ENDPOINT_URL", "https://s3.cloud.ru")
    access_key = os.getenv("S3_ACCESS_KEY_ID", "")
    secret_key = os.getenv("S3_SECRET_ACCESS_KEY", "")
    bucket_name = os.getenv("S3_BUCKET_NAME", "")
    region = os.getenv("S3_REGION", "ru-central-1")
    
    # Context7: Поддержка различных addressing styles из env.example
    addressing_style = os.getenv("S3_ADDRESSING_STYLE", "virtual")  # 'virtual' | 'path'
    signature_version = os.getenv("AWS_SIGNATURE_VERSION", "s3v4")

    if not bucket_name or not access_key or not secret_key:
        print("ERROR: Missing S3 credentials or bucket name.")
        print("Required: S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET_NAME")
        sys.exit(2)

    media_ttl = int(os.getenv("S3_MEDIA_TTL_DAYS", "30"))
    vision_ttl = int(os.getenv("S3_VISION_TTL_DAYS", "14"))
    crawl_ttl = int(os.getenv("S3_CRAWL_TTL_DAYS", "7"))
    abort_multipart_days = int(os.getenv("S3_ABORT_MULTIPART_DAYS", "1"))

    # Context7: Создание S3 клиента с настройками из env
    # Для bucket-specific endpoints используем path-style или отключаем SSL verify
    use_ssl = os.getenv("S3_USE_SSL", "true").lower() == "true"
    verify_ssl = os.getenv("S3_VERIFY_SSL", "true").lower() == "true"
    
    cfg = Config(
        signature_version=signature_version,
        s3={"addressing_style": addressing_style}
    )
    
    # Если endpoint содержит bucket name, используем path-style
    if bucket_name in endpoint_url and addressing_style == "virtual":
        addressing_style = "path"
        cfg = Config(
            signature_version=signature_version,
            s3={"addressing_style": addressing_style}
        )
    
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=cfg
        )
        
        # Проверка доступности bucket
        s3.head_bucket(Bucket=bucket_name)
        print(f"✅ Connected to bucket: {bucket_name}")
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        print(f"❌ ERROR: Failed to connect to S3 bucket")
        print(f"   Bucket: {bucket_name}")
        print(f"   Error: {error_code} - {str(e)}")
        sys.exit(1)
    except BotoCoreError as e:
        print(f"❌ ERROR: S3 connection failed")
        print(f"   Error: {str(e)}")
        sys.exit(1)

    rules = [
        {
            "ID": "media-expire",
            "Status": "Enabled",
            "Filter": {"Prefix": "media/"},
            "Expiration": {"Days": media_ttl},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": abort_multipart_days},
        },
        {
            "ID": "vision-expire",
            "Status": "Enabled",
            "Filter": {"Prefix": "vision/"},
            "Expiration": {"Days": vision_ttl},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": abort_multipart_days},
        },
        {
            "ID": "crawl-expire",
            "Status": "Enabled",
            "Filter": {"Prefix": "crawl/"},
            "Expiration": {"Days": crawl_ttl},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": abort_multipart_days},
        },
        {
            "ID": "abort-multipart-global",
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": abort_multipart_days},
        },
    ]

    lifecycle = {"Rules": rules}

    try:
        # Context7: Установка lifecycle правил (idempotent)
        s3.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle,
        )
        
        print("\n✅ Successfully applied lifecycle configuration to bucket:")
        print(f"   Bucket: {bucket_name}")
        print(f"   Rules: {len(rules)}")
        print("\n📋 Configuration:")
        print(json.dumps(lifecycle, indent=2))
        
        # Проверка применённой конфигурации
        try:
            applied = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            print(f"\n✅ Verified: {len(applied.get('Rules', []))} rules active")
        except ClientError:
            print("\n⚠️  Warning: Could not verify applied configuration")
            
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        print(f"\n❌ ERROR: Failed to apply lifecycle configuration")
        print(f"   Error: {error_code} - {str(e)}")
        sys.exit(1)
    except BotoCoreError as e:
        print(f"\n❌ ERROR: S3 operation failed")
        print(f"   Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()


