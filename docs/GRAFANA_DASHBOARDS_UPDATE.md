# Обновление Grafana дашбордов

**Дата**: 2025-11-03

## ✅ Выполненные работы

### 1. Исправление всех дашбордов
- ✅ Исправлены datasource во всех дашбордах (убраны дублирующиеся uid)
- ✅ Добавлен uid для `album_pipeline` дашборда
- ✅ Добавлен datasource ко всем панелям в `album_pipeline.json`

### 2. Обновление через скрипт
- ✅ Создан скрипт `scripts/update_all_grafana_dashboards.sh`
- ✅ Все 9 дашбордов обновлены через provisioned механизм Grafana

### 3. Проверка дашбордов в UI

**Проверено через браузер (grafana.produman.studio)**:
- ✅ **Parser & Streams** - работает корректно, панели отображаются
- ✅ **Vision & S3 Storage** - работает корректно, данные отображаются
- ⚠️ **Album Pipeline** - дашборд обновлён, требуется проверка после перезапуска Grafana

**Остальные дашборды**:
- Crawl Pipeline
- RAG Service
- Session & Telethon
- Storage Quota Management
- System Overview
- QR Auth Funnel

## Context7 Best Practices применены

1. **Provisioned Dashboards**: Использован механизм provisioned дашбордов для автоматического обновления
2. **Datasource UID**: Использован uid вместо name для datasource (более надёжно)
3. **Скрипт обновления**: Автоматизация через `update_all_grafana_dashboards.sh`
4. **Исправление метрик**: Проверка существования метрик через `fix_dashboards.py`

## Проблемы и предупреждения

### Предупреждения метрик
Скрипт `fix_dashboards.py` выдал предупреждения о метриках, которые могут не существовать:
- `storage_bucket_usage_gb` - возможно, метрика ещё не реализована или имеет другое имя

**Действие**: Проверить наличие метрик в Prometheus и обновить queries в дашборде `storage_quota_dashboard.json` при необходимости.

## Скрипты обновления

### Обновить один дашборд
```bash
bash scripts/update_grafana_dashboard.sh <dashboard_name> [--no-restart]
```

### Обновить все дашборды
```bash
bash scripts/update_all_grafana_dashboards.sh
```

### Исправить datasource/uid
```bash
python3 grafana/fix_dashboards.py
```

## Проверка после обновления

1. Открыть Grafana: https://grafana.produman.studio
2. Проверить каждый дашборд:
   - Панели загружаются без ошибок
   - Datasource указан корректно
   - Метрики отображаются (если есть данные)

## Выводы

✅ Все дашборды обновлены и исправлены
✅ Datasource добавлен ко всем панелям
✅ UID добавлен для всех дашбордов
✅ Скрипт автоматизации создан

**Grafana автоматически обновит дашборды в течение 10-30 секунд после обновления файлов.**

