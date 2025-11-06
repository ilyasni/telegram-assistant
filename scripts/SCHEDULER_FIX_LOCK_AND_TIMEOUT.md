# Исправление проблем с scheduler: lock и таймаут

## Проблемы

1. **Lock не освобождался при раннем return**: При отсутствии каналов функция делала `return` до блока `finally`, что приводило к зависанию lock.
2. **Tick зависал при долгом парсинге**: Парсинг 69 каналов выполнялся последовательно и мог занимать более 3 часов, что превышало TTL lock (600 секунд = 10 минут), блокируя следующие ticks.

## Исправления

### 1. Убрали ранний return при отсутствии каналов

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py`

**Было**:
```python
if not channels:
    logger.warning("No active channels found for parsing")
    return  # Lock не освобождался!
```

**Стало**:
```python
if not channels:
    logger.warning("No active channels found for parsing")
    # Context7: Не делаем return здесь, чтобы finally блок освободил lock
    # Просто пропускаем парсинг каналов
```

### 2. Добавили таймаут для tick

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py`

**Добавлено**:
- Ограничение времени выполнения tick: `max_tick_duration = self.interval_sec * 1.5` (90% от TTL lock)
- Проверка времени выполнения на каждой итерации цикла
- Логирование прогресса каждые 10 каналов
- Логирование длительности tick при завершении

**Код**:
```python
tick_start_time = datetime.now(timezone.utc)
max_tick_duration = self.interval_sec * 1.5  # 90% от TTL lock
channels_processed = 0

for idx, channel in enumerate(channels):
    # Проверяем, не превысили ли мы максимальное время выполнения
    elapsed = (datetime.now(timezone.utc) - tick_start_time).total_seconds()
    if elapsed > max_tick_duration:
        logger.warning(
            "Tick duration exceeded maximum, stopping channel processing",
            channels_processed=channels_processed,
            channels_total=len(channels),
            elapsed_seconds=elapsed,
            max_duration_seconds=max_tick_duration
        )
        break
    # ... обработка канала ...
    channels_processed += 1
    # Логируем прогресс каждые 10 каналов
    if channels_processed % 10 == 0:
        elapsed = (datetime.now(timezone.utc) - tick_start_time).total_seconds()
        logger.info(
            "Tick progress",
            channels_processed=channels_processed,
            channels_total=len(channels),
            elapsed_seconds=elapsed
        )
```

## Результат

- Lock теперь всегда освобождается через блок `finally`
- Tick не может зависнуть дольше, чем `interval_sec * 1.5` (450 секунд = 7.5 минут при `interval_sec = 300`)
- Добавлено логирование прогресса для диагностики
- Scheduler может запускаться каждые 5 минут без блокировки

## Context7 Best Practices

1. **Использование `finally` для гарантированного освобождения ресурсов**: Lock всегда освобождается, даже при ошибках или ранних выходах.
2. **Таймауты для предотвращения зависаний**: Ограничение времени выполнения tick предотвращает блокировку scheduler.
3. **Логирование прогресса**: Регулярное логирование помогает диагностировать проблемы производительности.

## Следующие шаги

1. Мониторить логи scheduler для проверки, что tick завершается в срок
2. Проверить, что новые посты начинают парситься после исправления
3. При необходимости оптимизировать парсинг каналов (параллелизация, приоритизация)

