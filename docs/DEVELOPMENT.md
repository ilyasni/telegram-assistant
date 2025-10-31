# Development Mode Guide (Context7)

## Quick start

**Рекомендуемый способ через Makefile:**
```bash
make up-dev        # Все сервисы в dev-режиме
make up-dev-api    # Только API в dev-режиме
```

**Или напрямую через docker compose:**
```bash
docker compose --env-file .env.development -f docker-compose.yml -f docker-compose.dev.yml up -d
```

- API runs with hot reload and polling.
- Workers/telethon-ingest use bind mounts, no reload.
- Dockerfile оптимизированы с cache mounts для быстрой пересборки.

## Run only API in dev

```bash
make up-dev-api
# или
docker compose --env-file .env.development -f docker-compose.yml -f docker-compose.dev.yml up -d api
```

## Fast worker cycle

```bash
docker compose restart worker telethon-ingest
```

## Docker Build Optimization

Все Dockerfile оптимизированы по Context7 best practices:
- Разделение слоёв: `COPY requirements.txt` → `pip install` → `COPY . .`
- Cache mounts для pip: `--mount=type=cache,target=/root/.cache/pip`
- Объединение apt-get команд с `--no-install-recommends`

**Преимущества:**
- Пересборка при изменении кода не переустанавливает зависимости
- Cache mounts ускоряют установку пакетов между сборками
- Меньший размер слоёв

## Environment

- `APP_ENV=development` в override только.
- Fail-fast CORS в prod: wildcard `*` prohibited.

## Troubleshooting

- No reload on Proxmox/SMB: polling enabled via `--reload-use-polling` and `--reload-delay 0.3`.
- Storm restarts: excluded `sessions/*`, `logs/*`, `__pycache__/*`.
- Docker build медленный: проверьте `.dockerignore`, убедитесь что используется cache mount.

## Safety checklist (before merge to prod)

- `APP_ENV` defaults to `production`.
- No `CORS_ORIGINS=*` in prod.
- No code bind mounts in prod compose.
- `.dockerignore` present and aggressive.
- Healthchecks independent from reload.
