# Quick Start: Code Cleanup Tools

[C7-ID: CODE-CLEANUP-031] Быстрый старт для использования инструментов очистки

## Установка (один раз)

```bash
# Установить все инструменты
pip install vulture ruff jscpd mypy bandit deptry detect-secrets import-linter pre-commit

# Установить pre-commit hooks
make pre-commit-install
# или
pre-commit install --install-hooks
```

## Ежедневное использование

### Перед коммитом (автоматически)

Pre-commit hooks запустятся автоматически:
```bash
git commit -m "your message"
# → автоматически запустятся все проверки
```

### Ручной запуск проверок

```bash
# Все проверки
make quality

# Отдельные проверки
make lint           # ruff check
make format         # ruff format
make dead-code      # vulture
make check-duplicates  # jscpd
make type-check     # mypy
```

### Инвентаризация технического долга

```bash
# Генерация отчётов
make inventory

# Результаты в docs/reports/:
# - dead_code_vulture.csv
# - unused_imports.csv
# - duplicates.json
# - cleanup_candidates.md
```

### Очистка legacy кода

```bash
# Проверка (dry-run)
make clean-legacy

# Удаление (после проверки)
python scripts/cleanup_legacy.py --force
```

## Использование Shared Package

### В коде

```python
# Feature flags (новая система)
from shared.feature_flags import feature_flags

if feature_flags.integrations.neo4j_enabled:
    # Use Neo4j

# OpenFeature семантика
value, reason = feature_flags.get_flag("neo4j_enabled")
```

### Локальная разработка

```bash
# Установка shared пакета
pip install -e ./shared/python
```

### Docker (уже настроено)

Dockerfile'ы автоматически устанавливают shared пакет.

## Проверка перед коммитом

Чеклист:
- [ ] `make quality` проходит без ошибок
- [ ] Нет импортов из `legacy/`
- [ ] Feature flags используют `shared.feature_flags`
- [ ] Pre-commit hooks установлены (`make pre-commit-install`)

## Troubleshooting

### ImportError: cannot import name 'feature_flags' from 'shared.feature_flags'

```bash
# Установите shared пакет
pip install -e ./shared/python
```

### Pre-commit hooks не запускаются

```bash
# Переустановите hooks
pre-commit uninstall
pre-commit install --install-hooks
```

### Ruff/vulture не найдены

```bash
pip install ruff vulture
```

