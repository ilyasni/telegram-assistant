# Legacy Code Quarantine

[C7-ID: CODE-CLEANUP-016] Context7 best practice: карантин deprecated кода с runtime-стражем

## Правила

1. **Файлы в `/legacy/` НЕ импортируются в production коде**
2. **Автоматическое удаление** через указанный срок (см. метки в файлах)
3. **Runtime-страж**: при импорте в `ENV=production` → `ImportError` + метрика Prometheus

## Структура

```
legacy/
├── deprecated_YYYY-MM-DD/    # Дата создания карантина
│   ├── README.md             # Причина, замена, дата удаления
│   └── [deprecated files]
└── experiments/              # Экспериментальный код
```

## Формат меток

Каждый файл в legacy должен иметь заголовок:

```python
"""
@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Duplicated with shared/feature_flags.py
Replacement: from shared.feature_flags import feature_flags
"""
```

## Автоматическое удаление

GitHub Actions workflow парсит метки `@deprecated remove_by=` и открывает PR на удаление за 3 дня до срока.

## Runtime Guard

При попытке импорта в production:

```python
from legacy.deprecated_module import something  # → ImportError
# Prometheus: legacy_import_attempts_total{module="deprecated_module"} += 1
```

