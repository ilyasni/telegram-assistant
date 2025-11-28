# Проверка Scheduler и пайплайна обработки постов

**Дата**: 2025-11-28  
**Context7**: Диагностика отсутствия новых постов

---

## Статус компонентов

### ✅ Scheduler (ParseAllChannelsTask)
- **Статус**: Запущен и работает
- **Контейнер**: `telegram-assistant-telethon-ingest-1` (Up 17 minutes, healthy)
- **Режим**: Incremental parsing (FEATURE_INCREMENTAL_PARSING_ENABLED=true)
- **Интервал**: 300 секунд (5 минут)
- **Проблема**: ❌ **KeyError: 'processed'** при парсинге каналов

### ✅ Worker Tasks
- **Статус**: Запущен и работает
- **Контейнер**: `telegram-assistant-worker-1` (Up 11 hours, healthy)
- **Задачи**: Все задачи активны

### ✅ Пайплайн обработки событий

#### Стримы Redis:
- **posts.parsed**: 7902 сообщений, lag=0 ✅
  - Consumer groups: `post_persist_workers` (270 consumers), `tagging_workers` (1 consumer)
- **posts.tagged**: 7896 сообщений, lag=0 ✅
  - Consumer groups: `crawl_trigger_workers` (1 consumer), `enrich_workers` (1 consumer)
- **posts.enriched**: 15454 сообщений, lag=0 ✅
  - Consumer groups: `indexing_workers` (1 consumer)
- **posts.indexed**: lag=10013 в monitoring группе ⚠️
  - Consumer groups: `indexing_monitoring` (0 consumers, lag=10013), `trend_workers` (122 consumers)

---

## Обнаруженные проблемы

### 🔴 Критично: KeyError в Scheduler

**Проблема**: Scheduler падает с ошибкой `KeyError: 'processed'` при парсинге каналов.

**Причина**: 
1. `parse_channel_messages` возвращает результат БЕЗ ключа `status`, но scheduler проверяет `result.get("status") == "success"`
2. `_process_message_batch` может вернуть результат без ключа `processed`, но код пытается получить `batch_result['processed']` напрямую

**Логи ошибки**:
```
[ERROR] Parse channel failed after 3 retries: 'processed'
[WARNING] Session in transaction after error, rolling back
```

**Исправление**: ✅ Исправлено
- Обновлена проверка результата в `_run_tick()` для работы с форматом без ключа `status`
- Добавлена безопасная обработка `batch_result` с проверкой наличия ключей

**Файлы**:
- `telethon-ingest/tasks/parse_all_channels_task.py` (строки 571-592)
- `telethon-ingest/services/channel_parser.py` (строки 479-500)

---

## Статистика постов

### Последние посты в БД:
- **Всего постов**: 6025
- **Последний пост**: 2025-11-26 20:55:25 (2 дня назад)
- **Последнее создание**: 2025-11-26 21:16:30

### Каналы:
- **Активных каналов**: 5+ (с `is_active = true`)
- **Каналы без last_parsed_at**: 
  - `dvapiva`
  - `PragmaticMarketingShkipin`
  - `aigentto`
  - `prostopropivo`
  - `beer_by`

**Проблема**: Новые каналы не парсятся из-за ошибки KeyError в scheduler.

---

## Пайплайн обработки постов

### Этапы пайплайна:

```
1. Telegram Message/Album
   ↓
2. ChannelParser → MediaProcessor → AtomicDBSaver
   ↓ posts.parsed ✅ (7902 сообщений, lag=0)
3. PostPersistenceTask (сохранение в БД)
   ↓
4. VisionAnalysisTask (Vision анализ)
   ↓ posts.vision.analyzed
5. RetaggingTask (ретеггинг с Vision)
   ↓ posts.tagged (trigger=vision_retag)
6. TaggingTask (тегирование новых постов)
   ↓ posts.tagged ✅ (7896 сообщений, lag=0)
7. TagPersistenceTask (сохранение тегов в БД)
   ↓ posts.enriched
8. EnrichmentTask (Crawl4AI обогащение)
   ↓ posts.enriched ✅ (15454 сообщений, lag=0)
9. IndexingTask (Qdrant + Neo4j)
   ↓ posts.indexed ⚠️ (lag=10013 в monitoring группе)
10. AlbumAssemblerTask (сборка альбомов)
    ↓ album.assembled
```

### Статус каждого этапа:

1. **Парсинг** ✅: Работает, но падает с KeyError на некоторых каналах
2. **Post Persistence** ✅: Работает (270 consumers)
3. **Vision Analysis** ✅: Работает (если настроен)
4. **Tagging** ✅: Работает (1 consumer, lag=0)
5. **Tag Persistence** ✅: Работает
6. **Enrichment (Crawl4AI)** ✅: Работает (1 consumer, lag=0)
7. **Indexing (Qdrant + Neo4j)** ⚠️: Работает, но есть lag в monitoring группе
8. **Album Assembler** ✅: Работает

---

## Рекомендации

### Немедленные действия:

1. ✅ **Исправлен KeyError в scheduler** - перезапустить контейнер `telethon-ingest` для применения исправлений
2. ⚠️ **Проверить lag в posts.indexed** - monitoring группа имеет lag=10013, но основная группа работает
3. ⚠️ **Проверить новые каналы** - каналы без `last_parsed_at` должны парситься после исправления

### Долгосрочные улучшения:

1. **Мониторинг Scheduler**:
   - Добавить метрики для отслеживания успешности парсинга
   - Настроить алерты на ошибки парсинга

2. **Обработка ошибок**:
   - Улучшить логирование ошибок парсинга
   - Добавить retry логику для transient ошибок

3. **Проверка пайплайна**:
   - Регулярная проверка lag в consumer groups
   - Мониторинг пропусков постов между этапами

---

## Команды для проверки

### Проверка статуса Scheduler:
```bash
docker logs telegram-assistant-telethon-ingest-1 --tail 50 | grep -i "scheduler\|tick\|lock"
```

### Проверка lag в стримах:
```bash
docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:posts:parsed
docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:posts:tagged
docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:posts:enriched
docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:posts:indexed
```

### Проверка последних постов:
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "SELECT COUNT(*), MAX(posted_at) FROM posts;"
```

### Проверка каналов:
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "SELECT id, title, last_parsed_at FROM channels WHERE is_active = true ORDER BY last_parsed_at DESC NULLS FIRST LIMIT 10;"
```

---

## Выводы

1. ✅ **Scheduler запущен** и работает, но падает с KeyError на некоторых каналах
2. ✅ **Пайплайн работает** - все этапы обрабатывают события без задержек (кроме monitoring группы)
3. ⚠️ **Новые посты отсутствуют** - последний пост был 2 дня назад, возможно из-за ошибки парсинга
4. ✅ **Исправление применено** - KeyError исправлен, требуется перезапуск контейнера

**Следующий шаг**: Перезапустить контейнер `telethon-ingest` для применения исправлений и проверить парсинг новых каналов.

