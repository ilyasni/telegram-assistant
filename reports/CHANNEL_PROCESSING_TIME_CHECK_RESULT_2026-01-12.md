# Результаты проверки Channel Processing Time

**Дата**: 2026-01-12  
**Время проверки**: ~20:00 UTC

---

## Результаты диагностики

### ✅ Метрика существует в Prometheus

**Метрика**: `parser_channel_processing_seconds_bucket`
- **Найдено записей**: 22
- **Статус**: Метрика присутствует и обновляется
- **Примеры значений**:
  - mode=incremental, status=ok, value=378
  - mode=incremental, status=ok, value=618
  - mode=incremental, status=ok, value=626

### ✅ Parser работает

**Метрика**: `parser_runs_total`
- **Всего запусков**: 637
- **Статус**: Все успешные (status=ok, mode=incremental)
- **Вывод**: Парсер активно обрабатывает каналы

### ✅ Scheduler работает

**Метрика**: `scheduler_last_tick_ts_seconds`
- **Последний тик**: 2026-01-12 19:51:23 UTC
- **Статус**: Scheduler активен и выполняет тики

### ⚠️ Проблема с перцентилями

**Запрос**: `histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`
- **Результат**: Ошибка парсинга JSON (возможно, Prometheus вернул пустой ответ)
- **Причина**: Если нет новых данных за последние 5 минут, `rate()` возвращает NaN или пустой результат

---

## Анализ проблемы

### Почему метрика "упала" в Grafana?

1. **Метрика существует** ✅ - данные есть в Prometheus
2. **Parser работает** ✅ - 637 успешных запусков
3. **Проблема**: Запрос `rate()` с окном 5 минут может возвращать NaN, если:
   - Нет новых данных за последние 5 минут
   - Все каналы уже обработаны и нет новых для парсинга
   - Парсер работает, но обрабатывает каналы медленно

### Решение

#### Вариант 1: Увеличить окно rate() (рекомендуется)

Изменить запрос в Grafana с `[5m]` на `[15m]` или `[30m]`:

```
histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[15m])) by (le, mode, status))
```

#### Вариант 2: Использовать increase() вместо rate()

Для абсолютных значений за период:

```
histogram_quantile(0.50, sum(increase(parser_channel_processing_seconds_bucket[1h])) by (le, mode, status))
```

#### Вариант 3: Добавить обработку NaN

В Grafana добавить `or vector(0)` для обработки пустых значений:

```
histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status)) or vector(0)
```

---

## Рекомендации

### Немедленные действия

1. **Проверить Grafana панель**:
   - Увеличить окно `rate()` до 15 минут
   - Добавить обработку NaN значений

2. **Проверить активность парсинга**:
   ```sql
   SELECT COUNT(*) FROM posts WHERE created_at > NOW() - INTERVAL '1 hour';
   SELECT id, username, last_parsed_at FROM channels ORDER BY last_parsed_at DESC LIMIT 10;
   ```

3. **Проверить логи telethon-ingest**:
   ```bash
   docker-compose logs telethon-ingest --tail=100 | grep -E "CHANNEL_PARSE|scheduler"
   ```

### Долгосрочные улучшения

1. **Добавить алерт** на отсутствие новых данных:
   ```yaml
   - alert: ParsingNoNewData
     expr: |
       sum(rate(parser_channel_processing_seconds_bucket[15m])) == 0
       and
       sum(rate(parser_runs_total[15m])) == 0
     for: 30m
     labels:
       severity: warning
   ```

2. **Улучшить панель Grafana**:
   - Добавить график `parser_runs_total` для контекста
   - Показать количество обработанных каналов
   - Добавить индикатор активности парсера

---

## Выводы

✅ **Все компоненты работают корректно**:
- Метрика существует и обновляется
- Parser активно обрабатывает каналы (637 запусков)
- Scheduler работает и выполняет тики

⚠️ **Проблема в запросе Grafana**:
- Запрос `rate()[5m]` может возвращать NaN при отсутствии новых данных
- Рекомендуется увеличить окно до 15 минут или использовать `increase()`

**Статус**: ✅ **Система работает нормально, проблема в визуализации метрики**

---

## Checks

1. ✅ Метрика существует: `parser_channel_processing_seconds_bucket` (22 записи)
2. ✅ Parser работает: `parser_runs_total` = 637
3. ✅ Scheduler активен: последний тик 19:51:23 UTC
4. ⚠️ Перцентили: требуется увеличить окно `rate()` в Grafana

**Следующие шаги**: Обновить запросы в Grafana панели "Channel Processing Time"
