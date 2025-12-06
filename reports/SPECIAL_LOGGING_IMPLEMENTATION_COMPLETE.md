# Специальное логирование для проблемных каналов - Реализация

**Дата**: 2025-12-03  
**Статус**: ✅ Реализовано  
**Context7**: Детальное логирование для диагностики проблемных каналов

---

## Проблема

Из аудита обнаружены каналы с критическими проблемами:
- Канал "Около Искусства": покрытие 0.01% (47 постов из 898,752)
- Большие gaps в message_id
- Отсутствие детальной диагностической информации в логах

---

## Решение

### Добавлен метод `_log_problematic_channel_stats()`

**Местоположение**: `telethon-ingest/services/channel_parser.py`

**Функциональность**:
1. Вычисляет статистику покрытия каналов
2. Определяет проблемные каналы по критериям
3. Логирует детальную информацию для диагностики
4. Обновляет метрику `channel_coverage_percent`

### Критерии проблемных каналов

1. **Низкое покрытие**: `coverage_percent < 10%`
   - Процент сохраненных постов от ожидаемого диапазона message_id

2. **Большие gaps**: `gap_size > 1000` постов
   - Разница между ожидаемым и фактическим количеством постов

3. **Нет обработанных сообщений**: `messages_processed == 0 && batch_count > 0`
   - Батчи были обработаны, но сообщения не сохранены

4. **Высокое соотношение пропусков**: `messages_skipped > messages_processed * 2`
   - Большое количество пропущенных сообщений относительно обработанных

### Детальное логирование

**Для проблемных каналов** (WARNING уровень):
```python
logger.warning(
    "PROBLEMATIC_CHANNEL_DETECTED",
    channel_id=channel_id,
    channel_username=username,
    channel_title=title,
    tg_channel_id=tg_channel_id,
    messages_processed=messages_processed,
    messages_skipped=messages_skipped,
    batch_count=batch_count,
    processing_time_seconds=processing_time,
    mode=mode,
    posts_count_in_db=posts_count,
    min_message_id=min_message_id,
    max_message_id=max_message_id,
    message_id_range=message_id_range,
    expected_posts=expected_posts,
    gap_size=gap_size,
    coverage_percent=coverage_percent,
    days_with_posts=days_with_posts,
    problem_reasons=problem_reasons,
    stats=self.stats.copy()
)
```

**Для нормальных каналов** (DEBUG уровень):
- Менее детальное логирование
- Только основные метрики

---

## Интеграция

### Вызов после парсинга

Метод вызывается в `parse_channel_messages()` после завершения парсинга:

```python
# Context7: Специальное логирование для проблемных каналов
await self._log_problematic_channel_stats(
    channel_id=channel_id,
    channel_entity=channel_entity,
    messages_processed=messages_processed,
    messages_skipped=self.stats.get('messages_skipped', 0),
    batch_count=batch_count,
    processing_time=processing_time,
    mode=mode
)
```

### Метрики

**Обновление метрики покрытия**:
- `channel_coverage_percent{channel_username="..."}` - процент покрытия канала
- Обновляется для всех каналов (проблемных и нормальных)

---

## Вычисляемые метрики

### Покрытие каналов

```python
# Диапазон message_id
message_id_range = max_message_id - min_message_id + 1

# Ожидаемое количество постов
expected_posts = message_id_range

# Процент покрытия
coverage_percent = (posts_count / expected_posts) * 100

# Размер gap
gap_size = message_id_range - posts_count
```

---

## Примеры использования

### Пример 1: Канал с низким покрытием

```
[WARNING] PROBLEMATIC_CHANNEL_DETECTED
  channel_id: "3e68034c-59b5-429d-911d-62ab3b8e5c95"
  channel_username: "okolo_art"
  channel_title: "Около Искусства"
  coverage_percent: 0.01
  expected_posts: 898752
  posts_count_in_db: 47
  gap_size: 898705
  problem_reasons: ["low_coverage_0.01%", "large_gap_898705"]
```

### Пример 2: Канал без обработанных сообщений

```
[WARNING] PROBLEMATIC_CHANNEL_DETECTED
  channel_id: "..."
  messages_processed: 0
  batch_count: 5
  problem_reasons: ["no_messages_processed"]
```

---

## Ожидаемые результаты

### До добавления логирования:
- ❌ Нет детальной информации о проблемных каналах
- ❌ Сложно диагностировать причины потерь постов
- ❌ Нет метрик покрытия каналов

### После добавления логирования:
- ✅ Детальное логирование для проблемных каналов
- ✅ Автоматическое определение проблемных каналов
- ✅ Метрики покрытия в Prometheus
- ✅ Улучшенная диагностика проблем

---

## Файлы изменены

- `telethon-ingest/services/channel_parser.py`
  - Добавлен метод `_log_problematic_channel_stats()`
  - Интегрирован вызов после парсинга канала

---

## Следующие шаги

1. ⏳ Перезапустить контейнер `telethon-ingest` для применения изменений
2. ⏳ Мониторить логи на наличие `PROBLEMATIC_CHANNEL_DETECTED`
3. ⏳ Проверить метрику `channel_coverage_percent` в Prometheus
4. ⏳ Использовать логи для диагностики проблемных каналов

---

**Статус**: ✅ Готово к применению

