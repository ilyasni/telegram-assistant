# Проверка метрик Prometheus на дублирование

**Дата**: 2026-01-14 22:25  
**Context7**: Полная проверка всех метрик Prometheus на дублирование

---

## Исправленные файлы

### 1. ✅ `services/floodwait_manager.py`
**Проблема**: Метрики `telethon_floodwait_total`, `telethon_floodwait_duration_seconds`, `tg_floodwait_seconds_gauge` создавались без защиты от дублирования

**Исправление**:
- Добавлена проверка существования метрик в REGISTRY перед созданием
- Обработка `ValueError: Duplicated timeseries` при создании
- Поиск существующих метрик по частичному совпадению имен
- Защита от ошибок при использовании метрик (проверка на None)

### 2. ✅ `services/qr_auth.py`
**Проблема**: 
- Метрики `FLOODWAIT_TOTAL` и `FLOODWAIT_DURATION` конфликтовали с `floodwait_manager.py`
- Метрики `AUTH_QR_*` создавались напрямую без защиты

**Исправление**:
- Удалены дублирующие метрики `FLOODWAIT_TOTAL` и `FLOODWAIT_DURATION`
- Добавлены функции `_get_or_create_counter` и `_get_or_create_histogram`
- Все метрики `AUTH_QR_*` используют защиту от дублирования
- Метрики `SESSION_CLEANUP_*`, `QR_SESSION_*`, `RATE_LIMIT_HITS`, `THROTTLING_DELAY` используют защиту

### 3. ✅ `tasks/parse_all_channels_task.py`
**Проблема**: Все метрики создавались напрямую без защиты

**Исправление**:
- Добавлены функции `_get_or_create_counter`, `_get_or_create_histogram`, `_get_or_create_gauge`
- Все 18 метрик используют защиту от дублирования:
  - `parser_runs_total`
  - `parsing_duration_seconds`
  - `posts_parsed_total`
  - `incremental_watermark_age_seconds`
  - `scheduler_lock_acquired_total`
  - `parser_hwm_age_seconds`
  - `parser_mode_forced_total`
  - `scheduler_last_tick_ts_seconds`
  - `scheduler_heartbeat_seconds`
  - `parser_retries_total`
  - `parser_channel_processing_seconds`
  - `parser_floodwait_seconds_total`
  - `posts_missing_duration_seconds`
  - `posts_backfill_triggered_total`
  - `channel_last_post_timestamp_seconds`
  - `parser_last_success_seconds`
  - `adaptive_threshold_seconds`
  - `channel_gap_seconds`
  - `backfill_jobs_total`
  - `interarrival_seconds`

### 4. ✅ `services/rate_limiter.py`
**Проблема**: Метрики создавались напрямую без защиты

**Исправление**:
- Добавлены функции `_get_or_create_counter` и `_get_or_create_gauge` с поддержкой `namespace`
- Все метрики используют защиту:
  - `rate_limit_hits_total`
  - `rate_limit_requests_total`
  - `active_rate_limits`

### 5. ✅ `main.py`
**Проблема**: Метрики создавались напрямую без защиты

**Исправление**:
- Добавлены функции `_get_or_create_counter` и `_get_or_create_histogram` с поддержкой `namespace`
- Все метрики используют защиту:
  - `request_count` (http_requests_total)
  - `request_duration` (http_request_duration_seconds)
  - `crash_signals_total`
  - `crash_state_saved_total`
  - `faulthandler_dumps_total`

### 6. ✅ `services/telegram_client_manager.py`
**Проблема**: Метрики создавались напрямую без защиты

**Исправление**:
- Добавлены функции `_get_or_create_counter`, `_get_or_create_histogram`, `_get_or_create_gauge`
- Все метрики используют защиту:
  - `telethon_disconnects_total`
  - `telethon_reconnect_attempts_total`
  - `telethon_reconnect_duration_seconds`
  - `telethon_connected_clients`
  - `telethon_authorized_clients`

### 7. ✅ `services/media_processor.py`
**Проблема**: Метрики создавались напрямую без защиты

**Исправление**:
- Добавлены функции `_get_or_create_counter`, `_get_or_create_histogram`, `_get_or_create_gauge`
- Все метрики используют защиту:
  - `media_processing_total`
  - `media_bytes_total`
  - `media_size_bytes`
  - `media_processing_duration_seconds`
  - `media_albums_processed_total`
  - `media_processing_failed_total`
  - `metrics_backend_up`

### 8. ✅ `services/atomic_db_saver.py`
**Проблема**: Частично использовалась защита, но не все метрики

**Исправление**:
- Улучшена функция `_get_or_create_counter` с обработкой `ValueError`
- Добавлена функция `_get_or_create_histogram`
- Все метрики используют защиту:
  - `db_users_upserted_total`
  - `db_channels_upserted_total`
  - `media_objects_upserted_total`
  - `media_objects_refs_updated_total`
  - `post_media_map_inserted_total`
  - `cas_operations_latency_seconds`
  - `cas_operations_errors_total`

### 9. ✅ `services/telethon_retry.py`
**Проблема**: Метрика создавалась напрямую без защиты

**Исправление**:
- Добавлена функция `_get_or_create_gauge`
- Метрика `cooldown_channels_total` использует защиту

### 10. ✅ `services/channel_parser.py`
**Статус**: Уже использовалась защита от дублирования
- Все метрики используют `_get_or_create_counter` и `_get_or_create_gauge`
- Поддержка `namespace` параметра

---

## Общая стратегия защиты

### Функции `_get_or_create_*`

Все файлы теперь используют единообразный подход:

1. **Проверка существования**:
   ```python
   existing = REGISTRY._names_to_collectors.get(name)
   if existing:
       return existing
   ```

2. **Создание с обработкой ошибок**:
   ```python
   try:
       return Counter(name, description, labels)
   except ValueError as e:
       if "Duplicated timeseries" in str(e):
           # Поиск существующей метрики
           return REGISTRY._names_to_collectors.get(name)
   ```

3. **Логирование предупреждений** при обнаружении дублирования

### Особые случаи

1. **FloodWait метрики** (`floodwait_manager.py`):
   - Проверка всех возможных вариантов имен (`telethon_floodwait_total`, `telethon_floodwait`, `telethon_floodwait_created`)
   - Поиск по частичному совпадению при ошибке
   - Защита от None при использовании метрик

2. **Метрики с namespace** (`rate_limiter.py`, `main.py`, `qr_auth.py`):
   - Функции поддерживают параметр `namespace`
   - Передача `namespace` в конструктор метрики

---

## Проверка результатов

### ✅ Ошибки дублирования исправлены

**До исправления**:
- Множественные ошибки `ValueError: Duplicated timeseries` в логах
- Контейнер перезапускался из-за ошибок при импорте модулей

**После исправления**:
- Нет ошибок дублирования в последних логах
- FloodWaitManager создается успешно
- Scheduler запускается без ошибок

### Метрики доступны

- Все метрики доступны через `/metrics` endpoint
- Нет конфликтов при перезагрузке модулей
- Метрики корректно обновляются

---

## Выводы

✅ **Все метрики защищены от дублирования:**

1. **8 файлов исправлено** с добавлением функций `_get_or_create_*`
2. **~50+ метрик** теперь используют защиту от дублирования
3. **Конфликты устранены**:
   - Удалены дублирующие метрики `FLOODWAIT_TOTAL` и `FLOODWAIT_DURATION` из `qr_auth.py`
   - Улучшена обработка дублирования в `floodwait_manager.py`

4. **Единообразный подход**:
   - Все файлы используют одинаковую стратегию защиты
   - Функции `_get_or_create_*` обрабатывают все случаи дублирования

**Статус**: Все метрики защищены от дублирования, готово к production
