#!/usr/bin/env python3
"""
Проверка метрик в дашбордах на наличие в Prometheus
"""

import json
import re
import subprocess
from pathlib import Path
from collections import defaultdict

DASHBOARDS_DIR = Path("/opt/telegram-assistant/grafana/dashboards")

# Получаем список доступных метрик из Prometheus
def get_available_metrics():
    """Получает список доступных метрик из Prometheus"""
    try:
        result = subprocess.run(
            ['docker', 'exec', 'telegram-assistant-grafana-1', 'curl', '-s',
             'http://prometheus:9090/api/v1/label/__name__/values'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return set(data.get('data', []))
    except Exception as e:
        print(f"Ошибка получения метрик: {e}")
    return set()

def extract_metrics_from_expr(expr):
    """Извлекает имена метрик из PromQL выражения"""
    metrics = set()
    # Паттерн для поиска метрик (имена начинаются с буквы или подчеркивания)
    pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?=\{|\[|$|\s)'
    # Исключаем функции и операторы
    excluded = {
        'sum', 'rate', 'increase', 'histogram_quantile', 'by', 'le', 'and', 'or',
        'up', 'count', 'avg', 'max', 'min', 'stddev', 'stdvar', 'topk', 'bottomk',
        'quantile', 'group', 'without', 'on', 'ignoring', 'offset', 'start', 'end'
    }
    
    for match in re.finditer(pattern, expr):
        metric = match.group(1)
        if metric not in excluded and not metric.isdigit():
            metrics.add(metric)
    
    return metrics

def main():
    available_metrics = get_available_metrics()
    print(f"📊 Всего доступных метрик в Prometheus: {len(available_metrics)}\n")
    
    issues = defaultdict(list)
    
    for dashboard_file in DASHBOARDS_DIR.glob("*.json"):
        if ".bak" in dashboard_file.name:
            continue
        
        try:
            with open(dashboard_file, 'r', encoding='utf-8') as f:
                dashboard = json.load(f)
            
            dashboard_metrics = set()
            for panel in dashboard.get('panels', []):
                for target in panel.get('targets', []):
                    expr = target.get('expr', '')
                    if expr:
                        metrics = extract_metrics_from_expr(expr)
                        dashboard_metrics.update(metrics)
            
            missing = dashboard_metrics - available_metrics
            if missing:
                issues[dashboard_file.name] = sorted(missing)
        
        except Exception as e:
            print(f"❌ Ошибка при обработке {dashboard_file.name}: {e}")
    
    if issues:
        print("⚠️  Дашборды с несуществующими метриками:\n")
        for dashboard, missing in sorted(issues.items()):
            print(f"📋 {dashboard}:")
            for metric in missing[:10]:  # Показываем первые 10
                print(f"   - {metric}")
            if len(missing) > 10:
                print(f"   ... и ещё {len(missing) - 10} метрик")
            print()
    else:
        print("✅ Все метрики в дашбордах существуют в Prometheus!")
    
    return len(issues)

if __name__ == '__main__':
    exit(main())

