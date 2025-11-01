#!/usr/bin/env python3
"""
Проверка синтаксиса Python файлов без импорта зависимостей.

Context7: Базовая валидация перед тестированием в Docker.
"""

import py_compile
import sys
import os

def test_file_syntax(filepath):
    """Проверка синтаксиса одного файла."""
    try:
        py_compile.compile(filepath, doraise=True)
        return True, None
    except py_compile.PyCompileError as e:
        return False, str(e)

def main():
    """Проверка всех изменённых файлов."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    files_to_check = [
        'api/alembic/versions/20250130_unify_post_enrichment_schema.py',
        'shared/python/shared/repositories/enrichment_repository.py',
        'shared/python/shared/repositories/__init__.py',
        'telethon-ingest/services/message_enricher.py',
        'telethon-ingest/services/atomic_db_saver.py',
        'worker/tasks/vision_analysis_task.py',
        'worker/tasks/tag_persistence_task.py',
        'worker/tasks/enrichment_task.py',
        'crawl4ai/crawl4ai_service.py',
        'api/models/database.py',
        'worker/shared/database.py',
    ]
    
    print("=" * 60)
    print("Syntax Validation Tests")
    print("=" * 60)
    print()
    
    errors = []
    for rel_path in files_to_check:
        filepath = os.path.join(base_dir, rel_path)
        if not os.path.exists(filepath):
            print(f"⚠ File not found: {rel_path}")
            continue
        
        success, error = test_file_syntax(filepath)
        if success:
            print(f"✓ {rel_path}")
        else:
            print(f"✗ {rel_path}")
            print(f"  Error: {error}")
            errors.append((rel_path, error))
    
    print()
    print("=" * 60)
    if errors:
        print(f"✗ {len(errors)} file(s) have syntax errors")
        return 1
    else:
        print("✓ All files have valid syntax")
        return 0

if __name__ == '__main__':
    sys.exit(main())

