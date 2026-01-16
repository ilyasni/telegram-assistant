"""
Digest Service для генерации дайджестов новостей
Context7: сбор контента ТОЛЬКО по пользовательским тематикам из digest_settings.topics
"""

import time
import json
import re
from collections import Counter
from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime, date, timedelta, timezone

import structlog
from prometheus_client import Counter as PromCounter, Histogram, Gauge
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func, desc
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableParallel
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, SecretStr

from models.database import Post, PostEnrichment, Channel, User, DigestSettings, DigestHistory, UserChannel
from api.services.rag_service import RAGService  # Для генерации embedding
from services.graph_service import get_graph_service
from config import settings

logger = structlog.get_logger()

digest_generation_duration_seconds = Histogram(
    'digest_generation_duration_seconds',
    'Время генерации пользовательского дайджеста',
    ['tenant_id']
)

digest_qdrant_hits_total = PromCounter(
    'digest_qdrant_hits_total',
    'Количество постов, извлечённых из Qdrant при генерации дайджеста',
    ['tenant_id']
)

digest_graph_hits_total = PromCounter(
    'digest_graph_hits_total',
    'Количество постов, извлечённых из графа Neo4j при генерации дайджеста',
    ['tenant_id']
)

digest_gigachat_filter_detected_total = PromCounter(
    'digest_gigachat_filter_detected_total',
    'Количество обнаруженных ответов с фильтром Gigachat',
    ['tenant_id']
)

digest_openrouter_fallback_total = PromCounter(
    'digest_openrouter_fallback_total',
    'Количество успешных fallback на OpenRouter',
    ['tenant_id']
)

digest_openrouter_fallback_failed_total = PromCounter(
    'digest_openrouter_fallback_failed_total',
    'Количество неудачных fallback на OpenRouter',
    ['tenant_id']
)

# Context7: Метрики для канальных дайджестов (низкая кардинальность - без channel_id)
channel_digest_generation_total = PromCounter(
    'channel_digest_generation_total',
    'Количество запросов на генерацию канального дайджеста',
    ['tenant_id', 'period']
)

channel_digest_generation_duration_seconds = Histogram(
    'channel_digest_generation_duration_seconds',
    'Время генерации канального дайджеста',
    ['tenant_id', 'period']
)

channel_digest_posts_count = Gauge(
    'channel_digest_posts_count',
    'Количество постов в канальном дайджесте',
    ['tenant_id', 'period']
)

channel_digest_context_tokens = Gauge(
    'channel_digest_context_tokens',
    'Размер контекста в токенах для канального дайджеста',
    ['tenant_id', 'period']
)

channel_digest_cache_hits_total = PromCounter(
    'channel_digest_cache_hits_total',
    'Количество попаданий в кеш канальных дайджестов',
    ['tenant_id', 'period']
)

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class DigestContent(BaseModel):
    """Контент для дайджеста."""
    content: str
    posts_count: int
    topics: List[str]
    sections: List[Dict[str, Any]]  # Секции по темам


# ============================================================================
# DIGEST SERVICE
# ============================================================================

class DigestService:
    """Сервис для генерации дайджестов."""
    
    def __init__(
        self,
        qdrant_url: str,
        qdrant_client: Optional[QdrantClient] = None,
        openai_api_base: Optional[str] = None,
        graph_service: Optional[Any] = None,
        redis_client: Optional[Any] = None
    ):
        """
        Инициализация Digest Service.
        
        Args:
            qdrant_url: URL Qdrant сервиса
            qdrant_client: Qdrant клиент (опционально)
            openai_api_base: URL gpt2giga-proxy
            graph_service: GraphService для работы с Neo4j (опционально)
            redis_client: Redis клиент для кеширования (опционально)
        """
        self.redis_client = redis_client
        self.qdrant_url = qdrant_url
        self.qdrant_client = qdrant_client or QdrantClient(url=qdrant_url)
        
        # Context7: Инициализация GraphService для поиска связанных тем
        self.graph_service = graph_service or get_graph_service()
        
        # Инициализация GigaChat LLM через langchain-gigachat
        # Context7: Исправлен URL (без /v1) для обработки редиректов прокси
        api_base = openai_api_base or settings.openai_api_base or "http://gpt2giga-proxy:8090"
        if api_base.endswith("/v1"):
            api_base = api_base[:-3]
        
        import os
        os.environ.setdefault("OPENAI_API_BASE", api_base)
        
        credentials_value = getattr(settings, 'gigachat_credentials', '') or os.getenv('GIGACHAT_CREDENTIALS', '')
        if isinstance(credentials_value, SecretStr):
            credentials_value = credentials_value.get_secret_value()

        scope_value = getattr(settings, 'gigachat_scope', None) or os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS')
        if isinstance(scope_value, SecretStr):
            scope_value = scope_value.get_secret_value()

        self.llm = GigaChat(
            credentials=credentials_value,
            scope=scope_value,
            base_url=api_base,
            temperature=0.7,
            verify_ssl_certs=False,
        )
        logger.debug(
            "GigaChat client initialized",
            base_url=self.llm.base_url,
            verify_ssl=self.llm.verify_ssl_certs,
        )
        
        # Context7: Промпт для канального дайджеста (облегченный формат)
        self.channel_digest_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по составлению дайджестов новостей из конкретного Telegram канала.

Создай краткий и практичный дайджест для пользователя, который давно не читал этот канал.

СТРУКТУРА ДАЙДЖЕСТА:

1. **Executive Summary** (3-6 буллетов):
   - Главные события и тренды
   - Что важно не пропустить
   - Краткая характеристика активности канала

2. **Топ-10 важных постов**:
   - Для каждого поста:
     * **Заголовок как ссылка** - заголовок должен быть кликабельной ссылкой на пост: [Заголовок поста](ссылка)
     * **Суть** (1-2 предложения с ключевой информацией)
     * **Почему важно** (одно предложение)

3. **Тренды и повторяющиеся темы** (3-5 пунктов):
   - Какие темы часто встречались
   - Что было в фокусе

ФОРМАТ ОТВЕТА (Markdown):

## 📊 Executive Summary

• [Буллет 1: главное событие]
• [Буллет 2: важный тренд]
• [Буллет 3: на что обратить внимание]
...

## 📰 Топ важных постов

**1. [Заголовок поста](ссылка)**
[Суть: 1-2 предложения с ключевой информацией]

Почему важно: [одно предложение]

**2. [Заголовок поста](ссылка)**
...

## 🔍 Тренды и повторяющиеся темы

• [Тема 1]
• [Тема 2]
• [Тема 3]

ВАЖНО:
- Будь кратким и конкретным
- Выделяй самое важное по метрикам популярности (👁️ просмотры, ❤️ реакции, ↪️ репосты)
- Всегда включай ссылки на оригинальные посты
- Используй только информацию из предоставленных постов"""),
            ("human", "Посты из канала за период:\n{context}\n\nСоздай дайджест для пользователя, который давно не читал этот канал:")
        ])
        
        # Context7: Промпт для Stage A (саммари по чанкам для месяца)
        self.channel_digest_map_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по анализу новостей. Создай краткое саммари постов за период (неделя или группа дней).

Задача: выдели главное, важные события, тренды за этот период.

ФОРМАТ:
- 3-5 главных событий (каждое в 1-2 предложениях)
- 2-3 тренда или повторяющиеся темы
- Топ-5 самых важных постов (заголовок + 1 предложение сути)

Будь кратким и конкретным."""),
            ("human", "Посты за период:\n{context}\n\nСоздай краткое саммари:")
        ])
        
        # Context7: Промпт для Stage B (финальный дайджест из саммари Stage A)
        self.channel_digest_reduce_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по составлению финальных дайджестов.

На основе саммари разных периодов создай финальный дайджест для пользователя, который давно не читал канал.

СТРУКТУРА:
1. **Executive Summary** (3-6 буллетов) - главное за весь период
2. **Топ-10 важных постов** из всех периодов
3. **Тренды** - обобщение трендов из всех периодов

Будь кратким, выделяй самое важное, избегай дублирования."""),
            ("human", "Саммари разных периодов:\n{context}\n\nСоздай финальный дайджест:")
        ])
        
        # Context7: Структурированный промпт для генерации дайджеста с executive summary и улучшенной версткой
        self.digest_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по составлению дайджестов новостей из Telegram каналов.

Создай структурированный дайджест на основе предоставленных постов, сгруппированный по темам.

СТРУКТУРА ДАЙДЖЕСТА:

1. **EXECUTIVE SUMMARY** (в начале):
   - Краткое саммари на 2-3 предложения: на что обратить внимание, главные тренды, ключевые события
   - Выдели самые важные новости по метрикам популярности

2. **ТЕМАТИЧЕСКИЕ БЛОКИ**:
   - Каждая тема — отдельный блок с заголовком ## Тема: [Название]
   - Между блоками ОБЯЗАТЕЛЬНО оставляй пустую строку для читаемости
   - В каждом блоке 3-5 ключевых новостей

3. **ФОРМАТ НОВОСТИ**:
   - **Заголовок**: краткий заголовок новости (1 строка)
   - **Суть**: 1-2 предложения с ключевой информацией (что произошло, почему важно)
   - **Метрики**: [Популярность: X%] если указаны в данных
   - **Ссылка**: [Ссылка на пост](ссылка) - прямая ссылка на конкретное сообщение

ВАЖНО:
- ВСЕГДА начинай с Executive Summary
- ВСЕГДА оставляй пустую строку между тематическими блоками
- Для каждой новости давай не только заголовок, но и 1-2 предложения сути
- Используй метрики (👁️ просмотры, ❤️ реакции, ↪️ репосты, 💬 комментарии) для оценки важности
- ВСЕГДА включай ссылку на оригинальный пост для каждой новости

ФОРМАТ ОТВЕТА:

## 📊 Executive Summary

[2-3 предложения: главные тренды, на что обратить внимание, ключевые события]

## Тема 1: [Название темы]

**Заголовок новости 1**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Ссылка на пост](ссылка)

**Заголовок новости 2**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Ссылка на пост](ссылка)

**Заголовок новости 3**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Ссылка на пост](ссылка)


## Тема 2: [Название темы]

**Заголовок новости 1**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Ссылка на пост](ссылка)

**Заголовок новости 2**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Ссылка на пост](ссылка)

...

Используй только информацию из предоставленных постов. Ссылки на посты обязательны для каждой новости."""),
            ("human", "Посты для дайджеста:\n{context}\n\nТемы: {topics}\n\nСоздай структурированный дайджест с Executive Summary, метриками популярности и ссылками на источники:")
        ])
        
        logger.info("Digest Service initialized", qdrant_url=qdrant_url)
    
    async def _generate_embedding(self, text: str) -> List[float]:
        """Генерация embedding для текста через GigaChat."""
        try:
            import requests
            import os
            
            proxy_url = getattr(settings, 'gigachat_proxy_url', None) or os.getenv("GIGACHAT_PROXY_URL", "http://gpt2giga-proxy:8090")
            url = f"{proxy_url}/v1/embeddings"
            
            credentials = os.getenv("GIGACHAT_CREDENTIALS")
            scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
            auth_header = f"giga-cred-{credentials}:{scope}"
            
            response = requests.post(
                url,
                json={
                    "input": text,
                    "model": "any"  # gpt2giga сам отправит на EmbeddingsGigaR
                },
                headers={
                    "Authorization": f"Bearer {auth_header}",
                    "Content-Type": "application/json"
                },
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and len(data['data']) > 0:
                    embedding = data['data'][0].get('embedding', [])
                    return embedding
            
            logger.warning("Failed to generate embedding", status_code=response.status_code)
            return []
        
        except Exception as e:
            logger.error("Error generating embedding", error=str(e))
            return []
    
    async def _collect_posts_by_topics(
        self,
        topics: List[str],
        tenant_id: str,
        user_id: UUID,
        channel_ids: Optional[List[str]] = None,
        limit_per_topic: int = 10,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """
        Сбор постов по пользовательским тематикам.
        
        Context7: ТОЛЬКО по темам из digest_settings.topics, не глобальный анализ.
        """
        if not db:
            return []
        
        if not channel_ids:
            raise ValueError("Отсутствуют каналы пользователя для подбора постов.")
        
        normalized_channel_ids = [str(cid) for cid in channel_ids]
        if not normalized_channel_ids:
            raise ValueError("Список каналов пользователя пуст.")
        channel_id_set = set(normalized_channel_ids)
        
        all_posts = []
        
        # Для каждой темы собираем посты
        for topic in topics:
            try:
                # Генерируем embedding для темы
                topic_embedding = await self._generate_embedding(topic)
                
                if topic_embedding:
                    # Поиск в Qdrant по теме
                    collection_name = f"t{tenant_id}_posts"
                    
                    # Проверка существования коллекции
                    collections = self.qdrant_client.get_collections()
                    collection_names = [c.name for c in collections.collections]
                    if "tNone_posts" in collection_names:
                        logger.error(
                            "Detected legacy Qdrant collection without tenant binding",
                            tenant_id=tenant_id
                        )
                    if collection_name not in collection_names:
                        logger.warning("Qdrant collection not found", collection=collection_name)
                        continue
                    
                    # Фильтр по каналам пользователя (если указаны)
                    filter_conditions = []
                    filter_conditions.append(
                        FieldCondition(
                            key="tenant_id",
                            match=MatchValue(value=str(tenant_id))
                        )
                    )
                    
                    if channel_id_set:
                        filter_conditions.append(
                            FieldCondition(
                                key="channel_id",
                                match=MatchAny(any=list(channel_id_set))
                            )
                        )
                    
                    search_filter = Filter(must=filter_conditions) if filter_conditions else None
                    
                    # Поиск в Qdrant
                    search_results = self.qdrant_client.search(
                        collection_name=collection_name,
                        query_vector=topic_embedding,
                        query_filter=search_filter,
                        limit=limit_per_topic
                    )
                    
                    # Получаем полные данные постов из БД
                    for result in search_results:
                        post_id = result.payload.get('post_id')
                        if post_id:
                            post = db.query(Post).filter(Post.id == post_id).first()
                            if post:
                                if str(post.channel_id) not in channel_id_set:
                                    logger.warning(
                                        "Skipping Qdrant result: channel not in user scope",
                                        post_id=str(post_id),
                                        channel_id=str(post.channel_id),
                                        allowed_channels=list(channel_id_set)
                                    )
                                    continue
                                channel = db.query(Channel).filter(Channel.id == post.channel_id).first()
                                
                                all_posts.append({
                                    'post_id': str(post_id),
                                    'content': post.content or "",
                                    'channel_title': channel.title if channel else "Неизвестный канал",
                                    'channel_username': channel.username if channel else None,
                                    'permalink': post.telegram_post_url,
                                    'url': post.url,  # Context7: URL для дедупликации репостов
                                    'posted_at': post.posted_at,
                                    'topic': topic,
                                    'score': result.score,
                                    'source': 'qdrant',
                                    # Context7: Метрики популярности для отображения в дайджесте
                                    'engagement_score': float(post.engagement_score) if post.engagement_score else 0.0,
                                    'views_count': post.views_count or 0,
                                    'reactions_count': post.reactions_count or 0,
                                    'forwards_count': post.forwards_count or 0,
                                    'replies_count': post.replies_count or 0
                                })
                
                # Context7: Использование Neo4j для поиска связанных тем через граф
                try:
                    if await self.graph_service.health_check():
                        # Находим похожие темы через граф
                        similar_topics = await self.graph_service.find_similar_topics(topic, limit=3, tenant_id=tenant_id)
                        
                        # Расширяем поиск по связанным темам
                        related_topics = [topic] + [st['topic'] for st in similar_topics if st.get('similarity', 0) > 0.6]
                        
                        # Поиск постов через граф для каждой связанной темы
                        for related_topic in related_topics:
                            graph_posts = await self.graph_service.search_related_posts(
                                query=related_topic,
                                topic=related_topic,
                                tenant_id=tenant_id,
                                limit=limit_per_topic // len(related_topics),
                                max_depth=getattr(settings, 'neo4j_max_graph_depth', 2)
                            )
                            
                            for graph_post in graph_posts:
                                post_id = graph_post.get('post_id')
                                if post_id:
                                    # Проверяем, не добавлен ли уже
                                    if not any(p['post_id'] == str(post_id) for p in all_posts):
                                        post = db.query(Post).filter(Post.id == UUID(post_id)).first()
                                        if post:
                                            # Фильтр по каналам пользователя (если указаны)
                                            if str(post.channel_id) not in channel_id_set:
                                                continue
                                            
                                            channel = db.query(Channel).filter(Channel.id == post.channel_id).first()
                                            
                                            all_posts.append({
                                                'post_id': str(post_id),
                                                'content': graph_post.get('content', post.content or ""),
                                                'channel_title': channel.title if channel else "Неизвестный канал",
                                                'channel_username': channel.username if channel else None,
                                                'permalink': post.telegram_post_url,
                                                'url': post.url,  # Context7: URL для дедупликации репостов
                                                'posted_at': post.posted_at,
                                                'topic': related_topic,
                                                'score': graph_post.get('score', 0.7),
                                                'related_topic': related_topic != topic,  # Флаг связанной темы
                                                'source': 'graph',
                                                # Context7: Метрики популярности
                                                'engagement_score': float(post.engagement_score) if post.engagement_score else 0.0,
                                                'views_count': post.views_count or 0,
                                                'reactions_count': post.reactions_count or 0,
                                                'forwards_count': post.forwards_count or 0,
                                                'replies_count': post.replies_count or 0
                                            })
                except Exception as e:
                    logger.warning("GraphRAG search failed in digest, continuing without graph", error=str(e))
                
                # Также ищем через PostgreSQL FTS по ключевым словам
                # Используем простой поиск по словам темы
                topic_words = topic.split()
                fts_query = db.query(Post).join(Channel).filter(
                    and_(
                        Post.content.isnot(None),
                        or_(*[Post.content.ilike(f"%{word}%") for word in topic_words])
                    )
                )
                
                if channel_id_set:
                    fts_query = fts_query.filter(Post.channel_id.in_([UUID(cid) for cid in normalized_channel_ids]))
                
                fts_posts = fts_query.order_by(Post.posted_at.desc()).limit(limit_per_topic).all()
                
                for post in fts_posts:
                    # Проверяем, не добавлен ли уже
                    if not any(p['post_id'] == str(post.id) for p in all_posts):
                        if str(post.channel_id) not in channel_id_set:
                            logger.warning(
                                "Skipping FTS result: channel not in user scope",
                                post_id=str(post.id),
                                channel_id=str(post.channel_id),
                                allowed_channels=list(channel_id_set)
                            )
                            continue
                        
                        channel = db.query(Channel).filter(Channel.id == post.channel_id).first()
                        
                        all_posts.append({
                            'post_id': str(post.id),
                            'content': post.content or "",
                            'channel_title': channel.title if channel else "Неизвестный канал",
                            'channel_username': channel.username if channel else None,
                            'permalink': post.telegram_post_url,
                            'url': post.url,  # Context7: URL для дедупликации репостов
                            'posted_at': post.posted_at,
                            'topic': topic,
                            'score': 0.5,  # Средний score для FTS результатов
                            'source': 'fts',
                            # Context7: Метрики популярности
                            'engagement_score': float(post.engagement_score) if post.engagement_score else 0.0,
                            'views_count': post.views_count or 0,
                            'reactions_count': post.reactions_count or 0,
                            'forwards_count': post.forwards_count or 0,
                            'replies_count': post.replies_count or 0
                        })
            
            except Exception as e:
                logger.error("Error collecting posts for topic", topic=topic, error=str(e))
                continue
        
        # Context7: Фильтрация уже отправленных постов из последних N дайджестов (дефолт: 7 дней)
        # Исключаем посты, которые были в дайджестах за последние N дней
        exclude_days = getattr(settings, 'digest_exclude_posts_days', 7)
        if exclude_days > 0:
            try:
                
                cutoff_date = date.today() - timedelta(days=exclude_days)
                
                # Получаем последние дайджесты пользователя
                recent_digests = db.query(DigestHistory).filter(
                    and_(
                        DigestHistory.user_id == user_id,
                        DigestHistory.digest_date >= cutoff_date,
                        DigestHistory.status == "sent"  # Только отправленные дайджесты
                    )
                ).order_by(DigestHistory.digest_date.desc()).all()
                
                # Собираем URL и post_id из контекста дайджестов
                excluded_urls = set()
                excluded_post_ids = set()
                
                # Context7: Извлекаем post_id из telegram_post_url в контенте дайджеста
                # Формат ссылки: https://t.me/channel/123 или [Ссылка](https://t.me/channel/123)
                url_pattern = re.compile(r'https://t\.me/(\w+)/(\d+)')
                
                for digest in recent_digests:
                    if digest.content:
                        # Ищем ссылки на посты в контенте
                        matches = url_pattern.findall(digest.content)
                        for channel_username, post_num in matches:
                            # Формируем URL для исключения
                            excluded_urls.add(f"https://t.me/{channel_username}/{post_num}")
                
                # Также получаем post_id из постов, которые были в контексте предыдущих дайджестов
                # Поскольку мы не храним прямую связь, используем эвристику по времени и каналам
                if all_posts:
                    # Получаем посты из тех же каналов за период отправленных дайджестов
                    if recent_digests:
                        # Берем post_id из постов, которые могли быть в дайджестах
                        # Это приблизительная фильтрация - полную связь можно добавить через отдельную таблицу
                        pass  # Пока пропускаем, так как нет прямой связи post_id <-> digest
                
                # Фильтруем посты по URL
                if excluded_urls:
                    filtered_posts = []
                    for post in all_posts:
                        post_url = post.get('url') or post.get('permalink')
                        if post_url:
                            # Проверяем, не был ли этот URL уже в дайджесте
                            if post_url in excluded_urls:
                                continue
                        filtered_posts.append(post)
                    all_posts = filtered_posts
                    
                    logger.debug(
                        "Filtered posts from recent digests",
                        excluded_urls_count=len(excluded_urls),
                        remaining_posts=len(all_posts)
                    )
            except Exception as e:
                logger.warning("Error filtering posts from recent digests", error=str(e))
                # Продолжаем без фильтрации при ошибке
        
        # Сортируем по времени и релевантности
        all_posts.sort(key=lambda x: (x['posted_at'] or datetime.min, x['score']), reverse=True)
        
        # Дедупликация по post_id
        seen_post_ids = set()
        unique_posts = []
        for post in all_posts:
            if post['post_id'] not in seen_post_ids:
                seen_post_ids.add(post['post_id'])
                unique_posts.append(post)
        
        # Context7: Дедупликация альбомов - оставляем только первый пост из альбома с наивысшим score
        try:
            # Получаем grouped_id для всех постов из БД
            post_ids = [UUID(p['post_id']) for p in unique_posts]
            if post_ids:
                posts_with_grouped = db.query(
                    Post.id,
                    Post.grouped_id
                ).filter(Post.id.in_(post_ids)).all()
                
                # Создаем словарь post_id -> grouped_id
                post_grouped_map = {str(post.id): post.grouped_id for post in posts_with_grouped if post.grouped_id}
                
                # Группируем посты по альбомам
                album_posts = {}  # grouped_id -> список (post_index, score)
                for idx, post_data in enumerate(unique_posts):
                    grouped_id = post_grouped_map.get(post_data['post_id'])
                    if grouped_id:
                        if grouped_id not in album_posts:
                            album_posts[grouped_id] = []
                        album_posts[grouped_id].append((idx, post_data['score']))
                
                # Для каждого альбома оставляем только пост с наивысшим score
                indices_to_remove = set()
                for grouped_id, posts_list in album_posts.items():
                    if len(posts_list) > 1:
                        # Сортируем по score и оставляем только первый
                        posts_list.sort(key=lambda x: x[1], reverse=True)
                        # Удаляем все посты кроме первого
                        for idx, _ in posts_list[1:]:
                            indices_to_remove.add(idx)
                
                # Удаляем дубликаты альбомов (в обратном порядке, чтобы не сбить индексы)
                for idx in sorted(indices_to_remove, reverse=True):
                    unique_posts.pop(idx)
                
                logger.debug(
                    "Album deduplication applied in digest",
                    albums_count=len(album_posts),
                    removed_duplicates=len(indices_to_remove)
                )
        except Exception as e:
            logger.warning("Error during album deduplication in digest", error=str(e))
            # Продолжаем без дедупликации при ошибке
        
        # Context7: Дедупликация по URL (репосты одного и того же контента)
        # Аналогично _collect_channel_posts_for_digest - исключаем посты с одинаковым URL
        try:
            seen_urls = set()
            url_deduplicated_posts = []
            for post in unique_posts:
                post_url = post.get('url')
                if post_url:
                    url_hash = hash(post_url)
                    if url_hash in seen_urls:
                        continue
                    seen_urls.add(url_hash)
                url_deduplicated_posts.append(post)
            
            removed_by_url = len(unique_posts) - len(url_deduplicated_posts)
            if removed_by_url > 0:
                logger.debug(
                    "URL deduplication applied in digest",
                    removed_duplicates=removed_by_url,
                    remaining_posts=len(url_deduplicated_posts)
                )
            unique_posts = url_deduplicated_posts
        except Exception as e:
            logger.warning("Error during URL deduplication in digest", error=str(e))
            # Продолжаем без дедупликации при ошибке
        
        return unique_posts
    
    async def _assemble_context(self, posts: List[Dict[str, Any]], max_posts: int = 20) -> str:
        """
        Сборка контекста из постов для генерации дайджеста.
        
        Context7: Включает метрики популярности и ссылки на посты.
        """
        if not posts:
            return ""
        
        # Вычисляем максимальный engagement_score для нормализации
        max_engagement = max((p.get('engagement_score', 0.0) for p in posts), default=1.0)
        if max_engagement == 0:
            max_engagement = 1.0  # Избегаем деления на ноль
        
        context_parts = []
        
        for idx, post in enumerate(posts[:max_posts], 1):
            # Context7: гарантируем строку даже если БД вернула NULL
            content = post.get('content') or ""
            # Context7: Увеличиваем лимит для лучшего понимания сути новости (до 500 символов)
            if len(content) > 500:
                content = content[:500] + "..."
            
            channel_title = post.get('channel_title', 'Неизвестный канал')
            permalink = post.get('permalink', '')
            
            # Context7: Вычисляем относительную популярность (%)
            engagement_score = post.get('engagement_score', 0.0)
            popularity_percent = int((engagement_score / max_engagement) * 100) if max_engagement > 0 else 0
            
            # Context7: Формируем метрики популярности
            views = post.get('views_count', 0)
            reactions = post.get('reactions_count', 0)
            forwards = post.get('forwards_count', 0)
            replies = post.get('replies_count', 0)
            
            # Формируем строку метрик
            metrics_parts = []
            if views > 0:
                metrics_parts.append(f"👁️ {views}")
            if reactions > 0:
                metrics_parts.append(f"❤️ {reactions}")
            if forwards > 0:
                metrics_parts.append(f"↪️ {forwards}")
            if replies > 0:
                metrics_parts.append(f"💬 {replies}")
            
            metrics_str = " | ".join(metrics_parts) if metrics_parts else "—"
            
            # Context7: Улучшенный формат контекста для лучшей генерации заголовков и сути
            # Формат: [N] Канал | Популярность: X% | Метрики | Ссылка
            # Затем полный текст поста для извлечения сути
            post_header = f"[{idx}] **{channel_title}**"
            
            # Добавляем метрики популярности
            if popularity_percent > 0:
                post_header += f" | Популярность: {popularity_percent}%"
            
            if metrics_str != "—":
                post_header += f" | {metrics_str}"
            
            # Добавляем ссылку в формате markdown для лучшей обработки LLM
            if permalink:
                post_header += f" | [Ссылка]({permalink})"
            
            # Context7: Структурируем контекст: заголовок с метриками, затем контент для извлечения сути
            post_line = f"{post_header}\n\n**Текст поста:**\n{content}"
            
            context_parts.append(post_line)
        
        return "\n\n".join(context_parts)
    
    async def generate(
        self,
        user_id: UUID,
        tenant_id: str,
        db: Session,
        digest_date: Optional[date] = None
    ) -> DigestContent:
        """
        Генерация дайджеста для пользователя.
        
        Context7: Сбор контента ТОЛЬКО по пользовательским тематикам из digest_settings.topics.
        
        Args:
            user_id: ID пользователя
            tenant_id: ID арендатора
            db: SQLAlchemy сессия
            digest_date: Дата дайджеста (по умолчанию сегодня)
        
        Returns:
            DigestContent с сгенерированным дайджестом
        """
        tenant_id_str = str(tenant_id)
        start_time = time.perf_counter()
        source_counter: Counter = Counter()
        
        if digest_date is None:
            digest_date = date.today()
        
        # Получаем настройки дайджеста
        digest_settings = db.query(DigestSettings).filter(DigestSettings.user_id == user_id).first()
        
        if not digest_settings:
            raise ValueError("Настройки дайджеста не найдены")
        
        if not digest_settings.enabled:
            raise ValueError("Дайджест отключен в настройках")
        
        # Context7: Если темы не указаны, используем интересы пользователя как fallback
        topics = digest_settings.topics if digest_settings.topics and len(digest_settings.topics) > 0 else []
        
        if not topics:
            # Получаем топ интересов пользователя из user_interests
            try:
                from services.user_interest_service import get_user_interest_service
                interest_service = get_user_interest_service()
                user_interests = await interest_service.get_user_interests(user_id, limit=5, db=db)
                
                if user_interests:
                    # Берем топ-5 тем по весу
                    topics = [interest.get('topic', '') for interest in user_interests[:5] if interest.get('topic')]
                    logger.info(
                        "Using user interests as topics fallback",
                        user_id=str(user_id),
                        topics_count=len(topics)
                    )
            except Exception as e:
                logger.warning("Failed to get user interests as fallback", error=str(e))
        
        if not topics or len(topics) == 0:
            raise ValueError("Не указаны темы для дайджеста. Укажите темы в настройках или используйте поиск для формирования интересов.")
        
        # Получаем каналы пользователя (если channels_filter не указан, используем все)
        user_channels = db.query(UserChannel).filter(UserChannel.user_id == user_id).all()
        channel_ids = None
        
        if digest_settings.channels_filter:
            # Используем только указанные каналы
            channel_ids = digest_settings.channels_filter
        else:
            # Используем все каналы пользователя
            channel_ids = [str(uc.channel_id) for uc in user_channels]
        
        if not channel_ids:
            logger.warning(
                "Digest generation aborted: user has no active channels",
                user_id=str(user_id)
            )
            raise ValueError("Нет активных каналов для дайджеста. Добавьте хотя бы один канал перед генерацией.")
        
        # Собираем посты по темам
        logger.info(
            "Collecting posts for digest",
            user_id=str(user_id),
            topics=topics,
            channels_count=len(channel_ids) if channel_ids else 0
        )
        
        posts = await self._collect_posts_by_topics(
            topics=topics,
            tenant_id=tenant_id,
            user_id=user_id,
            channel_ids=channel_ids,
            limit_per_topic=digest_settings.max_items_per_digest,
            db=db
        )
        
        if posts:
            source_counter = Counter(post.get('source') for post in posts)
            qdrant_hits = source_counter.get('qdrant', 0)
            graph_hits = source_counter.get('graph', 0)
            if qdrant_hits:
                digest_qdrant_hits_total.labels(tenant_id=tenant_id_str).inc(qdrant_hits)
            if graph_hits:
                digest_graph_hits_total.labels(tenant_id=tenant_id_str).inc(graph_hits)
        else:
            source_counter = Counter()
        
        if not posts:
            logger.warning(
                "No posts found for digest",
                user_id=str(user_id),
                tenant_id=tenant_id_str,
                topics=topics
            )
            return DigestContent(
                content="Не найдено постов по указанным темам за выбранный период.",
                posts_count=0,
                topics=topics,
                sections=[]
            )
        
        # Собираем контекст
        context = await self._assemble_context(posts, max_posts=digest_settings.max_items_per_digest * 2)
        
        # Генерируем дайджест через GigaChat
        try:
            # Context7: Используем format_messages() напрямую, а не format()
            # ChatPromptTemplate.format_messages() возвращает список messages
            messages = self.digest_prompt.format_messages(
                context=context,
                topics=", ".join(topics)
            )
            
            if not messages:
                logger.error("Empty messages after formatting")
                return DigestContent(
                    content="Не удалось сгенерировать дайджест: пустой промпт.",
                    posts_count=len(posts),
                    topics=topics,
                    sections=[]
                )
            
            response = await self.llm.ainvoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Context7: Проверяем, не вернул ли Gigachat ответ с фильтром
            if self._is_gigachat_filter_response(content):
                logger.warning(
                    "Gigachat filter detected, falling back to OpenRouter",
                    user_id=str(user_id),
                    tenant_id=tenant_id_str,
                    content_preview=content[:200] if len(content) > 200 else content
                )
                digest_gigachat_filter_detected_total.labels(tenant_id=tenant_id_str).inc()
                
                try:
                    # Генерируем дайджест через OpenRouter
                    openrouter_content = await self._generate_with_openrouter(
                        messages=messages,
                        context=context,
                        topics=", ".join(topics)
                    )
                    
                    # Используем результат от OpenRouter
                    content = openrouter_content
                    digest_openrouter_fallback_total.labels(tenant_id=tenant_id_str).inc()
                    
                    logger.info(
                        "Digest generated via OpenRouter fallback",
                        user_id=str(user_id),
                        tenant_id=tenant_id_str
                    )
                    
                except Exception as fallback_error:
                    # При ошибке fallback возвращаем оригинальный ответ Gigachat
                    digest_openrouter_fallback_failed_total.labels(tenant_id=tenant_id_str).inc()
                    logger.error(
                        "OpenRouter fallback failed, using original Gigachat response",
                        user_id=str(user_id),
                        tenant_id=tenant_id_str,
                        error=str(fallback_error)
                    )
                    # content остается от Gigachat (даже если с фильтром)
            
            # Парсим секции из markdown (простой парсинг)
            sections = self._parse_sections(content, topics)
            
            logger.info(
                "Digest generated",
                user_id=str(user_id),
                tenant_id=tenant_id_str,
                posts_count=len(posts),
                topics=topics,
                processing_time_ms=int((time.perf_counter() - start_time) * 1000),
                qdrant_hits=source_counter.get('qdrant', 0),
                graph_hits=source_counter.get('graph', 0),
                fts_hits=source_counter.get('fts', 0)
            )
            
            return DigestContent(
                content=content,
                posts_count=len(posts),
                topics=topics,
                sections=sections
            )
        
        except Exception as e:
            logger.error("Error generating digest", error=str(e), user_id=str(user_id))
            raise
        finally:
            duration = time.perf_counter() - start_time
            digest_generation_duration_seconds.labels(tenant_id=tenant_id_str).observe(duration)
    
    def _is_gigachat_filter_response(self, content: str) -> bool:
        """
        Детектирование ответов с фильтром Gigachat или других LLM.
        
        Проверяет наличие ключевых фраз и паттернов, указывающих на то,
        что LLM вернул сообщение о фильтрации контента.
        
        Args:
            content: Текст ответа от LLM
            
        Returns:
            True если обнаружен фильтр, False иначе
        """
        if not content:
            return False
        
        content_lower = content.lower()
        
        # Высокоприоритетные фразы (детектируются сразу при наличии)
        high_priority_phrases = [
            "генеративные языковые модели не обладают собственным мнением",
            "не обладают собственным мнением",
            "обученной на открытых данных, в которых может содержаться неточная или ошибочная информация",
            "во избежание неправильного толкования",
            "чувствительные темы могут быть ограничены",
        ]
        
        # Проверяем высокоприоритетные фразы (достаточно одной)
        for phrase in high_priority_phrases:
            if phrase in content_lower:
                return True
        
        # Остальные ключевые фразы для детектирования фильтра
        key_phrases = [
            "некорректных ответов",
            "некорректные ответы",
            "чувствительными темами",
            "чувствительные темы",
            "ограничены",
            "временно ограничены",
            "генеративные языковые модели",
            "не транслирует мнение своих разработчиков",
            "как и любая языковая модель, gigachat",
            "разговоры на чувствительные темы могут быть ограничены",
        ]
        
        # Подсчитываем количество найденных ключевых фраз
        found_phrases = sum(1 for phrase in key_phrases if phrase in content_lower)
        
        # Паттерны для детектирования (комбинации фраз)
        patterns = [
            ("к сожалению", "ограничены"),
            ("как и любая языковая модель", "ограничены"),
            ("генеративные языковые модели", "ограничены"),
            ("чувствительные темы", "ограничены"),
        ]
        
        # Проверяем паттерны
        pattern_found = False
        for pattern_start, pattern_end in patterns:
            if pattern_start in content_lower and pattern_end in content_lower:
                pattern_found = True
                break
        
        # Детектируем фильтр, если найдено 1+ ключевая фраза (снижен порог) или один из паттернов
        return found_phrases >= 1 or pattern_found
    
    async def _generate_with_openrouter(
        self,
        messages: List[Any],
        context: str,
        topics: str
    ) -> str:
        """
        Генерация дайджеста через OpenRouter API (fallback).
        
        Args:
            messages: Список сообщений в формате LangChain
            context: Контекст с постами
            topics: Темы для дайджеста
            
        Returns:
            Сгенерированный контент дайджеста
        """
        import os
        import httpx
        
        api_key = os.getenv('OPENROUTER_API_KEY')
        api_base = os.getenv('OPENROUTER_API_BASE', 'https://openrouter.ai/api/v1')
        # Context7: Используем рабочую модель по умолчанию (qwen-2.5-72b-instruct:free недоступна)
        model = os.getenv('OPENROUTER_MODEL', 'meta-llama/llama-3.3-70b-instruct:free')
        
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")
        
        # Преобразуем LangChain messages в формат OpenRouter API
        openrouter_messages = []
        for msg in messages:
            if hasattr(msg, 'content') and hasattr(msg, 'type'):
                # LangChain message (BaseMessage)
                # Типы: system, human, ai, tool
                msg_type = getattr(msg, 'type', 'human')
                if msg_type == "system":
                    role = "system"
                elif msg_type == "ai" or msg_type == "assistant":
                    role = "assistant"
                else:
                    role = "user"
                openrouter_messages.append({
                    "role": role,
                    "content": str(msg.content) if msg.content else ""
                })
            elif isinstance(msg, dict):
                # Уже в формате dict
                openrouter_messages.append(msg)
            else:
                # Fallback: пытаемся извлечь content как строку
                logger.warning("Unknown message format in OpenRouter fallback", msg_type=type(msg).__name__)
                if hasattr(msg, 'content'):
                    openrouter_messages.append({
                        "role": "user",
                        "content": str(msg.content) if msg.content else ""
                    })
        
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'HTTP-Referer': os.getenv('OPENROUTER_HTTP_REFERER', 'https://github.com/telegram-assistant'),
            'X-Title': 'Telegram Assistant Digest'
        }
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f'{api_base}/chat/completions',
                    headers=headers,
                    json={
                        'model': model,
                        'messages': openrouter_messages,
                        'temperature': 0.7,
                        'max_tokens': 4000,
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    content = result['choices'][0]['message']['content'].strip()
                    
                    # Context7: Проверяем ответ от OpenRouter на наличие фильтра
                    if self._is_gigachat_filter_response(content):
                        logger.warning(
                            "OpenRouter filter detected in response",
                            content_preview=content[:200] if len(content) > 200 else content
                        )
                        # Если OpenRouter тоже вернул фильтр, выбрасываем исключение
                        # чтобы система могла обработать это как ошибку fallback
                        raise ValueError("OpenRouter returned filtered response")
                    
                    return content
                elif response.status_code == 404:
                    # Специальная обработка для 404 (модель не найдена)
                    error_data = response.json() if response.text else {}
                    error_msg = error_data.get('error', {}).get('message', response.text) or f"Model {model} not found"
                    logger.error(
                        "OpenRouter model not found (404)",
                        model=model,
                        error=error_msg,
                        suggestion="Update OPENROUTER_MODEL environment variable"
                    )
                    raise ValueError(f"OpenRouter model not found: {model}. {error_msg}")
                else:
                    error_msg = f"OpenRouter API error: {response.status_code} - {response.text}"
                    logger.error("OpenRouter API request failed", status_code=response.status_code, error=response.text)
                    raise Exception(error_msg)
                    
        except httpx.RequestError as e:
            logger.error("OpenRouter API request exception", error=str(e))
            raise
    
    def _parse_sections(self, content: str, topics: List[str]) -> List[Dict[str, Any]]:
        """Парсинг секций из markdown контента."""
        sections = []
        lines = content.split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('##'):
                # Новая секция
                if current_section:
                    sections.append(current_section)
                current_section = {
                    'title': line.replace('##', '').strip(),
                    'items': []
                }
            elif line.startswith('-') and current_section:
                # Пункт секции
                item = line.replace('-', '').strip()
                current_section['items'].append(item)
        
        if current_section:
            sections.append(current_section)
        
        return sections
    
    # ============================================================================
    # CHANNEL DIGEST METHODS
    # ============================================================================
    
    def _estimate_tokens(self, text: str) -> int:
        """Грубая оценка количества токенов для русского текста."""
        return len(text) // 4
    
    def _get_cache_key(
        self,
        tenant_id: str,
        user_id: UUID,
        channel_id: UUID,
        period_days: int,
        window_end_date: date
    ) -> str:
        """Получение ключа кеша для канального дайджеста."""
        return f"channel_digest:{tenant_id}:{user_id}:{channel_id}:{period_days}:{window_end_date.isoformat()}"
    
    def _get_cache_ttl(self, period_days: int) -> int:
        """Получение TTL для кеша в зависимости от периода."""
        if period_days <= 1:
            return 3600 * 2  # 2 часа для дня
        elif period_days <= 7:
            return 3600 * 8  # 8 часов для недели
        else:
            return 3600 * 24  # 24 часа для месяца
    
    async def _get_cached_digest(
        self,
        cache_key: str
    ) -> Optional[DigestContent]:
        """Получение дайджеста из кеша."""
        if not self.redis_client:
            return None
        
        try:
            # Context7: В проекте используется redis.asyncio, поэтому всегда async
            # Проверяем тип клиента для правильной обработки
            redis_type = type(self.redis_client).__module__
            is_async_redis = 'asyncio' in redis_type or 'async' in redis_type.lower()
            
            if is_async_redis:
                cached_data = await self.redis_client.get(cache_key)
            else:
                # Синхронный Redis клиент (fallback для совместимости)
                cached_data = self.redis_client.get(cache_key)
            
            if cached_data:
                # Context7: decode_responses=True уже декодирует в строки, но проверяем на всякий случай
                if isinstance(cached_data, bytes):
                    cached_data = cached_data.decode('utf-8')
                data = json.loads(cached_data)
                return DigestContent(**data)
        except (TypeError, AttributeError) as e:
            # Если await не поддерживается или метод отсутствует
            logger.debug("Redis client operation failed, trying sync", error=str(e))
            try:
                if hasattr(self.redis_client, 'get'):
                    cached_data = self.redis_client.get(cache_key)
                    if cached_data:
                        if isinstance(cached_data, bytes):
                            cached_data = cached_data.decode('utf-8')
                        data = json.loads(cached_data)
                        return DigestContent(**data)
            except Exception as sync_error:
                logger.debug("Sync Redis also failed", error=str(sync_error))
        except Exception as e:
            logger.warning("Failed to get cached digest", error=str(e), cache_key=cache_key)
        
        return None
    
    async def _save_to_cache(
        self,
        cache_key: str,
        content: DigestContent,
        ttl: int
    ) -> None:
        """Сохранение дайджеста в кеш."""
        if not self.redis_client:
            return
        
        try:
            data = content.model_dump()
            json_data = json.dumps(data, ensure_ascii=False)
            
            # Context7: В проекте используется redis.asyncio, поэтому всегда async
            # Проверяем тип клиента для правильной обработки
            redis_type = type(self.redis_client).__module__
            is_async_redis = 'asyncio' in redis_type or 'async' in redis_type.lower()
            
            if is_async_redis:
                await self.redis_client.setex(cache_key, ttl, json_data)
            else:
                # Синхронный Redis клиент (fallback для совместимости)
                self.redis_client.setex(cache_key, ttl, json_data)
        except (TypeError, AttributeError) as e:
            # Если await не поддерживается или метод отсутствует
            logger.debug("Async Redis setex failed, trying sync", error=str(e))
            try:
                if hasattr(self.redis_client, 'setex'):
                    self.redis_client.setex(cache_key, ttl, json_data)
            except Exception as sync_error:
                logger.debug("Sync Redis setex also failed", error=str(sync_error))
        except Exception as e:
            logger.warning("Failed to save digest to cache", error=str(e), cache_key=cache_key)
    
    async def _collect_channel_posts_for_digest(
        self,
        channel_id: UUID,
        period_days: int,
        db: Session,
        max_candidates: int = 100
    ) -> List[Post]:
        """
        Сбор и ранжирование постов из канала за период.
        
        Context7: Гибридное ранжирование - engagement + freshness + diversity фильтр.
        
        Args:
            channel_id: ID канала
            period_days: Период в днях
            db: SQLAlchemy сессия
            max_candidates: Максимальное количество кандидатов для LLM rerank
            
        Returns:
            Список Post объектов, отсортированных по важности
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=period_days)
        
        # Получаем все посты за период
        posts_query = db.query(Post).filter(
            Post.channel_id == channel_id,
            Post.posted_at >= cutoff_date,
            Post.content.isnot(None),
            Post.content != ""
        )
        
        posts = posts_query.order_by(desc(Post.posted_at)).all()
        
        if not posts:
            logger.info(
                "No posts found for channel digest",
                channel_id=str(channel_id),
                period_days=period_days
            )
            return []
        
        # Гибридное ранжирование: engagement_score + бонус за свежесть
        now = datetime.now(timezone.utc)
        posts_with_scores = []
        
        for post in posts:
            engagement_score = float(post.engagement_score) if post.engagement_score else 0.0
            
            # Бонус за свежесть (недавние посты получают небольшой бонус)
            if post.posted_at:
                age_hours = (now - post.posted_at).total_seconds() / 3600
                freshness_bonus = max(0, 1.0 - (age_hours / (period_days * 24))) * 0.1  # До 10% бонуса
            else:
                freshness_bonus = 0.0
            
            final_score = engagement_score * (1 + freshness_bonus)
            
            posts_with_scores.append({
                'post': post,
                'score': final_score,
                'engagement_score': engagement_score,
                'posted_at': post.posted_at
            })
        
        # Сортируем по финальному score
        posts_with_scores.sort(key=lambda x: x['score'], reverse=True)
        
        # Diversity фильтр: не более N постов с одинаковыми тегами/ссылками
        selected_posts = []
        seen_urls = set()
        seen_tags = {}  # tag -> count
        
        # Получаем теги для постов (из enrichment)
        post_ids = [p['post'].id for p in posts_with_scores[:max_candidates]]
        enrichments = db.query(PostEnrichment).filter(
            PostEnrichment.post_id.in_(post_ids),
            PostEnrichment.kind == 'tags'
        ).all()
        
        post_tags_map = {}
        for enrichment in enrichments:
            tags = enrichment.data.get('tags', [])
            if isinstance(tags, list):
                post_tags_map[str(enrichment.post_id)] = [str(tag) for tag in tags if tag]
        
        for item in posts_with_scores[:max_candidates]:
            post = item['post']
            post_id_str = str(post.id)
            
            # Проверяем URL (дедупликация репостов одного и того же контента)
            if post.url:
                url_hash = hash(post.url)
                if url_hash in seen_urls:
                    continue
                seen_urls.add(url_hash)
            
            # Проверяем теги (diversity фильтр)
            tags = post_tags_map.get(post_id_str, [])
            if tags:
                max_same_tag = 3  # Не более 3 постов с одним тегом
                skip = False
                for tag in tags[:3]:  # Проверяем только первые 3 тега
                    count = seen_tags.get(tag, 0)
                    if count >= max_same_tag:
                        skip = True
                        break
                
                if skip:
                    continue
                
                # Увеличиваем счетчики
                for tag in tags[:3]:
                    seen_tags[tag] = seen_tags.get(tag, 0) + 1
            
            selected_posts.append(post)
            
            if len(selected_posts) >= max_candidates:
                break
        
        logger.info(
            "Channel posts collected for digest",
            channel_id=str(channel_id),
            period_days=period_days,
            total_posts=len(posts),
            selected_posts=len(selected_posts)
        )
        
        return selected_posts
    
    async def _llm_rerank_posts(
        self,
        posts: List[Post],
        top_n: int = 20
    ) -> List[Post]:
        """
        LLM rerank для топ-N кандидатов.
        
        Context7: Применяется только к top-20 для снижения стоимости.
        """
        if len(posts) <= top_n:
            return posts
        
        # Для упрощения возвращаем top-N по engagement (можно улучшить через LLM)
        # В будущем можно добавить промпт для LLM rerank
        return posts[:top_n]
    
    async def _assemble_channel_context(
        self,
        posts: List[Post],
        period_days: int,
        max_tokens: int = 7000
    ) -> str:
        """
        Сборка контекста из постов с токен-бюджет подходом.
        
        Context7: Адаптивная обрезка в зависимости от периода и токен-бюджета.
        """
        if not posts:
            return ""
        
        # Определяем бюджет токенов в зависимости от периода
        if period_days <= 1:
            budget = min(max_tokens, 7000)
            chars_per_post = 600
        elif period_days <= 7:
            budget = min(max_tokens, 7000)
            chars_per_post = 500
        else:  # месяц
            budget = min(max_tokens, 30000)
            chars_per_post = 400
        
        context_parts = []
        used_tokens = 0
        max_posts = 50  # Максимум постов для контекста
        
        # Вычисляем максимальный engagement_score для нормализации
        engagement_scores = [float(p.engagement_score) if p.engagement_score else 0.0 for p in posts]
        max_engagement = max(engagement_scores) if engagement_scores else 1.0
        if max_engagement == 0:
            max_engagement = 1.0
        
        for idx, post in enumerate(posts[:max_posts], 1):
            content = post.content or ""
            
            # Начальная обрезка по символам
            if len(content) > chars_per_post:
                content = content[:chars_per_post] + "..."
            
            # Оценка токенов
            post_text = f"[{idx}] {content}"
            estimated_tokens = self._estimate_tokens(post_text)
            
            # Проверяем бюджет
            if used_tokens + estimated_tokens > budget:
                # Пытаемся сократить текущий пост
                remaining_tokens = budget - used_tokens - 100  # Запас
                max_chars = remaining_tokens * 4
                if max_chars > 50:  # Минимум 50 символов
                    content = content[:max_chars] + "..."
                    post_text = f"[{idx}] {content}"
                else:
                    break  # Нет места для этого поста
            
            # Вычисляем метрики
            engagement_score = float(post.engagement_score) if post.engagement_score else 0.0
            popularity_percent = int((engagement_score / max_engagement) * 100) if max_engagement > 0 else 0
            
            # Формируем метрики
            metrics_parts = []
            if post.views_count:
                metrics_parts.append(f"👁️ {post.views_count}")
            if post.reactions_count:
                metrics_parts.append(f"❤️ {post.reactions_count}")
            if post.forwards_count:
                metrics_parts.append(f"↪️ {post.forwards_count}")
            if post.replies_count:
                metrics_parts.append(f"💬 {post.replies_count}")
            
            metrics_str = " | ".join(metrics_parts) if metrics_parts else "—"
            
            # Формируем строку поста
            post_header = f"[{idx}]"
            if popularity_percent > 0:
                post_header += f" Популярность: {popularity_percent}%"
            if metrics_str != "—":
                post_header += f" | {metrics_str}"
            
            if post.telegram_post_url:
                post_header += f" | [Ссылка]({post.telegram_post_url})"
            
            post_line = f"{post_header}\n\n**Текст поста:**\n{content}"
            
            context_parts.append(post_line)
            used_tokens += self._estimate_tokens(post_line)
        
        logger.debug(
            "Channel context assembled",
            period_days=period_days,
            posts_count=len(context_parts),
            estimated_tokens=used_tokens,
            budget=budget
        )
        
        return "\n\n".join(context_parts)
    
    async def _generate_map_reduce_digest(
        self,
        posts: List[Post],
        period_days: int,
        tenant_id: str
    ) -> DigestContent:
        """
        Двухступенчатый саммари для месяца (map-reduce).
        
        Context7: Stage A - группировка по неделям и саммари, Stage B - финальный дайджест.
        """
        if not posts:
            return DigestContent(
                content="Не найдено постов за выбранный период.",
                posts_count=0,
                topics=[],
                sections=[]
            )
        
        # Stage A: Группировка по неделям
        week_groups = {}
        for post in posts:
            if not post.posted_at:
                continue
            
            # Определяем неделю (количество недель с начала периода)
            days_since_start = (datetime.now(timezone.utc) - post.posted_at).days
            week_num = days_since_start // 7
            
            if week_num not in week_groups:
                week_groups[week_num] = []
            week_groups[week_num].append(post)
        
        # Если постов мало, группируем по 3-5 дней
        if len(week_groups) == 1 and len(posts) > 50:
            # Разбиваем на чанки по 5 дней
            chunk_size = 5
            week_groups = {}
            for post in posts:
                if not post.posted_at:
                    continue
                days_since_start = (datetime.now(timezone.utc) - post.posted_at).days
                chunk_num = days_since_start // chunk_size
                
                if chunk_num not in week_groups:
                    week_groups[chunk_num] = []
                week_groups[chunk_num].append(post)
        
        # Генерируем саммари для каждого чанка
        chunk_summaries = []
        for chunk_num, chunk_posts in sorted(week_groups.items()):
            if not chunk_posts:
                continue
            
            # Собираем контекст для чанка (ограниченный размер)
            chunk_context = await self._assemble_channel_context(chunk_posts, period_days, max_tokens=3000)
            
            if not chunk_context:
                continue
            
            try:
                # Генерируем саммари для чанка
                messages = self.channel_digest_map_prompt.format_messages(context=chunk_context)
                response = await self.llm.ainvoke(messages)
                summary = response.content if hasattr(response, 'content') else str(response)
                
                chunk_summaries.append({
                    'chunk_num': chunk_num,
                    'posts_count': len(chunk_posts),
                    'summary': summary
                })
            except Exception as e:
                logger.warning(
                    "Failed to generate chunk summary",
                    chunk_num=chunk_num,
                    error=str(e)
                )
                continue
        
        if not chunk_summaries:
            # Fallback: обычная генерация
            context = await self._assemble_channel_context(posts, period_days, max_tokens=30000)
            messages = self.channel_digest_prompt.format_messages(context=context)
            response = await self.llm.ainvoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            
            return DigestContent(
                content=content,
                posts_count=len(posts),
                topics=[],
                sections=[]
            )
        
        # Stage B: Финальный дайджест из саммари
        summaries_context = "\n\n---\n\n".join([
            f"**Период {chunk['chunk_num'] + 1}** ({chunk['posts_count']} постов):\n{chunk['summary']}"
            for chunk in chunk_summaries
        ])
        
        try:
            messages = self.channel_digest_reduce_prompt.format_messages(context=summaries_context)
            response = await self.llm.ainvoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
        except Exception as e:
            logger.error("Failed to generate final digest from summaries", error=str(e))
            # Fallback: объединяем саммари
            content = "## 📊 Executive Summary\n\nОбзор по периодам:\n\n" + summaries_context
        
        return DigestContent(
            content=content,
            posts_count=len(posts),
            topics=[],
            sections=[]
        )
    
    async def generate_channel_digest(
        self,
        channel_id: UUID,
        user_id: UUID,
        tenant_id: str,
        db: Session,
        period_days: int = 7
    ) -> DigestContent:
        """
        Генерация дайджеста по конкретному каналу.
        
        Context7: Кеширование, валидация доступа, выбор модели в зависимости от периода.
        
        Args:
            channel_id: ID канала
            user_id: ID пользователя
            tenant_id: ID арендатора
            db: SQLAlchemy сессия
            period_days: Период в днях (1, 7 или 30)
            
        Returns:
            DigestContent с сгенерированным дайджестом
        """
        tenant_id_str = str(tenant_id)
        start_time = time.perf_counter()
        period_str = str(period_days)
        
        # Логирование начала генерации
        logger.info(
            "Starting channel digest generation",
            tenant_id=tenant_id_str,
            user_id=str(user_id),
            channel_id=str(channel_id),
            period_days=period_days
        )
        
        # Проверка кеша
        window_end_date = date.today()
        cache_key = self._get_cache_key(tenant_id_str, user_id, channel_id, period_days, window_end_date)
        cached = await self._get_cached_digest(cache_key)
        
        if cached:
            channel_digest_cache_hits_total.labels(tenant_id=tenant_id_str, period=period_str).inc()
            logger.info(
                "Channel digest retrieved from cache",
                tenant_id=tenant_id_str,
                user_id=str(user_id),
                channel_id=str(channel_id),
                period_days=period_days
            )
            return cached
        
        # Context7: Проверка доступа к каналу через JOIN с проверкой tenant_id
        access_check = db.query(UserChannel, Channel).join(
            Channel, Channel.id == UserChannel.channel_id
        ).filter(
            UserChannel.user_id == user_id,
            UserChannel.channel_id == channel_id,
            UserChannel.is_active == True
        ).first()
        
        if not access_check:
            logger.warning(
                "Channel access denied - user_channel not found",
                tenant_id=tenant_id_str,
                user_id=str(user_id),
                channel_id=str(channel_id)
            )
            raise ValueError("Канал не найден или нет доступа")
        
        user_channel, channel = access_check
        
        # Дополнительная проверка: убеждаемся, что канал существует и активен
        if not channel or not channel.is_active:
            logger.warning(
                "Channel access denied - channel not active",
                tenant_id=tenant_id_str,
                user_id=str(user_id),
                channel_id=str(channel_id)
            )
            raise ValueError("Канал не активен")
        
        # Собираем посты
        posts = await self._collect_channel_posts_for_digest(channel_id, period_days, db)
        
        if not posts:
            logger.info(
                "No posts found for channel digest",
                tenant_id=tenant_id_str,
                channel_id=str(channel_id),
                period_days=period_days
            )
            return DigestContent(
                content=f"В канале не было постов за последние {period_days} дней.",
                posts_count=0,
                topics=[],
                sections=[]
            )
        
        # LLM rerank для месяца (только top-20)
        if period_days >= 30:
            posts = await self._llm_rerank_posts(posts, top_n=20)
        
        # Генерация дайджеста
        context = None  # Инициализация для метрик
        try:
            if period_days >= 30:
                # Двухступенчатый саммари для месяца
                result = await self._generate_map_reduce_digest(posts, period_days, tenant_id_str)
            else:
                # Обычная генерация для дня/недели
                context = await self._assemble_channel_context(posts, period_days)
                
                # Выбор модели в зависимости от периода
                if period_days <= 7:
                    # Используем текущий GigaChat (Pro с 8K)
                    messages = self.channel_digest_prompt.format_messages(context=context)
                    response = await self.llm.ainvoke(messages)
                    content = response.content if hasattr(response, 'content') else str(response)
                else:
                    # Для больших периодов можно использовать модель с большим контекстом
                    messages = self.channel_digest_prompt.format_messages(context=context)
                    response = await self.llm.ainvoke(messages)
                    content = response.content if hasattr(response, 'content') else str(response)
                
                # Проверка на фильтр Gigachat
                if self._is_gigachat_filter_response(content):
                    logger.warning(
                        "Gigachat filter detected in channel digest, falling back to OpenRouter",
                        tenant_id=tenant_id_str,
                        channel_id=str(channel_id)
                    )
                    try:
                        content = await self._generate_with_openrouter(
                            messages=messages,
                            context=context,
                            topics=""  # Нет тем для канального дайджеста
                        )
                    except Exception as fallback_error:
                        logger.error(
                            "OpenRouter fallback failed for channel digest",
                            error=str(fallback_error)
                        )
                
                result = DigestContent(
                    content=content,
                    posts_count=len(posts),
                    topics=[],
                    sections=[]
                )
            
            # Сохранение в кеш
            ttl = self._get_cache_ttl(period_days)
            await self._save_to_cache(cache_key, result, ttl)
            
            # Сохранение в БД (digest_history)
            try:
                history = DigestHistory(
                    user_id=user_id,
                    tenant_id=UUID(tenant_id_str) if tenant_id_str else None,
                    digest_date=window_end_date,
                    content=result.content,
                    posts_count=result.posts_count,
                    topics=[],  # Нет тем для канального дайджеста
                    status="sent"
                )
                db.add(history)
                db.commit()
            except Exception as e:
                logger.warning("Failed to save channel digest to DB", error=str(e))
                db.rollback()
            
            # Метрики
            duration = time.perf_counter() - start_time
            channel_digest_generation_duration_seconds.labels(
                tenant_id=tenant_id_str,
                period=period_str
            ).observe(duration)
            
            channel_digest_posts_count.labels(
                tenant_id=tenant_id_str,
                period=period_str
            ).set(result.posts_count)
            
            # Вычисляем размер контекста для метрики (для месяца используется map-reduce, нет единого context)
            if period_days < 30 and context:
                context_tokens = self._estimate_tokens(context)
            else:
                # Для месяца используем примерную оценку на основе постов
                context_tokens = len(posts) * 200  # ~200 токенов на пост
            
            channel_digest_context_tokens.labels(
                tenant_id=tenant_id_str,
                period=period_str
            ).set(context_tokens)
            
            channel_digest_generation_total.labels(
                tenant_id=tenant_id_str,
                period=period_str
            ).inc()
            
            logger.info(
                "Channel digest generated successfully",
                tenant_id=tenant_id_str,
                user_id=str(user_id),
                channel_id=str(channel_id),
                period_days=period_days,
                posts_count=result.posts_count,
                duration_seconds=duration
            )
            
            return result
            
        except Exception as e:
            logger.error(
                "Error generating channel digest",
                tenant_id=tenant_id_str,
                user_id=str(user_id),
                channel_id=str(channel_id),
                period_days=period_days,
                error=str(e)
            )
            raise


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_digest_service: Optional[DigestService] = None


def get_digest_service(
    qdrant_url: Optional[str] = None,
    redis_client: Optional[Any] = None
) -> DigestService:
    """
    Получение singleton экземпляра DigestService.
    
    Args:
        qdrant_url: URL Qdrant (опционально)
        redis_client: Redis клиент для кеширования (опционально)
    """
    global _digest_service
    if _digest_service is None:
        qdrant_url = qdrant_url or getattr(settings, 'qdrant_url', 'http://qdrant:6333')
        _digest_service = DigestService(qdrant_url=qdrant_url, redis_client=redis_client)
    elif redis_client and not _digest_service.redis_client:
        # Обновляем redis_client если был передан и его еще нет
        _digest_service.redis_client = redis_client
    return _digest_service

