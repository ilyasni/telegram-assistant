# ✅ Checklist развертывания новых компонентов

## 🎯 Предварительные проверки

### 1. Файлы и структура
- [x] ✅ Все новые файлы созданы
- [x] ✅ Все обновленные файлы изменены
- [x] ✅ E2E тесты созданы
- [x] ✅ Документация обновлена

### 2. Миграции БД
- [x] ✅ Миграция 002 (индексы) создана
- [x] ✅ Миграция 003 (альбомы) создана
- [ ] ⚠️  Миграции должны быть применены в БД перед запуском

**Проверка применения миграций:**
```sql
-- Проверка таблиц media_groups
SELECT COUNT(*) FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name = 'media_groups';

-- Проверка таблиц media_group_items
SELECT COUNT(*) FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name = 'media_group_items';

-- Проверка поля grouped_id в posts
SELECT COUNT(*) FROM information_schema.columns 
WHERE table_name = 'posts' AND column_name = 'grouped_id';

-- Проверка индексов
SELECT indexname FROM pg_indexes 
WHERE tablename IN ('media_groups', 'media_group_items', 'posts')
AND indexname LIKE 'idx_%' OR indexname LIKE 'ux_%';
```

### 3. Зависимости
- [x] ✅ Все зависимости в `worker/requirements.txt`
- [x] ✅ `prometheus-client` присутствует
- [x] ✅ `structlog` присутствует
- [x] ✅ `asyncpg` присутствует

**Проверка в контейнере:**
```bash
docker compose exec worker pip list | grep -E "prometheus|structlog|asyncpg"
```

## 🚀 Развертывание

### Шаг 1: Применение миграций

```bash
# Подключение к БД через Supabase Dashboard или psql
# Применить миграцию 002 (если не применена)
psql -U postgres -d postgres -f telethon-ingest/migrations/002_add_post_enrichment_and_posts_indexes.sql

# Применить миграцию 003
psql -U postgres -d postgres -f telethon-ingest/migrations/003_add_media_groups_tables_safe.sql

# Применить индекс CONCURRENTLY (отдельно)
psql -U postgres -d postgres -f telethon-ingest/migrations/003_add_media_groups_index_concurrent.sql
```

### Шаг 2: Пересборка и перезапуск worker

```bash
# Пересборка worker с новым кодом
docker compose build worker

# Перезапуск worker
docker compose restart worker

# Проверка логов
docker compose logs -f worker | grep -i "retagging\|media.*group"
```

### Шаг 3: Проверка метрик

```bash
# Проверка метрик Prometheus
curl http://localhost:8001/metrics | grep -E "retagging|media_processing_total|media_bytes_total"

# Должны присутствовать:
# - retagging_processed_total
# - retagging_duration_seconds
# - retagging_skipped_total
# - retagging_dlq_total
# - media_processing_total{stage,media,outcome}
# - media_bytes_total
# - media_size_bytes_bucket
```

### Шаг 4: Проверка работы RetaggingTask

```bash
# Проверка, что RetaggingTask запущен
docker compose logs worker | grep "RetaggingTask started"

# Должна быть строка:
# "RetaggingTask started successfully"
```

### Шаг 5: Проверка обработки альбомов

```bash
# Проверка логов channel_parser на обработку альбомов
docker compose logs telethon-ingest | grep -i "album\|media.*group\|grouped_id"

# Должны быть логи:
# - "Media group processed"
# - "Media group saved to DB"
```

## 🧪 Тестирование

### E2E тесты

```bash
# Запуск тестов альбомов
pytest tests/e2e/test_media_groups.py -v

# Запуск тестов ретеггинга
pytest tests/e2e/test_retagging.py -v
```

### Ручное тестирование

1. **Проверка альбомов:**
   - Создать тестовый канал с альбомом фотографий
   - Запустить парсинг канала
   - Проверить в БД наличие записей в `media_groups` и `media_group_items`

2. **Проверка ретеггинга:**
   - Создать пост с изображением
   - Дождаться Vision анализа
   - Проверить, что RetaggingTask обработал событие
   - Проверить обновление тегов в `post_enrichment`

## 📊 Мониторинг

### Grafana Dashboard

Добавить новые панели для:
- `retagging_processed_total{changed,outcome}`
- `retagging_duration_seconds{changed}`
- `media_processing_total{stage,media,outcome}`
- `media_bytes_total{media}`
- `media_size_bytes_bucket{media,le}`

### Алерты

Настроить алерты на:
- Высокий rate ошибок ретеггинга: `rate(retagging_processed_total{outcome="err"}[5m]) > 0.1`
- Длительный ретеггинг: `histogram_quantile(0.95, rate(retagging_duration_seconds_bucket[5m])) > 5`
- Высокий rate ошибок обработки медиа: `rate(media_processing_total{outcome="err"}[5m]) > 0.1`

## 🔄 Rollback план

В случае проблем:

1. **Откат RetaggingTask:**
   ```bash
   # Комментировать RetaggingTask в worker/run_all_tasks.py
   # Пересобрать и перезапустить worker
   docker compose build worker && docker compose restart worker
   ```

2. **Откат обработки альбомов:**
   ```bash
   # Отключить сохранение альбомов в channel_parser.py
   # Перезапустить telethon-ingest
   docker compose restart telethon-ingest
   ```

3. **Откат миграций:**
   ```sql
   -- Удалить таблицы (если нужно)
   DROP TABLE IF EXISTS media_group_items CASCADE;
   DROP TABLE IF EXISTS media_groups CASCADE;
   
   -- Удалить поле (если нужно)
   ALTER TABLE posts DROP COLUMN IF EXISTS grouped_id;
   ```

## ✅ Финальная проверка

После развертывания проверить:

- [ ] Worker запущен и все tasks активны
- [ ] Метрики Prometheus доступны
- [ ] RetaggingTask обрабатывает события
- [ ] Альбомы сохраняются в БД
- [ ] Логи не содержат критических ошибок
- [ ] E2E тесты проходят

## 📝 Документация

- [x] ✅ `IMPLEMENTATION_COMPLETE.md` - итоговая документация
- [x] ✅ `ANTI_LOOP_MECHANISM.md` - механизм анти-петли
- [x] ✅ `DEPLOYMENT_CHECKLIST.md` - этот файл

