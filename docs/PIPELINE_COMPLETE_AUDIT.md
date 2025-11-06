# Полный аудит пайплайна постов и альбомов

**Дата**: 2025-02-01  
**Context7**: Проверка всего пайплайна от парсинга до Qdrant и Neo4j

---

## Обзор пайплайна

```
1. Telegram Message/Album
   ↓
2. ChannelParser → MediaProcessor → AtomicDBSaver
   ↓ posts.parsed
3. VisionAnalysisTask (Vision анализ)
   ↓ posts.vision.analyzed
4. RetaggingTask (ретеггинг с Vision)
   ↓ posts.tagged (trigger=vision_retag)
5. TaggingTask (тегирование новых постов)
   ↓ posts.tagged
6. TagPersistenceTask (сохранение тегов в БД)
   ↓ posts.enriched
7. EnrichmentTask (Crawl4AI обогащение)
   ↓ posts.enriched (обновленное)
8. IndexingTask (Qdrant + Neo4j)
   ↓ posts.indexed
9. AlbumAssemblerTask (сборка альбомов)
   ↓ album.assembled
```

---

## 1. Парсинг постов и альбомов

### Компоненты
- **ChannelParser** (`telethon-ingest/channel_parser.py`)
- **MediaProcessor** (`telethon-ingest/services/media_processor.py`)
- **AtomicDBSaver** (`telethon-ingest/services/atomic_db_saver.py`)
- **MediaGroupSaver** (`telethon-ingest/services/media_group_saver.py`)

### Проверка Context7 Best Practices

#### ✅ Идемпотентность
- **Статус**: ✅ Реализовано
- **Механизм**: `UNIQUE (channel_id, telegram_message_id)` в БД
- **Код**: `ON CONFLICT (channel_id, telegram_message_id) DO NOTHING`
- **Проблемы**: Нет ранней дедупликации по `grouped_id` (удалена из-за race conditions)

#### ✅ Обработка альбомов
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - `iter_messages()` с окном ±20 сообщений
  - Параллельная загрузка через `asyncio.gather()`
  - Сохранение `grouped_id` в `posts.grouped_id`
- **Проблемы**: При больших альбомах возможен пропуск элементов

#### ✅ Observability
- **Метрики**: 
  - `db_posts_insert_success_total`
  - `db_posts_insert_failures_total{reason}`
  - `db_batch_commit_latency_seconds`
- **Логирование**: Структурированное логирование с `trace_id`

#### ⚠️ Потенциальные проблемы
1. **Race conditions**: Нет ранней дедупликации по `grouped_id`
2. **Большие альбомы**: Окно ±20 сообщений может пропустить элементы
3. **Нет проверки**: Отсутствует валидация `grouped_id` перед сохранением

---

## 2. Vision анализ

### Компоненты
- **VisionAnalysisTask** (`worker/tasks/vision_analysis_task.py`)
- **GigaChatVisionAdapter** (`worker/ai_adapters/gigachat_vision.py`)
- **EnrichmentRepository** (`shared/python/shared/repositories/enrichment_repository.py`)

### Проверка Context7 Best Practices

#### ✅ Идемпотентность
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - SHA256 дедупликация через Redis
  - `ON CONFLICT (post_id, kind) DO UPDATE SET` в БД
  - `COALESCE(EXCLUDED.params_hash, post_enrichment.params_hash)` для защиты от перезаписи на NULL
- **Код**: 
  ```sql
  ON CONFLICT (post_id, kind) DO UPDATE SET
      params_hash = COALESCE(EXCLUDED.params_hash, post_enrichment.params_hash),
      data = EXCLUDED.data
  ```

#### ✅ S3 кэширование
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Кэш Vision результатов в S3 (`vision/{tenant}/...`)
  - Чтение через `get_json()` с автоматической декомпрессией
  - Сохранение через `put_json()` с gzip сжатием
- **Проблемы**: Нет TTL для кэша (может расти бесконечно)

#### ✅ Observability
- **Метрики**: 
  - `vision_analysis_requests_total{status, provider, tenant_id, reason}`
  - `vision_analysis_duration_seconds{provider, has_ocr}`
  - `vision_cache_hits_total{cache_type}`
  - `vision_media_total{result, reason}`
- **Логирование**: 
  - Детальное логирование OCR извлечения
  - Логирование до и после сохранения в БД
  - Верификация данных после сохранения

#### ✅ Обработка ошибок
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Retry logic с экспоненциальным backoff
  - Обработка poison-pattern (невалидный JSON)
  - DLQ для failed events
- **Проблемы**: Нет circuit breaker для GigaChat API

#### ⚠️ Потенциальные проблемы
1. **OCR потеря**: Исправлено через логирование и верификацию
2. **params_hash потеря**: Исправлено через COALESCE
3. **Нет TTL для S3 кэша**: Может расти бесконечно

---

## 3. Тегирование и ретеггинг

### Компоненты
- **TaggingTask** (`worker/tasks/tagging_task.py`)
- **RetaggingTask** (`worker/tasks/retagging_task.py`)
- **TagPersistenceTask** (`worker/tasks/tag_persistence_task.py`)

### Проверка Context7 Best Practices

#### ✅ Идемпотентность
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Redis дедупликация через `tagging:processed:{post_id}`
  - `ON CONFLICT (post_id, kind) DO UPDATE SET` в БД
  - Проверка `tags_hash` для предотвращения дублирования
- **Код**: 
  ```python
  # RetaggingTask проверяет версии
  if vision_version > tags_version:
      # Ретеггинг с Vision обогащением
  ```

#### ✅ Vision обогащение
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - `TaggingTask` и `RetaggingTask` используют Vision данные для обогащения текста
  - OCR текст добавляется к тексту поста перед тегированием
- **Код**: 
  ```python
  if vision_enrichment.get('ocr_text'):
      enrichment_parts.append(f"[Текст с изображения: {vision_enrichment['ocr_text']}]")
  ```

#### ✅ Observability
- **Метрики**: 
  - `tagging_processed_total{status}`
  - `tagging_latency_seconds{provider}`
  - `tags_persisted_total{status}`
  - `tags_persist_conflicts_total`
- **Логирование**: Структурированное логирование с `trace_id`

#### ⚠️ Потенциальные проблемы
1. **Анти-петля**: `TaggingTask` игнорирует события с `trigger=vision_retag` (✅ реализовано)
2. **Нет проверки**: Отсутствует валидация тегов перед сохранением

---

## 4. Обогащение с Crawl4AI

### Компоненты
- **EnrichmentTask** (`worker/tasks/enrichment_task.py`)
- **Crawl4AIService** (`crawl4ai/crawl4ai_service.py`)
- **EnrichmentEngine** (`crawl4ai/enrichment_engine.py`)

### Проверка Context7 Best Practices

#### ✅ Идемпотентность
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - `enrichment_key` (SHA256 от нормализованного URL + policy_version)
  - Кеширование результатов в Redis
  - `ON CONFLICT (post_id, kind) DO UPDATE SET` в БД
- **Код**: 
  ```python
  enrichment_key = hashlib.sha256(
      f"{normalized_url}|{policy_version}".encode()
  ).hexdigest()
  ```

#### ✅ S3 интеграция
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Использование `S3StorageService.put_text()` для HTML/MD
  - Автоматическое gzip сжатие
  - Метрики Prometheus для операций S3
- **Код**: 
  ```python
  await self.s3_service.put_text(
      content=html_content_bytes,
      s3_key=html_s3_key,
      content_type='text/html',
      compress=True
  )
  ```

#### ✅ Observability
- **Метрики**: 
  - `enrichment_triggers_total{type, decision}`
  - `enrichment_crawl_requests_total{domain, status}`
  - `enrichment_crawl_duration_seconds`
  - `enrichment_budget_checks_total{type, result}`
- **Логирование**: Структурированное логирование с `trace_id`

#### ✅ Обработка ошибок
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Graceful degradation при недоступности Redis
  - Retry logic с экспоненциальным backoff
  - DLQ для failed events
- **Проблемы**: Нет circuit breaker для Crawl4AI

#### ⚠️ Потенциальные проблемы
1. **Нет проверки**: Отсутствует валидация URLs перед crawl
2. **Нет лимита**: Отсутствует лимит на размер crawl результатов

---

## 5. Индексация в Qdrant

### Компоненты
- **IndexingTask** (`worker/tasks/indexing_task.py`)
- **EmbeddingService** (`worker/tasks/embeddings.py`)

### Проверка Context7 Best Practices

#### ✅ Идемпотентность
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - `vector_id = post_id` для детерминированной идентификации
  - Upsert в Qdrant (замена существующих точек)
  - Проверка `indexing_status` в БД перед индексацией
- **Код**: 
  ```python
  vector_id = f"{post_id}"
  await qdrant_client.upsert(
      collection_name=collection_name,
      points=[PointStruct(id=vector_id, vector=embedding, payload=payload)]
  )
  ```

#### ✅ Multi-tenancy
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Отдельные коллекции per-tenant: `tenant_{tenant_id}_posts`
  - Получение `tenant_id` из БД с fallback на 'default'
  - Логирование предупреждений при отсутствии tenant_id
- **Код**: 
  ```python
  tenant_id = await self._get_tenant_id_from_post(post_id)
  if not tenant_id or tenant_id == 'default':
      logger.warning("tenant_id not found, using 'default'")
      tenant_id = 'default'
  collection_name = f"tenant_{tenant_id}_posts"
  ```

#### ✅ Обогащение данных
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Объединение текста поста, Vision OCR, Crawl MD
  - Нормализация текста перед генерацией эмбеддинга
  - Дедупликация частей текста
- **Код**: 
  ```python
  text_parts = [post_text]
  if vision_ocr_text:
      text_parts.append(vision_ocr_text)
  if crawl_md:
      text_parts.append(crawl_md)
  final_text = ' '.join(unique_parts)
  ```

#### ✅ Payload структура
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Расширенный payload с enrichment данными
  - Фасеты для фильтрации: `tags`, `vision.is_meme`, `vision.labels`
  - `album_id` для постов из альбомов
- **Код**: 
  ```python
  payload = {
      "post_id": post_id,
      "tags": tags,
      "vision": {
          "is_meme": vision_data.get("is_meme"),
          "labels": vision_data.get("labels"),
          "nsfw_score": vision_data.get("nsfw_score")
      },
      "album_id": album_id
  }
  ```

#### ✅ Observability
- **Метрики**: 
  - `indexing_processed_total{status}`
  - `indexing_qdrant_duration_seconds`
  - `indexing_embedding_duration_seconds`
- **Логирование**: Структурированное логирование с `trace_id`

#### ⚠️ Потенциальные проблемы
1. **Нет TTL**: Отсутствует автоматическое удаление expired постов из Qdrant
2. **Нет проверки**: Отсутствует валидация эмбеддингов перед сохранением

---

## 6. Сохранение в Neo4j

### Компоненты
- **IndexingTask** (`worker/tasks/indexing_task.py`)
- **Neo4jClient** (`worker/integrations/neo4j_client.py`)

### Проверка Context7 Best Practices

#### ✅ Идемпотентность
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - `MERGE` для создания узлов (idempотентно)
  - Проверка существования узлов перед созданием связей
  - Обработка дубликатов через `ON CREATE SET`
- **Код**: 
  ```cypher
  MERGE (p:Post {id: $post_id})
  ON CREATE SET p.created_at = datetime()
  ON MATCH SET p.updated_at = datetime()
  ```

#### ✅ Multi-tenancy
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Использование `tenant_id` в узлах и связях
  - Фильтрация по `tenant_id` в запросах
  - Fallback на 'default' при отсутствии tenant_id
- **Код**: 
  ```cypher
  MERGE (p:Post {id: $post_id, tenant_id: $tenant_id})
  ```

#### ✅ Альбомы
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Создание узлов альбомов через `create_album_node_and_relationships`
  - Связи `BELONGS_TO_ALBUM` между постами и альбомами
  - Агрегация данных альбомов
- **Код**: 
  ```python
  if album_id and success:
      await self.neo4j_client.create_album_node_and_relationships(
          album_id=album_id,
          post_id=post_id,
          channel_id=channel_id,
          tenant_id=tenant_id
      )
  ```

#### ✅ Теги
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Создание узлов тегов через `MERGE`
  - Связи `HAS_TAG` между постами и тегами
  - Дедупликация тегов
- **Код**: 
  ```cypher
  MERGE (t:Tag {name: $tag_name})
  MERGE (p:Post {id: $post_id})-[:HAS_TAG]->(t)
  ```

#### ✅ Observability
- **Метрики**: 
  - `indexing_neo4j_duration_seconds`
  - `neo4j_operations_total{operation, status}`
- **Логирование**: Структурированное логирование с `trace_id`

#### ⚠️ Потенциальные проблемы
1. **Нет TTL**: Отсутствует автоматическое удаление expired узлов из Neo4j
2. **Нет проверки**: Отсутствует валидация данных перед сохранением

---

## 7. Обработка альбомов (AlbumAssemblerTask)

### Компоненты
- **AlbumAssemblerTask** (`worker/tasks/album_assembler_task.py`)

### Проверка Context7 Best Practices

#### ✅ Идемпотентность
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Состояние альбомов в Redis с TTL 24 часа
  - Проверка завершенности перед сборкой
  - Защита от повторной обработки
- **Код**: 
  ```python
  album_state_key = f"album:state:{album_id}"
  state = await self.redis.get(album_state_key)
  if state:
      state_data = json.loads(state)
      if state_data.get('completed'):
          return  # Уже собран
  ```

#### ✅ Vision агрегация
- **Статус**: ✅ Реализовано
- **Механизм**: 
  - Агрегация vision summary на уровне альбома
  - Приоритизация, дедупликация, нормализация
  - Сохранение в S3 (`album/{tenant}/{album_id}_vision_summary_v1.json`)
- **Код**: 
  ```python
  vision_summary = self._aggregate_vision_summary(album_items)
  await self.s3_service.put_json(
      data=vision_summary,
      s3_key=s3_key,
      compress=True
  )
  ```

#### ✅ Observability
- **Метрики**: 
  - `albums_parsed_total{status}`
  - `albums_assembled_total{status}`
  - `album_assembly_lag_seconds`
  - `album_items_count_gauge{album_id, status}`
  - `album_vision_summary_size_bytes`
  - `album_aggregation_duration_ms`
- **Логирование**: Структурированное логирование с `trace_id`

#### ⚠️ Потенциальные проблемы
1. **TTL Redis**: 24 часа может быть недостаточно для больших альбомов
2. **Нет проверки**: Отсутствует валидация состояния альбомов

---

## Общие проблемы и рекомендации

### ❌ Критические проблемы

1. **Отсутствие TTL для Qdrant и Neo4j**
   - **Проблема**: Expired посты не удаляются автоматически
   - **Решение**: Использовать `CleanupTask` для периодической очистки
   - **Приоритет**: Высокий

2. **Отсутствие circuit breaker**
   - **Проблема**: Нет защиты от каскадных сбоев для внешних API (GigaChat, Crawl4AI)
   - **Решение**: Добавить circuit breaker для всех внешних вызовов
   - **Приоритет**: Средний

3. **Нет валидации данных**
   - **Проблема**: Отсутствует валидация перед сохранением в БД/Qdrant/Neo4j
   - **Решение**: Добавить Pydantic модели для валидации
   - **Приоритет**: Средний

### ⚠️ Потенциальные улучшения

1. **Улучшение обработки больших альбомов**
   - Увеличить окно поиска сообщений (±20 → ±50)
   - Добавить проверку полноты альбома

2. **Улучшение observability**
   - Добавить distributed tracing (OpenTelemetry)
   - Добавить алерты на SLO нарушения

3. **Улучшение производительности**
   - Batch операции для Qdrant и Neo4j
   - Connection pooling для всех внешних сервисов

---

## Итоговая оценка

| Этап | Идемпотентность | Observability | Обработка ошибок | Multi-tenancy | Context7 соответствие |
|------|----------------|---------------|------------------|---------------|----------------------|
| Парсинг | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Vision | ✅ | ✅ | ✅ | ✅ | ✅ |
| Тегирование | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Crawl4AI | ✅ | ✅ | ✅ | ✅ | ✅ |
| Qdrant | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Neo4j | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Альбомы | ✅ | ✅ | ⚠️ | ✅ | ✅ |

**Общая оценка**: ✅ **Хорошо** (7/7 этапов соответствуют Context7 best practices)

---

## Рекомендации по улучшению

1. ✅ **Добавить TTL для Qdrant и Neo4j** (высокий приоритет) - **РЕАЛИЗОВАНО**
   - Периодический запуск TTL cleanup каждые 6 часов
   - Улучшенные метрики и логирование
   - См. `docs/PIPELINE_IMPROVEMENTS_STATUS.md`

2. ✅ **Добавить circuit breaker** (средний приоритет) - **РЕАЛИЗОВАНО**
   - CircuitBreaker класс создан
   - Интегрирован в GigaChatVisionAdapter
   - Интегрирован в EnrichmentEngine (Crawl4AI)
   - См. `docs/PIPELINE_IMPROVEMENTS_STATUS.md`

3. ✅ **Добавить валидацию данных** (средний приоритет) - **РЕАЛИЗОВАНО**
   - Pydantic модели созданы (VisionEnrichmentData, CrawlEnrichmentData, QdrantPayload, Neo4jPostNode)
   - Интегрирована валидация в VisionAnalysisTask, IndexingTask (Qdrant и Neo4j)
   - См. `docs/PIPELINE_IMPROVEMENTS_STATUS.md`

4. ✅ **Улучшить обработку больших альбомов** (низкий приоритет) - **РЕАЛИЗОВАНО**
   - Увеличено окно поиска с ±5 до ±10 минут (настраивается)
   - Увеличен лимит поиска с 30 до 50 сообщений
   - Добавлена проверка полноты альбома
   - См. `docs/PIPELINE_IMPROVEMENTS_STATUS.md`

5. ⏳ **Добавить distributed tracing** (низкий приоритет) - **НЕ РЕАЛИЗОВАНО**
   - План: Интеграция OpenTelemetry
   - Примечание: Текущая реализация использует `trace_id` для корреляции, что покрывает основные потребности
   - См. `docs/PIPELINE_IMPROVEMENTS_STATUS.md` и `docs/PIPELINE_IMPROVEMENTS_SUMMARY.md`

