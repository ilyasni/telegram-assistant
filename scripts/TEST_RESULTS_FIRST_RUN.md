# Результаты первого запуска тестов

## Дата и время
2025-11-06 07:47 MSK

## Статус тестов

### ✅ Успешные проверки

1. **Parsing**: 481 пост за 24 часа
2. **Streams**: Все потоки работают, нет pending сообщений
3. **Tagging**: 802 поста с тегами
4. **Vision**: 471 пост с vision enrichments
5. **Qdrant**: 415 векторов в 4 коллекциях
6. **Neo4j**: 421 пост, 1093 связей TAGGED_AS
7. **S3**: 2 объекта найдены

### ❌ Проблемы

#### 1. Crawl4AI обогащения: 0%
- **Статус**: Критично
- **Детали**: Нет записей с `kind='crawl'` в `post_enrichment`
- **Причина**: API crawl4ai обновлен (LLMConfig), но crawl не выполняется
- **Исправление**: Применено обновление API, ожидается работа

#### 2. Scheduler idle
- **Статус**: Предупреждение
- **Детали**: Нет lock, нет HWM
- **Причина**: Возможно, scheduler еще не запустился

#### 3. Indexing failed: 42.77%
- **Статус**: Высокий приоритет
- **Детали**: 343 failed из 802 всего
- **Причина**: Требуется анализ ошибок индексации
- **Порог**: 20% (превышен)

#### 4. Neo4j vision gap: 100%
- **Статус**: Высокий приоритет
- **Детали**: Нет связей HAS_VISION в Neo4j
- **Причина**: Vision enrichments не индексируются в Neo4j
- **Порог**: 20% (превышен)

#### 5. Pipeline flow: пост без тегов
- **Статус**: Средний приоритет
- **Детали**: Выбранный пост не имеет тегов
- **Причина**: 392 поста с пустыми тегами (49% от общего числа)

## Детальная статистика

### Enrichments
- **Tags**: 802 поста (100%)
- **Vision**: 471 пост (58.7%)
- **Crawl**: 0 постов (0%) ❌

### Indexing
- **Total**: 802
- **Completed**: 402 (50.1%)
- **Failed**: 343 (42.8%) ❌
- **Skipped**: 57 (7.1%)

### Qdrant
- **Collections**: 4
- **Vectors**: 415
- **Payload coverage**: 100% ✅

### Neo4j
- **Posts**: 421
- **TAGGED_AS**: 1093 ✅
- **HAS_VISION**: 0 ❌

## Следующие шаги

1. ⏳ Ожидание 20 минут
2. ⏳ Повторный запуск тестов
3. ⏳ Проверка улучшений:
   - Crawl4AI обогащения
   - Scheduler активность
   - Indexing успешность
   - Neo4j vision связи

## Команды для мониторинга

```bash
# Проверка crawl4ai обогащений
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as total, 
       COUNT(*) FILTER (WHERE kind = 'crawl') as crawl_count
FROM post_enrichment 
WHERE updated_at > NOW() - INTERVAL '1 hour';
"

# Проверка scheduler
docker compose exec -T redis redis-cli GET "scheduler:lock"
docker compose exec -T redis redis-cli KEYS "parse_hwm:*"

# Проверка indexing ошибок
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT embedding_status, COUNT(*) 
FROM indexing_status 
GROUP BY embedding_status;
"

# Проверка Neo4j vision связей
docker compose exec -T neo4j cypher-shell -u neo4j -p changeme "MATCH ()-[r:HAS_VISION]->() RETURN count(r);"
```
