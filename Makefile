SHELL := /bin/bash

.PHONY: guard env-check up-core up-app logs smoke up-dev up-dev-api

guard:
	./scripts/compose-guard.sh

env-check:
	./scripts/env-check.sh

up-core: guard
	# Context7: realtime и storage только в dev режиме, не включаем в production
	docker compose --profile core up -d supabase-db kong rest meta auth supabase-studio redis qdrant caddy

up-app: guard
	docker compose up -d api worker telethon-ingest

# [C7-ID: dev-mode-009] Dev-режим сборки с горячей перезагрузкой
up-dev: guard
	@echo "Starting in DEV mode with hot reload..."
	docker compose --env-file .env.development -f docker-compose.yml -f docker-compose.dev.yml up -d

up-dev-api: guard
	@echo "Starting API in DEV mode with hot reload..."
	docker compose --env-file .env.development -f docker-compose.yml -f docker-compose.dev.yml up -d api

logs:
	docker compose ps
	docker compose logs --since=20m api | head -n 120
	docker compose logs --since=20m worker | head -n 120
	docker compose logs --since=20m telethon-ingest | head -n 120

smoke:
	@curl -sk -o /dev/null -w "REST % {http_code}\n" https://$${SUPABASE_HOST}/rest/v1/ || true
	@curl -sk -o /dev/null -w "AUTH % {http_code}\n" https://$${SUPABASE_HOST}/auth/v1/ || true
	@curl -sk -o /dev/null -w "STOR % {http_code}\n" https://$${SUPABASE_HOST}/storage/v1/ || true

# ============================================================================
# CODE QUALITY & CLEANUP
# ============================================================================
# [C7-ID: CODE-CLEANUP-018] Makefile команды для code quality инструментов

.PHONY: lint format dead-code check-duplicates type-check clean-legacy inventory

lint:
	@echo "Running ruff linter..."
	ruff check .
	@echo "✓ Linter passed"

format:
	@echo "Formatting code with ruff..."
	ruff format .
	@echo "✓ Code formatted"

format-check:
	@echo "Checking code formatting..."
	ruff format --check .
	@echo "✓ Formatting OK"

dead-code:
	@echo "Checking for dead code with vulture..."
	vulture . --min-confidence 80 --exclude tests/ --exclude legacy/ || true

check-duplicates:
	@echo "Checking for code duplicates with jscpd..."
	jscpd --min-tokens 80 --threshold 1% --languages "python,javascript" . || true

type-check:
	@echo "Running type checker (mypy) on shared/ and api/..."
	mypy shared/python/shared/ api/ || true

inventory:
	@echo "Running dead code inventory..."
	python scripts/inventory_dead_code.py

clean-legacy:
	@echo "Checking legacy files for auto-removal..."
	python scripts/cleanup_legacy.py --dry-run

pre-commit-install:
	@echo "Installing pre-commit hooks..."
	pre-commit install --install-hooks

pre-commit-run:
	@echo "Running pre-commit hooks..."
	pre-commit run --all-files

quality: lint format-check dead-code check-duplicates
	@echo "✓ All quality checks completed"
