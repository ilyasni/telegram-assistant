#!/bin/bash
# Скрипт для проверки подключения к Cloud.ru S3 согласно документации
# Использование: ./scripts/test_s3_connection.sh

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 ПРОВЕРКА ПОДКЛЮЧЕНИЯ К Cloud.ru S3"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "📋 Параметры подключения (из документации Cloud.ru):"
echo "   • Endpoint: s3.cloud.ru"
echo "   • Default Region: ru-central-1"
echo "   • Access Key Format: <tenant_id>:<key_id>"
echo "   • Use HTTPS: Yes"
echo ""

echo "📋 Проверка DNS:"
docker compose exec -T worker python3 << 'PYEOF'
import socket
import sys

hostname = 's3.cloud.ru'
try:
    ip = socket.gethostbyname(hostname)
    print(f'   ✅ DNS резолвинг: {hostname} → {ip}')
except socket.gaierror as e:
    print(f'   ❌ DNS ошибка: {e}')
    sys.exit(1)

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    result = sock.connect_ex((hostname, 443))
    sock.close()
    if result == 0:
        print(f'   ✅ HTTPS соединение: {hostname}:443 доступен')
    else:
        print(f'   ❌ HTTPS недоступен (код: {result})')
except Exception as e:
    print(f'   ❌ Ошибка соединения: {e}')
PYEOF

echo ""
echo "📋 Проверка boto3 подключения:"
docker compose exec -T worker python3 << 'PYEOF'
import os
import sys
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

print("   Подключение к S3...")

endpoint = os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru')
bucket = os.getenv('S3_BUCKET_NAME', 'bucket-467940')
region = os.getenv('S3_REGION', 'ru-central-1')
access_key = os.getenv('S3_ACCESS_KEY_ID', '')
secret_key = os.getenv('S3_SECRET_ACCESS_KEY', '')

if not access_key or not secret_key:
    print("   ⚠️  S3_ACCESS_KEY_ID или S3_SECRET_ACCESS_KEY не установлены")
    sys.exit(1)

config = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path'},
    retries={'max_attempts': 3, 'mode': 'standard'},
    connect_timeout=10,
    read_timeout=10
)

try:
    s3_client = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=config,
        use_ssl=True,
        verify=True
    )
    
    # Тест: List buckets
    print(f"   Тест: List buckets...")
    response = s3_client.list_buckets()
    buckets = [b['Name'] for b in response.get('Buckets', [])]
    print(f"   ✅ Успешно: найдено {len(buckets)} buckets")
    
    if bucket in buckets:
        print(f"   ✅ Bucket '{bucket}' найден")
    else:
        print(f"   ⚠️  Bucket '{bucket}' не найден (доступны: {', '.join(buckets[:5])}...)")
        
except EndpointConnectionError as e:
    print(f"   ❌ Ошибка подключения к endpoint: {e}")
    print(f"   💡 Проверьте DNS резолвинг для {endpoint}")
    sys.exit(1)
except ClientError as e:
    error_code = e.response.get('Error', {}).get('Code', 'Unknown')
    print(f"   ❌ Ошибка S3: {error_code}")
    if error_code == 'InvalidAccessKeyId':
        print(f"   💡 Проверьте правильность Access Key (формат: tenant_id:key_id)")
    elif error_code == 'SignatureDoesNotMatch':
        print(f"   💡 Проверьте правильность Secret Key")
    sys.exit(1)
except Exception as e:
    print(f"   ❌ Неожиданная ошибка: {e}")
    sys.exit(1)

print("")
print("   ✅ Подключение к Cloud.ru S3 работает корректно")
PYEOF

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
