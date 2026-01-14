#!/usr/bin/env python3
"""
Проверка метрик Prometheus на дубли и ошибки.
Context7: Проверка всех метрик на конфликты имен и неправильное использование labels.
"""

import os
import sys
import re
import ast
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple, Any

PROJECT_ROOT = Path("/opt/telegram-assistant")
sys.path.insert(0, str(PROJECT_ROOT))

def find_metric_definitions(root_dir: Path) -> Dict[str, List[Tuple[str, int, Dict[str, Any]]]]:
    """Находит все определения метрик Prometheus."""
    metrics = defaultdict(list)
    
    # Паттерны для поиска метрик
    patterns = [
        (r'(Counter|Histogram|Gauge|Summary)\s*\(', 'direct'),
        (r'_safe_create_metric\s*\(', 'safe'),
        (r'_safe_register_metric\s*\(', 'safe_register'),
    ]
    
    for py_file in root_dir.rglob("*.py"):
        if 'venv' in str(py_file) or '__pycache__' in str(py_file):
            continue
            
        try:
            content = py_file.read_text(encoding='utf-8')
            lines = content.split('\n')
            
            for pattern, pattern_type in patterns:
                for i, line in enumerate(lines, 1):
                    match = re.search(pattern, line)
                    if match:
                        # Ищем имя метрики в следующих строках
                        for j in range(i, min(i + 10, len(lines))):
                            metric_match = re.search(r"['\"]([a-z_][a-z0-9_]+)['\"]", lines[j])
                            if metric_match:
                                metric_name = metric_match.group(1)
                                
                                # Пытаемся найти labels
                                labels = None
                                for k in range(j, min(j + 5, len(lines))):
                                    labels_match = re.search(r"\[([^\]]+)\]", lines[k])
                                    if labels_match:
                                        labels_str = labels_match.group(1)
                                        labels = [l.strip().strip("'\"") for l in labels_str.split(',')]
                                        break
                                
                                metrics[metric_name].append((
                                    str(py_file.relative_to(root_dir)),
                                    i,
                                    {
                                        'pattern_type': pattern_type,
                                        'labels': labels,
                                        'line': lines[j].strip()
                                    }
                                ))
                                break
        except Exception as e:
            print(f"Error processing {py_file}: {e}", file=sys.stderr)
            continue
    
    return dict(metrics)

def check_metric_usage(root_dir: Path, metric_name: str) -> List[Tuple[str, int, str]]:
    """Проверяет использование метрики."""
    usages = []
    
    for py_file in root_dir.rglob("*.py"):
        if 'venv' in str(py_file) or '__pycache__' in str(py_file):
            continue
            
        try:
            content = py_file.read_text(encoding='utf-8')
            lines = content.split('\n')
            
            for i, line in enumerate(lines, 1):
                if metric_name in line:
                    # Проверяем использование .labels() или .observe()
                    if '.labels(' in line or '.observe(' in line or '.inc(' in line:
                        usages.append((
                            str(py_file.relative_to(root_dir)),
                            i,
                            line.strip()
                        ))
        except Exception as e:
            continue
    
    return usages

def main():
    print("Проверка метрик Prometheus на дубли и ошибки...\n")
    
    root_dir = PROJECT_ROOT / "api" / "worker"
    if not root_dir.exists():
        root_dir = PROJECT_ROOT
    
    metrics = find_metric_definitions(root_dir)
    
    # Проверка на дубли
    duplicates = {name: defs for name, defs in metrics.items() if len(defs) > 1}
    
    if duplicates:
        print("⚠️  Найдены дубли метрик:\n")
        for metric_name, defs in duplicates.items():
            print(f"  {metric_name}:")
            for file_path, line_num, info in defs:
                labels_str = f" labels={info['labels']}" if info['labels'] else " (без labels)"
                print(f"    - {file_path}:{line_num} ({info['pattern_type']}){labels_str}")
            print()
    else:
        print("✅ Дубли метрик не найдены\n")
    
    # Проверка проблемных метрик
    print("Проверка проблемных метрик:\n")
    
    # vision_analysis_duration_seconds
    if 'vision_analysis_duration_seconds' in metrics:
        defs = metrics['vision_analysis_duration_seconds']
        print(f"  vision_analysis_duration_seconds: {len(defs)} определений")
        for file_path, line_num, info in defs:
            labels_str = f" labels={info['labels']}" if info['labels'] else " (без labels)"
            print(f"    - {file_path}:{line_num}{labels_str}")
        
        # Проверяем использование
        usages = check_metric_usage(root_dir, 'vision_analysis_duration_seconds')
        print(f"    Использований: {len(usages)}")
        for file_path, line_num, line in usages[:5]:
            print(f"      {file_path}:{line_num} - {line[:80]}")
        print()
    
    # vision_media_duration_seconds
    if 'vision_media_duration_seconds' in metrics:
        defs = metrics['vision_media_duration_seconds']
        print(f"  vision_media_duration_seconds: {len(defs)} определений")
        for file_path, line_num, info in defs:
            labels_str = f" labels={info['labels']}" if info['labels'] else " (без labels)"
            print(f"    - {file_path}:{line_num}{labels_str}")
        print()
    
    # Статистика
    print(f"\nВсего уникальных метрик: {len(metrics)}")
    print(f"Метрик с дублями: {len(duplicates)}")
    
    return 0 if not duplicates else 1

if __name__ == "__main__":
    sys.exit(main())
