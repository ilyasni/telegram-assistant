# Миграция Feature Flags на Shared Package

[C7-ID: CODE-CLEANUP-023] Инструкция по миграции на единую систему feature flags

## Текущее состояние

Два отдельных модуля:
- `worker/feature_flags.py` - интеграции (Neo4j, GigaChat, OpenRouter)
- `api/config/feature_flags.py` - диагностические флаги (AUTH_*)

## Цель

Единый модуль: `shared.feature_flags`

## Шаги миграции

### 1. Обновить импорты в worker

**Было:**
```python
from worker.feature_flags import feature_flags

if feature_flags.neo4j_enabled:
    ...
```

**Стало:**
```python
from shared.feature_flags import feature_flags

if feature_flags.integrations.neo4j_enabled:
    ...
```

### 2. Обновить импорты в api

**Было:**
```python
from api.config.feature_flags import feature_flags

if feature_flags.is_enabled("AUTH_FINALIZE_DB_BYPASS"):
    ...
```

**Стало:**
```python
from shared.feature_flags import feature_flags

if feature_flags.diagnostics.finalize_db_bypass:
    # или
if feature_flags.is_enabled("AUTH_FINALIZE_DB_BYPASS"):  # обратная совместимость
    ...
```

### 3. Добавить deprecation warnings

Добавить в старые модули (`worker/feature_flags.py`, `api/config/feature_flags.py`):

```python
import warnings
from shared.feature_flags import feature_flags as _new_flags

warnings.warn(
    "worker.feature_flags is deprecated. Use shared.feature_flags instead.",
    DeprecationWarning,
    stacklevel=2
)

# Re-export для обратной совместимости
feature_flags = _new_flags
```

### 4. Тестирование

После миграции проверить:
- ✅ Все сервисы запускаются
- ✅ Feature flags работают корректно
- ✅ Нет regressions в поведении

### 5. Удаление старых модулей

После 2 недель использования нового модуля:
- Переместить старые файлы в `legacy/deprecated_YYYY-MM-DD/`
- Добавить метки `@deprecated remove_by=YYYY-MM-DD+14`
- Обновить документацию

## Переменные окружения

Все переменные остаются теми же:
- `FEATURE_NEO4J_ENABLED=true`
- `FEATURE_GIGACHAT_ENABLED=true`
- `AUTH_FINALIZE_DB_BYPASS=on`

Pydantic-settings автоматически парсит их с правильными префиксами.

## Обратная совместимость

Метод `is_enabled()` сохраняет обратную совместимость:
```python
# Старый способ (работает)
feature_flags.is_enabled("AUTH_FINALIZE_DB_BYPASS")

# Новый способ (рекомендуется)
feature_flags.diagnostics.finalize_db_bypass
```

## OpenFeature семантика

Новый API поддерживает OpenFeature:
```python
value, reason = feature_flags.get_flag("neo4j_enabled")
# Returns: (True, FlagReason.DEFAULT)
```

