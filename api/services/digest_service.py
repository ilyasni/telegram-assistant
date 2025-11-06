"""
Digest Service для генерации дайджестов новостей
Context7: сбор контента ТОЛЬКО по пользовательским тематикам из digest_settings.topics
"""

import time
from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime, date, timezone

import structlog
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableParallel
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel

from models.database import Post, PostEnrichment, Channel, User, DigestSettings, DigestHistory, UserChannel
from services.rag_service import RAGService  # Для генерации embedding
from services.graph_service import get_graph_service
from config import settings

logger = structlog.get_logger()

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
        graph_service: Optional[Any] = None
    ):
        """
        Инициализация Digest Service.
        
        Args:
            qdrant_url: URL Qdrant сервиса
            qdrant_client: Qdrant клиент (опционально)
            openai_api_base: URL gpt2giga-proxy
            graph_service: GraphService для работы с Neo4j (опционально)
        """
        self.qdrant_url = qdrant_url
        self.qdrant_client = qdrant_client or QdrantClient(url=qdrant_url)
        
        # Context7: Инициализация GraphService для поиска связанных тем
        self.graph_service = graph_service or get_graph_service()
        
        # Инициализация GigaChat LLM через langchain-gigachat
        # Context7: Исправлен URL (без /v1) для обработки редиректов прокси
        api_base = openai_api_base or settings.openai_api_base or "http://gpt2giga-proxy:8090"
        
        import os
        os.environ.setdefault("OPENAI_API_BASE", api_base)
        
        self.llm = GigaChat(
            credentials=getattr(settings, 'gigachat_credentials', '') or os.getenv('GIGACHAT_CREDENTIALS', ''),
            scope=getattr(settings, 'gigachat_scope', None) or os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS'),
            model="GigaChat",
            base_url=api_base,
            temperature=0.7,
        )
        
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
   - **Ссылка**: [Канал](ссылка) на оригинальный пост

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
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Канал](ссылка)

**Заголовок новости 2**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Канал](ссылка)

**Заголовок новости 3**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Канал](ссылка)


## Тема 2: [Название темы]

**Заголовок новости 1**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Канал](ссылка)

**Заголовок новости 2**
Суть новости: [1-2 предложения с ключевой информацией] [Популярность: X%] [Канал](ссылка)

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
                    if collection_name not in [c.name for c in collections.collections]:
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
                    
                    if channel_ids:
                        filter_conditions.append(
                            FieldCondition(
                                key="channel_id",
                                match=MatchAny(any=[str(cid) for cid in channel_ids])
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
                                channel = db.query(Channel).filter(Channel.id == post.channel_id).first()
                                
                                all_posts.append({
                                    'post_id': str(post_id),
                                    'content': post.content or "",
                                    'channel_title': channel.title if channel else "Неизвестный канал",
                                    'channel_username': channel.username if channel else None,
                                    'permalink': post.telegram_post_url,
                                    'posted_at': post.posted_at,
                                    'topic': topic,
                                    'score': result.score,
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
                        similar_topics = await self.graph_service.find_similar_topics(topic, limit=3)
                        
                        # Расширяем поиск по связанным темам
                        related_topics = [topic] + [st['topic'] for st in similar_topics if st.get('similarity', 0) > 0.6]
                        
                        # Поиск постов через граф для каждой связанной темы
                        for related_topic in related_topics:
                            graph_posts = await self.graph_service.search_related_posts(
                                query=related_topic,
                                topic=related_topic,
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
                                            if channel_ids and str(post.channel_id) not in channel_ids:
                                                continue
                                            
                                            channel = db.query(Channel).filter(Channel.id == post.channel_id).first()
                                            
                                            all_posts.append({
                                                'post_id': str(post_id),
                                                'content': graph_post.get('content', post.content or ""),
                                                'channel_title': channel.title if channel else "Неизвестный канал",
                                                'channel_username': channel.username if channel else None,
                                                'permalink': post.telegram_post_url,
                                                'posted_at': post.posted_at,
                                                'topic': related_topic,
                                                'score': graph_post.get('score', 0.7),
                                                'related_topic': related_topic != topic,  # Флаг связанной темы
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
                
                if channel_ids:
                    fts_query = fts_query.filter(Post.channel_id.in_([UUID(cid) for cid in channel_ids]))
                
                fts_posts = fts_query.order_by(Post.posted_at.desc()).limit(limit_per_topic).all()
                
                for post in fts_posts:
                    # Проверяем, не добавлен ли уже
                    if not any(p['post_id'] == str(post.id) for p in all_posts):
                        channel = db.query(Channel).filter(Channel.id == post.channel_id).first()
                        
                        all_posts.append({
                            'post_id': str(post.id),
                            'content': post.content or "",
                            'channel_title': channel.title if channel else "Неизвестный канал",
                            'channel_username': channel.username if channel else None,
                            'permalink': post.telegram_post_url,
                            'posted_at': post.posted_at,
                            'topic': topic,
                            'score': 0.5,  # Средний score для FTS результатов
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
        
        # Сортируем по времени и релевантности
        all_posts.sort(key=lambda x: (x['posted_at'] or datetime.min, x['score']), reverse=True)
        
        # Дедупликация по post_id
        seen = set()
        unique_posts = []
        for post in all_posts:
            if post['post_id'] not in seen:
                seen.add(post['post_id'])
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
            content = post.get('content', '')
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
        start_time = time.time()
        
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
        
        if not posts:
            logger.warning("No posts found for digest", user_id=str(user_id), topics=topics)
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
            
            # Парсим секции из markdown (простой парсинг)
            sections = self._parse_sections(content, topics)
            
            logger.info(
                "Digest generated",
                user_id=str(user_id),
                posts_count=len(posts),
                topics=topics,
                processing_time_ms=int((time.time() - start_time) * 1000)
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
# SINGLETON INSTANCE
# ============================================================================

_digest_service: Optional[DigestService] = None


def get_digest_service(
    qdrant_url: Optional[str] = None
) -> DigestService:
    """Получение singleton экземпляра DigestService."""
    global _digest_service
    if _digest_service is None:
        qdrant_url = qdrant_url or getattr(settings, 'qdrant_url', 'http://qdrant:6333')
        _digest_service = DigestService(qdrant_url=qdrant_url)
    return _digest_service

