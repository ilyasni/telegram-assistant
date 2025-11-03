# Grafana Best Practices - Context7

**Дата**: 2025-11-03  
**Применено**: Все дашборды обновлены согласно Context7 best practices

## Context7 Best Practices для Grafana Provisioning

### 1. Dashboard UID (Критично!)

**Проблема**: Дашборды без `uid` не могут быть правильно идентифицированы Grafana.

**Решение**: Каждый provisioned дашборд должен иметь уникальный `uid` в корне `dashboard`.

```json
{
  "dashboard": {
    "uid": "album-pipeline-monitoring",
    "title": "Album Pipeline Monitoring",
    ...
  }
}
```

**Конвенция именования uid**:
- Используем kebab-case
- Генерируем из title: "Album Pipeline" → "album-pipeline"
- Максимум 40 символов (ограничение Grafana)

### 2. Datasource UID

**Best Practice**: Всегда использовать `uid` вместо `name` для datasource.

```json
{
  "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
  }
}
```

**Преимущества**:
- Более надёжная идентификация (name может меняться)
- Поддержка разных окружений
- Лучшая миграция между инстансами

### 3. Provisioned Datasource Configuration

**Важно**: Datasource также должен иметь `uid` в provisioning конфигурации.

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus  # Context7: Критично для правильной работы
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

### 4. Dashboard Versioning

**Best Practice**: Для provisioned дашбордов:
- `version`: 0 или 1 (Grafana автоматически управляет версиями)
- `id`: null (для новых дашбордов)
- `schemaVersion`: 38 (для Grafana 10+)

### 5. Datasource в панелях

**Best Practice**: Указывать datasource на уровне панели И на уровне target:

```json
{
  "panels": [
    {
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "targets": [
        {
          "expr": "rate(metric[5m])",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          }
        }
      ]
    }
  ]
}
```

### 6. Provisioning Path Structure

**Best Practice**: Чёткая структура provisioning:

```
grafana/
├── provisioning/
│   ├── datasources/
│   │   └── datasources.yml  # Datasource с uid
│   └── dashboards/
│       └── dashboards.yml   # Dashboard provider конфигурация
└── dashboards/
    ├── album_pipeline.json  # Дашборды с uid
    ├── system_overview.json
    └── ...
```

### 7. Dashboard Provider Configuration

**Best Practice**: Использовать `foldersFromFilesStructure` для автоматической организации:

```yaml
apiVersion: 1

providers:
  - name: "Telegram Assistant Dashboards"
    folder: "Telegram Assistant"
    type: file
    options:
      path: /etc/grafana/dashboards
      foldersFromFilesStructure: true
      allowUiUpdates: false  # Файлы - источник правды
```

### 8. Автоматизация обновлений

**Best Practice**: Скрипты для автоматического исправления:

```bash
# Исправление всех дашбордов
python3 grafana/fix_dashboards_with_uid.py

# Обновление в Grafana
bash scripts/update_all_grafana_dashboards.sh
```

## Исправленные проблемы

### До исправления
- ❌ 8 из 9 дашбордов без `uid`
- ❌ Datasource без `uid` в provisioning
- ❌ Неполная структура datasource в панелях

### После исправления
- ✅ Все дашборды имеют уникальный `uid`
- ✅ Datasource имеет `uid: prometheus` в provisioning
- ✅ Все панели имеют правильный datasource с `uid`
- ✅ Соответствие Grafana schemaVersion 38

## Проверка

### Проверить uid дашбордов:
```bash
python3 -c "import json; import glob; \
  [print(f'{f}: {json.load(open(f)).get(\"dashboard\", {}).get(\"uid\", \"MISSING\")}') \
  for f in glob.glob('grafana/dashboards/*.json') if not f.endswith('.bak')]"
```

### Проверить datasource uid:
```bash
grep -A 2 "uid:" grafana/provisioning/datasources/datasources.yml
```

## Источники Context7

- Grafana Official Documentation: Provisioning Dashboards
- Grafana API Documentation: Dashboard UID
- Grafana Best Practices: Datasource UID

## Автоматизация

Создан скрипт `grafana/fix_dashboards_with_uid.py` для:
1. Автоматического добавления `uid` к дашбордам без uid
2. Исправления datasource конфигурации
3. Проверки соответствия best practices

**Использование**:
```bash
python3 grafana/fix_dashboards_with_uid.py
```

## Выводы

✅ Все дашборды соответствуют Context7 best practices  
✅ Используется `uid` для идентификации дашбордов и datasources  
✅ Provisioning настроен правильно  
✅ Автоматизация исправлений создана

