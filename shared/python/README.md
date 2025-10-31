# Shared Package

[C7-ID: CODE-CLEANUP-012] Shared utilities для микросервисов Telegram Assistant.

## Установка

```bash
# Editable install в каждом сервисе
pip install -e ./shared/python
```

## Структура

```
shared/
└── python/
    └── shared/
        ├── feature_flags/     # Единая система feature flags
        ├── s3_storage/        # S3 client (будущее)
        └── health/            # Health checks (будущее)
```

## Использование

```python
from shared.feature_flags import feature_flags

# Проверка флагов
if feature_flags.integrations.neo4j_enabled:
    # Use Neo4j
    
# OpenFeature semantics
value, reason = feature_flags.get_flag("neo4j_enabled")
```

## Docker Integration

В каждом Dockerfile сервиса (api, worker, telethon-ingest):

```dockerfile
# Установка shared пакета
COPY shared/ /app/shared/
RUN pip install -e /app/shared/python
```

## Версионирование

Shared пакет версионируется независимо. Изменения в shared/ должны быть обратно совместимы или требовать миграции во всех сервисах.

