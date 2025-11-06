# План выполнения тестов пайплайна

## Дата
2025-11-06 07:47 MSK (первый запуск)
2025-11-06 08:07 MSK (повторный запуск через 20 минут)

## Первый запуск тестов

### ✅ Выполнено

1. **check_pipeline_e2e.py**
   - ✅ Smoke режим
   - ✅ E2E режим
   - ✅ Deep режим

2. **check_pipeline_health.py**
   - ✅ Smoke режим
   - ✅ E2E режим
   - ✅ Deep режим

### Выявленные проблемы

1. **Crawl4AI обогащения: 0%**
   - **Причина**: `crawl_results` не передавались из `crawl_enrichment` в `enrichment_data`
   - **Исправление**: Добавлена передача `crawl_results` из `crawl_enrichment` в `enrichment_data`
   - **Статус**: ✅ Исправлено

2. **Scheduler idle**
   - **Причина**: Нет lock, нет HWM
   - **Статус**: ⏳ Мониторинг

3. **Indexing failed: 42.77%**
   - **Причина**: Требуется анализ ошибок
   - **Статус**: ⏳ Требует проверки

4. **Neo4j vision gap: 100%**
   - **Причина**: Vision enrichments не индексируются в Neo4j
   - **Статус**: ⏳ Требует проверки

5. **Pipeline flow: пост без тегов**
   - **Причина**: 392 поста с пустыми тегами
   - **Статус**: ⏳ Требует проверки

## Исправления применены

### 1. Исправление передачи crawl_results

**Файл**: `worker/tasks/enrichment_task.py` (строки 418-430)

**Проблема**: `_enrich_post_urls` возвращает `enrichment_data` с `crawl_results`, но в `_handle_post_tagged` это сохранялось как `crawl_data`, а в `_save_enrichment_data` проверялся `crawl_results`.

**Решение**: Добавлена передача `crawl_results` из `crawl_enrichment` в `enrichment_data`:

```python
if crawl_enrichment:
    if isinstance(crawl_enrichment, dict) and 'crawl_results' in crawl_enrichment:
        enrichment_data['crawl_results'] = crawl_enrichment.get('crawl_results', [])
        enrichment_data['total_word_count'] = crawl_enrichment.get('total_word_count', 0)
        enrichment_data['crawl_data'] = crawl_enrichment  # Для совместимости
```

### 2. Обновление логирования

**Файл**: `worker/tasks/enrichment_task.py` (строки 388-393)

Добавлено логирование типа `crawl_enrichment` для диагностики.

## Повторный запуск тестов

### Планирование

- **Время**: 2025-11-06 08:07 MSK (через 20 минут после первого запуска)
- **Скрипт**: `/tmp/run_tests_after_20min.sh`
- **Лог**: `/tmp/test_results_after_20min.log`

### Ожидаемые улучшения

1. **Crawl4AI обогащения**: должны появиться записи с `kind='crawl'`
2. **Scheduler**: должен активироваться и начать парсинг
3. **Indexing**: процент failed должен снизиться
4. **Neo4j vision**: связи HAS_VISION должны появиться

## Команды для проверки

```bash
# Проверка crawl обогащений
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as total, 
       COUNT(*) FILTER (WHERE kind = 'crawl') as crawl_count
FROM post_enrichment 
WHERE updated_at > NOW() - INTERVAL '1 hour';
"

# Проверка scheduler
docker compose exec -T redis redis-cli GET "scheduler:lock"
docker compose exec -T redis redis-cli KEYS "parse_hwm:*"

# Проверка логов crawl
docker compose logs --since=30m worker 2>&1 | grep -E "(enrich_crawl_done|has_results|results_count)" | tail -10

# Проверка результатов тестов
cat /tmp/test_results_after_20min.log
```

## Контекст

### Context7 Best Practices применены

1. ✅ Исправление передачи данных между компонентами
2. ✅ Улучшение логирования для диагностики
3. ✅ Сохранение обратной совместимости (legacy формат)
4. ✅ Синхронизация legacy полей в EnrichmentRepository

### Следующие шаги

1. ⏳ Ожидание 20 минут
2. ⏳ Автоматический повторный запуск тестов
3. ⏳ Анализ результатов
4. ⏳ Исправление оставшихся проблем
