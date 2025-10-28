#!/usr/bin/env python3
"""
Проверка здоровья системы через анализ логов.

Context7 best practice: Автоматизированный анализ логов для обнаружения проблем.
"""

import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta

def check_logs(service="worker", minutes=10):
    """Проверка логов сервиса."""
    cmd = f"docker compose logs {service} --since {minutes}m 2>&1"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd="/opt/telegram-assistant")
    
    logs = result.stdout.split('\n')
    
    # Статистика
    stats = {
        'errors': [],
        'warnings': [],
        'info_keywords': defaultdict(int),
        'services_status': {}
    }
    
    error_patterns = [
        r'error|Error|ERROR',
        r'Exception|exception',
        r'Failed|failed|FAILED',
        r'traceback|Traceback',
        r'CRITICAL|critical'
    ]
    
    warning_patterns = [
        r'warning|Warning|WARNING',
        r'mismatch',
        r'deprecated|Deprecated'
    ]
    
    info_keywords = [
        'started', 'initialized', 'connected', 'successful',
        'Crawl triggered', 'posted_at', 'create_post_node',
        'indexed', 'tagged', 'enriched'
    ]
    
    for line in logs:
        line_lower = line.lower()
        
        # Ошибки
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in error_patterns):
            if 'UserWarning' not in line and 'warnings.warn' not in line:  # Исключаем Pydantic warnings
                stats['errors'].append(line[:200])
        
        # Предупреждения
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in warning_patterns):
            if 'UserWarning' not in line:
                stats['warnings'].append(line[:200])
        
        # Ключевые слова
        for keyword in info_keywords:
            if keyword.lower() in line_lower:
                stats['info_keywords'][keyword] += 1
    
    return stats, logs


def main():
    """Главная функция."""
    print("="*80)
    print("LOG HEALTH CHECK")
    print("="*80)
    
    # Проверка worker
    print("\n## Worker Logs Analysis (last 10 minutes):")
    stats, logs = check_logs("worker", 10)
    
    print(f"\nErrors found: {len(stats['errors'])}")
    if stats['errors']:
        print("\nSample errors:")
        for error in stats['errors'][:5]:
            print(f"  ⚠️  {error}")
    
    print(f"\nWarnings found: {len(stats['warnings'])}")
    if stats['warnings']:
        print("\nSample warnings:")
        for warning in stats['warnings'][:5]:
            print(f"  ⚠️  {warning}")
    
    print(f"\nKey events:")
    for keyword, count in sorted(stats['info_keywords'].items(), key=lambda x: -x[1])[:10]:
        print(f"  {keyword}: {count}")
    
    # Проверка crawl4ai
    print("\n## Crawl4AI Logs Analysis:")
    crawl_stats, _ = check_logs("crawl4ai", 10)
    print(f"Errors: {len(crawl_stats['errors'])}")
    print(f"Warnings: {len(crawl_stats['warnings'])}")
    
    # Проверка telethon-ingest
    print("\n## Telethon-Ingest Logs Analysis:")
    ingest_stats, _ = check_logs("telethon-ingest", 10)
    print(f"Errors: {len(ingest_stats['errors'])}")
    print(f"Scheduler ticks: {ingest_stats['info_keywords'].get('scheduler', 0)}")
    
    # Итог
    total_errors = len(stats['errors']) + len(crawl_stats['errors']) + len(ingest_stats['errors'])
    print(f"\n{'='*80}")
    if total_errors == 0:
        print("✅ All services are healthy (no errors found)")
    else:
        print(f"⚠️  Found {total_errors} errors across services")
    
    print("="*80)
    
    sys.exit(0 if total_errors == 0 else 1)


if __name__ == "__main__":
    main()

