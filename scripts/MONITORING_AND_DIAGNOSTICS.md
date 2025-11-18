# Мониторинг и диагностика альбомов и обогащений

## Дата
2025-11-06 06:45 MSK

## Контекст
После исправления проблем с альбомами и обогащениями необходимо постоянно мониторить работу системы и проверять результаты.

## Добавленное логирование

### Альбомы
- ✅ Проверка состояния транзакции перед сохранением
- ✅ Логирование успешного сохранения альбомов
- ✅ Логирование ошибок при сохранении

### Обогащения Crawl4AI
- ✅ `enrich_trigger_check_start` - начало проверки триггеров
- ✅ `enrich_trigger_check_done` - завершение проверки триггеров
- ✅ `enrich_crawl_check` - проверка наличия URLs
- ✅ `enrich_crawl_start` - начало crawl
- ✅ `enrich_crawl_done` - завершение crawl
- ✅ `enrich_crawl_error` - ошибка при crawl
- ✅ `enrich_save_start` - начало сохранения
- ✅ `enrich_save_ok` - успешное сохранение
- ✅ `enrich_save_fail` - ошибка при сохранении

## Команды для мониторинга

### Альбомы

```bash
# Проверка альбомов в БД
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as total_albums, 
       COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour,
       COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as last_24h
FROM media_groups;
"

# Проверка постов с grouped_id без альбомов
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as posts_without_albums
FROM posts p
WHERE p.grouped_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM media_groups mg 
    WHERE mg.grouped_id = p.grouped_id 
      AND mg.channel_id = p.channel_id
  )
  AND p.created_at > NOW() - INTERVAL '24 hours';
"

# Логи сохранения альбомов
docker compose logs --since=30m telethon-ingest | grep -E "(Media group saved|Failed to save albums|Checking for albums)"
```

### Обогащения Crawl4AI

```bash
# Проверка обогащений в БД
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as total_enrichments,
       COUNT(*) FILTER (WHERE updated_at > NOW() - INTERVAL '1 hour') as last_hour,
       COUNT(*) FILTER (WHERE kind = 'crawl' OR kind = 'crawl4ai') as crawl_total,
       COUNT(*) FILTER (WHERE (kind = 'crawl' OR kind = 'crawl4ai') AND updated_at > NOW() - INTERVAL '1 hour') as crawl_last_hour,
       COUNT(*) FILTER (WHERE crawl_md IS NOT NULL AND LENGTH(crawl_md) > 0) as with_crawl_md
FROM post_enrichment;
"

# Проверка постов с URLs без обогащений
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as posts_with_urls_no_crawl
FROM posts p
WHERE p.content ~ 'https?://'
  AND p.created_at > NOW() - INTERVAL '24 hours'
  AND NOT EXISTS (
    SELECT 1 FROM post_enrichment pe 
    WHERE pe.post_id = p.id 
      AND (pe.kind = 'crawl' OR pe.kind = 'crawl4ai')
      AND pe.crawl_md IS NOT NULL
  );
"

# Логи обогащений
docker compose logs --since=30m worker | grep -E "(enrich_crawl_start|enrich_crawl_done|enrich_crawl_error|enrich_save_start|enrich_save_ok|enrich_save_fail)"
```

### Детальные логи

```bash
# Логи проверки триггеров
docker compose logs --since=30m worker | grep -E "(enrich_trigger_check_start|enrich_trigger_check_done|enrich_crawl_check)"

# Ошибки обогащения
docker compose logs --since=30m worker | grep -E "(Error processing post|Traceback)" -A 10

# Логи альбомов
docker compose logs --since=30m telethon-ingest | grep -E "(album|media_group|grouped_id)" | tail -50
```

## Метрики Prometheus

### Альбомы
- `media_groups_total` - общее количество альбомов
- `media_groups_created_total` - количество созданных альбомов
- `albums_save_errors_total` - ошибки при сохранении альбомов

### Обогащения
- `enrichment_requests_total{provider="crawl4ai"}` - запросы на обогащение
- `enrichment_crawl_requests_total` - запросы на crawl
- `enrichment_crawl_duration_seconds` - длительность crawl
- `enrichment_skipped_total{reason}` - пропущенные обогащения
- `enrichment_triggers_total{type,decision}` - триггеры обогащения

## Проверка политики обогащения

### Триггеры
1. **URL триггер** (приоритет 1): если есть URL в посте
2. **Теги** (приоритет 2): теги `ai`, `research`, `report`
3. **Word count** (приоритет 3): >= 100 слов

### Конфигурация
Файл: `worker/config/enrichment_policy.yml`

```yaml
crawl4ai:
  enabled: true
  trigger_tags:
    - ai
    - research
    - report
  min_word_count: 100
```

## Следующие шаги

1. ⏳ Мониторить логи в реальном времени
2. ⏳ Проверить сохранение альбомов при следующем парсинге
3. ⏳ Проверить выполнение crawl4ai обогащений
4. ⏳ Проверить метрики Prometheus/Grafana
5. ⏳ Настроить алерты на ошибки

## Context7 Best Practices

### ✅ Observability
- Детальное логирование для диагностики
- Метрики для отслеживания проблем
- Структурированные логи с контекстом

### ✅ Error Handling
- Graceful degradation при ошибках
- Логирование ошибок без прерывания обработки
- Retry механизмы для transient ошибок

### ✅ Monitoring
- Проверка состояния БД
- Мониторинг логов
- Метрики Prometheus

## Рекомендации

1. **Настроить алерты** на:
   - Ошибки сохранения альбомов
   - Ошибки обогащений
   - Отсутствие новых альбомов/обогащений

2. **Регулярно проверять**:
   - Метрики в Grafana
   - Логи на ошибки
   - Состояние БД

3. **Оптимизировать**:
   - Политику обогащения (триггеры, теги)
   - Бюджеты crawl4ai
   - Кеширование результатов
