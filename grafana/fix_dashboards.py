#!/usr/bin/env python3
"""
Скрипт для исправления всех дашбордов Grafana:
1. Удаляет дублирующиеся uid в datasource
2. Проверяет и исправляет метрики
"""

import json
import os
import sys
from pathlib import Path

DASHBOARDS_DIR = Path("/opt/telegram-assistant/grafana/dashboards")

def fix_datasource_uid(obj):
    """Рекурсивно исправляет дублирующиеся uid в datasource"""
    if isinstance(obj, dict):
        if 'datasource' in obj and isinstance(obj['datasource'], dict):
            # Проверяем на дублирующиеся uid
            if 'uid' in obj['datasource']:
                uid_value = obj['datasource']['uid']
                ds_type = obj['datasource'].get('type', 'prometheus')
                # Создаем чистый объект datasource
                obj['datasource'] = {
                    'type': ds_type,
                    'uid': uid_value
                }
                # Добавляем name если это Prometheus
                if ds_type == 'prometheus' and uid_value == 'prometheus':
                    obj['datasource']['name'] = 'Prometheus'
        
        # Рекурсивно обрабатываем все значения
        for key, value in obj.items():
            fix_datasource_uid(value)
    elif isinstance(obj, list):
        for item in obj:
            fix_datasource_uid(item)

def check_metric_exists(metric_name, available_metrics):
    """Проверяет существование метрики"""
    # Убираем функции и фильтры из запроса для проверки базовой метрики
    base_metric = metric_name.split('{')[0].split('[')[0].strip()
    return base_metric in available_metrics

def main():
    # Загружаем список доступных метрик
    available_metrics = set()
    metrics_file = "/tmp/available_metrics.txt"
    if os.path.exists(metrics_file):
        with open(metrics_file, 'r') as f:
            available_metrics = set(line.strip() for line in f if line.strip())
    
    fixed_count = 0
    errors = []
    
    # Обрабатываем каждый дашборд
    for dashboard_file in DASHBOARDS_DIR.glob("*.json"):
        if ".bak" in dashboard_file.name:
            continue
        
        try:
            with open(dashboard_file, 'r', encoding='utf-8') as f:
                dashboard = json.load(f)
            
            # Исправляем дублирующиеся uid
            fix_datasource_uid(dashboard)
            
            # Проверяем метрики (опционально - только предупреждение)
            if available_metrics:
                for panel in dashboard.get('panels', []):
                    for target in panel.get('targets', []):
                        expr = target.get('expr', '')
                        if expr:
                            # Извлекаем базовое имя метрики
                            import re
                            metrics_in_expr = re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*\{?', expr)
                            for metric in metrics_in_expr:
                                if metric not in ['sum', 'rate', 'increase', 'histogram_quantile', 'by', 'le', 'and', 'or', 'up']:
                                    if not check_metric_exists(metric, available_metrics):
                                        errors.append(f"{dashboard_file.name}: метрика '{metric}' не найдена в запросе '{expr}'")
            
            # Сохраняем исправленный дашборд
            with open(dashboard_file, 'w', encoding='utf-8') as f:
                json.dump(dashboard, f, indent=2, ensure_ascii=False)
            
            fixed_count += 1
            print(f"✅ Исправлен: {dashboard_file.name}")
            
        except Exception as e:
            print(f"❌ Ошибка при обработке {dashboard_file.name}: {e}", file=sys.stderr)
    
    print(f"\n📊 Всего исправлено дашбордов: {fixed_count}")
    
    if errors:
        print(f"\n⚠️  Предупреждения (метрики могут не существовать):")
        for error in errors[:10]:  # Показываем первые 10
            print(f"  - {error}")
        if len(errors) > 10:
            print(f"  ... и ещё {len(errors) - 10} предупреждений")

if __name__ == '__main__':
    main()

