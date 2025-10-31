#!/usr/bin/env python3
"""
Промежуточные тесты Vision + S3 интеграции
Проверка структуры и базовой функциональности без внешних зависимостей
"""

import sys
import os
import ast

def test_syntax(file_path):
    """Проверка синтаксиса файла."""
    try:
        with open(file_path, 'r') as f:
            ast.parse(f.read())
        return True, None
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} на строке {e.lineno}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)}"

def test_imports_structure(file_path):
    """Проверка структуры импортов."""
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Проверка наличия основных импортов
        checks = {
            'has_class': 'class ' in content,
            'has_async': 'async def' in content,
            'has_logger': 'logger' in content or 'structlog' in content,
        }
        
        return checks
    except Exception as e:
        return {'error': str(e)}

def main():
    """Основная функция тестирования."""
    print("=" * 60)
    print("ПРОМЕЖУТОЧНОЕ ТЕСТИРОВАНИЕ VISION + S3 ИНТЕГРАЦИИ")
    print("=" * 60)
    print()
    
    test_files = {
        'S3 Storage Service': 'api/services/s3_storage.py',
        'Storage Quota Service': 'worker/services/storage_quota.py',
        'URL Canonicalizer': 'api/services/url_canonicalizer.py',
        'Budget Gate Service': 'worker/services/budget_gate.py',
        'Vision Policy Engine': 'worker/services/vision_policy_engine.py',
        'Retry Policy': 'worker/services/retry_policy.py',
        'OCR Fallback': 'worker/services/ocr_fallback.py',
        'GigaChat Vision Adapter': 'worker/ai_adapters/gigachat_vision.py',
        'Vision Analysis Task': 'worker/tasks/vision_analysis_task.py',
        'Vision Event Schemas': 'worker/events/schemas/posts_vision_v1.py',
        'DLQ Event Schema': 'worker/events/schemas/dlq_v1.py',
        'Media Processor': 'telethon-ingest/services/media_processor.py',
    }
    
    results = []
    
    for name, file_path in test_files.items():
        if not os.path.exists(file_path):
            results.append((name, file_path, False, "Файл не найден"))
            continue
        
        syntax_ok, syntax_error = test_syntax(file_path)
        structure = test_imports_structure(file_path)
        
        if syntax_ok:
            results.append((name, file_path, True, "OK", structure))
        else:
            results.append((name, file_path, False, syntax_error, structure))
    
    # Вывод результатов
    print("РЕЗУЛЬТАТЫ ПРОВЕРКИ:")
    print("-" * 60)
    
    passed = 0
    failed = 0
    
    for name, file_path, ok, error, *structure in results:
        status = "✓ PASS" if ok else "✗ FAIL"
        print(f"{status:8} | {name:30} | {os.path.basename(file_path)}")
        if not ok:
            print(f"         | Ошибка: {error}")
        if structure and structure[0]:
            struct_info = structure[0]
            if 'error' not in struct_info:
                info_parts = []
                if struct_info.get('has_class'):
                    info_parts.append("class")
                if struct_info.get('has_async'):
                    info_parts.append("async")
                if struct_info.get('has_logger'):
                    info_parts.append("logger")
                if info_parts:
                    print(f"         | Структура: {', '.join(info_parts)}")
        
        if ok:
            passed += 1
        else:
            failed += 1
    
    print("-" * 60)
    print(f"ИТОГО: {passed} прошли, {failed} провалились из {len(test_files)}")
    print()
    
    # Проверка миграций
    print("ПРОВЕРКА МИГРАЦИЙ БД:")
    print("-" * 60)
    migration_path = 'api/alembic/versions/20250128_add_media_registry_vision.py'
    if os.path.exists(migration_path):
        syntax_ok, error = test_syntax(migration_path)
        if syntax_ok:
            print(f"✓ Миграция {os.path.basename(migration_path)}: синтаксис корректен")
        else:
            print(f"✗ Миграция {os.path.basename(migration_path)}: {error}")
    else:
        print(f"? Миграция не найдена: {migration_path}")
    
    print()
    
    # Итоговый статус
    if failed == 0:
        print("✓ ВСЕ ТЕСТЫ ПРОШЛИ")
        return 0
    else:
        print(f"✗ НАЙДЕНЫ ОШИБКИ ({failed} файлов)")
        return 1

if __name__ == "__main__":
    sys.exit(main())

