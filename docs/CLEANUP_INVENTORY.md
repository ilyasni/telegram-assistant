# Инвентаризация для очистки кода

**Дата:** 2025-02-01  
**Цель:** Выявление дублирования кода, хардкода и устаревших файлов

## 1. Дублирование кода

### 1.1. Feature Flags импорты
**Найдено использование старых импортов:**
- `worker/tasks/cleanup_task.py` - использует `from worker.feature_flags`
- `worker/health.py` - использует `from worker.feature_flags`
- `tests/unit/test_gigachain_adapter.py` - использует `from worker.feature_flags`

**Требуется миграция на:** `shared.feature_flags`

### 1.2. Database Connection Strings
**Дублирование логики получения connection strings найдено в:**
- `scripts/test_vision_pipeline.py` - функция `get_db_connection_string()`
- `scripts/test_vision_manual_trigger.py` - функция `get_db_connection_string()`
- `scripts/test_media_audit_features.py` - функция `get_db_connection_string()`
- `telethon-ingest/services/channel_parser.py` - хардкод `postgresql://user:pass@localhost/db`

**Требуется:** Создать `shared/python/shared/utils/db_connection.py`

### 1.3. Health Check модули
**Найдены модули для проверки:**
- `worker/health_check.py` - проверить использование
- `worker/simple_health_server.py` - проверить использование
- `worker/health_server.py` - проверить использование

**Основной модуль:** `worker/health.py`

## 2. Хардкод

### 2.1. Пароли и секреты
**Найдены хардкод пароли:**
- `api/config.py:146` - `neo4j_password: str = "neo4j123"` ⚠️ КРИТИЧНО
- `worker/config.py:54` - `neo4j_password: str = os.getenv("NEO4J_PASSWORD", "neo4j123")` ⚠️
- `neo4j/health_server.py:133` - `neo4j_password = os.getenv('NEO4J_PASSWORD', 'changeme')` ⚠️
- `scripts/verify_system_after_cleanup.py:168` - дефолт `"neo4j123"`
- `scripts/cleanup_all_test_data.py:610` - дефолт `"neo4j123"`
- `scripts/test_album_pipeline_full.py:322` - дефолт `"changeme"`

**Требуется:** Использовать `SecretStr` из Pydantic, убрать дефолты

### 2.2. localhost дефолты
**Найдены хардкод localhost:**
- `worker/tasks/cleanup_task.py:79-80` - `qdrant_url: str = "http://localhost:6333"`, `neo4j_url: str = "bolt://localhost:7687"`
- `crawl4ai/enrichment_engine.py:103` - `redis_url: str = "redis://localhost:6379"`
- `telethon-ingest/services/channel_parser.py:65` - `redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")`
- `telethon-ingest/services/channel_parser.py:68` - `db_url: str = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/db")`
- `telethon-ingest/services/channel_parser.py:2571` - `create_async_engine("postgresql+asyncpg://user:pass@localhost/db")`

**Требуется:** Заменить на ENV переменные без дефолтов localhost

### 2.3. Magic Numbers
**Требуется анализ:** Вынести таймауты, лимиты, размеры буферов в константы

## 3. Устаревшие файлы

### 3.1. test_*.py в корне
**Найдены:**
- `api/test_telegram_formatter.py` → переместить в `tests/unit/`
- `worker/test_async.py` → переместить в `tests/unit/` или удалить если не используется
- `gpt2giga-proxy/test_*.py` (4 файла) → проверить, возможно оставить в gpt2giga-proxy/

### 3.2. .bak файлы
**Найдены:**
- `telethon-ingest/main.py.bak` → удалить
- `grafana/dashboards/system_overview.json.bak` → удалить
- `grafana/dashboards/parser_streams.json.bak` → удалить
- `grafana/dashboards/rag_service.json.bak` → удалить

### 3.3. Временная документация
**Требуется анализ:** Проверить docs/ и scripts/ на временные статус-отчеты

## 4. Архитектурные границы

### 4.1. Worker → API импорты
**Найдено нарушение:**
- `worker` импортирует `api.services.s3_storage` (временно, с TODO [ARCH-SHARED-001])

**Требуется:** Зафиксировать в importlinter.ini как временное исключение

## 5. Инструменты

### 5.1. Не установлены
- `jscpd` - для проверки дубликатов (опционально)
- `vulture` - для поиска мёртвого кода (опционально)

### 5.2. Доступны
- `ruff` - для линтинга и форматирования
- `grep` / `rg` - для поиска паттернов

## Следующие шаги

1. ✅ Инвентаризация завершена
2. ⏳ Настроить pre-commit hooks (Этап 0)
3. ⏳ Мигрировать feature flags
4. ⏳ Создать db_connection утилиту
5. ⏳ Удалить хардкод паролей
6. ⏳ Заменить localhost дефолты

