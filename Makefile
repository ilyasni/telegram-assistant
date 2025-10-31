SHELL := /bin/bash

.PHONY: guard env-check up-core up-app logs smoke up-dev up-dev-api

guard:
	./scripts/compose-guard.sh

env-check:
	./scripts/env-check.sh

up-core: guard
	docker compose up -d supabase-db kong rest meta auth supabase-studio realtime storage redis qdrant caddy

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
