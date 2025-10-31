# Code Analysis Reports

Автоматически генерируемые отчёты о качестве кода.

## Файлы

- `dead_code_vulture.csv` - мёртвый код, найденный Vulture
- `unused_imports.csv` - неиспользуемые импорты (Ruff)
- `duplicates.json` - дубликаты кода (JSCPD)
- `cleanup_candidates.md` - сводный отчёт

## Генерация

```bash
python scripts/inventory_dead_code.py
```

## Частота

Рекомендуется запускать еженедельно для отслеживания технического долга.

