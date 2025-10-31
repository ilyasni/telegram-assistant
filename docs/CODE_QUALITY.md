# Code Quality & Cleanup

[C7-ID: CODE-CLEANUP-021] Руководство по инструментам code quality

## Установка инструментов

```bash
# Установка всех инструментов
pip install vulture ruff jscpd mypy bandit deptry detect-secrets import-linter pre-commit

# Или через requirements-dev.txt (если создан)
pip install -r requirements-dev.txt
```

## Pre-commit Hooks

### Установка

```bash
# Установить hooks
make pre-commit-install

# Или вручную
pre-commit install --install-hooks
```

### Использование

```bash
# Запустить на всех файлах
make pre-commit-run

# Или автоматически при git commit
git commit -m "..."  # hooks запустятся автоматически
```

## Code Quality команды

```bash
# Линтинг
make lint              # ruff check
make format            # ruff format
make format-check      # проверить форматирование

# Поиск проблем
make dead-code         # vulture (мёртвый код)
make check-duplicates  # jscpd (дубликаты)
make type-check        # mypy (типы)
make inventory         # полная инвентаризация

# Все проверки
make quality           # lint + format-check + dead-code + check-duplicates
```

## Автоматическая инвентаризация

Генерация отчётов о техническом долге:

```bash
python scripts/inventory_dead_code.py
```

Результаты сохраняются в `docs/reports/`:
- `dead_code_vulture.csv` - мёртвый код
- `unused_imports.csv` - неиспользуемые импорты
- `duplicates.json` - дубликаты кода
- `cleanup_candidates.md` - сводный отчёт

## Очистка Legacy кода

```bash
# Проверка (dry-run)
make clean-legacy

# Или напрямую
python scripts/cleanup_legacy.py --dry-run

# Удаление (после проверки)
python scripts/cleanup_legacy.py --force
```

## Shared Package

### Установка в локальной среде

```bash
pip install -e ./shared/python
```

### Использование в Docker

Добавить в Dockerfile каждого сервиса:

```dockerfile
# Установка shared пакета
COPY shared/ /app/shared/
RUN pip install -e /app/shared/python
```

## Архитектурные границы (Import Linter)

Проверка архитектурных правил:

```bash
lint-imports
```

Правила в `pyproject.toml` и `.importlinter`:
- `api` не импортирует `worker` или `telethon_ingest`
- Все кросс-сервисные импорты через `shared.*`

## Detect Secrets

Базовая линия для поиска секретов:

```bash
# Первый запуск: создать baseline
detect-secrets scan --update .secrets.baseline

# Проверка новых коммитов
detect-secrets scan --baseline .secrets.baseline

# Аудит baseline
detect-secrets audit .secrets.baseline
```

## CI/CD Integration

Все инструменты интегрированы в pre-commit hooks и будут автоматически запускаться:
- При локальном коммите (pre-commit hooks)
- В CI pipeline (GitHub Actions)

См. `.github/workflows/` для конфигурации CI.

