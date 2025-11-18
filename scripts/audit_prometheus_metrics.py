#!/usr/bin/env python3
"""
Context7: Скрипт для автоматического аудита всех метрик Prometheus.
Проверяет соответствие определений метрик и их использований.
"""

import re
import os
import sys
from typing import Dict, List, Tuple, Any

def find_metric_definitions(content: str, file_path: str) -> Dict[str, List[str]]:
    """Context7: Находит все определения метрик в файле."""
    metric_defs = {}
    
    # Паттерн для поиска определений метрик
    # Поддерживает Gauge, Counter, Histogram, PromCounter
    pattern = r'(\w+)\s*=\s*(Gauge|Counter|Histogram|PromCounter)\([^)]+\[([^\]]+)\][^)]*\)'
    
    for match in re.finditer(pattern, content):
        metric_name = match.group(1)
        labels_str = match.group(3)
        # Парсим labels, убирая кавычки и пробелы
        labels = [l.strip().strip('"').strip("'") for l in labels_str.split(',')]
        metric_defs[metric_name] = labels
    
    return metric_defs

def find_metric_usages(content: str, file_path: str) -> List[Dict[str, Any]]:
    """Context7: Находит все использования метрик с .labels()."""
    usages = []
    lines = content.split('\n')
    
    for line_num, line in enumerate(lines, 1):
        # Ищем все вызовы .labels()
        matches = re.finditer(r'(\w+)\.labels\(([^)]+)\)', line)
        for match in matches:
            metric_name = match.group(1)
            labels_str = match.group(2)
            
            # Подсчитываем количество именованных аргументов
            arg_count = labels_str.count('=')
            
            # Извлекаем имена аргументов
            arg_names = []
            for arg_match in re.finditer(r'(\w+)\s*=', labels_str):
                arg_names.append(arg_match.group(1))
            
            usages.append({
                'metric': metric_name,
                'file': file_path,
                'line': line_num,
                'args': labels_str,
                'arg_count': arg_count,
                'arg_names': arg_names,
            })
    
    return usages

def check_sanitization(content: str, file_path: str) -> List[Dict[str, Any]]:
    """Context7: Проверяет, что все значения labels санитизированы."""
    issues = []
    lines = content.split('\n')
    
    for line_num, line in enumerate(lines, 1):
        # Ищем вызовы .labels() без _sanitize_prometheus_label
        matches = re.finditer(r'\.labels\(([^)]+)\)', line)
        for match in matches:
            labels_str = match.group(1)
            
            # Проверяем, что все значения используют _sanitize_prometheus_label
            # Исключаем безопасные константы
            safe_constants = [
                '"overall"', '"circuit_open"', '"exception"', '"empty_window"',
                '"too_few_messages"', '"quality_below_threshold"', '"missing_scope"',
                '"complete"', '"success"', '"non_retryable"', '"error"',
                '"generate"', '"failed"', '"send"', '"telegram_error"',
                '"unknown"', '"processed"', '"budget_exhausted"', '"idempotency"',
                '"policy"', '"ocr_primary"', '"ocr_fallback"', '"queued_low_priority"',
                '"emit_failed"', '"parse_error"', '"s3_missing"', '"s3_forbidden"',
                '"skipped"', '"ok"', '"pending"', '"completed"',
            ]
            
            # Разбиваем аргументы
            args = [arg.strip() for arg in labels_str.split(',')]
            for arg in args:
                if '=' not in arg:
                    continue
                
                value_part = arg.split('=', 1)[1].strip()
                
                # Проверяем, что значение санитизировано или является безопасной константой
                if not value_part.startswith('_sanitize_prometheus_label('):
                    # Проверяем, не является ли это безопасной константой
                    if value_part not in safe_constants and not value_part.startswith('safe_'):
                        # Проверяем, не является ли это переменной, которая уже санитизирована
                        if not (value_part.startswith('"') and value_part.endswith('"')):
                            issues.append({
                                'file': file_path,
                                'line': line_num,
                                'arg': arg,
                                'value': value_part,
                            })
    
    return issues

def audit_file(file_path: str) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Context7: Аудит одного файла."""
    full_path = os.path.join('/opt/telegram-assistant', file_path)
    if not os.path.exists(full_path):
        return {}, [], []
    
    with open(full_path, 'r') as f:
        content = f.read()
    
    definitions = find_metric_definitions(content, file_path)
    usages = find_metric_usages(content, file_path)
    sanitization_issues = check_sanitization(content, file_path)
    
    return definitions, usages, sanitization_issues

def main():
    """Context7: Главная функция аудита."""
    files_to_check = [
        'api/worker/tasks/group_digest_agent.py',
        'api/worker/tasks/digest_worker.py',
    ]
    
    all_definitions = {}
    all_usages = []
    all_sanitization_issues = []
    
    print("Context7: Аудит метрик Prometheus\n")
    print("=" * 80)
    
    # Собираем все определения и использования
    for file_path in files_to_check:
        definitions, usages, sanitization_issues = audit_file(file_path)
        all_definitions.update(definitions)
        all_usages.extend(usages)
        all_sanitization_issues.extend(sanitization_issues)
    
    # Выводим найденные определения
    print("\n=== НАЙДЕННЫЕ ОПРЕДЕЛЕНИЯ МЕТРИК ===")
    for metric_name, labels in sorted(all_definitions.items()):
        print(f"  {metric_name}: {labels}")
    
    # Проверяем соответствие количества labels
    print("\n=== ПРОВЕРКА СООТВЕТСТВИЯ КОЛИЧЕСТВА LABELS ===")
    label_count_issues = []
    for usage in all_usages:
        metric_name = usage['metric']
        if metric_name not in all_definitions:
            continue  # Метрика не определена в этих файлах
        
        expected_count = len(all_definitions[metric_name])
        actual_count = usage['arg_count']
        
        if actual_count != expected_count:
            label_count_issues.append({
                'metric': metric_name,
                'file': usage['file'],
                'line': usage['line'],
                'expected': expected_count,
                'actual': actual_count,
                'args': usage['args'][:100],
            })
    
    if label_count_issues:
        print(f"\n✗ Найдено {len(label_count_issues)} проблем с количеством labels:")
        for issue in label_count_issues:
            print(f"  {issue['metric']} в {issue['file']}:{issue['line']}")
            print(f"    Ожидалось: {issue['expected']}, найдено: {issue['actual']}")
            print(f"    {issue['args']}")
    else:
        print("\n✓ Все метрики используют правильное количество labels")
    
    # Проверяем санитизацию
    print("\n=== ПРОВЕРКА САНИТИЗАЦИИ LABELS ===")
    if all_sanitization_issues:
        print(f"\n⚠ Найдено {len(all_sanitization_issues)} потенциальных проблем с санитизацией:")
        for issue in all_sanitization_issues[:10]:  # Показываем первые 10
            print(f"  {issue['file']}:{issue['line']}")
            print(f"    {issue['arg']}")
    else:
        print("\n✓ Все labels санитизированы (или используют безопасные константы)")
    
    # Итоговый отчет
    print("\n" + "=" * 80)
    print("ИТОГОВЫЙ ОТЧЕТ:")
    print(f"  Определений метрик: {len(all_definitions)}")
    print(f"  Использований метрик: {len(all_usages)}")
    print(f"  Проблем с количеством labels: {len(label_count_issues)}")
    print(f"  Потенциальных проблем с санитизацией: {len(all_sanitization_issues)}")
    
    if label_count_issues or all_sanitization_issues:
        print("\n✗ НАЙДЕНЫ ПРОБЛЕМЫ - требуется исправление")
        return 1
    else:
        print("\n✓ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
        return 0

if __name__ == '__main__':
    sys.exit(main())

