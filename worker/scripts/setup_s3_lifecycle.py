"""
S3 Lifecycle Setup for Cloud.ru (Context7 best practices)

- media/: expire after MEDIA_TTL_DAYS (default 30)
- vision/: expire after VISION_TTL_DAYS (default 14)
- crawl/:  expire after CRAWL_TTL_DAYS (default 7)
- Abort incomplete multipart uploads after 1 day

Idempotent: put_bucket_lifecycle_configuration is replace-by-default.
"""

import os
import sys
import json
import boto3
from botocore.config import Config


def main():
    endpoint_url = os.getenv("S3_ENDPOINT_URL", "https://s3.cloud.ru")
    access_key = os.getenv("S3_ACCESS_KEY_ID", "")
    secret_key = os.getenv("S3_SECRET_ACCESS_KEY", "")
    bucket_name = os.getenv("S3_BUCKET_NAME", "")
    region = os.getenv("S3_REGION", "ru-central-1")

    if not bucket_name or not access_key or not secret_key:
        print("ERROR: Missing S3 credentials or bucket name. Set S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET_NAME.")
        sys.exit(2)

    media_ttl = int(os.getenv("S3_MEDIA_TTL_DAYS", "30"))
    vision_ttl = int(os.getenv("S3_VISION_TTL_DAYS", "14"))
    crawl_ttl = int(os.getenv("S3_CRAWL_TTL_DAYS", "7"))
    abort_multipart_days = int(os.getenv("S3_ABORT_MULTIPART_DAYS", "1"))

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(s3={"addressing_style": "virtual"})
    )

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

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket_name,
        LifecycleConfiguration=lifecycle,
    )

    print("Applied lifecycle configuration to bucket:")
    print(json.dumps(lifecycle, indent=2))


if __name__ == "__main__":
    main()


