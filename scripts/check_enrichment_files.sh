#!/bin/bash
# Скрипт для проверки всех изменённых файлов

set -e

echo "=========================================="
echo "Checking Enrichment Migration Files"
echo "=========================================="
echo

# Проверка синтаксиса
echo "1. Checking Python syntax..."
python3 scripts/test_syntax_only.py
echo

# Проверка миграции
echo "2. Validating migration SQL..."
python3 scripts/validate_migration_sql.py
echo

# Проверка наличия файлов
echo "3. Checking file existence..."
files=(
    "api/alembic/versions/20250130_unify_post_enrichment_schema.py"
    "shared/python/shared/repositories/enrichment_repository.py"
    "shared/python/shared/repositories/__init__.py"
    "telethon-ingest/services/message_enricher.py"
    "scripts/test_enrichment_migration.sql"
    "docs/TESTING_ENRICHMENT_MIGRATION.md"
    "docs/TESTING_REPORT.md"
)

for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        echo "  ✓ $file"
    else
        echo "  ✗ $file (NOT FOUND)"
        exit 1
    fi
done

echo
echo "=========================================="
echo "✓ All checks passed!"
echo "=========================================="
echo
echo "Next steps:"
echo "1. Start Docker containers: docker compose up -d"
echo "2. Apply migration: docker compose exec api alembic upgrade head"
echo "3. Run tests: docker compose exec worker pytest tests/unit/ -v"
echo

