# Vision Baseline Snapshot (Context7)

## Цель

Зафиксировать базовые значения ключевых метрик Vision до включения оптимизаций.
Это позволяет сравнивать динамику токенов и количества запросов после развёртывания
PaddleOCR и последующих волн.

## Скрипт экспорта

Добавлен утилитарный скрипт `worker/scripts/export_vision_baseline.py`.

### Требования

- Prometheus с доступом по HTTP (по умолчанию `http://localhost:9090`).
- Python 3.11+ и зависимости worker (`httpx`, `structlog`).

### Пример запуска

```bash
export PROMETHEUS_BASE_URL="http://localhost:9090"
python worker/scripts/export_vision_baseline.py \
  --output reports/vision_baseline_$(date -u +%Y%m%dT%H%M%SZ).json
```

Параметры:

| Флаг | Описание |
|------|----------|
| `--prometheus-url` | Базовый URL Prometheus (по умолчанию `PROMETHEUS_BASE_URL` или `http://localhost:9090`). |
| `--output` | Путь к JSON-файлу с результатами (stdout, если не указан). |
| `--timeout` | Таймаут HTTP-запросов (сек). |

### Выходной формат

```json
{
  "timestamp": "2025-11-06T22:55:12.123456+00:00",
  "prometheus_url": "http://localhost:9090",
  "metrics": [
    {
      "metric": "vision_tokens_used_total",
      "query": "sum(vision_tokens_used_total)",
      "value": 12345.0,
      "raw": [...]
    }
  ]
}
```

Если запрос завершился ошибкой, вместо `value` будет поле `error` с описанием.

## Рекомендации по baseline

1. Выполнить экспорт минимум дважды (утро/вечер) до включения новой волны.
2. Сохранить JSON в `reports/` и приложить к change-log.
3. Для сравнения скачивание повторить после развёртывания фичи; дельту
   использовать в отчёте мониторинга.

## Дальнейшие шаги

- Настроить Cron/CI job для ежедневного снимка baseline.
- Доработать скрипт для агрегирования `increase()` по дневным интервалам,
  если потребуется анализ тренда.

## Сохранение Grafana Dashboard

Для визуальной части baseline используем вспомогательный скрипт
`scripts/save_grafana_dashboard_baseline.py` — он копирует 
`grafana/dashboards/vision_s3_dashboard.json` в каталог
`reports/grafana-baseline/` с меткой времени.

```bash
python scripts/save_grafana_dashboard_baseline.py --label pre-waveA
```

После запуска в `reports/grafana-baseline/` появится файл вида
`vision_s3_dashboard_20251106T230101Z_pre-waveA.json`. Рекомендуется хранить минимум
два снапшота: до и после изменений.

