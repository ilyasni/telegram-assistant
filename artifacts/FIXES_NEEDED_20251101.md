# 🔧 Рекомендации по исправлению проблем E2E тестов

**Дата**: 2025-11-01  
**Статус**: Требуется исправление

---

## ✅ Исправлено

### 1. Несоответствие размерности в скрипте проверки

**Проблема**: Скрипт `check_pipeline_e2e.py` использовал дефолт 384 вместо 2560

**Исправление**: ✅ Обновлён `scripts/check_pipeline_e2e.py` (строка 81)
- Теперь использует `EMBEDDING_DIMENSION` (основной) или `EMBEDDING_DIM` (fallback)
- Дефолт изменён на 2560 (соответствует GigaChat EmbeddingsGigaR)

**Что изменилось**:
```python
# Было:
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))

# Стало:
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIMENSION", os.getenv("EMBEDDING_DIM", "2560")))
```

---

## ⚠️ Требуется проверка/исправление

### 2. Отсутствие обогащения постов (0 обогащённых постов)

**Проблема**: Посты не проходят через Crawl4AI обогащение

**Возможные причины**:
1. **Триггерные теги отсутствуют**: Обогащение срабатывает только для постов с определёнными тегами
   - Триггеры: `longread`, `research`, `paper`, `release`, `law`, `deepdive`, `analysis`, `report`, `study`, `whitepaper`
   - Проверка: Нужно проверить, есть ли у постов эти теги

2. **Минимальное количество слов**: Обогащение не выполняется для коротких постов
   - Минимум: 500 слов (`min_word_count: 500`)
   - Проверка: Нужно проверить длину постов

3. **Лимиты пользователя**: Могут быть превышены лимиты на обогащение

**Проверка**:
```bash
# Проверить посты с триггерными тегами
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
  SELECT p.id, pe.tags, LENGTH(p.text) as text_length
  FROM posts p
  JOIN post_enrichment pe ON p.id = pe.post_id
  WHERE pe.tags::text LIKE '%longread%'
     OR pe.tags::text LIKE '%research%'
     OR pe.tags::text LIKE '%paper%'
  LIMIT 10;
"

# Проверить конфигурацию обогащения
docker compose exec worker cat /app/config/enrichment_policy.yml | grep -A 20 crawl4ai
```

**Рекомендации**:
- Если посты должны обогащаться, но не имеют триггерных тегов → добавить теги или изменить конфигурацию
- Если посты слишком короткие → уменьшить `min_word_count` в `enrichment_policy.yml`
- Если проблема в лимитах → проверить `ENRICHMENT_SKIP_LIMITS` в `.env`

---

### 3. Провал индексации постов в Qdrant/Neo4j

**Проблема**: Посты с тегами не индексируются (не попадают в Qdrant и Neo4j)

**Статус**:
- IndexingTask запущен и работает ✅
- Consumer group `indexing_workers` активен ✅
- В stream `posts.enriched` есть 4238 сообщений ✅
- Но посты не появляются в хранилищах ❌

**Проверка**:
```bash
# Проверить логи на ошибки индексации
docker compose logs worker --since 2h | grep -i -E "Failed to process post|error.*indexing|embedding.*failed|graph.*failed"

# Проверить pending посты
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
  SELECT COUNT(*) as pending_count
  FROM post_enrichment
  WHERE embedding_status = 'pending' OR graph_status = 'pending';
"

# Проверить failed посты
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
  SELECT post_id, embedding_status, graph_status, error_message
  FROM post_enrichment
  WHERE embedding_status = 'failed' OR graph_status = 'failed'
  LIMIT 5;
"
```

**Возможные причины**:
1. **Ошибки при генерации эмбеддингов**: Проблемы с GigaChat API
2. **Ошибки при записи в Qdrant**: Проблемы с подключением или структурой данных
3. **Ошибки при записи в Neo4j**: Проблемы с подключением или графом

**Рекомендации**:
- Проверить логи worker на конкретные ошибки
- Проверить доступность Qdrant и Neo4j
- Если есть failed посты → проверить error_message для понимания причины

---

### 4. Scheduler в режиме idle

**Проблема**: Scheduler не активен (lock не найден, HWM count: 0)

**Статус**: Не критично, если парсинг происходит через другой механизм

**Проверка**:
```bash
# Проверить статус scheduler
docker compose logs telethon-ingest | grep -i scheduler | tail -10

# Проверить, работает ли парсинг
docker compose logs telethon-ingest | grep -i "parsed.*messages" | tail -5
```

**Рекомендации**:
- Если парсинг работает через `run_ingest_loop` → это нормально
- Если парсинг должен работать через scheduler → проверить конфигурацию и логи

---

## 📋 План действий

### Немедленные действия (высокий приоритет)

1. ✅ **Исправлен скрипт проверки размерности** (выполнено)

2. **Проверить логи индексации** (15 минут)
   ```bash
   docker compose logs worker --since 2h | grep -i "Failed to process post" -A 5
   ```
   - Найти конкретные ошибки
   - Определить причину провала индексации

3. **Проверить обогащение** (15 минут)
   ```bash
   # Проверить посты с тегами
   docker compose exec -T supabase-db psql -U postgres -d postgres -c "
     SELECT COUNT(*) as posts_with_tags, 
            COUNT(CASE WHEN pe.tags::text LIKE '%longread%' THEN 1 END) as with_trigger_tags
     FROM posts p
     JOIN post_enrichment pe ON p.id = pe.post_id
     WHERE pe.tags IS NOT NULL;
   "
   ```
   - Определить, почему обогащение не срабатывает
   - При необходимости скорректировать конфигурацию

### Средний приоритет

4. **Проверить статус Scheduler** (10 минут)
   - Выяснить, должен ли он работать или это нормально

5. **Проверить доступность сервисов** (5 минут)
   ```bash
   docker compose exec worker curl -s http://qdrant:6333/health
   docker compose exec worker curl -s http://crawl4ai:8080/health
   ```

### Долгосрочные улучшения

6. **Добавить мониторинг размерности эмбеддингов**
   - Алерт при несоответствии

7. **Улучшить диагностику обогащения**
   - Логирование причин пропуска обогащения

8. **Добавить метрики успешности индексации**
   - Prometheus метрики для отслеживания

---

## ✅ Проверка после исправлений

После выполнения исправлений, повторить E2E тесты:

```bash
# Запустить все тесты снова
docker compose exec -T worker python3 /app/check_pipeline_e2e.py --mode smoke --json
docker compose exec -T worker python3 /app/check_pipeline_e2e.py --mode e2e --json
docker compose exec -T worker python3 /app/check_pipeline_e2e.py --mode deep --json
```

Ожидаемые результаты:
- ✅ Размерность эмбеддингов: проверка должна пройти (2560 = 2560)
- ⚠️ Обогащение: зависит от наличия триггерных тегов и конфигурации
- ⚠️ Индексация: зависит от результатов проверки логов

---

**Приоритет исправлений**:
1. 🔴 Высокий: Проверка логов индексации (проблема блокирует работу)
2. 🟡 Средний: Настройка обогащения (функциональность не критична, но желательна)
3. 🟢 Низкий: Scheduler (работает альтернативный механизм)

