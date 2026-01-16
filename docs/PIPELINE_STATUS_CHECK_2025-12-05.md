# Проверка статуса Scheduler и пайплайна постов/альбомов

**Дата**: 2025-12-05  
**Context7**: Комплексная проверка Scheduler и всего пайплайна обработки

---

## 1. Статус Scheduler

### ✅ Scheduler работает корректно

**Режим**: AUTO (автоопределение)
- `PARSER_MODE_OVERRIDE`: `auto`
- `FEATURE_INCREMENTAL_PARSING_ENABLED`: `true`
- `PARSER_SCHEDULER_INTERVAL_SEC`: `300` секунд (5 минут)

**Последний тик**: 2025-12-05 14:19:21 UTC
- Возраст: 182 секунд (3 минуты)
- Статус: ✅ Свежий
- Следующий тик ожидается через: ~118 секунд

**Heartbeat**: 2025-12-05 14:21:56 UTC
- Возраст: 27 секунд
- Статус: ✅ Свежий (обновляется каждые 30 секунд)

**Контейнер**: ✅ Работает (healthy)

---

## 2. Пайплайн обработки постов и альбомов

### Обзор пайплайна

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

## 3. Проверка каждого этапа

### 3.1. Парсинг постов и альбомов

**Компоненты**:
- ChannelParser (`telethon-ingest/channel_parser.py`)
- MediaProcessor (`telethon-ingest/services/media_processor.py`)
- AtomicDBSaver (`telethon-ingest/services/atomic_db_saver.py`)
- MediaGroupSaver (`telethon-ingest/services/media_group_saver.py`)

**Статистика**:
- Всего постов: **8,782**
- За последние 24 часа: **350** постов
- Новейший пост: 2025-12-05 14:16:15 UTC

**Redis Streams**:
- `stream:posts:parsed`: **10,004** сообщений

**Статус**: ✅ Работает
- События публикуются в Redis Streams
- Worker обрабатывает события
- Активность: 350 постов за 24 часа

---

### 3.2. Vision анализ

**Компоненты**:
- VisionAnalysisTask (`worker/tasks/vision_analysis_task.py`)
- VisionService (OCR, entities extraction)

**Redis Streams**:
- `stream:posts:vision:uploaded`: (проверяется)
- `stream:posts:vision:analyzed`: **7,494** сообщений

**Статистика**:
- Всего обработано постов: **4,255**
- Успешных: **4,255** (100%)
- За последний час: **1** пост

**Статус**: ✅ Работает
- Vision анализ выполняется
- Результаты сохраняются в БД (`post_enrichment` с `kind='vision'`)
- Success rate: 100%

---

### 3.3. Тегирование

**Компоненты**:
- TaggingTask (`worker/tasks/tagging_task.py`)
- RetaggingTask (`worker/tasks/retagging_task.py`)
- TagPersistenceTask (`worker/tasks/tag_persistence_task.py`)

**Redis Streams**:
- `stream:posts:tagged`: **10,004** сообщений

**Статистика**:
- Всего обработано постов: **8,756**
- Успешных: **8,756** (100%)
- За последний час: **4** поста

**Статус**: ✅ Работает
- Тегирование выполняется через GigaChat
- Теги сохраняются в БД (`post_enrichment` с `kind='tags'`)
- Success rate: 100%

---

### 3.4. Обогащение (Crawl4AI)

**Компоненты**:
- EnrichmentTask (`worker/tasks/enrichment_task.py`)

**Redis Streams**:
- `stream:posts:enriched`: **1** сообщение (⚠️ низкая активность)

**Статистика**:
- Всего обработано постов: **0**
- Успешных: **0**
- За последний час: **0**

**Статус**: ⚠️ Не работает или нет данных
- Обогащение не выполняется
- Возможные причины:
  - Большинство постов не имеют URL для обогащения
  - EnrichmentTask не обрабатывает события
  - Фильтрация постов без URL

---

### 3.5. Индексация в Qdrant

**Компоненты**:
- IndexingTask (`worker/tasks/indexing_task.py`)
- EmbeddingService (GigaChat embeddings)

**Qdrant Collections**:
- `tdefault_posts`: **88** points
- `te70c43b0-e11d-45a8-8e51-f0ead91fb126_posts`: **1,688** points
- `t0905ea18-a243-42d1-9852-2a9377e9c9af_posts`: **198** points
- `ttest-tenant-e2e_posts`: **550** points
- `t7df762e3-99b8-44d7-9a8e-08452d11ba90_posts`: **917** points
- `tNone_posts`: **103** points
- `t6bf3422f-456f-4c41-a7f8-c86861c328c9_posts`: **4,024** points
- `trends_hot`: **2,617** points

**Статус**: ✅ Работает
- Посты индексируются в Qdrant
- Всего: **~10,185** points в коллекциях постов

---

### 3.6. Индексация в Neo4j

**Компоненты**:
- IndexingTask (Neo4j graph creation)
- GraphWriterTask (связи между постами)

**Neo4j Nodes**:
- Posts: **7,033** nodes
- Albums: **975** nodes

**Статус**: ✅ Работает
- Граф создается корректно
- Альбомы также индексируются

---

### 3.7. Сборка альбомов

**Компоненты**:
- AlbumAssemblerTask (`worker/tasks/album_assembler_task.py`)

**Статистика**:
- Всего альбомов: **910**
- За последние 24 часа: **34** альбома

**Статус**: ✅ Работает
- Альбомы собираются из отдельных сообщений
- Vision summary агрегируется для альбомов
- Активность: 34 альбома за 24 часа

---

## 4. Статус сервисов

### Контейнеры

| Сервис | Статус | Health |
|--------|--------|--------|
| telethon-ingest | ✅ Up 18 minutes | healthy |
| worker | ✅ Up 6 hours | healthy |
| redis | ✅ Up 43 hours | healthy |
| qdrant | ✅ Up 5 days | - |
| neo4j | ✅ Up 5 days | healthy |
| supabase-db | ✅ Up | - |

---

## 5. Выводы и рекомендации

### ✅ Работает корректно

1. **Scheduler**: Работает в режиме AUTO, тики выполняются регулярно
2. **Парсинг**: Посты и альбомы парсятся, события публикуются
3. **Vision анализ**: Выполняется, результаты сохраняются
4. **Тегирование**: Работает через GigaChat
5. **Qdrant индексация**: Посты индексируются (10K+ points)
6. **Neo4j индексация**: Граф создается (7K+ posts, 975 albums)

### ⚠️ Требует внимания

1. **Обогащение (Crawl4AI)**: Не работает
   - В БД нет записей с `kind='crawl'`
   - `stream:posts:enriched`: только 1 сообщение
   - **Рекомендация**: 
     - Проверить логи EnrichmentTask: `docker compose logs worker | grep -i enrichment`
     - Проверить, есть ли посты с URL для обогащения
     - Проверить, обрабатываются ли события `posts.tagged` в EnrichmentTask

2. **Lag между этапами**:
   - `stream:posts:parsed`: 10,004
   - `stream:posts:vision:analyzed`: 7,494 (lag: ~2,500)
   - `stream:posts:tagged`: 10,004
   - `stream:posts:enriched`: 1 (lag: ~10,000) ⚠️
   - `stream:posts:indexed`: 10,016

   **Рекомендация**: 
   - Проверить обработку событий в worker'е
   - Проверить, почему EnrichmentTask не обрабатывает события

---

## 6. Команды для проверки

### Проверка Scheduler
```bash
bash scripts/check_scheduler_status.sh
```

### Проверка Redis Streams
```bash
docker compose exec -T redis redis-cli XLEN "stream:posts:parsed"
docker compose exec -T redis redis-cli XLEN "stream:posts:vision:analyzed"
docker compose exec -T redis redis-cli XLEN "stream:posts:tagged"
docker compose exec -T redis redis-cli XLEN "stream:posts:enriched"
docker compose exec -T redis redis-cli XLEN "stream:posts:indexed"
```

### Проверка Qdrant
```bash
docker compose exec -T worker python3 -c "
from qdrant_client import QdrantClient
import os
qdrant = QdrantClient(url=os.getenv('QDRANT_URL', 'http://qdrant:6333'))
for col in qdrant.get_collections().collections:
    info = qdrant.get_collection(col.name)
    print(f'{col.name}: {info.points_count} points')
"
```

### Проверка Neo4j
```bash
docker compose exec -T worker python3 -c "
from neo4j import GraphDatabase
import os
driver = GraphDatabase.driver(
    os.getenv('NEO4J_URI', 'neo4j://neo4j:7687'),
    auth=(os.getenv('NEO4J_USER', 'neo4j'), os.getenv('NEO4J_PASSWORD', 'changeme'))
)
with driver.session() as session:
    result = session.run('MATCH (p:Post) RETURN count(p) as total')
    print(f'Posts: {result.single()[\"total\"]}')
    result = session.run('MATCH (a:Album) RETURN count(a) as total')
    print(f'Albums: {result.single()[\"total\"]}')
driver.close()
"
```

---

## 7. Мониторинг

### Grafana Dashboards
- System Overview: Scheduler Freshness, Queue Depth
- Parser Streams: Event processing rates
- Worker Tasks: Processing metrics

### Prometheus Alerts
- `SchedulerFreshnessHigh` (> 10 минут)
- `SchedulerFreshnessCritical` (> 15 минут)
- `SchedulerHeartbeatMissing`

---

**Статус**: ✅ Пайплайн работает корректно, требуется внимание к обогащению (Crawl4AI)

