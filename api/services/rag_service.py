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
from datetime import datetime, timezone, timedelta

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
from worker.agents.context_router_agent import get_context_router
from worker.services.episodic_memory_service import get_episodic_memory_service
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
    # Performance metrics (для отслеживания Performance KPIs)
    llm_calls: int = 1
    tokens_used: int = 0
    agent_steps: int = 1


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
        
        # Инициализация IntentClassifier (fallback)
        self.intent_classifier = get_intent_classifier(redis_client=redis_client)
        
        # Context Router Agent для интеллектуальной маршрутизации
        # Performance: сверхлёгкий router с эвристикой и кэшем
        try:
            self.context_router = get_context_router()
        except Exception as exc:
            logger.warning("context_router.initialization_failed", error=str(exc))
            self.context_router = None
        
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
        
        # Context7: Получаем credentials как строку (не SecretStr)
        # Используем load_gigachat_credentials для правильного формата
        try:
            from api.worker.tasks.group_digest_agent import load_gigachat_credentials
            gigachat_creds = load_gigachat_credentials()
            credentials_str = gigachat_creds.get("credentials")
            scope_str = gigachat_creds.get("scope", "GIGACHAT_API_PERS")
            base_url_giga = gigachat_creds.get("base_url") or api_base
            verify_ssl = gigachat_creds.get("verify_ssl_certs", False)
        except Exception as e:
            logger.warning("rag_service.gigachat_credentials_load_failed", error=str(e))
            # Fallback на env переменные
            credentials_str = os.getenv('GIGACHAT_CREDENTIALS', '')
            scope_str = os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS')
            base_url_giga = api_base
            verify_ssl = False
        
        # Context7: Преобразуем SecretStr в строку, если нужно
        if hasattr(credentials_str, 'get_secret_value'):
            credentials_str = credentials_str.get_secret_value()
        
        self.llm = GigaChat(
            credentials=credentials_str,
            scope=scope_str,
            base_url=base_url_giga,
            verify_ssl_certs=verify_ssl,
            model="GigaChat",
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
        channel_ids: Optional[List[str]] = None,
        time_filter: Optional[tuple[datetime, Optional[datetime]]] = None,
        db: Optional[Session] = None
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
            
            # Context7: Qdrant не поддерживает фильтрацию по времени напрямую
            # Берем больше результатов для последующей фильтрации по времени
            search_limit = limit * 3 if time_filter else limit
            
            # Поиск
            # Context7: Используем query_points (правильный метод для QdrantClient)
            # query_points принимает вектор напрямую в параметре query и filter отдельно
            try:
                search_response = self.qdrant_client.query_points(
                    collection_name=collection_name,
                    query=query_embedding,  # Вектор напрямую
                    query_filter=search_filter,  # Фильтр отдельно
                    limit=search_limit
                )
                
                # Context7: query_points возвращает QueryResponse с points
                search_results = search_response.points if hasattr(search_response, 'points') else []
                
            except AttributeError as e:
                # Context7: Fallback на search если query_points недоступен (старая версия)
                try:
                    search_results = self.qdrant_client.search(
                        collection_name=collection_name,
                        query_vector=query_embedding,
                        query_filter=search_filter,
                        limit=search_limit
                    )
                except Exception as fallback_error:
                    logger.error(
                        "QdrantClient search methods not available",
                        error=str(fallback_error),
                        collection_name=collection_name,
                        qdrant_client_type=type(self.qdrant_client).__name__
                    )
                    return []
            except Exception as e:
                logger.error(
                    "Error searching Qdrant",
                    error=str(e),
                    collection_name=collection_name,
                    error_type=type(e).__name__
                )
                # Возвращаем пустой список вместо падения
                return []
            
            results = []
            # Context7: Обработка результатов из query_points
            logger.info(
                "Processing Qdrant search results",
                collection_name=collection_name,
                results_count=len(search_results) if search_results else 0,
                has_search_results=bool(search_results)
            )
            
            for result in search_results:
                # query_points возвращает ScoredPoint объекты
                if hasattr(result, 'payload') and hasattr(result, 'score'):
                    # Формат query_points (ScoredPoint)
                    payload = result.payload if result.payload else {}
                    score = result.score if hasattr(result, 'score') else 0.0
                elif hasattr(result, 'point'):
                    # Альтернативный формат (если есть)
                    point = result.point
                    payload = point.payload if hasattr(point, 'payload') else {}
                    score = result.score if hasattr(result, 'score') else 0.0
                else:
                    # Неизвестный формат - пропускаем
                    logger.warning("Unknown result format in Qdrant search", result_type=type(result).__name__)
                    continue
                
                post_id = payload.get('post_id')
                if not post_id:
                    logger.debug("Skipping result without post_id", payload_keys=list(payload.keys()))
                    continue
                
                results.append({
                    'post_id': post_id,
                    'score': score,
                    'payload': payload
                })
            
            logger.info(
                "Qdrant search completed",
                collection_name=collection_name,
                total_results=len(results),
                limit=limit,
                tenant_id=tenant_id
            )
            
            # Context7: Фильтруем результаты по времени после получения из БД
            if time_filter and db and results:
                filter_start_time, filter_end_time = time_filter
                logger.info(
                    "Filtering Qdrant results by time",
                    collection_name=collection_name,
                    results_before=len(results),
                    filter_start_time=filter_start_time.isoformat() if filter_start_time else None,
                    filter_end_time=filter_end_time.isoformat() if filter_end_time else None,
                    tenant_id=tenant_id
                )
                post_ids = [UUID(r['post_id']) for r in results if r.get('post_id')]
                
                if post_ids:
                    # Загружаем посты из БД для проверки времени
                    posts = db.query(Post).filter(Post.id.in_(post_ids)).all()
                    post_map = {str(post.id): post for post in posts}
                    
                    logger.info(
                        "Posts loaded from DB for time filtering",
                        collection_name=collection_name,
                        post_ids_count=len(post_ids),
                        posts_found=len(posts),
                        tenant_id=tenant_id
                    )
                    
                    # Фильтруем результаты по времени
                    filtered_results = []
                    skipped_no_post = 0
                    skipped_no_time = 0
                    skipped_too_old = 0
                    skipped_too_new = 0
                    
                    for result in results:
                        post_id = result.get('post_id')
                        if not post_id:
                            continue
                        
                        post = post_map.get(post_id)
                        if not post:
                            skipped_no_post += 1
                            logger.debug("Post not found in DB", post_id=post_id)
                            continue
                        
                        if not post.posted_at:
                            skipped_no_time += 1
                            logger.debug("Post has no posted_at", post_id=post_id)
                            continue
                        
                        # Проверяем временной фильтр
                        # Context7: Учитываем timezone при сравнении
                        post_time = post.posted_at
                        if post_time.tzinfo is None:
                            # Если время без timezone, считаем его UTC
                            from datetime import timezone
                            post_time = post_time.replace(tzinfo=timezone.utc)
                        
                        # Приводим filter_start_time к timezone-aware если нужно
                        filter_start_aware = filter_start_time
                        if filter_start_aware and filter_start_aware.tzinfo is None:
                            from datetime import timezone
                            filter_start_aware = filter_start_aware.replace(tzinfo=timezone.utc)
                        
                        # Приводим filter_end_time к timezone-aware если нужно
                        filter_end_aware = filter_end_time
                        if filter_end_aware and filter_end_aware.tzinfo is None:
                            from datetime import timezone
                            filter_end_aware = filter_end_aware.replace(tzinfo=timezone.utc)
                        
                        if filter_start_aware and post_time < filter_start_aware:
                            skipped_too_old += 1
                            logger.debug(
                                "Post filtered out: too old",
                                post_id=post_id,
                                post_time=post_time.isoformat(),
                                filter_start_time=filter_start_aware.isoformat()
                            )
                            continue
                        if filter_end_aware and post_time > filter_end_aware:
                            skipped_too_new += 1
                            logger.debug(
                                "Post filtered out: too new",
                                post_id=post_id,
                                post_time=post_time.isoformat(),
                                filter_end_time=filter_end_aware.isoformat()
                            )
                            continue
                        
                        filtered_results.append(result)
                    
                    logger.info(
                        "Time filtering completed",
                        collection_name=collection_name,
                        results_after=len(filtered_results),
                        filtered_out=len(results) - len(filtered_results),
                        skipped_no_post=skipped_no_post,
                        skipped_no_time=skipped_no_time,
                        skipped_too_old=skipped_too_old,
                        skipped_too_new=skipped_too_new,
                        tenant_id=tenant_id
                    )
                    
                    # Context7: Если все результаты отфильтрованы, возвращаем хотя бы часть без фильтра
                    # Это защита от слишком строгого временного фильтра
                    if not filtered_results and results:
                        logger.warning(
                            "All results filtered out by time filter, returning unfiltered results",
                            collection_name=collection_name,
                            original_count=len(results),
                            tenant_id=tenant_id
                        )
                        # Возвращаем первые результаты без фильтрации по времени
                        results = results[:limit]
                    else:
                        # Ограничиваем количество результатов
                        results = filtered_results[:limit]
            
            return results
        
        except Exception as e:
            logger.error("Error searching Qdrant", error=str(e))
            return []
    
    def _parse_time_filter(self, query: str) -> Optional[tuple[datetime, Optional[datetime]]]:
        """
        Парсит временные фразы из запроса и возвращает временной интервал.
        
        Context7: Поддерживает различные временные фразы для фильтрации постов по времени публикации.
        
        Args:
            query: Текст запроса пользователя
            
        Returns:
            Tuple (start_time, end_time) или None если временная фраза не найдена.
            end_time может быть None для открытых интервалов (например, "сегодня").
        """
        query_lower = query.lower()
        now = datetime.now(timezone.utc)
        
        # "сегодня" → [today 00:00 UTC; now)
        if "сегодня" in query_lower:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return (start, None)
        
        # "вчера" → [yesterday 00:00 UTC; yesterday 23:59:59 UTC]
        if "вчера" in query_lower:
            yesterday = now - timedelta(days=1)
            start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
            return (start, end)
        
        # "за эту неделю", "на этой неделе" → [понедельник 00:00 UTC; now)
        if "за эту неделю" in query_lower or "на этой неделе" in query_lower or "эту неделю" in query_lower:
            # Находим понедельник текущей недели
            days_since_monday = now.weekday()  # 0 = понедельник
            monday = now - timedelta(days=days_since_monday)
            start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
            return (start, None)
        
        # "за прошлую неделю" → [понедельник прошлой недели 00:00; воскресенье 23:59:59]
        if "за прошлую неделю" in query_lower or "на прошлой неделе" in query_lower:
            days_since_monday = now.weekday()
            # Понедельник прошлой недели
            last_monday = now - timedelta(days=days_since_monday + 7)
            start = last_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            # Воскресенье прошлой недели
            last_sunday = last_monday + timedelta(days=6)
            end = last_sunday.replace(hour=23, minute=59, second=59, microsecond=999999)
            return (start, end)
        
        # "за последние 24 часа" / "за последние N часов"
        import re
        hours_match = re.search(r'за последние (\d+)\s*час', query_lower)
        if hours_match:
            hours = int(hours_match.group(1))
            start = now - timedelta(hours=hours)
            return (start, None)
        
        # "за последние 7 дней" / "за последнюю неделю"
        if "за последние 7 дней" in query_lower or "за последнюю неделю" in query_lower:
            start = now - timedelta(days=7)
            return (start, None)
        
        # "за последний месяц"
        if "за последний месяц" in query_lower:
            start = now - timedelta(days=30)
            return (start, None)
        
        # "за день" / "за неделю" / "за месяц"
        if "за день" in query_lower:
            start = now - timedelta(days=1)
            return (start, None)
        
        if "за неделю" in query_lower:
            start = now - timedelta(days=7)
            return (start, None)
        
        if "за месяц" in query_lower:
            start = now - timedelta(days=30)
            return (start, None)
        
        # "за последние N дней"
        days_match = re.search(r'за последние (\d+)\s*дн', query_lower)
        if days_match:
            days = int(days_match.group(1))
            start = now - timedelta(days=days)
            return (start, None)
        
        return None
    
    def _extract_keywords_for_fts(self, query: str) -> str:
        """
        Извлекает ключевые слова из запроса для FTS.
        
        Context7: Убирает служебные слова и временные фразы, оставляет только значимые термины.
        """
        # Список стоп-слов для русского и английского
        stop_words = {
            'найти', 'найди', 'найти', 'поиск', 'искать', 'ищу',
            'новости', 'новость', 'новостей', 'новостями',
            'по', 'за', 'за последние', 'за последние 24 часа', 'за последний', 'за день', 'за неделю',
            'последние', 'последний', 'последних', 'последними',
            '24', 'часа', 'час', 'часов', 'часам',
            'день', 'дня', 'дней', 'дням',
            'неделя', 'недели', 'недель', 'неделям',
            'месяц', 'месяца', 'месяцев', 'месяцам',
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'having', 'do', 'does', 'did', 'doing',
            'will', 'would', 'could', 'should', 'may', 'might', 'must',
            'find', 'search', 'look', 'for', 'news', 'about', 'last', 'hours', 'days'
        }
        
        # Разбиваем запрос на слова и фильтруем
        words = query.lower().split()
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        # Если ключевых слов нет, возвращаем упрощенный запрос
        if not keywords:
            # Пытаемся найти хотя бы одно значимое слово
            all_words = query.lower().split()
            keywords = [w for w in all_words if len(w) > 2][:5]  # Берем первые 5 слов длиннее 2 символов
        
        return ' '.join(keywords) if keywords else query
    
    async def _search_postgres_fts(
        self,
        query: str,
        tenant_id: str,
        limit: int = 10,
        channel_ids: Optional[List[str]] = None,
        db: Optional[Session] = None,
        time_filter: Optional[tuple[datetime, Optional[datetime]]] = None
    ) -> List[Dict[str, Any]]:
        """Поиск через PostgreSQL Full-Text Search."""
        if not db:
            return []
        
        try:
            # Context7: Извлекаем ключевые слова из запроса для более точного поиска
            fts_query_text = self._extract_keywords_for_fts(query)
            
            # Context7: PostgreSQL FTS для keyword search
            # Используем tsvector для поиска по content
            # Context7: Фильтрация по tenant_id через JOIN с user_channel и users (каналы глобальные, нет tenant_id)
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
            
            params = {"query": fts_query_text, "tenant_id": tenant_id, "limit": limit}
            
            # Добавляем фильтрацию по времени, если указана
            if time_filter:
                filter_start_time, filter_end_time = time_filter
                if filter_start_time:
                    base_query += " AND p.posted_at >= :start_time"
                    params["start_time"] = filter_start_time
                if filter_end_time:
                    base_query += " AND p.posted_at <= :end_time"
                    params["end_time"] = filter_end_time
            
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
    
    async def _search_postgres_fts_relaxed(
        self,
        query: str,
        tenant_id: str,
        limit: int = 10,
        channel_ids: Optional[List[str]] = None,
        db: Optional[Session] = None,
        time_filter: Optional[tuple[datetime, Optional[datetime]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Relaxed PostgreSQL FTS поиск - использует ILIKE для частичного совпадения.
        
        Context7: Используется как fallback когда точный FTS не находит результаты.
        Ищет по отдельным словам запроса, а не по всем словам сразу.
        """
        if not db:
            return []
        
        try:
            # Разбиваем запрос на слова
            query_words = query.split()
            if not query_words:
                return []
            
            # Context7: Используем ILIKE для поиска по отдельным словам
            # Это более мягкий поиск, который найдет посты содержащие хотя бы одно слово
            # Context7: Фильтрация по tenant_id через JOIN с user_channel и users (каналы глобальные)
            word_conditions = " OR ".join([f"p.content ILIKE :word_{i}" for i in range(len(query_words))])
            
            base_query = f"""
                SELECT DISTINCT
                    p.id,
                    p.channel_id,
                    p.content,
                    p.telegram_post_url,
                    p.posted_at,
                    0.5 as rank
                FROM posts p
                JOIN channels c ON p.channel_id = c.id
                JOIN user_channel uc ON uc.channel_id = c.id
                JOIN users u ON u.id = uc.user_id
                WHERE ({word_conditions})
                    AND u.tenant_id = CAST(:tenant_id AS uuid)
                    AND p.content IS NOT NULL
                    AND p.content != ''
            """
            
            params = {"tenant_id": tenant_id, "limit": limit}
            for i, word in enumerate(query_words):
                params[f"word_{i}"] = f"%{word}%"
            
            # Добавляем фильтрацию по времени, если указана
            if time_filter:
                filter_start_time, filter_end_time = time_filter
                if filter_start_time:
                    base_query += " AND p.posted_at >= :start_time"
                    params["start_time"] = filter_start_time
                if filter_end_time:
                    base_query += " AND p.posted_at <= :end_time"
                    params["end_time"] = filter_end_time
            
            # Добавляем фильтрацию по channel_ids если указаны
            if channel_ids:
                base_query += " AND p.channel_id = ANY(CAST(:channel_ids AS uuid[]))"
                params["channel_ids"] = channel_ids
            
            base_query += " ORDER BY p.posted_at DESC LIMIT :limit"
            
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
                    'score': float(row.rank) if row.rank else 0.3  # Низкий score для relaxed поиска
                })
            
            return results
        
        except Exception as e:
            logger.error("Error searching PostgreSQL FTS relaxed", error=str(e))
            return []
    
    async def _search_neo4j_graph(
        self,
        query: str,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 10,
        max_depth: Optional[int] = None,
        time_filter: Optional[tuple[datetime, Optional[datetime]]] = None
    ) -> List[Dict[str, Any]]:
        """
        GraphRAG поиск через Neo4j.
        
        Context7: Использует графовые связи для поиска связанных постов
        
        Args:
            query: Текст запроса
            user_id: ID пользователя (для фильтрации по интересам)
            limit: Максимальное количество результатов
            max_depth: Максимальная глубина обхода графа
            time_filter: Временной фильтр (start_time, end_time)
        
        Returns:
            Список связанных постов из графа
        """
        try:
            # Context7: Проверка кэша Redis (включаем time_filter в ключ кэша)
            if self.redis_client:
                time_filter_str = f"{time_filter[0].isoformat() if time_filter and time_filter[0] else ''}_{time_filter[1].isoformat() if time_filter and time_filter[1] else ''}" if time_filter else ""
                cache_key = f"graphrag:query:{hashlib.sha1((query + (user_id or '') + time_filter_str).encode()).hexdigest()}"
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
                max_depth=max_depth,
                time_filter=time_filter
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
        user_id: Optional[str] = None,
        time_filter: Optional[tuple[datetime, Optional[datetime]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search: Qdrant ANN + PostgreSQL FTS + Neo4j GraphRAG с re-ranking.
        
        Context7: Тройной гибрид:
        - Qdrant (вес 0.5) - семантический поиск
        - PostgreSQL FTS (вес 0.2) - keyword search
        - Neo4j GraphRAG (вес 0.3) - графовые связи и интересы пользователя
        """
        # Параллельный поиск в Qdrant, PostgreSQL и Neo4j
        qdrant_results = await self._search_qdrant(query_embedding, tenant_id, limit * 2, channel_ids, time_filter=time_filter, db=db)
        fts_results = await self._search_postgres_fts(query, tenant_id, limit * 2, channel_ids, db, time_filter=time_filter)
        
        logger.info(
            "Hybrid search intermediate results",
            qdrant_count=len(qdrant_results) if qdrant_results else 0,
            fts_count=len(fts_results) if fts_results else 0,
            graph_count=0,  # Будет обновлено после GraphRAG
            query=query[:50],
            tenant_id=tenant_id
        )
        
        # Context7: GraphRAG поиск (с fallback при недоступности Neo4j)
        graph_results = []
        try:
            graph_results = await self._search_neo4j_graph(query, user_id, tenant_id=tenant_id, limit=limit * 2, time_filter=time_filter)
            logger.info(
                "GraphRAG search completed",
                graph_count=len(graph_results) if graph_results else 0,
                query=query[:50],
                tenant_id=tenant_id
            )
        except Exception as e:
            logger.warning("GraphRAG search failed, continuing without graph results", error=str(e), tenant_id=tenant_id)
        
        # Объединение и дедупликация результатов
        post_scores = {}
        
        # Добавляем результаты из Qdrant (вес 0.5)
        qdrant_added = 0
        for result in qdrant_results:
            post_id = result.get('post_id')
            if not post_id:
                logger.warning("Qdrant result without post_id", result_keys=list(result.keys()))
                continue
            score = result.get('score', 0.0) * 0.5
            if post_id not in post_scores:
                post_scores[post_id] = {
                    'post_id': post_id,
                    'payload': result.get('payload', {}),
                    'qdrant_score': result.get('score', 0.0),
                    'fts_score': 0.0,
                    'graph_score': 0.0,
                    'hybrid_score': score
                }
                qdrant_added += 1
            else:
                post_scores[post_id]['hybrid_score'] += score
                post_scores[post_id]['qdrant_score'] = result.get('score', 0.0)
        
        logger.info(
            "Qdrant results added to hybrid search",
            qdrant_input=len(qdrant_results),
            qdrant_added=qdrant_added,
            post_scores_count=len(post_scores),
            tenant_id=tenant_id
        )
        
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
        
        final_results = sorted_results[:limit]
        
        logger.info(
            "Hybrid search completed",
            query=query[:50],
            total_posts=len(post_scores),
            final_results=len(final_results),
            top_scores=[round(r['hybrid_score'], 3) for r in final_results[:3]] if final_results else [],
            tenant_id=tenant_id,
            qdrant_input=len(qdrant_results) if qdrant_results else 0,
            fts_input=len(fts_results) if fts_results else 0,
            graph_input=len(graph_results) if graph_results else 0
        )
        
        if not final_results:
            logger.warning(
                "Hybrid search returned empty results",
                query=query[:50],
                qdrant_count=len(qdrant_results) if qdrant_results else 0,
                fts_count=len(fts_results) if fts_results else 0,
                graph_count=len(graph_results) if graph_results else 0,
                post_scores_count=len(post_scores),
                tenant_id=tenant_id
            )
        
        return final_results
    
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
    
    def _safe_get_processing_time_ms(self, start_time: Any) -> int:
        """
        Context7: Безопасное вычисление времени обработки.
        Защищает от переопределения start_time (может быть datetime из time_filter).
        
        Args:
            start_time: Начальное время (float timestamp или datetime)
        
        Returns:
            Время обработки в миллисекундах
        """
        if hasattr(start_time, 'timestamp'):
            # Если start_time это datetime, конвертируем в timestamp
            start_timestamp = start_time.timestamp()
        elif isinstance(start_time, (int, float)):
            # Если start_time это число (timestamp), используем как есть
            start_timestamp = start_time
        else:
            # Если start_time не число и не datetime, используем текущее время
            logger.warning(
                "Invalid start_time type, using current time",
                start_time_type=type(start_time).__name__
            )
            start_timestamp = time.time()
        
        return int((time.time() - start_timestamp) * 1000)
    
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
        query_start_time = time.time()  # Context7: Используем query_start_time вместо start_time
        
        # Episodic Memory: запись события run_started для RAG
        try:
            episodic_memory = get_episodic_memory_service()
            episodic_memory.record_event(
                tenant_id=tenant_id,
                entity_type="rag",
                event_type="run_started",
                metadata={
                    "user_id": str(user_id),
                    "query_length": len(query),
                    "has_audio": audio_file_id is not None,
                },
            )
        except Exception as mem_exc:
            logger.warning("episodic_memory.record_start_failed", error=str(mem_exc), tenant_id=tenant_id)
        
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
            
            # 1. Классификация намерения через Context Router Agent
            # Performance: сначала Context Router (эвристика + кэш), fallback на IntentClassifier
            if intent_override:
                intent = intent_override
                confidence = 1.0  # Высокая уверенность для принудительного намерения
                logger.debug("Using intent override", intent=intent, query=query[:50])
            else:
                # Получаем последние события из episodic memory для диагностики
                recent_events = []
                try:
                    episodic_memory = get_episodic_memory_service()
                    recent_events = episodic_memory.get_recent_events(
                        tenant_id=tenant_id,
                        entity_type="rag",
                        limit=5,
                        hours=24,
                    )
                    # Преобразуем в формат для Context Router
                    recent_events = [
                        {
                            "event_type": event.event_type,
                            "entity_type": event.entity_type,
                            "created_at": event.created_at.isoformat() if hasattr(event.created_at, 'isoformat') else str(event.created_at),
                        }
                        for event in recent_events
                    ]
                except Exception as mem_exc:
                    logger.warning("episodic_memory.fetch_failed", error=str(mem_exc))
                
                # Используем Context Router Agent (если доступен)
                if self.context_router:
                    try:
                        route_result = self.context_router.route(
                            query=query,
                            tenant_id=tenant_id,
                            user_id=str(user_id),
                            recent_events=recent_events,
                        )
                        
                        # Маппинг route_type в intent для обратной совместимости
                        route_to_intent = {
                            "digest": "digest",
                            "qna": "ask",
                            "trend": "trend",
                            "search": "search",
                            "enrichment": "search",  # Обогащение → поиск
                            "admin": "search",  # Админ → поиск
                        }
                        intent = route_to_intent.get(route_result.route_type, "ask")
                        confidence = route_result.confidence
                        
                        logger.info(
                            "context_router.route_success",
                            query=query[:50],
                            route_type=route_result.route_type,
                            intent=intent,
                            confidence=confidence,
                            method=route_result.method,
                        )
                    except Exception as router_exc:
                        # Fallback на IntentClassifier при ошибке Context Router
                        logger.warning(
                            "context_router.route_failed",
                            error=str(router_exc),
                            falling_back_to="intent_classifier"
                        )
                        intent_result = await self.intent_classifier.classify(query, str(user_id))
                        intent = intent_result.intent
                        confidence = intent_result.confidence
                else:
                    # Fallback на IntentClassifier, если Context Router недоступен
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
                        
                        processing_time = self._safe_get_processing_time_ms(query_start_time)
                        
                        return RAGResult(
                            answer=answer,
                            sources=sources[:limit],
                            confidence=confidence,
                            intent=intent,
                            processing_time_ms=processing_time,
                            llm_calls=1,  # Один вызов LLM для генерации ответа
                            tokens_used=0,  # TODO: отслеживать токены из LLM ответа
                            agent_steps=1  # Один шаг агента для рекомендаций
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
            
            # 2. Парсинг временного фильтра из запроса
            time_filter = self._parse_time_filter(query)
            if time_filter:
                filter_start_time, filter_end_time = time_filter
                logger.info(
                    "Time filter applied",
                    query=query[:50],
                    filter_start_time=filter_start_time.isoformat() if filter_start_time else None,
                    filter_end_time=filter_end_time.isoformat() if filter_end_time else None,
                    tenant_id=tenant_id,
                    user_id=str(user_id)
                )
                # Обновляем time_filter с правильными именами переменных
                time_filter = (filter_start_time, filter_end_time)
            else:
                logger.info(
                    "No time filter in query",
                    query=query[:50],
                    tenant_id=tenant_id
                )
            
            # 3. Генерация embedding для запроса
            query_embedding = await self._generate_embedding(query)
            
            if not query_embedding:
                logger.warning(
                    "Failed to generate embedding, falling back to FTS only",
                    query=query[:50],
                    tenant_id=tenant_id,
                    user_id=str(user_id)
                )
            else:
                logger.info(
                    "Embedding generated successfully",
                    query=query[:50],
                    embedding_dim=len(query_embedding) if query_embedding else 0,
                    tenant_id=tenant_id
                )
            
            # 4. Hybrid search (Qdrant + PostgreSQL FTS + Neo4j GraphRAG)
            if query_embedding:
                search_results = await self._hybrid_search(
                    query, query_embedding, tenant_id, limit * 2, channel_ids, db, user_id=str(user_id), time_filter=time_filter
                )
                logger.info(
                    "Hybrid search completed in query",
                    query=query[:50],
                    results_count=len(search_results) if search_results else 0,
                    has_embedding=True,
                    tenant_id=tenant_id
                )
            else:
                # Fallback на FTS + GraphRAG (без векторов)
                logger.debug("No embedding, using FTS + GraphRAG fallback", query=query[:50])
                fts_results = await self._search_postgres_fts(
                    query, tenant_id, limit * 2, channel_ids, db, time_filter=time_filter
                )
                graph_results = await self._search_neo4j_graph(query, str(user_id), tenant_id=tenant_id, limit=limit * 2, time_filter=time_filter)
                
                logger.debug(
                    "FTS + GraphRAG fallback completed",
                    fts_count=len(fts_results) if fts_results else 0,
                    graph_count=len(graph_results) if graph_results else 0
                )
                
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
                
                logger.debug(
                    "FTS + GraphRAG results combined",
                    total_results=len(search_results)
                )
            
            logger.info(
                "Search results after hybrid search",
                query=query[:50],
                results_count=len(search_results) if search_results else 0,
                tenant_id=tenant_id
            )
            
            if not search_results:
                logger.warning("No search results found", query=query[:50], tenant_id=tenant_id)
                # Context7: Улучшенная обработка отсутствия результатов
                # Сначала пытаемся найти похожие посты через более мягкий поиск
                # (без точного совпадения всех слов)
                logger.info("Trying relaxed FTS search", query=query[:50])
                relaxed_fts_results = await self._search_postgres_fts_relaxed(
                    query, tenant_id, limit * 2, channel_ids, db, time_filter=time_filter
                )
                
                if relaxed_fts_results:
                    logger.info("Found results with relaxed search", count=len(relaxed_fts_results))
                    # Преобразуем relaxed результаты в формат search_results для дальнейшей обработки
                    # Context7: Формат должен быть совместим с _assemble_context (нужен прямой доступ к полям)
                    search_results = []
                    for r in relaxed_fts_results:
                        search_results.append({
                            'post_id': r['post_id'],
                            'channel_id': r.get('channel_id'),
                            'content': r.get('content', ''),
                            'permalink': r.get('permalink'),
                            'score': r['score'],
                            'hybrid_score': r['score']
                        })
                else:
                    # Только если и relaxed поиск не дал результатов - используем SearXNG
                    logger.info("No results even with relaxed search, using SearXNG fallback")
                    searxng_response = await self.searxng_service.search(
                        query, str(user_id), lang="ru"
                    )
                    
                    if searxng_response.results:
                        # Context7: Даже внешние источники должны проходить через LLM для структурированного ответа
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
                            for idx, result in enumerate(searxng_response.results[:5])  # Берем больше для контекста
                        ]
                        
                        # Context7: Формируем контекст из внешних источников для LLM
                        external_context_parts = []
                        for idx, source in enumerate(external_sources, 1):
                            if source.permalink:
                                external_context_parts.append(
                                    f"[Внешний источник {idx}] [{source.channel_title}]({source.permalink}): {source.content}"
                                )
                            else:
                                external_context_parts.append(
                                    f"[Внешний источник {idx}] {source.channel_title}: {source.content}"
                                )
                        
                        context = "Внешние источники:\n\n" + "\n\n".join(external_context_parts)
                        
                        # Context7: Генерируем структурированный ответ через LLM
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
                        
                        result = RAGResult(
                            answer=answer,
                            sources=external_sources[:limit],  # Ограничиваем количество источников
                            confidence=0.4,
                            intent=intent,
                            processing_time_ms=self._safe_get_processing_time_ms(query_start_time),
                            llm_calls=1,
                            tokens_used=0,  # TODO: отслеживать токены
                            agent_steps=1
                        )
                        
                        # Сохраняем в историю
                        try:
                            from models.database import RAGQueryHistory
                            rag_history = RAGQueryHistory(
                                user_id=user_id,
                                query_text=query,
                                query_type=intent,
                                intent=intent,
                                confidence=0.4,
                                response_text=answer[:5000],
                                sources_count=len(external_sources),
                                processing_time_ms=result.processing_time_ms,
                                audio_file_id=audio_file_id,
                                transcription_text=transcription_text,
                                transcription_provider="salutespeech" if transcription_text else None
                            )
                            db.add(rag_history)
                            db.commit()
                        except Exception as e:
                            logger.warning("Failed to save RAG query to history (external only)", error=str(e))
                            try:
                                db.rollback()
                            except Exception:
                                pass
                        
                        return result
                    else:
                        result = RAGResult(
                            answer="К сожалению, по вашему запросу не найдено информации в каналах.",
                            sources=[],
                            confidence=0.0,
                            intent=intent,
                            processing_time_ms=self._safe_get_processing_time_ms(query_start_time)
                        )
                        # Возвращаем результат сразу, не продолжаем обработку
                        return result
                
                # Context7: Если дошли до этого места без результатов, создаем пустой результат
                # Это может произойти, если все поиски (включая relaxed и SearXNG) не дали результатов
                result = RAGResult(
                    answer="К сожалению, по вашему запросу не найдено информации в каналах.",
                    sources=[],
                    confidence=0.0,
                    intent=intent,
                    processing_time_ms=self._safe_get_processing_time_ms(query_start_time)
                )
                
                # Context7: Сохранение в историю даже при отсутствии результатов (критично для аналитики)
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
                    logger.info(
                        "RAG query saved to history (no results)",
                        user_id=str(user_id),
                        query_id=str(rag_history.id),
                        intent=intent
                    )
                except Exception as e:
                    logger.error(
                        "Failed to save RAG query to history (no results)",
                        user_id=str(user_id),
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True
                    )
                    try:
                        db.rollback()
                    except Exception:
                        pass
                
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
            
            # 6. Сохранение в историю запросов (Context7: критично для аналитики)
            try:
                from models.database import RAGQueryHistory
                from datetime import timezone
                
                processing_time_ms = self._safe_get_processing_time_ms(query_start_time)
                
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
                
                logger.info(
                    "RAG query saved to history",
                    user_id=str(user_id),
                    query_id=str(rag_history.id),
                    intent=intent,
                    sources_count=len(sources),
                    confidence=confidence
                )
            except Exception as e:
                logger.error(
                    "Failed to save RAG query to history",
                    user_id=str(user_id),
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True
                )
                # Context7: Пытаемся откатить транзакцию и продолжить
                try:
                    db.rollback()
                except Exception as rollback_error:
                    logger.warning("Failed to rollback after RAG history save error", error=str(rollback_error))
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
                            # Context7: Извлекаем теги только из enrichment.data
                            # Убрана ссылка на несуществующую колонку enrichment.tags
                            tags = enrichment.data.get('tags', [])
                            
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
            
            processing_time = self._safe_get_processing_time_ms(query_start_time)
            
            # Episodic Memory: запись события run_completed для RAG
            try:
                episodic_memory = get_episodic_memory_service()
                episodic_memory.record_event(
                    tenant_id=tenant_id,
                    entity_type="rag",
                    event_type="run_completed",
                    metadata={
                        "user_id": str(user_id),
                        "intent": intent,
                        "confidence": confidence,
                        "sources_count": len(sources),
                        "processing_time_ms": processing_time,
                        "enrichment_applied": enrichment_applied,
                    },
                )
            except Exception as mem_exc:
                logger.warning("episodic_memory.record_complete_failed", error=str(mem_exc), tenant_id=tenant_id)
            
            logger.info(
                "RAG query completed",
                query=query[:50],
                intent=intent,
                sources_count=len(sources),
                confidence=confidence,
                enrichment_applied=enrichment_applied,
                processing_time_ms=processing_time
            )
            
            # Подсчет метрик производительности
            # LLM вызовы: router (1) + генерация ответа (1) = 2 минимум
            llm_calls_count = 2
            # Если использовался Context Router с LLM, добавляем еще один вызов
            if self.context_router and hasattr(self.context_router, '_llm_chain') and self.context_router._llm_chain:
                # Context Router может использовать LLM для маршрутизации
                # Но если использовалась эвристика, то LLM не вызывался
                # Для простоты считаем, что router может использовать LLM
                pass  # Уже учтено в базовом подсчете
            
            return RAGResult(
                answer=answer,
                sources=sources[:limit],
                confidence=confidence,
                intent=intent,
                processing_time_ms=processing_time,
                llm_calls=llm_calls_count,
                tokens_used=0,  # TODO: отслеживать токены из LLM ответа
                agent_steps=1  # Один шаг агента для RAG запроса
            )
        
        except Exception as e:
            logger.error("Error in RAG query", error=str(e), query=query[:50])
            
            # Episodic Memory: запись события error для RAG
            try:
                episodic_memory = get_episodic_memory_service()
                episodic_memory.record_event(
                    tenant_id=tenant_id,
                    entity_type="rag",
                    event_type="error",
                    metadata={
                        "user_id": str(user_id),
                        "error_type": type(e).__name__,
                        "error_message": str(e)[:500],
                    },
                )
            except Exception as mem_exc:
                logger.warning("episodic_memory.record_error_failed", error=str(mem_exc), tenant_id=tenant_id)
            
            # Context7: Используем безопасную функцию для вычисления времени обработки
            error_result = RAGResult(
                answer="Произошла ошибка при обработке запроса. Попробуйте позже.",
                sources=[],
                confidence=0.0,
                intent="search",
                processing_time_ms=self._safe_get_processing_time_ms(query_start_time),
                llm_calls=0,  # При ошибке LLM не вызывался
                tokens_used=0,
                agent_steps=0
            )
            
            # Context7: Сохранение в историю даже при ошибке (критично для аналитики и отладки)
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
                logger.info(
                    "RAG query saved to history (error case)",
                    user_id=str(user_id),
                    query_id=str(rag_history.id),
                    error=str(e)
                )
            except Exception as save_error:
                logger.error(
                    "Failed to save RAG query to history (error case)",
                    user_id=str(user_id),
                    error=str(save_error),
                    error_type=type(save_error).__name__,
                    exc_info=True
                )
                try:
                    db.rollback()
                except Exception:
                    pass
            
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

