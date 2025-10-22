# Интеграция RAG и Graph Intelligence

## Архитектура системы

Система построена на трёх компонентах:
- **PostgreSQL** — основное хранилище данных
- **Qdrant** — векторное хранилище для семантического поиска
- **Neo4j** — граф знаний для связей между сущностями

## Qdrant векторное хранилище

### Структура коллекций

#### Per-tenant коллекции
```python
# Naming convention: tenant_{tenant_id}_posts
collection_name = f"tenant_{tenant_id}_posts"

# Payload структура
payload = {
    "post_id": str(post.id),
    "channel_id": str(post.channel_id),
    "tg_channel_id": post.channel.tg_channel_id,
    "tags": post_enrichment.tags,
    "posted_at": int(post.posted_at.timestamp()),
    "has_media": post.has_media,
    "media_types": [media.media_type for media in post.media],
    "content_length": len(post.content or ""),
    "enrichment_provider": post_enrichment.enrichment_provider
}
```

#### Конфигурация коллекции
```python
from qdrant_client.models import Distance, VectorParams, CollectionStatus

# Создание коллекции
client.create_collection(
    collection_name=collection_name,
    vectors_config=VectorParams(
        size=1536,  # GigaChat embeddings
        distance=Distance.COSINE
    ),
    # Настройки для производительности
    hnsw_config={
        "m": 16,
        "ef_construct": 100,
        "full_scan_threshold": 10000
    }
)
```

### Hybrid Search

#### Dense + Sparse векторы
```python
# Dense векторы (GigaChat embeddings)
dense_vector = await generate_embedding(text, provider="gigachat")

# Sparse векторы (BM25)
sparse_vector = bm25.encode(text)

# Fusion scoring
def hybrid_score(dense_score, sparse_score, alpha=0.7):
    return alpha * dense_score + (1 - alpha) * sparse_score
```

#### Поиск с фильтрацией
```python
from qdrant_client.models import Filter, FieldCondition, MatchValue

# Фильтр по каналам пользователя
user_channels = get_user_subscribed_channels(user_id)
channel_filter = Filter(
    must=[
        FieldCondition(
            key="channel_id",
            match=MatchValue(any=user_channels)
        )
    ]
)

# Поиск с фильтрами
results = client.search(
    collection_name=collection_name,
    query_vector=dense_vector,
    query_filter=channel_filter,
    limit=10,
    score_threshold=0.7
)
```

### Кэширование эмбеддингов

#### Redis кэш
```python
import redis
import json
from hashlib import md5

redis_client = redis.Redis(host='localhost', port=6379, db=0)

def get_cached_embedding(text: str, provider: str) -> Optional[List[float]]:
    """Получить эмбеддинг из кэша."""
    cache_key = f"embedding:{provider}:{md5(text.encode()).hexdigest()}"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    return None

def cache_embedding(text: str, provider: str, embedding: List[float], ttl: int = 604800):
    """Кэшировать эмбеддинг на 7 дней."""
    cache_key = f"embedding:{provider}:{md5(text.encode()).hexdigest()}"
    redis_client.setex(cache_key, ttl, json.dumps(embedding))
```

#### Batch processing
```python
async def batch_embedding_generation(texts: List[str], batch_size: int = 100):
    """Генерация эмбеддингов батчами."""
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        batch_embeddings = await generate_embeddings_batch(batch)
        embeddings.extend(batch_embeddings)
    return embeddings
```

## Neo4j GraphRAG

### Схема графа

#### Вершины (Nodes)
```cypher
// Посты
CREATE (p:Post {
    post_id: $post_id,
    content: $content,
    posted_at: $posted_at,
    has_media: $has_media
})

// Каналы
CREATE (c:Channel {
    channel_id: $channel_id,
    tg_channel_id: $tg_channel_id,
    title: $title,
    username: $username
})

// Теги
CREATE (t:Tag {
    name: $tag_name,
    category: $tag_category,
    confidence: $confidence
})

// URL
CREATE (u:Url {
    url: $url,
    domain: $domain,
    title: $title
})

// Сущности (из NER)
CREATE (e:Entity {
    name: $entity_name,
    type: $entity_type,
    confidence: $confidence
})

// Пользователи
CREATE (user:User {
    user_id: $user_id,
    telegram_id: $telegram_id,
    username: $username
})
```

#### Рёбра (Relationships)
```cypher
// Пост принадлежит каналу
(p:Post)-[:IN_CHANNEL]->(c:Channel)

// Пост имеет теги
(p:Post)-[:HAS_TAG {confidence: $confidence}]->(t:Tag)

// Пост ссылается на URL
(p:Post)-[:REFERS_TO {context: $context}]->(u:Url)

// Пост упоминает сущности
(p:Post)-[:MENTIONS {context: $context}]->(e:Entity)

// Пост связан с другими постами (по темам)
(p1:Post)-[:RELATED_TO {similarity: $similarity}]->(p2:Post)

// Пользователь упомянут в группе
(user:User)-[:MENTIONED_IN {context: $context}]->(gm:GroupMessage)
```

### Идемпотентность операций

#### Upsert стратегия
```python
def upsert_post_node(session, post_data):
    """Создать или обновить узел поста."""
    query = """
    MERGE (p:Post {post_id: $post_id})
    SET p.content = $content,
        p.posted_at = $posted_at,
        p.has_media = $has_media,
        p.updated_at = datetime()
    RETURN p
    """
    return session.run(query, **post_data)

def upsert_tag_relationship(session, post_id, tag_name, confidence):
    """Создать связь пост-тег."""
    query = """
    MATCH (p:Post {post_id: $post_id})
    MERGE (t:Tag {name: $tag_name})
    MERGE (p)-[r:HAS_TAG]->(t)
    SET r.confidence = $confidence,
        r.updated_at = datetime()
    RETURN r
    """
    return session.run(query, post_id=post_id, tag_name=tag_name, confidence=confidence)
```

### GraphRAG запросы

#### Поиск связанных постов
```cypher
// Найти посты, связанные с определённой темой
MATCH (p:Post)-[:HAS_TAG]->(t:Tag {name: "AI"})
MATCH (p)-[:IN_CHANNEL]->(c:Channel)
RETURN p.content, c.title, p.posted_at
ORDER BY p.posted_at DESC
LIMIT 10
```

#### Анализ влияния каналов
```cypher
// Каналы с наибольшим количеством связанных постов
MATCH (c:Channel)<-[:IN_CHANNEL]-(p:Post)-[:RELATED_TO]-(p2:Post)
RETURN c.title, COUNT(DISTINCT p2) as influence_score
ORDER BY influence_score DESC
```

#### Community detection
```cypher
// Найти сообщества тегов
CALL gds.louvain.stream('tag_community')
YIELD nodeId, communityId
MATCH (t:Tag) WHERE id(t) = nodeId
RETURN t.name, communityId
ORDER BY communityId, t.name
```

## Пайплайн обогащения

### Этапы обработки

#### 1. Parse → Save
```python
async def parse_and_save_post(channel_id: UUID, message_data: dict):
    """Парсинг и сохранение поста."""
    # Сохранить пост
    post = Post(
        channel_id=channel_id,
        tg_message_id=message_data['message_id'],
        content=message_data['text'],
        posted_at=message_data['date'],
        url=message_data.get('url')
    )
    db.add(post)
    db.commit()
    
    # Сохранить медиа
    for media in message_data.get('media', []):
        media_obj = PostMedia(
            post_id=post.id,
            media_type=media['type'],
            media_url=media['url'],
            tg_file_id=media.get('file_id')
        )
        db.add(media_obj)
    
    db.commit()
    return post
```

#### 2. Tagging (GigaChat → OpenRouter fallback)
```python
async def enrich_post_tags(post_id: UUID, content: str):
    """Обогащение тегами с fallback."""
    try:
        # Попытка через GigaChat
        tags = await gigachat_tagging(content)
        provider = "gigachat"
    except Exception as e:
        logger.warning(f"GigaChat failed: {e}, trying OpenRouter")
        # Fallback на OpenRouter
        tags = await openrouter_tagging(content)
        provider = "openrouter"
    
    # Сохранить обогащение
    enrichment = PostEnrichment(
        post_id=post_id,
        tags=tags,
        enrichment_provider=provider,
        enriched_at=datetime.utcnow()
    )
    db.add(enrichment)
    db.commit()
```

#### 3. Vision/OCR (если есть медиа)
```python
async def process_media_vision(post_id: UUID):
    """Обработка изображений."""
    post_media = db.query(PostMedia).filter(
        PostMedia.post_id == post_id,
        PostMedia.media_type == 'photo'
    ).all()
    
    for media in post_media:
        # Vision анализ
        vision_labels = await analyze_image(media.media_url)
        
        # OCR текст
        ocr_text = await extract_text_from_image(media.media_url)
        
        # Обновить enrichment
        enrichment = db.query(PostEnrichment).filter(
            PostEnrichment.post_id == post_id
        ).first()
        
        enrichment.vision_labels = vision_labels
        enrichment.ocr_text = ocr_text
        db.commit()
```

#### 4. Crawl4AI (если есть URL)
```python
async def crawl_post_url(post_id: UUID, url: str):
    """Обогащение через crawl4ai."""
    try:
        crawl_result = await crawl4ai.crawl(url)
        crawl_md = crawl_result.markdown
        
        # Обновить enrichment
        enrichment = db.query(PostEnrichment).filter(
            PostEnrichment.post_id == post_id
        ).first()
        
        enrichment.crawl_md = crawl_md
        db.commit()
        
    except Exception as e:
        logger.error(f"Crawl4AI failed for {url}: {e}")
```

#### 5. Upsert в Qdrant
```python
async def index_post_in_qdrant(post_id: UUID, tenant_id: UUID):
    """Индексация поста в Qdrant."""
    post = db.query(Post).filter(Post.id == post_id).first()
    enrichment = db.query(PostEnrichment).filter(
        PostEnrichment.post_id == post_id
    ).first()
    
    # Подготовить текст для эмбеддинга
    text_parts = [post.content or ""]
    if enrichment and enrichment.crawl_md:
        text_parts.append(enrichment.crawl_md)
    if enrichment and enrichment.ocr_text:
        text_parts.append(enrichment.ocr_text)
    
    full_text = "\n\n".join(text_parts)
    
    # Генерация эмбеддинга
    embedding = await generate_embedding(full_text)
    
    # Upsert в Qdrant
    collection_name = f"tenant_{tenant_id}_posts"
    client.upsert(
        collection_name=collection_name,
        points=[{
            "id": str(post_id),
            "vector": embedding,
            "payload": {
                "post_id": str(post_id),
                "channel_id": str(post.channel_id),
                "tg_channel_id": post.channel.tg_channel_id,
                "tags": enrichment.tags if enrichment else [],
                "posted_at": int(post.posted_at.timestamp()),
                "has_media": post.has_media,
                "content_length": len(full_text)
            }
        }]
    )
```

#### 6. Neo4j Graph Construction
```python
async def build_post_graph(post_id: UUID):
    """Построение графа для поста."""
    post = db.query(Post).filter(Post.id == post_id).first()
    enrichment = db.query(PostEnrichment).filter(
        PostEnrichment.post_id == post_id
    ).first()
    
    with neo4j_driver.session() as session:
        # Создать узел поста
        await upsert_post_node(session, {
            "post_id": str(post.id),
            "content": post.content,
            "posted_at": post.posted_at.isoformat(),
            "has_media": post.has_media
        })
        
        # Создать связи с тегами
        if enrichment and enrichment.tags:
            for tag in enrichment.tags:
                await upsert_tag_relationship(
                    session, 
                    str(post.id), 
                    tag['name'], 
                    tag.get('confidence', 0.5)
                )
        
        # Создать связь с каналом
        await create_channel_relationship(session, str(post.id), str(post.channel_id))
```

### Мониторинг пайплайна

#### Статусы обработки
```python
class ProcessingStatus(Enum):
    PENDING = "pending"
    TAGGED = "tagged"
    VISIONED = "visioned"
    CRAWLED = "crawled"
    EMBEDDED = "embedded"
    IN_QDRANT = "in_qdrant"
    GRAPH_BUILT = "graph_built"
    FAILED = "failed"

# Обновление статуса
def update_processing_status(post_id: UUID, status: ProcessingStatus, error: str = None):
    """Обновить статус обработки поста."""
    status_obj = db.query(IndexingStatus).filter(
        IndexingStatus.post_id == post_id
    ).first()
    
    if not status_obj:
        status_obj = IndexingStatus(post_id=post_id)
        db.add(status_obj)
    
    status_obj.status = status.value
    status_obj.last_error = error
    status_obj.updated_at = datetime.utcnow()
    db.commit()
```

#### Метрики качества
```python
# Faithfulness — соответствие ответа источникам
def calculate_faithfulness(answer: str, sources: List[str]) -> float:
    """Оценить соответствие ответа источникам."""
    # Реализация метрики faithfulness
    pass

# Answer relevance — релевантность ответа запросу
def calculate_answer_relevance(query: str, answer: str) -> float:
    """Оценить релевантность ответа запросу."""
    # Реализация метрики answer relevance
    pass

# Context precision — точность контекста
def calculate_context_precision(query: str, retrieved_docs: List[str]) -> float:
    """Оценить точность извлечённого контекста."""
    # Реализация метрики context precision
    pass
```

## SLA и производительность

### Целевые метрики
- **Vector search** — <200ms для 95% запросов
- **Graph queries** — <500ms для простых, <2s для сложных
- **Hybrid search** — <300ms для комбинированных запросов
- **RAG generation** — <5s для ответов до 1000 токенов

### Мониторинг
```python
import time
from prometheus_client import Counter, Histogram, Gauge

# Метрики
search_duration = Histogram('rag_search_duration_seconds', 'Search duration')
embedding_generation = Counter('rag_embeddings_generated_total', 'Total embeddings generated')
active_connections = Gauge('rag_active_connections', 'Active connections')

# Декоратор для мониторинга
def monitor_performance(metric_name):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                search_duration.labels(operation=metric_name).observe(time.time() - start_time)
                return result
            except Exception as e:
                logger.error(f"Error in {metric_name}: {e}")
                raise
        return wrapper
    return decorator
```

## Fallback стратегии

### LLM провайдеры
```python
async def generate_embedding_with_fallback(text: str) -> List[float]:
    """Генерация эмбеддинга с fallback."""
    providers = ["gigachat", "openrouter", "local"]
    
    for provider in providers:
        try:
            if provider == "gigachat":
                return await gigachat_embedding(text)
            elif provider == "openrouter":
                return await openrouter_embedding(text)
            elif provider == "local":
                return await local_embedding(text)
        except Exception as e:
            logger.warning(f"Provider {provider} failed: {e}")
            continue
    
    raise Exception("All embedding providers failed")

# Circuit breaker
class CircuitBreaker:
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    def call(self, func, *args, **kwargs):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "HALF_OPEN"
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = func(*args, **kwargs)
            self.on_success()
            return result
        except Exception as e:
            self.on_failure()
            raise e
    
    def on_success(self):
        self.failure_count = 0
        self.state = "CLOSED"
    
    def on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
```

### Vector search fallback
```python
async def search_with_fallback(query: str, tenant_id: UUID, limit: int = 10):
    """Поиск с fallback стратегиями."""
    try:
        # Основной поиск в Qdrant
        return await qdrant_search(query, tenant_id, limit)
    except Exception as e:
        logger.warning(f"Qdrant search failed: {e}")
        
        try:
            # Fallback на PostgreSQL с pgvector
            return await postgres_vector_search(query, tenant_id, limit)
        except Exception as e2:
            logger.warning(f"PostgreSQL search failed: {e2}")
            
            # Последний fallback — простой текстовый поиск
            return await simple_text_search(query, tenant_id, limit)
```

## Оптимизация ресурсов

### Connection pooling
```python
from sqlalchemy.pool import QueuePool
from qdrant_client import QdrantClient
from neo4j import GraphDatabase

# PostgreSQL connection pool
engine = create_engine(
    settings.database_url,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True
)

# Qdrant client с connection pooling
qdrant_client = QdrantClient(
    host="localhost",
    port=6333,
    timeout=30,
    # Встроенный connection pooling
)

# Neo4j driver с connection pooling
neo4j_driver = GraphDatabase.driver(
    "bolt://localhost:7687",
    max_connection_lifetime=3600,
    max_connection_pool_size=50
)
```

### Batch operations
```python
async def batch_index_posts(post_ids: List[UUID], tenant_id: UUID):
    """Батчевая индексация постов."""
    # Получить все посты
    posts = db.query(Post).filter(Post.id.in_(post_ids)).all()
    
    # Подготовить данные
    points = []
    for post in posts:
        embedding = await generate_embedding(post.content)
        points.append({
            "id": str(post.id),
            "vector": embedding,
            "payload": prepare_payload(post)
        })
    
    # Батчевая вставка в Qdrant
    collection_name = f"tenant_{tenant_id}_posts"
    client.upsert(
        collection_name=collection_name,
        points=points
    )
```

### Cache warming
```python
async def warm_cache(tenant_id: UUID):
    """Предзагрузка популярных запросов."""
    popular_queries = [
        "AI технологии",
        "машинное обучение",
        "программирование",
        "стартапы"
    ]
    
    for query in popular_queries:
        # Предзагрузить результаты в Redis
        results = await search_posts(query, tenant_id, limit=20)
        cache_key = f"search:{tenant_id}:{hash(query)}"
        redis_client.setex(cache_key, 3600, json.dumps(results))
```

## Заключение

Интеграция RAG и Graph Intelligence обеспечивает:

1. **Семантический поиск** через Qdrant с hybrid scoring
2. **Граф знаний** через Neo4j для связей между сущностями
3. **Идемпотентность** операций для надёжности
4. **Fallback стратегии** для отказоустойчивости
5. **Мониторинг** качества и производительности

Система готова к масштабированию и обеспечивает высокое качество RAG-ответов.
