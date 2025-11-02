#!/usr/bin/env python3
"""
Smoke Test 8: Контрольные точки метрик/логов

Цель: Верификация всех метрик и структуры логов
"""

import argparse
import json
import os
import sys
import subprocess
import re
from typing import Dict, List

# Проверка метрик через Prometheus endpoint или docker logs
# Проверка структуры логов через docker logs


def get_prometheus_metrics(worker_url: str = "http://worker:8000/metrics") -> Dict[str, float]:
    """Получение метрик через Prometheus endpoint."""
    try:
        import urllib.request
        response = urllib.request.urlopen(worker_url, timeout=5)
        metrics_text = response.read().decode('utf-8')
        
        metrics = {}
        for line in metrics_text.split('\n'):
            if line.startswith('vision_'):
                # Парсинг строки метрики (Prometheus format)
                # Пример: vision_events_total{status="success"} 10.0
                match = re.match(r'^vision_(\w+)\{([^}]+)\}\s+(\d+\.?\d*)$', line)
                if match:
                    metric_name = match.group(1)
                    labels = match.group(2)
                    value = float(match.group(3))
                    
                    # Создание ключа с labels
                    key = f"{metric_name}_{labels}"
                    metrics[key] = value
        
        return metrics
    except Exception as e:
        print(f"  ⚠ Cannot fetch Prometheus metrics: {e}")
        return {}


def get_docker_logs(container: str, lines: int = 1000, grep: str = None) -> List[str]:
    """Получение логов из Docker контейнера."""
    try:
        cmd = ["docker", "logs", "--tail", str(lines), container]
        if grep:
            # Используем grep через shell
            result = subprocess.run(
                f"docker logs --tail {lines} {container} | grep -i '{grep}'",
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            return result.stdout.split('\n')
        return []
    except Exception as e:
        print(f"  ⚠ Cannot fetch docker logs: {e}")
        return []


def check_log_structure(logs: List[str]) -> Dict[str, any]:
    """Проверка структуры логов (structlog JSON)."""
    required_fields = ['trace_id', 'post_id']
    optional_fields = ['stream_id', 'delivery_count', 'media_sha256', 'reason']
    
    logs_with_fields = 0
    logs_without_trace_id = 0
    logs_without_post_id = 0
    
    for log_line in logs:
        if not log_line.strip():
            continue
        
        try:
            # Парсинг JSON (structlog формат)
            log_data = json.loads(log_line)
            
            has_trace_id = 'trace_id' in log_data or any('trace_id' in str(v) for v in log_data.values())
            has_post_id = 'post_id' in log_data or any('post_id' in str(v) for v in log_data.values())
            
            if has_trace_id and has_post_id:
                logs_with_fields += 1
            else:
                if not has_trace_id:
                    logs_without_trace_id += 1
                if not has_post_id:
                    logs_without_post_id += 1
        except json.JSONDecodeError:
            # Не JSON лог - пропускаем
            continue
    
    return {
        'total_checked': len(logs),
        'with_required_fields': logs_with_fields,
        'without_trace_id': logs_without_trace_id,
        'without_post_id': logs_without_post_id
    }


def check_skipped_reasons(logs: List[str]) -> Dict[str, int]:
    """Проверка категорийных причин для skipped событий."""
    reasons = {
        's3_missing': 0,
        'idempotency': 0,
        'policy': 0,
        'budget': 0,
        'parse_error': 0,
        'other': 0
    }
    
    for log_line in logs:
        if not log_line.strip():
            continue
        
        try:
            log_data = json.loads(log_line)
            log_str = json.dumps(log_data).lower()
            
            # Поиск причин
            if 's3_missing' in log_str or 's3 object not found' in log_str:
                reasons['s3_missing'] += 1
            elif 'idempotency' in log_str or 'already processed' in log_str:
                reasons['idempotency'] += 1
            elif 'policy' in log_str:
                reasons['policy'] += 1
            elif 'budget' in log_str:
                reasons['budget'] += 1
            elif 'parse' in log_str or 'json decode' in log_str:
                reasons['parse_error'] += 1
            elif 'skipped' in log_str:
                reasons['other'] += 1
        except json.JSONDecodeError:
            continue
    
    return reasons


def main():
    parser = argparse.ArgumentParser(description="Smoke Test 8: Контрольные точки метрик/логов")
    parser.add_argument("--worker-container", type=str, default="telegram-assistant-worker-1", help="Worker container name")
    parser.add_argument("--prometheus-url", type=str, default="http://worker:8000/metrics", help="Prometheus metrics URL")
    parser.add_argument("--log-lines", type=int, default=1000, help="Number of log lines to check")
    parser.add_argument("--check-metrics", action="store_true", help="Check Prometheus metrics")
    parser.add_argument("--check-logs", action="store_true", help="Check log structure")
    
    args = parser.parse_args()
    
    print("=== Smoke Test 8: Контрольные точки метрик/логов ===")
    print("")
    
    all_checks_passed = True
    
    # 1. Проверка метрик
    if args.check_metrics:
        print("1. Checking Prometheus metrics...")
        metrics = get_prometheus_metrics(args.prometheus_url)
        
        if metrics:
            print(f"  ✓ Found {len(metrics)} vision metrics")
            
            # Проверка наличия ключевых метрик
            required_metrics = [
                'events_total',
                'media_total',
                'retries_total',
                'event_duration_seconds',
                'media_duration_seconds',
                'pel_size'
            ]
            
            found_metrics = []
            for req_metric in required_metrics:
                found = any(req_metric in key for key in metrics.keys())
                found_metrics.append((req_metric, found))
                status = "✓" if found else "✗"
                print(f"    {status} {req_metric}")
            
            if not all(found for _, found in found_metrics):
                all_checks_passed = False
                print("  ⚠ Some required metrics are missing")
        else:
            print("  ⚠ No metrics found (Prometheus endpoint might be unavailable)")
            all_checks_passed = False
        print("")
    else:
        print("1. Skipping metrics check (--check-metrics not set)")
        print("")
    
    # 2. Проверка структуры логов
    if args.check_logs:
        print("2. Checking log structure...")
        logs = get_docker_logs(args.worker_container, lines=args.log_lines)
        
        if logs:
            print(f"  Total log lines: {len(logs)}")
            
            # Фильтрация vision-related логов
            vision_logs = [log for log in logs if 'vision' in log.lower()]
            print(f"  Vision-related logs: {len(vision_logs)}")
            
            if vision_logs:
                structure = check_log_structure(vision_logs)
                print(f"    Logs with required fields: {structure['with_required_fields']}")
                print(f"    Logs without trace_id: {structure['without_trace_id']}")
                print(f"    Logs without post_id: {structure['without_post_id']}")
                
                if structure['without_trace_id'] > 0 or structure['without_post_id'] > 0:
                    all_checks_passed = False
                    print("  ⚠ Some logs missing required fields")
                else:
                    print("  ✓ All logs have required fields")
                
                # Проверка категорийных причин для skipped
                print("")
                print("3. Checking skipped reasons...")
                reasons = check_skipped_reasons(vision_logs)
                print(f"    s3_missing: {reasons['s3_missing']}")
                print(f"    idempotency: {reasons['idempotency']}")
                print(f"    policy: {reasons['policy']}")
                print(f"    budget: {reasons['budget']}")
                print(f"    parse_error: {reasons['parse_error']}")
                print(f"    other: {reasons['other']}")
            else:
                print("  ⚠ No vision-related logs found")
                all_checks_passed = False
        else:
            print("  ⚠ No logs found")
            all_checks_passed = False
        print("")
    else:
        print("2. Skipping log check (--check-logs not set)")
        print("")
    
    # 3. Итоговый результат
    print("=== Test Result ===")
    if all_checks_passed:
        print("✓ SUCCESS: All metrics and log checks passed")
        return 0
    else:
        print("✗ FAILED: Some checks failed")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)

