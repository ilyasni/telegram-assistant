"""
Cleanup S3 TTL for Cloud.ru Evolution (no Lifecycle API)

Context7: безопасная имитация lifecycle правил через приложение.

Удаляет объекты старше TTL для префиксов:
- media/: S3_MEDIA_TTL_DAYS (default 30)
- vision/: S3_VISION_TTL_DAYS (default 14)
- crawl/:  S3_CRAWL_TTL_DAYS  (default 7)

Поддерживает DRY-RUN: CLEANUP_DRY_RUN=true (по умолчанию).
Запуск: python -m worker.scripts.cleanup_s3_ttl
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import boto3
from botocore.config import Config


def iter_objects(client, bucket: str, prefix: str):
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, MaxKeys=1000):
        for obj in page.get('Contents', []):
            yield obj


def main():
    endpoint = os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru')
    region = os.getenv('S3_REGION', 'ru-central-1')
    access_key = os.getenv('S3_ACCESS_KEY_ID', '')
    secret_key = os.getenv('S3_SECRET_ACCESS_KEY', '')
    bucket = os.getenv('S3_BUCKET_NAME', '')
    addressing_style = os.getenv('S3_ADDRESSING_STYLE', 'path')
    signature_version = os.getenv('AWS_SIGNATURE_VERSION', 's3v4')

    if not bucket or not access_key or not secret_key:
        print('ERROR: Missing S3 credentials or bucket name')
        sys.exit(2)

    dry_run = os.getenv('CLEANUP_DRY_RUN', 'true').lower() != 'false'

    media_ttl = int(os.getenv('S3_MEDIA_TTL_DAYS', '30'))
    vision_ttl = int(os.getenv('S3_VISION_TTL_DAYS', '14'))
    crawl_ttl = int(os.getenv('S3_CRAWL_TTL_DAYS', '7'))

    cfg = Config(signature_version=signature_version, s3={'addressing_style': addressing_style})
    s3 = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=cfg,
    )

    now = datetime.now(timezone.utc)
    rules = [
        ('media/', timedelta(days=media_ttl)),
        ('vision/', timedelta(days=vision_ttl)),
        ('crawl/', timedelta(days=crawl_ttl)),
    ]

    total_checked = 0
    total_to_delete = 0
    total_deleted = 0

    print(f"Cleanup start | bucket={bucket} dry_run={dry_run} addr={addressing_style} sig={signature_version}")

    for prefix, ttl in rules:
        cutoff = now - ttl
        checked = 0
        to_delete = 0
        deleted = 0
        
        for obj in iter_objects(s3, bucket, prefix):
            checked += 1
            last_modified = obj['LastModified']
            if last_modified < cutoff:
                to_delete += 1
                if not dry_run:
                    s3.delete_object(Bucket=bucket, Key=obj['Key'])
                    deleted += 1
        
        total_checked += checked
        total_to_delete += to_delete
        total_deleted += deleted
        print(f"prefix={prefix} checked={checked} to_delete={to_delete} deleted={deleted}")

    print(f"Cleanup done | checked={total_checked} to_delete={total_to_delete} deleted={total_deleted}")


if __name__ == '__main__':
    main()


