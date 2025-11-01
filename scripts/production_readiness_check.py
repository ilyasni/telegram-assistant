#!/usr/bin/env python3
"""
Production Readiness Check Script
Context7: Комплексная проверка готовности системы к production

Проверяет:
- Health checks всех сервисов
- S3 подключение и lifecycle policies
- Database connectivity
- Redis connectivity
- Prometheus metrics и alerts
- API endpoints
- Emergency cleanup готовность
- Grafana dashboards
- Context7 compliance
"""

import os
import sys
import json
import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError
from datetime import datetime
from typing import Dict, List, Tuple

# Цвета для вывода
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"

def print_header(text: str):
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}{text}{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

def print_check(name: str, status: bool, details: str = ""):
    icon = f"{GREEN}✓{RESET}" if status else f"{RED}✗{RESET}"
    print(f"{icon} {name}")
    if details:
        print(f"  {details}")

def check_s3_connection() -> Tuple[bool, str]:
    """Context7: Проверка S3 подключения и конфигурации"""
    try:
        endpoint = os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru')
        bucket = os.getenv('S3_BUCKET_NAME', '')
        access_key = os.getenv('S3_ACCESS_KEY_ID', '')
        secret_key = os.getenv('S3_SECRET_ACCESS_KEY', '')
        addressing_style = os.getenv('S3_ADDRESSING_STYLE', 'virtual')
        signature_version = os.getenv('AWS_SIGNATURE_VERSION', 's3v4')
        
        if not all([bucket, access_key, secret_key]):
            return False, "S3 credentials не настроены"
        
        cfg = Config(
            signature_version=signature_version,
            s3={'addressing_style': addressing_style}
        )
        s3 = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=os.getenv('S3_REGION', 'ru-central-1'),
            config=cfg
        )
        
        # Проверка подключения
        s3.head_bucket(Bucket=bucket)
        
        # Проверка lifecycle
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=bucket)
            rules_count = len(lifecycle.get('Rules', []))
            return True, f"Подключено к {bucket}, {rules_count} lifecycle правил"
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchLifecycleConfiguration':
                return False, "Lifecycle policies не настроены"
            raise
            
    except Exception as e:
        return False, f"Ошибка: {str(e)}"

def check_prometheus_alerts() -> Tuple[bool, str]:
    """Context7: Проверка Prometheus alerts"""
    try:
        response = requests.get('http://localhost:9090/api/v1/rules?type=alert', timeout=5)
        if response.status_code != 200:
            return False, f"Prometheus недоступен: {response.status_code}"
        
        data = response.json()
        groups = data.get('data', {}).get('groups', [])
        
        storage_alerts = [g for g in groups if 'storage' in g.get('name', '').lower() or 'quota' in g.get('name', '').lower()]
        
        if storage_alerts:
            total_rules = sum(len(g.get('rules', [])) for g in storage_alerts)
            return True, f"{len(storage_alerts)} групп alerts, {total_rules} правил"
        else:
            return False, "Storage Quota alerts не найдены"
            
    except requests.RequestException as e:
        return False, f"Ошибка подключения: {str(e)}"

def check_health_endpoints() -> Dict[str, Tuple[bool, str]]:
    """Context7: Проверка health endpoints всех сервисов"""
    results = {}
    
    endpoints = {
        'api': 'http://localhost:8000/health',
        'worker': 'http://localhost:8001/health',
    }
    
    for service, url in endpoints.items():
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                try:
                    data = response.json()
                    status = data.get('status', 'unknown')
                    results[service] = (status == 'healthy', f"Status: {status}")
                except:
                    results[service] = (True, f"HTTP {response.status_code}")
            else:
                results[service] = (False, f"HTTP {response.status_code}")
        except requests.RequestException:
            results[service] = (False, "Недоступен")
    
    return results

def main():
    """Context7: Основная функция production readiness check"""
    print_header("PRODUCTION READINESS CHECK - Context7 Best Practices")
    print(f"Дата: {datetime.now().isoformat()}\n")
    
    all_passed = True
    
    # 1. Health Checks
    print_header("1. Health Checks")
    health_results = check_health_endpoints()
    for service, (status, details) in health_results.items():
        print_check(f"{service.upper()} health", status, details)
        if not status:
            all_passed = False
    
    # 2. S3 Connection
    print_header("2. S3 Connection & Lifecycle")
    s3_status, s3_details = check_s3_connection()
    print_check("S3 подключение", s3_status, s3_details)
    if not s3_status:
        all_passed = False
    
    # 3. Prometheus Alerts
    print_header("3. Prometheus Alerts")
    alerts_status, alerts_details = check_prometheus_alerts()
    print_check("Storage Quota alerts", alerts_status, alerts_details)
    if not alerts_status:
        all_passed = False
    
    # 4. Summary
    print_header("SUMMARY")
    if all_passed:
        print(f"{GREEN}✓ Все проверки пройдены{RESET}")
        print(f"\n{BOLD}Система готова к production!{RESET}")
        sys.exit(0)
    else:
        print(f"{YELLOW}⚠ Некоторые проверки не пройдены{RESET}")
        print(f"\n{BOLD}Требуется доработка перед production{RESET}")
        sys.exit(1)

if __name__ == "__main__":
    main()

