#!/bin/bash
# Context7 best practice: Применение SQL миграций с отслеживанием через schema_migrations

set -euo pipefail

MIGRATIONS_DIR="${MIGRATIONS_DIR:-telethon-ingest/migrations}"
DB_CONTAINER="${DB_CONTAINER:-supabase-db}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-postgres}"

echo "Applying migrations from $MIGRATIONS_DIR"

# Создаем таблицу отслеживания миграций
docker compose exec -T "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SQL

# Применяем каждую миграцию
for migration_file in "$MIGRATIONS_DIR"/*.sql; do
    if [ ! -f "$migration_file" ]; then
        echo "No migration files found"
        exit 0
    fi
    
    version=$(basename "$migration_file" .sql)
    echo "Checking migration: $version"
    
    # Проверяем, применена ли миграция
    already_applied=$(docker compose exec -T "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = '$version');" | tr -d ' \n')
    
    if [ "$already_applied" = "t" ]; then
        echo "  ✓ Migration $version already applied, skipping"
        continue
    fi
    
    echo "  → Applying migration $version..."
    
    # Применяем миграцию
    if docker compose exec -T "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" < "$migration_file" > /dev/null 2>&1; then
        # Отмечаем как примененную
        docker compose exec -T "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "INSERT INTO schema_migrations (version) VALUES ('$version') ON CONFLICT DO NOTHING;" > /dev/null 2>&1
        echo "  ✓ Migration $version applied successfully"
    else
        echo "  ✗ Failed to apply migration $version"
        exit 1
    fi
done

echo "All migrations completed successfully"

