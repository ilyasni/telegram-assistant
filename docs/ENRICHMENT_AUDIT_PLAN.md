# План аудита и исправления функционала обогащения (обновлённый)

## Контекст и функциональная цель

**Цель Vision-обогащения**: GigaChat Vision должен извлекать смысловую информацию о содержимом изображения (объекты/сцены/отношения), формировать текстовое описание (caption) и enriched-факты для downstream-задач — теги, фильтры, связи в графе и улучшенные эмбеддинги.

**Целевая архитектура**: RAG-контур с обогащением и гибридной индексацией (Postgres + Qdrant + Neo4j), где `post_enrichment` — единый источник истины для всех обогащений.

**Проверка реализованного функционала**:
1. **GigaChat Vision** должен определять тип контента (мем/фото/документ), извлекать объекты/сцены и сохранять результаты в БД
2. **Crawl4ai** должен использовать S3 для долговечного хранения HTML/Markdown контента
3. **Neo4j и Qdrant** должны использовать всю обогащённую информацию из post_enrichment для построения связей и улучшения поиска

## Обнаруженные проблемы

### 1. GigaChat Vision парсинг и структура данных (Критично)

**Статус**: ⚠️ Результаты сохраняются в БД, но парсинг упрощённый, отсутствует строгая валидация

**Проблемы**:
- `_parse_vision_response()` использует regex для поиска JSON или fallback на ключевые слова
- Не гарантируется структурированный JSON ответ от GigaChat
- Fallback классификация может быть неточной
- **Отсутствуют обязательные поля**: caption, objects[], scene, nsfw_score, aesthetic_score, dominant_colors[]
- **Нет валидации** структуры данных перед сохранением
- Обогащённые данные не используются в downstream-задачах (эмбеддинги, Qdrant, Neo4j)

**Где**: `worker/ai_adapters/gigachat_vision.py:438-491`, `worker/tasks/vision_analysis_task.py:429-456`

**Что исправить**:

1. **Создать Pydantic-схему VisionEnrichment** для строгой валидации:
```python
from pydantic import BaseModel, Field, constr
from typing import Literal, Optional, List

class VisionEnrichment(BaseModel):
    classification: Literal["photo", "meme", "document", "screenshot", "infographic", "other"]
    description: constr(min_length=5)  # краткий caption
    is_meme: bool
    labels: List[constr(strip_whitespace=True, min_length=1)] = []  # классы/атрибуты
    objects: List[str] = []  # объекты на изображении
    scene: Optional[str] = None  # описание сцены
    ocr: Optional[dict] = Field(default=None)  # {"text": "...", "engine": "...", "confidence": 0.0}
    nsfw_score: Optional[float] = Field(default=None, ge=0, le=1)
    aesthetic_score: Optional[float] = Field(default=None, ge=0, le=1)
    dominant_colors: List[str] = []  # hex или css-названия
    context: dict = Field(default_factory=dict)  # emotions, themes, relationships
    s3_keys: dict = Field(default_factory=dict)  # {"image": "...", "thumb": "..."}
```

2. **Реализовать жёсткий structured-output парсинг**:
   - Приоритет 1: Function-calling/JSON-режим GigaChat API (если поддерживается)
   - Приоритет 2: Двухшаговый протокол:
     - (а) LLM формирует JSON с strict schema hints в промпте
     - (б) Валидируем через Pydantic, если невалидно — мини-репромпт «почини JSON только по схеме X»
   - Приоритет 3: Tolerant-parser (json5/relaxed) + скобочный экстрактор для неполных ответов

3. **Встроенная нормализация**:
   - Lowercasing для labels
   - Top-K усечение (max 20 labels, max 10 objects)
   - Пороги для is_meme (confidence >= 0.7), nsfw_score (>= 0.5 → flag)

4. **Интеграционные эффекты**:
   - `caption/description + ocr.text` → используются в эмбеддинге (IndexingTask)
   - `labels/objects/scene/is_meme/nsfw_score` → Qdrant payload + индексы для фильтрации
   - В Neo4j → узлы `(:Image)` и связи `(:Post)-[:HAS_IMAGE]->(:Image)` + `(:Image)-[:HAS_LABEL]->(:Label)`

### 2. Crawl4ai НЕ использует S3 (Критично)

**Статус**: ❌ HTML контент сохраняется только в Redis кэш, не в S3

**Проблемы**:
- `EnrichmentEngine._enrich_url()` сохраняет только в Redis кэш (`_save_to_cache`)
- Метод `build_crawl_key()` существует в `S3StorageService`, но не используется
- HTML контент теряется при перезапуске Redis или истечении TTL
- **Нет долговечности и адресуемости** обогащённых артефактов

**Где**: `crawl4ai/enrichment_engine.py:372-381`, `crawl4ai/crawl4ai_service.py:339-400`

**Что исправить**:

1. **Интегрировать S3StorageService в EnrichmentEngine**:
   - Добавить `s3_service: Optional[S3StorageService]` в `__init__()`
   - Передавать `tenant_id` и `post_id` для генерации ключей

2. **S3 ключи и структура**:
   - HTML: `crawl/{tenant_id}/{post_id}/{url_hash}.html`
   - Markdown: `crawl/{tenant_id}/{post_id}/{url_hash}.md` (если есть markdown)
   - Использовать `build_crawl_key(tenant_id, url_hash, suffix='.html')`

3. **Контроль целостности и идемпотентность**:
   - Content-MD5 для проверки целостности при upload
   - HEAD проверка перед PUT (идемпотентность)
   - Если ETag совпадает — не перезаливаем
   - Хранить `md5` в `post_enrichment.data->'checksums'`

4. **Структура данных в post_enrichment(kind='crawl')**:
```python
{
  "url": "<original>",
  "url_hash": "<sha256>",
  "content_sha256": "<sha256>",
  "s3_keys": {
    "html": "crawl/{tenant}/{post_id}/{url_hash}.html",
    "md": "crawl/{tenant}/{post_id}/{url_hash}.md"  # опционально
  },
  "md_excerpt": "<первые ~1-2k символов>",  # для быстрого доступа
  "markdown": "<полный markdown>",  # или ссылка на S3
  "meta": {
    "title": "...",
    "lang": "...",
    "word_count": 1234
  },
  "checksums": {
    "html_md5": "...",
    "md_md5": "..."
  }
}
```

5. **Context7 best practices**:
   - Идемпотентность через HEAD проверку перед upload
   - Content-MD5 для целостности
   - Gzip compression для HTML/Markdown
   - Redis кэш — для быстрого доступа, S3 — источник истины

### 3. Neo4j/Qdrant НЕ используют enrichment данные (Критично)

**Статус**: ❌ Обогащённые данные не используются при индексации

#### 3.1 IndexingTask не загружает post_enrichment

- `_get_post_data()` загружает только базовые поля из `posts` таблицы
- Не загружаются данные из `post_enrichment` (vision, crawl, tags)
- `enrichment_data=None` передаётся в `create_post_node()`

**Где**: `worker/tasks/indexing_task.py:449-474`, строка 563

#### 3.2 Qdrant payload не содержит enrichment данных

- `_index_to_qdrant()` сохраняет только `post_id`, `channel_id`, `text`, `telegram_message_id`, `created_at`
- Не включает: tags, vision labels, crawl markdown, OCR text, is_meme

**Где**: `worker/tasks/indexing_task.py:500-526`

#### 3.3 Embedding не включает enrichment данные

- `_generate_embedding()` использует только `post_data.get('text')`
- Не включает: crawl markdown, OCR text, vision caption для более качественных эмбеддингов

**Где**: `worker/tasks/indexing_task.py:476-498`

#### 3.4 Neo4j не использует enrichment для связей

- `_index_to_neo4j()` не создаёт связи с тегами из enrichment
- Не создаёт ImageContent nodes с vision данными
- Не использует crawl данные для создания URL узлов

**Где**: `worker/tasks/indexing_task.py:528-576`

## План исправлений

### Этап 1: Улучшение парсинга GigaChat Vision

**Файлы**: `worker/ai_adapters/gigachat_vision.py`, `worker/events/schemas/posts_vision_v1.py`

**Изменения**:
1. Создать Pydantic-схему `VisionEnrichment` в `worker/events/schemas/posts_vision_v1.py`
2. Обновить `_parse_vision_response()`:
   - Парсинг JSON с валидацией через Pydantic
   - Tolerant-parser (json5) для неполных ответов
   - Repair-prompt при невалидных данных
3. Нормализация данных (lowercasing, top-K, пороги)
4. Обновить `vision_analysis_task.py` для использования валидированной схемы

### Этап 2: Интеграция Crawl4ai с S3

**Файлы**: `crawl4ai/enrichment_engine.py`, `crawl4ai/crawl4ai_service.py`

**Изменения**:
1. Добавить `S3StorageService` в `EnrichmentEngine.__init__()`
2. В `_enrich_url()` после получения HTML:
   - Сохранить HTML в S3 через `s3_service.put_json()` или `s3_service.put_media()`
   - Использовать `build_crawl_key(tenant_id, post_id, url_hash, '.html')`
   - HEAD проверка перед upload (идемпотентность)
   - Content-MD5 для целостности
3. Обновить структуру данных в `_save_enrichment()` для включения `s3_keys` и `checksums`

### Этап 3: Интеграция IndexingTask с post_enrichment

**Файлы**: `worker/tasks/indexing_task.py`

#### 3.1 Расширить `_get_post_data()` для агрегации всех обогащений

**Структура возвращаемых данных** (плоский агрегат):
```python
{
    "id": str,
    "channel_id": str,
    "text": str,
    "telegram_message_id": int,
    "created_at": datetime,
    "vision": VisionEnrichment | None,  # Pydantic модель
    "crawl": {
        "md": str | None,
        "html_key": str | None,
        "md_key": str | None,
        "urls": list[dict],
        "word_count": int
    } | None,
    "tags": list[str] | None
}
```

**SQL запрос** (с JOIN для производительности):
```sql
SELECT 
    p.id, 
    p.channel_id, 
    p.content as text, 
    p.telegram_message_id, 
    p.created_at,
    pe_vision.data as vision_data,
    pe_crawl.data as crawl_data,
    pe_tags.data as tags_data
FROM posts p
LEFT JOIN post_enrichment pe_vision ON pe_vision.post_id = p.id AND pe_vision.kind = 'vision'
LEFT JOIN post_enrichment pe_crawl ON pe_crawl.post_id = p.id AND pe_crawl.kind = 'crawl'
LEFT JOIN post_enrichment pe_tags ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
WHERE p.id = %s
```

**Валидация**:
- Валидировать `vision_data` через `VisionEnrichment` Pydantic модель
- Извлечь `tags` из `tags_data->'tags'` (поддержать text[] и JSONB массивы)

#### 3.2 Улучшить `_generate_embedding()` с композицией текста и лимитами

**Жёсткие правила композиции** (с лимитами для контроля токенов):
```python
async def _generate_embedding(self, post_data: Dict[str, Any]) -> list:
    text_parts = []
    
    # 1. Основной текст поста (приоритет 1, лимит 2000 символов)
    post_text = post_data.get('text', '')[:2000]
    if post_text.strip():
        text_parts.append(post_text)
    
    # 2. Vision description/caption (приоритет 2, лимит 500 символов)
    if vision := post_data.get('vision'):
        if description := vision.get('description'):
            text_parts.append(description[:500])
        # Vision OCR (приоритет 3, лимит 300 символов)
        if ocr_text := vision.get('ocr', {}).get('text'):
            text_parts.append(ocr_text[:300])
    
    # 3. Crawl markdown (приоритет 4, предпочтительно заголовки+аннотация, лимит 1500 символов)
    if crawl := post_data.get('crawl'):
        if md := crawl.get('md'):
            md_preview = md[:1500]
            text_parts.append(md_preview)
        # Crawl OCR (приоритет 5, лимит 300 символов)
        if ocr_texts := crawl.get('ocr_texts'):
            ocr_combined = '\n'.join(ocr.get('text', '')[:100] for ocr in ocr_texts[:3])
            if ocr_combined.strip():
                text_parts.append(ocr_combined[:300])
    
    # Дедупликация и нормализация
    full_text = '\n\n'.join(filter(None, text_parts))
    full_text = ' '.join(full_text.split())  # Удаление избыточных пробелов
    
    if not full_text.strip():
        raise ValueError("No text content available for embedding")
    
    embedding = await self.embedding_service.generate_embedding(full_text)
    return embedding
```

#### 3.3 Расширить `_index_to_qdrant()` payload для фасетирования

**Структура payload**:
```python
payload = {
    # Базовые поля
    "post_id": post_id,
    "channel_id": post_data.get('channel_id'),
    "telegram_message_id": post_data.get('telegram_message_id'),
    "created_at": post_data.get('created_at').isoformat() if post_data.get('created_at') else None,
    "text_short": post_data.get('text', '')[:500],  # preview для UI
    
    # Tags (для фильтрации)
    "tags": post_data.get('tags', []),
    
    # Vision данные (структурированные для фильтрации)
    "vision": {
        "is_meme": vision.get('is_meme', False) if (vision := post_data.get('vision')) else False,
        "classification": vision.get('classification', 'other') if vision else 'other',
        "labels": vision.get('labels', []) if vision else [],
        "objects": vision.get('objects', [])[:10] if vision else [],  # top 10
        "scene": vision.get('scene') if vision else None,
        "nsfw_score": vision.get('nsfw_score') if vision else None,
        "aesthetic_score": vision.get('aesthetic_score') if vision else None,
        "dominant_colors": vision.get('dominant_colors', [])[:5] if vision else []  # top 5
    } if post_data.get('vision') else None,
    
    # Crawl данные (метаинформация, полный текст в S3)
    "crawl": {
        "has_crawl": bool(post_data.get('crawl')),
        "md_len": len(crawl.get('md', '')) if (crawl := post_data.get('crawl')) else 0,
        "html_key": crawl.get('html_key') if crawl else None,
        "md_key": crawl.get('md_key') if crawl else None,
        "urls_count": len(crawl.get('urls', [])) if crawl else 0,
        "word_count": crawl.get('word_count', 0) if crawl else 0
    } if post_data.get('crawl') else None
}
```

**Индексы payload в Qdrant**:
- `channel_id`: INTEGER index
- `tags`: KEYWORD array index
- `vision.is_meme`: BOOL index
- `vision.labels`: KEYWORD array index
- `vision.classification`: KEYWORD index
- `vision.scene`: KEYWORD index (опционально)
- `vision.nsfw_score`: FLOAT index (для фильтрации NSFW)

#### 3.4 Улучшить `_index_to_neo4j()` для всех узлов и связей

**Минимум необходимых сущностей и связей**:
```cypher
(:Post {id}) -[:IN_CHANNEL]-> (:Channel {id})
(:Post) -[:HAS_IMAGE]-> (:Image {post_id, sha256, s3_key, ...})
(:Image) -[:HAS_LABEL]-> (:Label {name})
(:Post) -[:HAS_TAG]-> (:Tag {name})
(:Post) -[:REFERS_TO]-> (:WebPage {url, s3_html_key})
```

**Реализация**:
- Создать/обновить Post node с enrichment_data
- Создать связи с тегами через `create_tag_relationships()`
- Создать ImageContent nodes для vision через `create_image_content_node()`
- Создать WebPage nodes для crawl через `create_webpage_node()` (если метод существует)

### Этап 4: Проверка и доработка Neo4j методов

**Файлы**: `worker/integrations/neo4j_client.py`

**Проверить/создать методы**:
- `create_image_content_node()` — существует, проверить параметры
- `create_label_nodes()` — создать если отсутствует
- `create_webpage_node()` — создать если отсутствует
- Батч-операции для связей (UNWIND для производительности)

### Этап 5: Миграции БД и контракты событий

**Миграции БД**:
1. Проверить структуру post_enrichment: композитный ключ `(post_id, kind)`, GIN индекс на `data`
2. Добавить проверочные constraint-триггеры (опционально):
   - Для `kind='vision'`: проверка наличия `description` и `is_meme`
   - Для `kind='crawl'`: проверка наличия `s3_keys`

**Контракты событий**:
- Стандартизировать `post.enriched.vision` и `post.enriched.crawl`
- Гарантировать идемпотентность через `event_id` / `dedupe_key`

### Этап 6: Тест-план

**Unit тесты**:
- Валидация VisionEnrichment Pydantic схемы
- Толерантный JSON-парсер
- Генерация S3 ключей и идемпотентность (HEAD → PUT)

**Integration тесты**:
- Vision полный цикл (10 картинок) → проверка post_enrichment, Qdrant, Neo4j
- Crawl4ai → S3 → пересоздание Redis → данные доступны из S3
- RAG запросы с фильтрами по vision.labels и is_meme=true

**Observability метрики**:
- `vision_parsed_total{status="success|error|fallback"}`
- `crawl_persist_total{status="success|error", destination="s3|redis"}`
- `enrichment_join_duration_seconds` (histogram)
- `embedding_tokens_used_total`

## Риски и митигация

1. **Нестабильность JSON из LLM**: Двухшаговый structured-output + строгая валидация + "repair-prompt"
2. **Рост токенов при эмбеддинге**: Жёсткие лимиты частей, дедуп, приоритизация (caption > ocr > crawl.md)
3. **Перегрев Qdrant payload**: Полные тексты (md/html) в S3, в payload — только фасеты/флаги/короткие превью (< 64KB)
4. **Расходимость графа и вектора**: Общий "источник истины" — `post_enrichment`; индексация из одного агрегата `_get_post_data()`

## Ожидаемый результат

После исправлений:
1. ✅ GigaChat Vision: Улучшенный парсинг с Pydantic валидацией, обязательные поля (caption, labels, objects, scene, is_meme)
2. ✅ Crawl4ai: HTML/Markdown контент сохраняется в S3 с контролем целостности, s3_key в post_enrichment
3. ✅ Neo4j: Использует enrichment данные для создания всех узлов и связей (tags, ImageContent, WebPage)
4. ✅ Qdrant: Эмбеддинги включают enrichment данные, payload содержит фасеты для фильтрации (tags, vision.labels, is_meme, nsfw_score)
