"""
RAG Service для интеллектуального поиска и ответов на вопросы
Context7 best practice: intent-based routing, hybrid search, context assembly, response generation
"""

import asyncio
import time
import json
import hashlib
from collections import defaultdict
from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime, timezone

import structlog
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import text
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableBranch, RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage
from pydantic import BaseModel

from models.database import Post, PostEnrichment, User
from services.intent_classifier import get_intent_classifier, IntentResponse
from services.searxng_service import get_searxng_service
from services.graph_service import get_graph_service
from config import settings

logger = structlog.get_logger()

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class RAGSource(BaseModel):
    """Источник информации для RAG ответа."""
    post_id: str
    channel_id: str
    channel_title: str
    channel_username: Optional[str]
    content: str
    score: float
    permalink: Optional[str] = None


class RAGResult(BaseModel):
    """Результат RAG поиска."""
    answer: str
    sources: List[RAGSource]
    confidence: float
    intent: str
    processing_time_ms: int


# ============================================================================
# RAG SERVICE
# ============================================================================

class RAGService:
    """Сервис для RAG поиска и генерации ответов."""
    
    def __init__(
        self,
        qdrant_url: str,
        qdrant_client: Optional[QdrantClient] = None,
        redis_client: Optional[Any] = None,
        openai_api_base: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        graph_service: Optional[Any] = None
    ):
        """
        Инициализация RAG Service.
        
        Args:
            qdrant_url: URL Qdrant сервиса
            qdrant_client: Qdrant клиент (опционально, создастся автоматически)
            redis_client: Redis клиент для кэширования
            openai_api_base: URL gpt2giga-proxy
            openai_api_key: API ключ
            graph_service: GraphService для работы с Neo4j (опционально)
        """
        self.qdrant_url = qdrant_url
        self.qdrant_client = qdrant_client or QdrantClient(url=qdrant_url)
        self.redis_client = redis_client
        
        # Инициализация IntentClassifier
        self.intent_classifier = get_intent_classifier(redis_client=redis_client)
        
        # Инициализация SearXNG
        self.searxng_service = get_searxng_service(redis_client=redis_client)
        
        # Context7: Инициализация GraphService для GraphRAG
        self.graph_service = graph_service or get_graph_service()
        
        # Инициализация GigaChat LLM через langchain-gigachat
        # Context7: Исправлен URL (без /v1) для обработки редиректов прокси
        api_base = openai_api_base or settings.openai_api_base or "http://gpt2giga-proxy:8090"
        api_key = openai_api_key or settings.openai_api_key or "dummy"
        
        import os
        os.environ.setdefault("OPENAI_API_BASE", api_base)
        os.environ.setdefault("OPENAI_API_KEY", api_key)
        
        self.llm = GigaChat(
            credentials=getattr(settings, 'gigachat_credentials', '') or os.getenv('GIGACHAT_CREDENTIALS', ''),
            scope=getattr(settings, 'gigachat_scope', None) or os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS'),
            model="GigaChat",
            base_url=api_base,
            temperature=0.7,
        )
        
        # Context7: Intent-based routing через LangChain RunnableBranch
        self.intent_router = self._create_intent_router()
        
        logger.info(
            "RAG Service initialized",
            qdrant_url=qdrant_url,
            api_base=api_base
        )
    
    def _create_intent_router(self) -> RunnableBranch:
        """Создание intent-based router через LangChain RunnableBranch с поддержкой conversation history."""
        
        # Context7: Промпты с поддержкой conversation history
        # Используем MessagesPlaceholder для динамического добавления истории
        ask_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по анализу контента из Telegram каналов.
Ответь на вопрос пользователя, используя только предоставленный контекст/историю.
Если информации недостаточно, честно сообщи об этом и предложи уточнить запрос.

Формат ответа (Markdown, строго соблюдай):
1. **Заголовок** одной строкой.
2. **Ключевые факты** — маркированный список до 4 пунктов, каждый пункт заверши ссылкой вида [канал](URL).
3. **Что дальше** — (опционально) список конкретных действий.
4. **Источники** — повтори ссылки списком `• [канал](URL) — краткое пояснение`.
   Если в контексте есть пометки `🖼` (vision) или `🕸` (Crawl4AI), явно укажи визуальные и веб-находки отдельной строкой.
   Если встречаются строки `[Внешний источник ...]`, вынеси их в подпункт «Внешние источники».

Ссылки всегда размещай рядом с утверждением. Не выдумывай факты.
Используй историю диалога (conversation_history), если она передана."""),
            # Context7: Динамически добавляем историю разговора если она есть
            MessagesPlaceholder(variable_name="conversation_history", optional=True),
            ("human", "Контекст:\n{context}\n\nВопрос: {query}\n\nОтвет:")
        ])
        
        search_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — помощник по поиску информации в Telegram каналах.
Сформируй структурированный обзор результатов.

Формат (Markdown):
1. **Запрос** — коротко перефразируй вопрос.
2. **Результаты** — маркированный список: `[канал](URL): тезис`. Если в контексте у этого пункта есть строки, начинающиеся с `🖼`, `📷` или `🕸`, добавь после тезиса подпункт «Признаки визуальных сигналов» и процитируй эти строки. Если таких строк нет — полностью пропусти подпункт (не пиши «нет данных»).
3. **Внешние источники** — добавляй только если в контексте есть записи `[Внешний источник …]`.
4. **Источники** — выведи один раз в конце, перечислив только ссылки, которые уже упомянуты выше (Context7: не дублируем).

Не добавляй чужой информации, используй контекст и историю диалога."""),
            MessagesPlaceholder(variable_name="conversation_history", optional=True),
            ("human", "Найденные посты:\n{context}\n\nЗапрос: {query}\n\nРезюме:")
        ])
        
        recommend_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — помощник по рекомендации контента.
На основе найденных постов предложи релевантные материалы и объясни ценность каждого.

Формат:
1. **Заголовок**.
2. **Рекомендации** — нумерованный список: `[канал](URL) — причина + упоминание vision/crawl, если есть`.
3. **Что почитать дополнительно** — (опционально) список внешних источников.
4. **Источники** — отдельный список ссылок.

Учитывай историю пользователя (conversation_history), не дублируй факты."""),
            MessagesPlaceholder(variable_name="conversation_history", optional=True),
            ("human", "Найденные посты:\n{context}\n\nЗапрос: {query}\n\nРекомендации:")
        ])
        
        trend_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — аналитик трендов.
Выдели ключевые темы, метрики и сигналы из предоставленного контекста.

Формат:
1. **Заголовок**.
2. **Тренды** — маркированный список. Для каждого тренда укажи:
   - краткое описание и ссылку `[канал](URL)`.
   - Если в соответствующем блоке контекста есть строки `🖼`, `📷` или `🕸`, добавь подпункт «Признаки визуальных сигналов» и процитируй их; если строк нет — пропусти подпункт.
3. **Что наблюдать** — список рекомендаций/метрик.
4. **Источники** — единый список ссылок (только те, что использованы в ответе). Отдельный блок «Внешние источники» выводи лишь при наличии `[Внешний источник …]` в контексте.

Всегда отделяй внешние источники в подпункт, если контекст содержит `[Внешний источник]`."""),
            MessagesPlaceholder(variable_name="conversation_history", optional=True),
            ("human", "Посты для анализа:\n{context}\n\nЗапрос: {query}\n\nАнализ трендов:")
        ])
        
        digest_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — составитель дайджестов новостей.
Сформируй структурированный дайджест из найденных постов.

Формат:
1. **Заголовок дайджеста**.
2. Для каждой темы:
   - `### Тема` (с кратким описанием).
   - `• [канал](URL): факт`. Если у темы в контексте есть строки `🖼`, `📷` или `🕸`, добавь подсписок «Признаки визуальных сигналов» и процитируй эти строки. Если таких строк нет — не вставляй подпункт.
3. **Внешние источники** — только если в контексте встречаются `[Внешний источник …]`.
4. **Источники** — единый список ссылок из ответа (Context7: не дублировать то же самое в нескольких блоках).

Темы и факты должны ссылаться на контекст. Не придумывай данные."""),
            MessagesPlaceholder(variable_name="conversation_history", optional=True),
            ("human", "Посты для дайджеста:\n{context}\n\nЗапрос: {query}\n\nДайджест:")
        ])
        
        # Context7: RunnableBranch для маршрутизации по намерениям
        return RunnableBranch(
            (lambda x: x["intent"] == "ask", ask_prompt | self.llm | StrOutputParser()),
            (lambda x: x["intent"] == "search", search_prompt | self.llm | StrOutputParser()),
            (lambda x: x["intent"] == "recommend", recommend_prompt | self.llm | StrOutputParser()),
            (lambda x: x["intent"] == "trend", trend_prompt | self.llm | StrOutputParser()),
            (lambda x: x["intent"] == "digest", digest_prompt | self.llm | StrOutputParser()),
            # Fallback на search
            search_prompt | self.llm | StrOutputParser()
        )
    
    async def _generate_embedding(self, text: str) -> List[float]:
        """Генерация embedding для запроса через GigaChat."""
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
    
    async def _search_qdrant(
        self,
        query_embedding: List[float],
        tenant_id: str,
        limit: int = 10,
        channel_ids: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Поиск в Qdrant по вектору."""
        try:
            collection_name = f"t{tenant_id}_posts"
            
            # Проверка существования коллекции
            collections = self.qdrant_client.get_collections()
            if collection_name not in [c.name for c in collections.collections]:
                logger.warning("Qdrant collection not found", collection=collection_name)
                return []
            
            # Подготовка фильтра
            filter_conditions = []
            filter_conditions.append(
                FieldCondition(
                    key="tenant_id",
                    match=MatchValue(value=str(tenant_id))
                )
            )
            
            if channel_ids:
                filter_conditions.append(
                    FieldCondition(
                        key="channel_id",
                        match=MatchValue(any=[str(cid) for cid in channel_ids])
                    )
                )
            
            search_filter = Filter(must=filter_conditions) if filter_conditions else None
            
            # Поиск
            search_results = self.qdrant_client.search(
                collection_name=collection_name,
                query_vector=query_embedding,
                query_filter=search_filter,
                limit=limit
            )
            
            results = []
            for result in search_results:
                results.append({
                    'post_id': result.payload.get('post_id'),
                    'score': result.score,
                    'payload': result.payload
                })
            
            return results
        
        except Exception as e:
            logger.error("Error searching Qdrant", error=str(e))
            return []
    
    async def _search_postgres_fts(
        self,
        query: str,
        tenant_id: str,
        limit: int = 10,
        channel_ids: Optional[List[str]] = None,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """Поиск через PostgreSQL Full-Text Search."""
        if not db:
            return []
        
        try:
            # Context7: PostgreSQL FTS для keyword search
            # Используем tsvector для поиска по content
            # Context7: Фильтрация по tenant_id через JOIN с user_channel и по channel_ids
            base_query = """
                SELECT DISTINCT
                    p.id,
                    p.channel_id,
                    p.content,
                    p.telegram_post_url,
                    ts_rank(to_tsvector('russian', COALESCE(p.content, '')), plainto_tsquery('russian', :query)) as rank
                FROM posts p
                JOIN channels c ON p.channel_id = c.id
                JOIN user_channel uc ON uc.channel_id = c.id
                JOIN users u ON u.id = uc.user_id
                WHERE to_tsvector('russian', COALESCE(p.content, '')) @@ plainto_tsquery('russian', :query)
                    AND u.tenant_id = CAST(:tenant_id AS uuid)
            """
            
            params = {"query": query, "tenant_id": tenant_id, "limit": limit}
            
            # Добавляем фильтрацию по channel_ids если указаны
            if channel_ids:
                base_query += " AND p.channel_id = ANY(CAST(:channel_ids AS uuid[]))"
                params["channel_ids"] = channel_ids
            
            base_query += " ORDER BY rank DESC LIMIT :limit"
            
            fts_query = text(base_query)
            result = db.execute(fts_query, params)
            rows = result.fetchall()
            
            results = []
            for row in rows:
                results.append({
                    'post_id': str(row.id),
                    'channel_id': str(row.channel_id),
                    'content': row.content,
                    'permalink': row.telegram_post_url,
                    'score': float(row.rank) if row.rank else 0.0
                })
            
            return results
        
        except Exception as e:
            logger.error("Error searching PostgreSQL FTS", error=str(e))
            return []
    
    async def _search_neo4j_graph(
        self,
        query: str,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 10,
        max_depth: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        GraphRAG поиск через Neo4j.
        
        Context7: Использует графовые связи для поиска связанных постов
        
        Args:
            query: Текст запроса
            user_id: ID пользователя (для фильтрации по интересам)
            limit: Максимальное количество результатов
            max_depth: Максимальная глубина обхода графа
        
        Returns:
            Список связанных постов из графа
        """
        try:
            # Context7: Проверка кэша Redis
            if self.redis_client:
                cache_key = f"graphrag:query:{hashlib.sha1((query + (user_id or '')).encode()).hexdigest()}"
                cached = self.redis_client.get(cache_key)
                if cached:
                    logger.debug("GraphRAG cache hit", query=query[:50])
                    return json.loads(cached)
            
            # Context7: Health check перед графовым запросом
            if not await self.graph_service.health_check():
                logger.warning("Neo4j unavailable, skipping GraphRAG search")
                return []
            
            max_depth = max_depth or getattr(settings, 'neo4j_max_graph_depth', 2)
            
            # Поиск связанных постов через граф
            graph_results = await self.graph_service.search_related_posts(
                query=query,
                topic=None,  # Можно извлечь тему из запроса
                tenant_id=tenant_id,
                limit=limit * 2,
                max_depth=max_depth
            )
            
            # Преобразуем результаты в формат, совместимый с hybrid_search
            results = []
            for item in graph_results:
                results.append({
                    'post_id': item.get('post_id'),
                    'content': item.get('content', ''),
                    'topic': item.get('topic'),
                    'topics': item.get('topics', []),
                    'channel_title': item.get('channel_title'),
                    'score': 0.8,  # Базовый score для графовых результатов
                    'graph_score': 0.8,
                    'relation_type': item.get('relation_type', 'direct')
                })
            
            # Context7: Кэширование результатов (TTL 5 минут)
            if self.redis_client and results:
                self.redis_client.setex(
                    cache_key,
                    300,  # 5 минут
                    json.dumps(results)
                )
            
            logger.debug("GraphRAG search completed", query=query[:50], results_count=len(results))
            return results
            
        except Exception as e:
            logger.error("Error in GraphRAG search", error=str(e), query=query[:50])
            # Context7: Graceful degradation - возвращаем пустой список при ошибке
            return []
    
    async def _hybrid_search(
        self,
        query: str,
        query_embedding: List[float],
        tenant_id: str,
        limit: int = 10,
        channel_ids: Optional[List[str]] = None,
        db: Optional[Session] = None,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search: Qdrant ANN + PostgreSQL FTS + Neo4j GraphRAG с re-ranking.
        
        Context7: Тройной гибрид:
        - Qdrant (вес 0.5) - семантический поиск
        - PostgreSQL FTS (вес 0.2) - keyword search
        - Neo4j GraphRAG (вес 0.3) - графовые связи и интересы пользователя
        """
        # Параллельный поиск в Qdrant, PostgreSQL и Neo4j
        qdrant_results = await self._search_qdrant(query_embedding, tenant_id, limit * 2, channel_ids)
        fts_results = await self._search_postgres_fts(query, tenant_id, limit * 2, channel_ids, db)
        
        # Context7: GraphRAG поиск (с fallback при недоступности Neo4j)
        graph_results = []
        try:
            graph_results = await self._search_neo4j_graph(query, user_id, tenant_id=tenant_id, limit=limit * 2)
        except Exception as e:
            logger.warning("GraphRAG search failed, continuing without graph results", error=str(e))
        
        # Объединение и дедупликация результатов
        post_scores = {}
        
        # Добавляем результаты из Qdrant (вес 0.5)
        for result in qdrant_results:
            post_id = result['post_id']
            score = result['score'] * 0.5
            if post_id not in post_scores:
                post_scores[post_id] = {
                    'post_id': post_id,
                    'payload': result.get('payload', {}),
                    'qdrant_score': result['score'],
                    'fts_score': 0.0,
                    'graph_score': 0.0,
                    'hybrid_score': score
                }
            else:
                post_scores[post_id]['hybrid_score'] += score
                post_scores[post_id]['qdrant_score'] = result['score']
        
        # Добавляем результаты из FTS (вес 0.2)
        for result in fts_results:
            post_id = result['post_id']
            score = result['score'] * 0.2
            if post_id not in post_scores:
                post_scores[post_id] = {
                    'post_id': post_id,
                    'payload': result,
                    'qdrant_score': 0.0,
                    'fts_score': result['score'],
                    'graph_score': 0.0,
                    'hybrid_score': score
                }
            else:
                post_scores[post_id]['hybrid_score'] += score
                post_scores[post_id]['fts_score'] = result['score']
                if 'content' not in post_scores[post_id]['payload']:
                    post_scores[post_id]['payload'].update(result)
        
        # Добавляем результаты из Neo4j GraphRAG (вес 0.3)
        for result in graph_results:
            post_id = result.get('post_id')
            if not post_id:
                continue
            
            score = result.get('graph_score', 0.8) * 0.3
            if post_id not in post_scores:
                post_scores[post_id] = {
                    'post_id': post_id,
                    'payload': {
                        'content': result.get('content', ''),
                        'topic': result.get('topic'),
                        'topics': result.get('topics', []),
                        'channel_title': result.get('channel_title')
                    },
                    'qdrant_score': 0.0,
                    'fts_score': 0.0,
                    'graph_score': result.get('graph_score', 0.8),
                    'hybrid_score': score,
                    'relation_type': result.get('relation_type', 'direct')
                }
            else:
                post_scores[post_id]['hybrid_score'] += score
                post_scores[post_id]['graph_score'] = result.get('graph_score', 0.8)
                # Обогащаем payload графовыми данными
                if 'topics' in result:
                    existing_topics = post_scores[post_id]['payload'].get('topics', [])
                    if isinstance(existing_topics, list):
                        post_scores[post_id]['payload']['topics'] = list(set(existing_topics + result.get('topics', [])))
        
        # Context7: Дедупликация альбомов - получаем grouped_id из БД и оставляем только первый пост с наивысшим score
        if db:
            try:
                # Получаем grouped_id для всех постов из БД
                post_ids = [UUID(pid) for pid in post_scores.keys() if pid]
                if post_ids:
                    posts_with_grouped = db.query(
                        Post.id,
                        Post.grouped_id
                    ).filter(Post.id.in_(post_ids)).all()
                    
                    # Создаем словарь post_id -> grouped_id
                    post_grouped_map = {str(post.id): post.grouped_id for post in posts_with_grouped if post.grouped_id}
                    
                    # Группируем посты по альбомам
                    album_posts = {}  # grouped_id -> список (post_id, hybrid_score)
                    for post_id, score_data in post_scores.items():
                        grouped_id = post_grouped_map.get(post_id)
                        if grouped_id:
                            if grouped_id not in album_posts:
                                album_posts[grouped_id] = []
                            album_posts[grouped_id].append((post_id, score_data['hybrid_score']))
                    
                    # Для каждого альбома оставляем только пост с наивысшим score
                    posts_to_remove = set()
                    for grouped_id, posts_list in album_posts.items():
                        if len(posts_list) > 1:
                            # Сортируем по score и оставляем только первый
                            posts_list.sort(key=lambda x: x[1], reverse=True)
                            # Удаляем все посты кроме первого
                            for post_id, _ in posts_list[1:]:
                                posts_to_remove.add(post_id)
                    
                    # Удаляем дубликаты альбомов
                    for post_id in posts_to_remove:
                        post_scores.pop(post_id, None)
                    
                    logger.debug(
                        "Album deduplication applied",
                        albums_count=len(album_posts),
                        removed_duplicates=len(posts_to_remove)
                    )
            except Exception as e:
                logger.warning("Error during album deduplication", error=str(e))
                # Продолжаем без дедупликации при ошибке
        
        # Сортировка по hybrid_score
        sorted_results = sorted(
            post_scores.values(),
            key=lambda x: x['hybrid_score'],
            reverse=True
        )
        
        return sorted_results[:limit]
    
    async def _assemble_context(
        self,
        results: List[Dict[str, Any]],
        db: Session
    ) -> tuple[str, List[RAGSource]]:
        """Сборка контекста из найденных постов с обогащениями Vision/Crawl."""
        max_context_posts = 5
        ordered_results: List[Dict[str, Any]] = []
        post_ids: List[UUID] = []
        post_ids_str: List[str] = []
        
        for result in results[:max_context_posts]:
            post_id_raw = result.get("post_id")
            if not post_id_raw:
                continue
            try:
                post_uuid = UUID(str(post_id_raw))
            except (ValueError, TypeError):
                continue
            ordered_results.append(result)
            post_ids.append(post_uuid)
            post_ids_str.append(str(post_uuid))
        
        if not ordered_results:
            return "", []
        
        posts = (
            db.query(Post)
            .options(selectinload(Post.channel))
            .filter(Post.id.in_(post_ids))
            .all()
        )
        post_map = {str(post.id): post for post in posts}
        
        enrichments = db.query(PostEnrichment).filter(
            PostEnrichment.post_id.in_(post_ids),
            PostEnrichment.kind.in_(("vision", "vision_ocr", "crawl", "general"))
        ).all()
        enrichment_map: defaultdict[str, dict[str, PostEnrichment]] = defaultdict(dict)
        for enrichment in enrichments:
            enrichment_map[str(enrichment.post_id)][enrichment.kind] = enrichment
        
        context_parts: List[str] = []
        sources: List[RAGSource] = []
        
        for idx, result in enumerate(ordered_results):
            post = post_map.get(str(result.get("post_id")))
            if not post:
                continue
            
            channel = post.channel
            channel_title = channel.title if channel else "Неизвестный канал"
            channel_username = channel.username if channel else None
            
            content = (post.content or "").strip()
            if len(content) > 500:
                content = content[:500].rstrip() + "…"
            if not content:
                content = "Без текста, доступно только медиа/обогащения."
            
            enrichment_bundle = enrichment_map.get(str(post.id), {})
            enrichment_snippets = self._render_enrichment_snippets(enrichment_bundle)
            
            if post.grouped_id and not any(snippet.startswith("📷") for snippet in enrichment_snippets):
                enrichment_snippets.insert(0, "📷 Альбом из нескольких медиа")
            
            enrichment_text = ""
            if enrichment_snippets:
                enrichment_text = "\n" + "\n".join(enrichment_snippets)
            
            permalink = post.telegram_post_url or ""
            if permalink:
                entry = f"[{idx + 1}] [{channel_title}]({permalink}): {content}{enrichment_text}"
            else:
                entry = f"[{idx + 1}] {channel_title}: {content}{enrichment_text}"
            context_parts.append(entry)
            
            source_content = content
            if enrichment_snippets:
                source_content = f"{content}\n" + "\n".join(enrichment_snippets)
            
            sources.append(
                RAGSource(
                    post_id=str(post.id),
                    channel_id=str(post.channel_id),
                    channel_title=channel_title,
                    channel_username=channel_username,
                    content=source_content,
                    score=result.get("hybrid_score", result.get("score", 0.0)),
                    permalink=post.telegram_post_url
                )
            )
        
        context = "\n\n".join(context_parts)
        return context, sources

    def _render_enrichment_snippets(
        self,
        enrichment_bundle: Optional[Dict[str, PostEnrichment]]
    ) -> List[str]:
        """Формирует дополнительные блоки текста из Vision/Crawl4AI обогащений."""
        snippets: List[str] = []
        if not enrichment_bundle:
            return snippets
        
        def _append_unique(
            prefix: str,
            text_value: Optional[str],
            limit: int = 280,
            skip_values: Optional[List[str]] = None
        ) -> None:
            if not text_value:
                return
            normalized = text_value.strip()
            if not normalized:
                return
            if skip_values and normalized.lower() in skip_values:
                return
            short_text = self._shorten_text(normalized, limit)
            if short_text and not any(snippet.startswith(prefix) for snippet in snippets):
                snippets.append(f"{prefix} {short_text}")
        
        vision = enrichment_bundle.get("vision")
        if vision and isinstance(getattr(vision, "data", None), dict):
            data = vision.data or {}
            caption = data.get("summary") or data.get("description") or data.get("caption")
            _append_unique(
                "🖼",
                caption,
                skip_values=[
                    "изображение без описания",
                    "image without description",
                    "no description",
                ]
            )
            labels = data.get("labels")
            if isinstance(labels, list) and labels:
                normalized_labels = ", ".join(str(label) for label in labels[:5] if label)
                _append_unique("🏷 Теги:", normalized_labels, limit=200)
            ocr_payload = data.get("ocr")
            ocr_text = None
            if isinstance(ocr_payload, dict):
                ocr_text = ocr_payload.get("text")
            elif isinstance(ocr_payload, str):
                ocr_text = ocr_payload
            cleaned_ocr = self._normalize_ocr_text(ocr_text)
            _append_unique("🔤 OCR:", cleaned_ocr, limit=240, skip_values=[""])
        
        vision_ocr = enrichment_bundle.get("vision_ocr")
        if vision_ocr and isinstance(getattr(vision_ocr, "data", None), dict):
            ocr_text = vision_ocr.data.get("text") or vision_ocr.data.get("raw_text")
            cleaned_ocr = self._normalize_ocr_text(ocr_text)
            _append_unique("🔤 OCR:", cleaned_ocr, limit=240, skip_values=[""])
        
        crawl = enrichment_bundle.get("crawl") or enrichment_bundle.get("general")
        if crawl and isinstance(getattr(crawl, "data", None), dict):
            crawl_data = crawl.data or {}
            crawl_excerpt = crawl_data.get("md_excerpt") or crawl_data.get("markdown")
            _append_unique("🕸 Crawl4AI:", crawl_excerpt, limit=320)
            crawl_summary = crawl_data.get("summary")
            _append_unique("📰", crawl_summary, limit=240)
        
        album_size = None
        for enrichment in enrichment_bundle.values():
            size = getattr(enrichment, "album_size", None)
            if size:
                album_size = max(album_size or 0, size)
        if album_size:
            snippets.append(f"📷 Альбом: {album_size} медиа")
        
        return snippets

    @staticmethod
    def _shorten_text(value: Optional[str], limit: int = 280) -> str:
        """Обрезает текст до заданной длины с добавлением многоточия."""
        if not value:
            return ""
        trimmed = value.strip()
        if len(trimmed) <= limit:
            return trimmed
        return trimmed[:limit].rstrip() + "…"
    
    @staticmethod
    def _normalize_ocr_text(value: Optional[str]) -> str:
        """Нормализует OCR-текст: убирает капслок и лишние пробелы."""
        if not value:
            return ""
        normalized = " ".join(value.split())
        # Если текст полностью в ВЕРХНЕМ регистре, переводим в предложение
        if normalized.isupper():
            normalized = normalized.capitalize()
        return normalized
    
    async def _should_enrich_with_searxng(
        self,
        search_results: List[Dict[str, Any]],
        confidence: float,
        query: str
    ) -> bool:
        """
        Проверка условий для обогащения ответа через SearXNG.
        
        Context7: Обогащение используется при:
        - Низкой уверенности (confidence < threshold)
        - Мало результатов (< minimum_results_threshold)
        - Низкие scores результатов (средний score < score_threshold)
        
        Args:
            search_results: Результаты поиска из каналов
            confidence: Уверенность в ответе (0.0-1.0)
            query: Поисковый запрос
            
        Returns:
            True если нужно обогащать ответ через SearXNG
        """
        # Проверяем, включено ли обогащение
        if not settings.searxng_enrichment_enabled or not self.searxng_service.enabled:
            logger.debug(
                "Enrichment disabled",
                searxng_enrichment_enabled=settings.searxng_enrichment_enabled,
                searxng_service_enabled=self.searxng_service.enabled
            )
            return False
        
        # Если результатов нет - используем внешнее обогащение как fallback
        if not search_results:
            logger.debug(
                "Enrichment triggered: no channel results",
                confidence=confidence
            )
            return True
        
        # Проверка 1: Низкая уверенность
        if confidence < settings.searxng_enrichment_confidence_threshold:
            logger.debug(
                "Enrichment triggered: low confidence",
                confidence=confidence,
                threshold=settings.searxng_enrichment_confidence_threshold
            )
            return True
        
        # Проверка 2: Мало результатов
        if len(search_results) < settings.searxng_enrichment_min_results_threshold:
            logger.debug(
                "Enrichment triggered: few results",
                results_count=len(search_results),
                threshold=settings.searxng_enrichment_min_results_threshold
            )
            return True
        
        # Проверка 3: Низкие scores результатов
        if search_results:
            avg_score = sum(
                r.get('hybrid_score', r.get('score', 0.0)) 
                for r in search_results
            ) / len(search_results)
            
            if avg_score < settings.searxng_enrichment_score_threshold:
                logger.debug(
                    "Enrichment triggered: low average score",
                    avg_score=avg_score,
                    threshold=settings.searxng_enrichment_score_threshold
                )
                return True
        
        return False
    
    async def _enrich_with_searxng(
        self,
        query: str,
        user_id: str,
        existing_sources: List[RAGSource],
        lang: str = "ru"
    ) -> tuple[List[RAGSource], float]:
        """
        Обогащение ответа внешними источниками через SearXNG.
        
        Context7: Graceful degradation - ошибки SearXNG не должны влиять на основной ответ.
        Обогащение выполняется параллельно и не блокирует основной flow.
        
        Args:
            query: Поисковый запрос
            user_id: ID пользователя для rate limiting
            existing_sources: Существующие источники из каналов
            lang: Язык поиска
            
        Returns:
            Tuple (обогащенные источники, дополнительный confidence boost)
        """
        enriched_sources = existing_sources.copy()
        confidence_boost = 0.0
        
        try:
            # Context7: Параллельный запрос к SearXNG (не блокирует основной flow)
            searxng_timeout = getattr(settings, "searxng_timeout_seconds", 8)
            searxng_response = await asyncio.wait_for(
                self.searxng_service.search(
                    query=query,
                    user_id=user_id,
                    lang=lang,
                    score_threshold=0.5  # Фильтруем только релевантные результаты
                ),
                timeout=searxng_timeout
            )
            
            if searxng_response.results:
                # Добавляем внешние источники с пометкой "external"
                external_count = min(
                    len(searxng_response.results),
                    settings.searxng_enrichment_max_external_results
                )
                
                for idx, result in enumerate(searxng_response.results[:external_count]):
                    external_source = RAGSource(
                        post_id=f"external_{idx}",
                        channel_id="external",
                        channel_title=result.title,
                        channel_username=None,
                        content=result.snippet,
                        score=0.5,  # Внешние источники имеют средний score
                        permalink=str(result.url)
                    )
                    enriched_sources.append(external_source)
                
                # Context7: Confidence boost на основе качества внешних источников
                # Чем больше релевантных внешних источников, тем выше boost
                confidence_boost = min(
                    0.15,  # Максимальный boost 0.15
                    len(searxng_response.results[:external_count]) * 0.05
                )
                
                logger.info(
                    "Enrichment completed",
                    query=query[:50],
                    external_results=external_count,
                    confidence_boost=confidence_boost
                )
            else:
                logger.debug("Enrichment: no external results found", query=query[:50])
        
        except asyncio.TimeoutError:
            logger.warning(
                "Enrichment failed due to timeout",
                query=query[:50],
                timeout_seconds=getattr(settings, "searxng_timeout_seconds", 8)
            )
        except Exception as e:
            # Context7: Graceful degradation - ошибки не влияют на основной ответ
            logger.warning(
                "Enrichment failed, continuing without external sources",
                error=str(e),
                query=query[:50]
            )
        
        return enriched_sources, confidence_boost
    
    async def _get_conversation_history(
        self,
        user_id: UUID,
        db: Session,
        max_turns: int = 5
    ) -> List[Dict[str, str]]:
        """
        Получение истории разговора для контекста.
        
        Context7: Использует последние N запросов и ответов из RAGQueryHistory
        для поддержания контекста разговора в multi-turn диалогах.
        
        Args:
            user_id: ID пользователя
            db: SQLAlchemy сессия
            max_turns: Максимальное количество пар вопрос-ответ для контекста
        
        Returns:
            Список сообщений в формате [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        try:
            from models.database import RAGQueryHistory
            from sqlalchemy import desc
            from datetime import timedelta
            
            # Context7: Ограничиваем окно времени для истории (по умолчанию 24 часа)
            window_hours = getattr(settings, 'rag_conversation_window_hours', 24)
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            
            # Получаем последние N запросов с ответами в пределах временного окна
            history_records = db.query(RAGQueryHistory).filter(
                RAGQueryHistory.user_id == user_id,
                RAGQueryHistory.response_text.isnot(None),
                RAGQueryHistory.created_at >= cutoff_time
            ).order_by(
                desc(RAGQueryHistory.created_at)
            ).limit(max_turns).all()
            
            # Формируем список сообщений в обратном порядке (от старых к новым)
            conversation = []
            for record in reversed(history_records):
                if record.query_text:
                    conversation.append({
                        "role": "user",
                        "content": record.query_text
                    })
                if record.response_text:
                    conversation.append({
                        "role": "assistant",
                        "content": record.response_text[:1000]  # Ограничиваем длину для экономии токенов
                    })
            
            logger.debug(
                "Conversation history retrieved",
                user_id=str(user_id),
                turns=len(conversation) // 2,
                total_messages=len(conversation)
            )
            
            return conversation
            
        except Exception as e:
            logger.warning(
                "Failed to get conversation history",
                error=str(e),
                user_id=str(user_id)
            )
            return []
    
    async def query(
        self,
        query: str,
        user_id: UUID,
        tenant_id: str,
        db: Session,
        limit: int = 5,
        channel_ids: Optional[List[str]] = None,
        audio_file_id: Optional[str] = None,
        transcription_text: Optional[str] = None,
        include_conversation_history: bool = True,
        max_conversation_turns: int = 5,
        intent_override: Optional[str] = None
    ) -> RAGResult:
        """
        Выполнение RAG запроса с intent-based routing и поддержкой контекста разговора.
        
        Context7: Поддерживает multi-turn conversations через conversation history.
        
        Args:
            query: Текст запроса пользователя
            user_id: ID пользователя
            tenant_id: ID арендатора
            db: SQLAlchemy сессия
            limit: Максимальное количество результатов
            channel_ids: Список ID каналов для фильтрации (опционально)
            audio_file_id: ID голосового файла (опционально)
            transcription_text: Текст транскрипции (опционально)
            include_conversation_history: Включать ли историю разговора в контекст
            max_conversation_turns: Максимальное количество пар вопрос-ответ для контекста
        
        Returns:
            RAGResult с ответом и источниками
        """
        start_time = time.time()
        
        try:
            # Context7: Получаем историю разговора для контекста
            # Используем настройки из config если не указаны явно
            use_history = include_conversation_history if include_conversation_history is not None else getattr(settings, 'rag_conversation_history_enabled', True)
            max_turns = max_conversation_turns if max_conversation_turns is not None else getattr(settings, 'rag_max_conversation_turns', 5)
            
            conversation_history = []
            if use_history:
                conversation_history = await self._get_conversation_history(
                    user_id=user_id,
                    db=db,
                    max_turns=max_turns
                )
            
            # 1. Классификация намерения
            # Context7: Если передан intent_override, используем его вместо классификации
            if intent_override:
                intent = intent_override
                confidence = 1.0  # Высокая уверенность для принудительного намерения
                logger.debug("Using intent override", intent=intent, query=query[:50])
            else:
                intent_result = await self.intent_classifier.classify(query, str(user_id))
                intent = intent_result.intent
                confidence = intent_result.confidence
            
            # Context7: Для intent="recommend" используем RecommendationService
            if intent == "recommend":
                from services.recommendation_service import get_recommendation_service
                recommendation_service = get_recommendation_service()
                
                # Получаем рекомендации через граф интересов
                recommendations = await recommendation_service.get_recommendations(
                    user_id=user_id,
                    limit=limit,
                    days=7,
                    db=db
                )
                
                if not recommendations:
                    # Fallback на collaborative filtering
                    recommendations = await recommendation_service.get_collaborative_recommendations(
                        user_id=user_id,
                        limit=limit,
                        days=7,
                        db=db
                    )
                
                if recommendations:
                    # Преобразуем рекомендации в формат источников
                    sources = []
                    context_parts = []
                    
                    for rec in recommendations:
                        post_id = rec.get('post_id')
                        if not post_id:
                            continue
                        
                        try:
                            # Безопасное преобразование post_id в UUID
                            if isinstance(post_id, UUID):
                                post_uuid = post_id
                            elif isinstance(post_id, str):
                                try:
                                    post_uuid = UUID(post_id)
                                except (ValueError, TypeError) as e:
                                    logger.warning(
                                        "Invalid post_id format in recommendation",
                                        post_id=post_id,
                                        error=str(e)
                                    )
                                    continue
                            else:
                                logger.warning(
                                    "Unexpected post_id type in recommendation",
                                    post_id=post_id,
                                    post_id_type=type(post_id).__name__
                                )
                                continue
                            
                            # Получаем полную информацию о посте из БД
                            post = db.query(Post).filter(Post.id == post_uuid).first()
                            if not post:
                                logger.debug("Post not found in database", post_id=str(post_uuid))
                                continue
                            
                            channel = db.query(Channel).filter(Channel.id == post.channel_id).first()
                            
                            # Используем post_id как строку для RAGSource
                            source = RAGSource(
                                post_id=str(post_uuid),
                                channel_id=str(post.channel_id),
                                channel_title=channel.title if channel else "Unknown",
                                channel_username=channel.username if channel else None,
                                content=rec.get('content', post.content or ''),
                                score=rec.get('recommendation_score', 0.8),
                                permalink=post.telegram_post_url
                            )
                            sources.append(source)
                            
                            context_parts.append(
                                f"Пост из канала {source.channel_title}:\n{source.content[:200]}"
                            )
                        except Exception as e:
                            logger.warning(
                                "Error processing recommendation",
                                post_id=post_id,
                                error=str(e),
                                exc_info=True
                            )
                            continue
                    
                    # Проверяем, что есть источники для генерации ответа
                    if not sources:
                        logger.warning("No valid sources found from recommendations", user_id=str(user_id))
                        # Fallback на обычный поиск
                        intent = "search"
                    else:
                        context = "\n\n".join(context_parts) if context_parts else ""
                        
                        # Генерация ответа через LLM с conversation history
                        # Context7: Преобразуем историю в LangChain Message объекты
                        history_messages = []
                        if conversation_history:
                            for msg in conversation_history:
                                if msg.get("role") == "user":
                                    history_messages.append(HumanMessage(content=msg.get("content", "")))
                                elif msg.get("role") == "assistant":
                                    history_messages.append(AIMessage(content=msg.get("content", "")))
                        
                        router_input = {
                            "query": query,
                            "context": context,
                            "intent": intent,
                            "conversation_history": history_messages if history_messages else []
                        }
                        
                        answer = await self.intent_router.ainvoke(router_input)
                        
                        # Отслеживание интересов
                        try:
                            from services.user_interest_service import get_user_interest_service
                            interest_service = get_user_interest_service(redis_client=self.redis_client)
                            
                            # Исправление: формируем список словарей с темами
                            sources_for_tracking = [
                                {'topics': [rec.get('interest_topic')]}
                                for rec in recommendations
                                if rec.get('interest_topic')
                            ]
                            
                            await interest_service.track_query(
                                user_id=user_id,
                                query_text=query,
                                intent=intent,
                                sources=sources_for_tracking,
                                db=db
                            )
                        except Exception as e:
                            logger.warning("Failed to track user interest", error=str(e))
                        
                        processing_time = int((time.time() - start_time) * 1000)
                        
                        return RAGResult(
                            answer=answer,
                            sources=sources[:limit],
                            confidence=confidence,
                            intent=intent,
                            processing_time_ms=processing_time
                        )
                else:
                    # Fallback на обычный поиск если нет рекомендаций
                    logger.debug("No recommendations found, falling back to regular search", user_id=str(user_id))
                    intent = "search"  # Переключаемся на обычный поиск
            
            logger.info(
                "Intent classified",
                query=query[:50],
                intent=intent,
                confidence=confidence
            )
            
            # 2. Генерация embedding для запроса
            query_embedding = await self._generate_embedding(query)
            
            if not query_embedding:
                logger.warning("Failed to generate embedding, falling back to FTS only")
            
            # 3. Hybrid search (Qdrant + PostgreSQL FTS + Neo4j GraphRAG)
            if query_embedding:
                search_results = await self._hybrid_search(
                    query, query_embedding, tenant_id, limit * 2, channel_ids, db, user_id=str(user_id)
                )
            else:
                # Fallback на FTS + GraphRAG (без векторов)
                fts_results = await self._search_postgres_fts(
                    query, tenant_id, limit * 2, channel_ids, db
                )
                graph_results = await self._search_neo4j_graph(query, str(user_id), tenant_id=tenant_id, limit=limit * 2)
                
                # Объединяем результаты
                post_scores = {}
                for result in fts_results:
                    post_id = result['post_id']
                    post_scores[post_id] = {
                        'post_id': post_id,
                        'payload': result,
                        'hybrid_score': result['score'] * 0.7
                    }
                
                for result in graph_results:
                    post_id = result.get('post_id')
                    if post_id:
                        score = result.get('graph_score', 0.8) * 0.3
                        if post_id in post_scores:
                            post_scores[post_id]['hybrid_score'] += score
                        else:
                            post_scores[post_id] = {
                                'post_id': post_id,
                                'payload': result,
                                'hybrid_score': score
                            }
                
                search_results = sorted(
                    post_scores.values(),
                    key=lambda x: x['hybrid_score'],
                    reverse=True
                )[:limit * 2]
            
            if not search_results:
                logger.warning("No search results found", query=query[:50])
                # Пробуем внешний поиск через SearXNG
                searxng_response = await self.searxng_service.search(
                    query, str(user_id), lang="ru"
                )
                
                if searxng_response.results:
                    external_sources = [
                        RAGSource(
                            post_id=f"external_{idx}",
                            channel_id="external",
                            channel_title=result.title,
                            channel_username=None,
                            content=result.snippet,
                            score=0.5,
                            permalink=str(result.url)
                        )
                        for idx, result in enumerate(searxng_response.results[:3])
                    ]
                    
                    result = RAGResult(
                        answer=f"По вашему запросу найдена информация из внешних источников:\n\n" + 
                               "\n".join([f"• {s.title}: {s.content[:200]}" for s in searxng_response.results[:3]]),
                        sources=external_sources,
                        confidence=0.4,
                        intent=intent,
                        processing_time_ms=int((time.time() - start_time) * 1000)
                    )
                else:
                    result = RAGResult(
                        answer="К сожалению, по вашему запросу не найдено информации в каналах.",
                        sources=[],
                        confidence=0.0,
                        intent=intent,
                        processing_time_ms=int((time.time() - start_time) * 1000)
                    )
                
                # Сохранение в историю даже при отсутствии результатов
                try:
                    from models.database import RAGQueryHistory
                    rag_history = RAGQueryHistory(
                        user_id=user_id,
                        query_text=query,
                        query_type=intent,
                        intent=intent,
                        confidence=confidence,
                        response_text=result.answer[:5000] if isinstance(result.answer, str) else str(result.answer)[:5000],
                        sources_count=len(result.sources),
                        processing_time_ms=result.processing_time_ms,
                        audio_file_id=audio_file_id,
                        transcription_text=transcription_text,
                        transcription_provider="salutespeech" if transcription_text else None
                    )
                    db.add(rag_history)
                    db.commit()
                    logger.debug("RAG query saved to history (no results)", user_id=str(user_id), query_id=str(rag_history.id))
                except Exception as e:
                    logger.warning("Failed to save RAG query to history", error=str(e))
                
                return result
            
            # 4. Context7: Обогащение ответа через SearXNG (если нужно)
            enrichment_applied = False
            if await self._should_enrich_with_searxng(search_results, confidence, query):
                logger.info(
                    "Enriching answer with external sources",
                    query=query[:50],
                    results_count=len(search_results),
                    confidence=confidence
                )
                
                # Сначала собираем базовые источники
                context, sources = await self._assemble_context(search_results, db)
                
                # Обогащаем внешними источниками
                enriched_sources, confidence_boost = await self._enrich_with_searxng(
                    query=query,
                    user_id=str(user_id),
                    existing_sources=sources,
                    lang="ru"
                )
                
                # Context7: Добавляем внешние источники в context для LLM
                external_sources_in_context = [
                    source for source in enriched_sources 
                    if source.channel_id == "external"
                ]
                
                if external_sources_in_context:
                    external_context_parts = []
                    for idx, source in enumerate(external_sources_in_context, 1):
                        # Context7: Добавляем ссылку в контекст для inline использования
                        if source.permalink:
                            external_context_parts.append(
                                f"[Внешний источник {idx}] [{source.channel_title}]({source.permalink}): {source.content}"
                            )
                        else:
                            external_context_parts.append(
                                f"[Внешний источник {idx}] {source.channel_title}: {source.content}"
                            )
                    
                    if external_context_parts:
                        context += "\n\n" + "Внешние источники:\n" + "\n\n".join(external_context_parts)
                    
                    logger.debug(
                        "External sources added to context",
                        external_count=len(external_sources_in_context),
                        context_length=len(context)
                    )
                
                # Обновляем источники и confidence
                sources = enriched_sources
                confidence = min(1.0, confidence + confidence_boost)
                enrichment_applied = True
                
                logger.info(
                    "Enrichment applied",
                    query=query[:50],
                    sources_count=len(sources),
                    confidence_boost=confidence_boost,
                    final_confidence=confidence
                )
            else:
                # 4. Сборка контекста (без обогащения)
                context, sources = await self._assemble_context(search_results, db)
            
            # 5. Подготовка истории разговора для LangChain
            # Context7: Преобразуем список dict в LangChain Message объекты
            history_messages = []
            if conversation_history:
                for msg in conversation_history:
                    if msg.get("role") == "user":
                        history_messages.append(HumanMessage(content=msg.get("content", "")))
                    elif msg.get("role") == "assistant":
                        history_messages.append(AIMessage(content=msg.get("content", "")))
            
            # 6. Генерация ответа через LangChain intent router с conversation history
            router_input = {
                "query": query,
                "context": context,
                "intent": intent,
                "conversation_history": history_messages if history_messages else []
            }
            
            answer = await self.intent_router.ainvoke(router_input)
            
            # 6. Сохранение в историю запросов
            try:
                from models.database import RAGQueryHistory
                from datetime import timezone
                
                processing_time_ms = int((time.time() - start_time) * 1000)
                
                rag_history = RAGQueryHistory(
                    user_id=user_id,
                    query_text=query,
                    query_type=intent,
                    intent=intent,
                    confidence=confidence,
                    response_text=answer if isinstance(answer, str) else str(answer)[:5000],  # Ограничение длины
                    sources_count=len(sources),
                    processing_time_ms=processing_time_ms,
                    audio_file_id=audio_file_id,
                    transcription_text=transcription_text,
                    transcription_provider="salutespeech" if transcription_text else None
                )
                db.add(rag_history)
                db.commit()
                
                logger.debug(
                    "RAG query saved to history",
                    user_id=str(user_id),
                    query_id=str(rag_history.id),
                    intent=intent
                )
            except Exception as e:
                logger.warning("Failed to save RAG query to history", error=str(e))
                # Не прерываем выполнение, если сохранение не удалось
            
            # 7. Context7: Отслеживание интересов пользователя
            try:
                from services.user_interest_service import get_user_interest_service
                interest_service = get_user_interest_service(redis_client=self.redis_client)
                
                # Извлекаем темы из постов через PostEnrichment
                sources_for_tracking = []
                for source in sources:
                    post_topics = []
                    try:
                        post_uuid = UUID(source.post_id)
                        # Получаем enrichment с тегами/темами
                        enrichment = db.query(PostEnrichment).filter(
                            PostEnrichment.post_id == post_uuid,
                            PostEnrichment.kind == 'tags'
                        ).first()
                        
                        if enrichment and enrichment.data:
                            # Извлекаем теги из data->'tags' или из legacy поля tags
                            tags = enrichment.data.get('tags', [])
                            if not tags and enrichment.tags:
                                tags = enrichment.tags
                            
                            if isinstance(tags, list):
                                post_topics = [str(tag) for tag in tags if tag]
                    except Exception as e:
                        logger.debug("Error extracting topics from post", post_id=source.post_id, error=str(e))
                    
                    # Если нет тем из enrichment, используем ключевые слова из запроса
                    if not post_topics:
                        # Простое извлечение: первые 2-3 слова из запроса
                        words = query.lower().split()[:3]
                        if words:
                            post_topics = [' '.join(words)]
                    
                    if post_topics:
                        sources_for_tracking.append({'topics': post_topics})
                
                await interest_service.track_query(
                    user_id=user_id,
                    query_text=query,
                    intent=intent,
                    sources=sources_for_tracking,
                    db=db
                )
            except Exception as e:
                logger.warning("Failed to track user interest", error=str(e))
            
            processing_time = int((time.time() - start_time) * 1000)
            
            logger.info(
                "RAG query completed",
                query=query[:50],
                intent=intent,
                sources_count=len(sources),
                confidence=confidence,
                enrichment_applied=enrichment_applied,
                processing_time_ms=processing_time
            )
            
            return RAGResult(
                answer=answer,
                sources=sources[:limit],
                confidence=confidence,
                intent=intent,
                processing_time_ms=processing_time
            )
        
        except Exception as e:
            logger.error("Error in RAG query", error=str(e), query=query[:50])
            error_result = RAGResult(
                answer="Произошла ошибка при обработке запроса. Попробуйте позже.",
                sources=[],
                confidence=0.0,
                intent="search",
                processing_time_ms=int((time.time() - start_time) * 1000)
            )
            
            # Сохранение в историю даже при ошибке
            try:
                from models.database import RAGQueryHistory
                rag_history = RAGQueryHistory(
                    user_id=user_id,
                    query_text=query,
                    query_type="search",
                    intent="search",
                    confidence=0.0,
                    response_text=error_result.answer[:5000],
                    sources_count=0,
                    processing_time_ms=error_result.processing_time_ms,
                    audio_file_id=audio_file_id,
                    transcription_text=transcription_text,
                    transcription_provider="salutespeech" if transcription_text else None
                )
                db.add(rag_history)
                db.commit()
                logger.debug("RAG query saved to history (error case)", user_id=str(user_id), query_id=str(rag_history.id))
            except Exception as save_error:
                logger.warning("Failed to save RAG query to history (error case)", error=str(save_error))
            
            return error_result


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_rag_service: Optional[RAGService] = None


def get_rag_service(
    qdrant_url: Optional[str] = None,
    redis_client: Optional[Any] = None
) -> RAGService:
    """Получение singleton экземпляра RAGService."""
    global _rag_service
    if _rag_service is None:
        qdrant_url = qdrant_url or getattr(settings, 'qdrant_url', 'http://qdrant:6333')
        _rag_service = RAGService(
            qdrant_url=qdrant_url,
            redis_client=redis_client
        )
    return _rag_service

