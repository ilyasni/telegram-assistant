---
title: Trend Pipeline Runbook
date: 2025-11-15
context7: true
---

## Обзор

Пайплайн трендов теперь состоит из двух режимов:

1. **Reactive / emerging** — `worker/trends_worker.py` подписан на `posts.indexed`, строит embedding, управляет кластерами (`trend_clusters`) и метриками (`trend_metrics`), сохраняет статистику в Redis и публикует события `trends.emerging`.
2. **Stable / подтверждение** — `api/tasks/scheduler_tasks.py::trends_stable_task` (ежечасно) анализирует `trend_metrics`, обновляет статус кластера, создаёт записи `TrendDetection` и связывает их через `resolved_trend_id`.

## Компоненты

| Компонент | Назначение | Ключевые файлы |
|-----------|------------|----------------|
| Redis schema | Единый нейминг ключей (`trend:{cluster}:freq:5m`, `stream:trends.emerging`) | `shared/python/shared/trends/redis_schema.py` |
| Reactive worker | ANN-кластеризация, burst расчёт, публикация events | `worker/trends_worker.py`, `worker/event_bus.py` |
| Stable task | Ежечасный cron (APScheduler) → перевод emerging → stable | `api/tasks/scheduler_tasks.py` |
| API | REST для `TrendDetection` + `/trends/emerging`, `/trends/clusters` | `api/routers/trends.py` |
| Bot | aiogram handlers (`trends:emerging`, `trend:cluster`) | `api/bot/handlers/trends_handlers.py` |

## Наблюдаемость

- **Prometheus**:
  - `trend_events_processed_total{status}` — обработанные `posts.indexed`.
  - `trend_emerging_events_total{status}` — сколько emerging событий опубликовано или отклонено.
  - `trend_worker_latency_seconds{outcome}` — латентность обработки.
  - `trend_card_llm_requests_total{outcome}` — попытки LLM-обогащения карточек (`requested|success|error|skipped`).
  - `trend_cluster_sample_posts_bucket` — распределение количества примерных постов в карточке (0/1/2/3/5/8/10/15).
- **Redis**:
  - `trend:{cluster}:freq:5m|1h|24h` — expiring counters (проверить `TTL` при возникновении дизбаланса).
  - `trend:{cluster}:sources` — множество каналов, TTL = 24h.
  - `stream:trends.emerging` — очередь событий для downstream.
- **Postgres**:
  - `trend_clusters` — статус, embedding, novelty.
  - `trend_metrics` — baseline snapshots, обновляются воркером.
  - `trends_detection` — итоговые подтверждённые тренды.

## Диагностика

1. **Reactive слой**
   ```bash
   docker compose logs worker | grep TrendDetectionWorker
   redis-cli LLEN stream:trends:emerging
   ```
2. **Stable слой**
   ```bash
   docker compose logs api | grep "Trends stable task"
   psql -c "select label,status,source_diversity from trend_clusters order by last_activity_at desc limit 5"
   ```
3. **API**
   ```bash
   curl -s http://localhost:8000/api/trends/emerging | jq '.clusters[0]'
   curl -s http://localhost:8000/api/trends/clusters?status=stable | jq '.total'
   curl -s -X POST http://localhost:8000/api/trends/summarize_cluster \
        -H 'Content-Type: application/json' \
        -d '{"cluster_id":"<uuid>","force":true}' | jq '.card'
   ```

4. **Реплей истории**
   ```bash
   # dry-run: проверяем сколько постов попадёт в реплей
   docker compose exec worker python scripts/replay_trend_posts.py --hours 6 --limit 100 --dry-run

   # фактический запуск
   docker compose exec worker python scripts/replay_trend_posts.py --hours 6 --limit 300
   ```
   Скрипт сам вытягивает свежие `posts.id` из Postgres и публикует `posts.indexed` события в Redis, чтобы воркер пересобрал кластеры и карточки.

## Тесты

- Unit-тесты: `pytest tests/unit/test_trend_redis_schema.py`
- Card helpers: `pytest tests/unit/test_trend_card_helpers.py`
- API smoke: `pytest tests/test_group_context_service.py -k trends` (проверяет маршрутизацию бота)

## Оповещения

- Настрой в Grafana:
  - `trend_emerging_events_total{status="failed"} > 0` — сигнал о сбоях публикации.
  - `trend_worker_latency_seconds{outcome="error"}` — аномально высокий лаг.
  - `trend_clusters` без обновления `last_activity_at` > 1h — возможная деградация воркера.


