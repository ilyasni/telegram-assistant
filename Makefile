SHELL := /bin/bash

.PHONY: guard env-check up-core up-app logs smoke

guard:
	./scripts/compose-guard.sh

env-check:
	./scripts/env-check.sh

up-core: guard
	docker compose up -d supabase-db kong rest meta auth supabase-studio realtime storage redis qdrant caddy

up-app: guard
	docker compose up -d api worker telethon-ingest

logs:
	docker compose ps
	docker compose logs --since=20m api | head -n 120
	docker compose logs --since=20m worker | head -n 120
	docker compose logs --since=20m telethon-ingest | head -n 120

smoke:
	@curl -sk -o /dev/null -w "REST % {http_code}\n" https://$${SUPABASE_HOST}/rest/v1/ || true
	@curl -sk -o /dev/null -w "AUTH % {http_code}\n" https://$${SUPABASE_HOST}/auth/v1/ || true
	@curl -sk -o /dev/null -w "STOR % {http_code}\n" https://$${SUPABASE_HOST}/storage/v1/ || true
