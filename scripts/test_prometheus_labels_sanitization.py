#!/usr/bin/env python3
"""
Context7: Скрипт для тестирования санитизации Prometheus метрик на реальных данных.

Использование:
    python3 scripts/test_prometheus_labels_sanitization.py
    docker compose exec worker python3 /opt/telegram-assistant/scripts/test_prometheus_labels_sanitization.py
"""

import sys
import os
from pathlib import Path

# Context7: Настройка путей для импорта
project_root = Path(__file__).resolve().parent.parent
api_path = project_root / "api"
sys.path.insert(0, str(api_path))
sys.path.insert(0, str(project_root))

import re


def _sanitize_prometheus_label(value):
    """Context7: Универсальная функция санитизации значений для Prometheus labels."""
    if value is None:
        return "unknown"
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return "unknown"
    # Заменяем невалидные символы на подчеркивания
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', value)
    # Убираем множественные подчеркивания
    sanitized = re.sub(r'_+', '_', sanitized)
    # Убираем подчеркивания в начале и конце
    sanitized = sanitized.strip('_')
    # Если начинается с цифры, добавляем префикс
    if sanitized and sanitized[0].isdigit():
        sanitized = f"label_{sanitized}"
    # Если пусто, возвращаем unknown
    return sanitized if sanitized else "unknown"


def test_sanitization():
    """Context7: Тест санитизации невалидных символов."""
    print("=== ТЕСТ 1: Санитизация невалидных символов ===")
    
    test_cases = [
        ("test:value", "test_value"),
        ("test-value", "test_value"),
        ("test.value", "test_value"),
        ("test/value", "test_value"),
        ("test@value", "test_value"),
        ("123test", "label_123test"),
        (None, "unknown"),
        ("", "unknown"),
        ("   ", "unknown"),
        ("_test_", "test"),
        ("test___value", "test_value"),
    ]
    
    all_passed = True
    for input_val, expected in test_cases:
        result = _sanitize_prometheus_label(input_val)
        status = "✓" if result == expected else "✗"
        print(f"{status} {repr(input_val)} -> {result} (ожидалось: {expected})")
        if result != expected:
            all_passed = False
    
    return all_passed


def test_real_tenant_ids():
    """Context7: Тест с реальными UUID tenant_id."""
    print("\n=== ТЕСТ 2: Реальные tenant_id ===")
    
    real_tenant_ids = [
        "e70c43b0-e11d-45a8-8e51-f0ead91fb126",
        "50997e52-0fd7-460f-8791-3e59e6ac88c0",
        "cc1e70c9-9058-4fd0-9b52-94012623f0e0",
    ]
    
    all_passed = True
    for tenant_id in real_tenant_ids:
        sanitized = _sanitize_prometheus_label(tenant_id)
        print(f"✓ {tenant_id[:20]}... -> {sanitized[:30]}...")
        if "-" in sanitized:
            print(f"  ✗ ОШИБКА: Дефисы не удалены: {sanitized}")
            all_passed = False
        # Проверяем, что результат валиден для Prometheus
        if not re.match(r'^[a-zA-Z0-9_]+$', sanitized):
            print(f"  ✗ ОШИБКА: Результат содержит невалидные символы: {sanitized}")
            all_passed = False
    
    return all_passed


def test_code_analysis():
    """Context7: Анализ кода на наличие несанитизированных метрик."""
    print("\n=== ТЕСТ 3: Анализ кода на несанитизированные метрики ===")
    
    files_to_check = [
        "api/worker/tasks/group_digest_agent.py",
        "api/worker/tasks/digest_worker.py",
    ]
    
    all_passed = True
    for file_path in files_to_check:
        full_path = project_root / file_path
        if not full_path.exists():
            print(f"⚠ Файл не найден: {file_path}")
            continue
        
        with open(full_path, 'r') as f:
            content = f.read()
        
        # Ищем все .labels( без _sanitize_prometheus_label
        pattern = r'\.labels\([^)]*\)'
        matches = re.findall(pattern, content)
        
        unsanitized = []
        for match in matches:
            if '_sanitize_prometheus_label' not in match:
                # Проверяем, не является ли это определением метрики
                if 'PromCounter' not in match and 'Gauge' not in match and 'Histogram' not in match:
                    # Проверяем, не является ли это безопасной константой
                    safe_constants = [
                        'success', 'failed', 'error', 'complete', 'generate', 'send',
                        'telegram_error', 'non_retryable', 'circuit_open', 'unknown',
                        'empty_window', 'too_few_messages', 'quality_below_threshold',
                        'missing_scope', 'circuit_open', 'exception', 'quality_retry',
                        'context_dedup', 'context_trimmed', 'context_history',
                    ]
                    has_safe_constant = any(f'"{c}"' in match or f"'{c}'" in match for c in safe_constants)
                    if not has_safe_constant:
                        unsanitized.append(match)
        
        print(f"\n{file_path}:")
        print(f"  Всего использований .labels(): {len(matches)}")
        print(f"  Без санитизации (исключая безопасные константы): {len(unsanitized)}")
        
        if unsanitized:
            print("  ✗ Найдены места без санитизации:")
            for m in unsanitized[:5]:
                print(f"    {m[:80]}")
            all_passed = False
        else:
            print("  ✓ Все метрики санитизированы или используют безопасные константы")
    
    return all_passed


def main():
    """Context7: Главная функция для запуска всех тестов."""
    print("Context7: Тестирование санитизации Prometheus метрик\n")
    
    results = []
    
    # Тест 1: Санитизация
    results.append(("Санитизация невалидных символов", test_sanitization()))
    
    # Тест 2: Реальные tenant_id
    results.append(("Реальные tenant_id", test_real_tenant_ids()))
    
    # Тест 3: Анализ кода
    results.append(("Анализ кода", test_code_analysis()))
    
    # Итоги
    print("\n" + "=" * 60)
    print("ИТОГИ:")
    print("=" * 60)
    
    all_passed = True
    for test_name, passed in results:
        status = "✓ ПРОЙДЕН" if passed else "✗ НЕ ПРОЙДЕН"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("✓ ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
        return 0
    else:
        print("✗ НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОЙДЕНЫ")
        return 1


if __name__ == "__main__":
    sys.exit(main())

