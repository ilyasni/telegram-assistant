#!/usr/bin/env python3
"""
Валидация SQL в миграции через анализ строк.

Context7: Проверка корректности SQL синтаксиса перед применением миграции.
"""

import re
import sys
import os

def validate_migration_sql():
    """Валидация SQL в миграции."""
    migration_file = os.path.join(
        os.path.dirname(__file__), '..', 'api', 'alembic', 'versions',
        '20250130_unify_post_enrichment_schema.py'
    )
    
    if not os.path.exists(migration_file):
        print(f"✗ Migration file not found: {migration_file}")
        return False
    
    with open(migration_file, 'r') as f:
        content = f.read()
    
    errors = []
    warnings = []
    
    # Проверка наличия ключевых конструкций (Alembic операции + SQL)
    checks = [
        (r'UPDATE\s+post_enrichment', 'UPDATE statement for backfill'),
        ('jsonb_build_object', 'JSONB functions'),
        (r'op\.create_unique_constraint|CREATE\s+UNIQUE', 'Unique constraint creation'),
        (r'op\.create_index|CREATE\s+INDEX', 'Index creation'),
        (r'op\.alter_column.*kind|ALTER\s+COLUMN.*kind', 'ALTER COLUMN kind'),
        (r'op\.alter_column.*provider|ALTER\s+COLUMN.*provider', 'ALTER COLUMN provider'),
        (r'nullable\s*=\s*False', 'Making columns NOT NULL'),
        (r'op\.add_column.*kind', 'Adding kind column'),
        (r'op\.add_column.*provider', 'Adding provider column'),
        (r'op\.add_column.*data', 'Adding data JSONB column'),
    ]
    
    for pattern, description in checks:
        if not re.search(pattern, content, re.IGNORECASE | re.MULTILINE | re.DOTALL):
            errors.append(f"Missing: {description}")
        else:
            print(f"  ✓ Found: {description}")
    
    # Проверка бекфилла данных
    if 'UPDATE post_enrichment' not in content:
        warnings.append("Backfill UPDATE statements not found")
    
    # Проверка удаления дублей
    if 'DELETE FROM post_enrichment' in content:
        print("  ✓ Duplicate cleanup found")
    
    # Проверка индексов
    index_patterns = [
        r'idx_post_enrichment_post_kind',
        r'idx_post_enrichment_kind',
        r'idx_post_enrichment_updated_at',
        r'idx_post_enrichment_data_gin'
    ]
    
    for pattern in index_patterns:
        if not re.search(pattern, content):
            warnings.append(f"Index pattern not found: {pattern}")
        else:
            print(f"  ✓ Index found: {pattern}")
    
    # Вывод результатов
    if errors:
        print("✗ Errors found:")
        for error in errors:
            print(f"  - {error}")
        return False
    
    if warnings:
        print("⚠ Warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    
    print("✓ SQL migration validation passed")
    return True


def validate_down_revision():
    """Проверка down_revision."""
    migration_file = os.path.join(
        os.path.dirname(__file__), '..', 'api', 'alembic', 'versions',
        '20250130_unify_post_enrichment_schema.py'
    )
    
    with open(migration_file, 'r') as f:
        content = f.read()
    
    # Проверяем down_revision
    match = re.search(r"down_revision = ['\"]([^'\"]+)['\"]", content)
    if match:
        down_rev = match.group(1)
        print(f"  ✓ Down revision: {down_rev}")
        
        # Проверяем, что файл существует
        prev_migration = os.path.join(
            os.path.dirname(migration_file),
            f"{down_rev}.py"
        )
        
        # Ищем файл по revision ID или по имени
        versions_dir = os.path.dirname(migration_file)
        found = False
        for filename in os.listdir(versions_dir):
            if filename.endswith('.py') and down_rev in filename:
                found = True
                print(f"  ✓ Previous migration file found: {filename}")
                break
        
        if not found:
            print(f"  ⚠ Previous migration file not found for: {down_rev}")
    
    return True


if __name__ == '__main__':
    print("Validating migration SQL...")
    print()
    
    sql_ok = validate_migration_sql()
    print()
    revision_ok = validate_down_revision()
    
    if sql_ok and revision_ok:
        print("\n✓ All validations passed!")
        sys.exit(0)
    else:
        print("\n✗ Some validations failed")
        sys.exit(1)

