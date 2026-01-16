# Диагностика падения Channel Processing Time

**Дата**: 2026-01-14T16:01:00+03:00  
**Проблема**: Метрика Channel Processing Time упала в Grafana  
**Context7**: Диагностика падения метрики `parser_channel_processing_seconds`

---

## Context

Метрика `parser_channel_processing_seconds` измеряет время обработки одного канала парсером. Падение метрики в Grafana может означать:
1. Парсер не обрабатывает каналы
2. Недостаточно данных для расчета перцентилей
3. Проблема с запросами в Grafana (слишком маленькое окно для rate())

---

## Результаты диагностики

### 1. ✅ Метрика существует и обновляется

**Метрика**: `parser_channel_processing_seconds_bucket`
- **Найдено записей**: 22
- **Статус**: Метрика присутствует и обновляется
- **Примеры значений**:
  - incremental/ok: le=0.5, value=13
  - incremental/ok: le=1.0, value=83
  - incremental/ok: le=2.5, value=147
  - incremental/ok: le=5.0, value=148

### 2. ✅ Parser работает

**Метрика**: `parser_runs_total`
- **Incremental**: 152 запусков
- **Historical**: 198 запусков
- **Статус**: Парсер активно обрабатывает каналы

### 3. ✅ Каналы парсятся

**Статистика парсинга**:
- **Всего активных каналов**: 122
- **Парсилось за последний час**: 22 канала
- **Парсилось за последние 6 часов**: 71 канал
- **Последний парсинг**: 2026-01-14 12:36:14 UTC (около 3.5 часов назад)

### 4. ⚠️ Проблема с запросами в Grafana

**Текущие запросы в Grafana** (окно `[5m]`):
- p50: `histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`
- p95: `histogram_quantile(0.95, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`
- p99: `histogram_quantile(0.99, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`

**Проблема**: 
- За 5 минут парсится ~0-2 канала (недостаточно данных)
- `rate([5m])` возвращает NaN или пустой результат при недостатке данных
- Перцентили не могут быть рассчитаны

**Решение с окном `[15m]`**:
- p50: incremental/ok: **1.04 сек**, historical/ok: **0.32 сек** ✅
- p95: incremental/ok: **2.35 сек**, historical/ok: **0.77 сек** ✅
- p99: incremental/ok: **2.47 сек**, historical/ok: **0.95 сек** ✅

---

## Причина падения

### Основная причина: Недостаточно данных за 5 минут

**Частота парсинга**:
- За 5 минут: ~0-2 канала
- За 15 минут: ~5-7 каналов
- За 1 час: ~22 канала
- За 6 часов: ~71 канал

**Вывод**: Парсер работает с интервалом 5 минут, но обрабатывает не все каналы за каждый тик. За 5 минут может быть обработано 0-2 канала, что недостаточно для расчета `rate([5m])`.

---

## Решение

### 1. Увеличить окно rate() в Grafana

**Рекомендуемое изменение**: Увеличить окно с `[5m]` до `[15m]` или `[30m]`

**Новые запросы**:
```promql
# p50
histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[15m])) by (le, mode, status))

# p95
histogram_quantile(0.95, sum(rate(parser_channel_processing_seconds_bucket[15m])) by (le, mode, status))

# p99
histogram_quantile(0.99, sum(rate(parser_channel_processing_seconds_bucket[15m])) by (le, mode, status))
```

### 2. Альтернативное решение: Использовать increase() вместо rate()

Если нужно видеть абсолютные значения за период:

```promql
# p50 за последний час
histogram_quantile(0.50, sum(increase(parser_channel_processing_seconds_bucket[1h])) by (le, mode, status))
```

---

## Текущие значения перцентилей (с окном 15m)

| Перцентиль | Incremental/ok | Historical/ok |
|------------|----------------|---------------|
| p50 | 1.04 сек | 0.32 сек |
| p95 | 2.35 сек | 0.77 сек |
| p99 | 2.47 сек | 0.95 сек |

**Вывод**: Метрика работает нормально, но требует большего окна для расчета перцентилей.

---

## Checks

Для проверки:

```bash
# Проверка метрики в Prometheus
docker compose exec prometheus wget -qO- "http://localhost:9090/api/v1/query?query=parser_channel_processing_seconds_bucket" | python3 -m json.tool

# Проверка перцентилей с окном 15m
docker compose exec prometheus wget -qO- "http://localhost:9090/api/v1/query?query=histogram_quantile(0.50,%20sum(rate(parser_channel_processing_seconds_bucket[15m]))%20by%20(le,%20mode,%20status))" | python3 -m json.tool

# Проверка активности парсинга
docker compose exec api python3 -c "
import asyncio
import asyncpg
async def check():
    conn = await asyncpg.connect(host='supabase-db', port=5432, user='postgres', password='postgres', database='postgres')
    row = await conn.fetchrow('SELECT COUNT(*) FILTER (WHERE last_parsed_at >= NOW() - INTERVAL \"1 hour\") as parsed_1h FROM channels WHERE is_active = true')
    print(f'Каналов парсилось за час: {row[\"parsed_1h\"]}')
    await conn.close()
asyncio.run(check())
"
```

---

## Impact / Rollback

**Impact**:
- Метрика работает корректно
- Проблема только в запросах Grafana (слишком маленькое окно)
- Увеличение окна до 15m решает проблему

**Rollback**: Не требуется, нужно только обновить запросы в Grafana

---

**Context7 Best Practices**: Метрика работает корректно, проблема в конфигурации запросов Grafana. Рекомендуется увеличить окно rate() для более стабильных результатов.
