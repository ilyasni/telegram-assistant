"""
Trend Detection Service для анализа трендов
Context7: multi-agent система через LangChain agents для глобального анализа ВСЕХ постов
"""

import time
from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime, date, timedelta, timezone

import structlog
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, text as sa_text
from qdrant_client import QdrantClient
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableParallel
from pydantic import BaseModel

from models.database import Post, PostEnrichment, Channel, TrendDetection
from services.rag_service import RAGService  # Для генерации embedding
from services.graph_service import get_graph_service
from config import settings

logger = structlog.get_logger()

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class TrendCandidate(BaseModel):
    """Кандидат на тренд."""
    keyword: str
    frequency: int
    growth_rate: float
    engagement_score: float
    channels_affected: List[str]
    posts_sample: List[Dict[str, Any]]


class TrendResult(BaseModel):
    """Обнаруженный тренд."""
    trend_id: UUID
    keyword: str
    frequency: int
    growth_rate: float
    engagement_score: float
    channels_affected: List[str]
    posts_sample: List[Dict[str, Any]]
    detected_at: datetime


# ============================================================================
# TREND DETECTION SERVICE
# ============================================================================

class TrendDetectionService:
    """Сервис для обнаружения трендов через multi-agent систему."""
    
    def __init__(
        self,
        qdrant_url: str,
        qdrant_client: Optional[QdrantClient] = None,
        openai_api_base: Optional[str] = None,
        graph_service: Optional[Any] = None
    ):
        """
        Инициализация Trend Detection Service.
        
        Args:
            qdrant_url: URL Qdrant сервиса
            qdrant_client: Qdrant клиент (опционально)
            openai_api_base: URL gpt2giga-proxy
            graph_service: GraphService для работы с Neo4j (опционально)
        """
        self.qdrant_url = qdrant_url
        self.qdrant_client = qdrant_client or QdrantClient(url=qdrant_url)
        
        # Context7: Инициализация GraphService для community detection и анализа влияния
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
            temperature=0.3,  # Низкая температура для более детерминированных результатов
        )
        
        # Context7: Multi-agent система через LangChain RunnableParallel
        # Агенты реализованы как отдельные функции для совместимости с langchain-gigachat
        
        logger.info("Trend Detection Service initialized", qdrant_url=qdrant_url)
    
    async def _analyze_engagement(self, posts_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Анализ engagement метрик через LLM (Engagement Analyzer Agent)."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — аналитик engagement метрик.
Твоя задача: проанализировать engagement метрики постов и определить, какие посты имеют высокий engagement.
Верни краткий анализ: какие посты наиболее популярны и почему."""),
            ("human", "Данные постов: {posts_data}\n\nПроанализируй engagement:")
        ])
        
        chain = prompt | self.llm | StrOutputParser()
        result = await chain.ainvoke({"posts_data": str(posts_data[:10])})
        
        return {"analysis": result}
    
    async def _extract_topics(self, posts_text: str) -> List[str]:
        """Извлечение тем из текста через LLM (Topic Extractor Agent)."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по извлечению тем из текста.
Твоя задача: определить основные темы, упомянутые в постах.
Верни список тем через запятую."""),
            ("human", "Текст постов: {posts_text}\n\nИзвлеки темы:")
        ])
        
        chain = prompt | self.llm | StrOutputParser()
        result = await chain.ainvoke({"posts_text": posts_text[:2000]})
        
        # Парсим темы из ответа
        topics = [t.strip() for t in result.split(',') if t.strip()]
        return topics[:10]  # Ограничиваем количество тем
    
    async def _classify_trend(self, trend_data: Dict[str, Any]) -> Dict[str, Any]:
        """Классификация тренда через LLM (Trend Classifier Agent)."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по определению трендов.
Твоя задача: определить, является ли набор данных трендом.
Учитывай: частоту упоминаний, рост упоминаний, engagement метрики.
Верни краткий ответ: является ли это трендом и почему."""),
            ("human", "Данные для анализа: {trend_data}\n\nКлассифицируй тренд:")
        ])
        
        chain = prompt | self.llm | StrOutputParser()
        result = await chain.ainvoke({"trend_data": str(trend_data)})
        
        return {"classification": result, "is_trend": "тренд" in result.lower() or "trend" in result.lower()}
    
    async def _collect_all_posts(
        self,
        days: int = 7,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """
        Сбор ВСЕХ постов за период для глобального анализа.
        
        Context7: Тренды анализируют ВСЕ посты, не учитывая пользовательские настройки.
        """
        if not db:
            return []
        
        try:
            # Получаем посты за последние N дней
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Context7: Посты удаляются физически из БД, поле deleted отсутствует
            posts = db.query(Post).join(PostEnrichment).filter(
                Post.posted_at >= cutoff_date
            ).order_by(desc(Post.posted_at)).limit(10000).all()  # Ограничение для производительности
            
            posts_data = []
            for post in posts:
                enrichment = db.query(PostEnrichment).filter(
                    PostEnrichment.post_id == post.id
                ).first()
                
                channel = db.query(Channel).filter(Channel.id == post.channel_id).first()
                
                posts_data.append({
                    'post_id': str(post.id),
                    'content': post.content or "",
                    'channel_id': str(post.channel_id),
                    'channel_title': channel.title if channel else "Неизвестный канал",
                    'posted_at': post.posted_at.isoformat() if post.posted_at else None,
                    'views_count': post.views_count or 0,
                    'reactions_count': post.reactions_count or 0,
                    'forwards_count': post.forwards_count or 0,
                    'replies_count': post.replies_count or 0,
                    'engagement_score': float(post.engagement_score) if post.engagement_score else 0.0,
                    'topics': enrichment.topics if enrichment and enrichment.topics else [],
                    'keywords': enrichment.keywords if enrichment and enrichment.keywords else []
                })
            
            # Context7: Дедупликация альбомов - оставляем только первый пост из альбома с наивысшим engagement_score
            try:
                # Получаем grouped_id для всех постов из БД
                post_ids = [UUID(p['post_id']) for p in posts_data]
                if post_ids:
                    posts_with_grouped = db.query(
                        Post.id,
                        Post.grouped_id,
                        Post.engagement_score
                    ).filter(Post.id.in_(post_ids)).all()
                    
                    # Создаем словарь post_id -> (grouped_id, engagement_score)
                    post_grouped_map = {
                        str(post.id): (post.grouped_id, float(post.engagement_score) if post.engagement_score else 0.0)
                        for post in posts_with_grouped if post.grouped_id
                    }
                    
                    # Группируем посты по альбомам
                    album_posts = {}  # grouped_id -> список (post_index, engagement_score)
                    for idx, post_data in enumerate(posts_data):
                        grouped_info = post_grouped_map.get(post_data['post_id'])
                        if grouped_info:
                            grouped_id, engagement = grouped_info
                            if grouped_id not in album_posts:
                                album_posts[grouped_id] = []
                            album_posts[grouped_id].append((idx, engagement))
                    
                    # Для каждого альбома оставляем только пост с наивысшим engagement_score
                    indices_to_remove = set()
                    for grouped_id, posts_list in album_posts.items():
                        if len(posts_list) > 1:
                            # Сортируем по engagement_score и оставляем только первый
                            posts_list.sort(key=lambda x: x[1], reverse=True)
                            # Удаляем все посты кроме первого
                            for idx, _ in posts_list[1:]:
                                indices_to_remove.add(idx)
                    
                    # Удаляем дубликаты альбомов (в обратном порядке, чтобы не сбить индексы)
                    for idx in sorted(indices_to_remove, reverse=True):
                        posts_data.pop(idx)
                    
                    logger.debug(
                        "Album deduplication applied in trends",
                        albums_count=len(album_posts),
                        removed_duplicates=len(indices_to_remove)
                    )
            except Exception as e:
                logger.warning("Error during album deduplication in trends", error=str(e))
                # Продолжаем без дедупликации при ошибке
            
            logger.info("Posts collected for trend analysis", count=len(posts_data), days=days)
            return posts_data
        
        except Exception as e:
            logger.error("Error collecting posts", error=str(e))
            return []
    
    async def detect_trends(
        self,
        days: int = 7,
        min_frequency: int = 10,
        min_growth: float = 0.2,
        min_engagement: float = 5.0,
        db: Optional[Session] = None
    ) -> List[TrendResult]:
        """
        Обнаружение трендов через multi-agent систему.
        
        Context7: Двухстадийная схема:
        1. Стадия A (аналитическая, без LLM): сбор ключевых фраз, подсчет частот, z-score
        2. Стадия B (семантическая, с LLM): кластеризация, формулирование трендов
        
        Args:
            days: Количество дней для анализа
            min_frequency: Минимальная частота упоминаний
            min_growth: Минимальный рост (z-score)
            min_engagement: Минимальный engagement score
            db: SQLAlchemy сессия
        
        Returns:
            Список обнаруженных трендов
        """
        start_time = time.time()
        
        # Стадия A: Сбор постов и аналитика
        logger.info("Stage A: Collecting posts and analyzing...")
        posts = await self._collect_all_posts(days=days, db=db)
        
        if not posts:
            logger.warning("No posts found for trend analysis")
            return []
        
        # Стадия A: Подсчет частот ключевых слов/фраз
        keyword_counts = {}
        keyword_engagement = {}
        keyword_channels = {}
        keyword_posts = {}
        
        for post in posts:
            # Используем keywords из enrichment
            keywords = post.get('keywords', [])
            if not keywords:
                # Fallback: извлекаем простые слова из content
                content = post.get('content', '')
                if content:
                    # Простая токенизация (в реальности использовать NLP)
                    words = content.lower().split()
                    keywords = [w for w in words if len(w) > 4][:10]
            
            for keyword in keywords:
                if keyword not in keyword_counts:
                    keyword_counts[keyword] = 0
                    keyword_engagement[keyword] = 0.0
                    keyword_channels[keyword] = set()
                    keyword_posts[keyword] = []
                
                keyword_counts[keyword] += 1
                keyword_engagement[keyword] += post.get('engagement_score', 0.0)
                keyword_channels[keyword].add(post.get('channel_id', ''))
                
                if len(keyword_posts[keyword]) < 10:  # Сохраняем до 10 примеров
                    keyword_posts[keyword].append({
                        'post_id': post.get('post_id'),
                        'content': post.get('content', '')[:200],
                        'engagement_score': post.get('engagement_score', 0.0)
                    })
        
        # Вычисляем средний engagement
        for keyword in keyword_engagement:
            if keyword_counts[keyword] > 0:
                keyword_engagement[keyword] /= keyword_counts[keyword]
        
        # Фильтруем по порогам
        candidates = []
        for keyword, count in keyword_counts.items():
            if count >= min_frequency:
                avg_engagement = keyword_engagement[keyword]
                if avg_engagement >= min_engagement:
                    # Простой расчет роста (в реальности использовать z-score)
                    growth_rate = 0.5  # Заглушка, нужно сравнивать с предыдущим периодом
                    
                    candidates.append(TrendCandidate(
                        keyword=keyword,
                        frequency=count,
                        growth_rate=growth_rate,
                        engagement_score=avg_engagement,
                        channels_affected=list(keyword_channels[keyword]),
                        posts_sample=keyword_posts[keyword]
                    ))
        
        # Сортируем по engagement и частоте
        candidates.sort(key=lambda x: (x.engagement_score, x.frequency), reverse=True)
        
        # Context7: Использование Neo4j community detection для кластеризации тем
        try:
            if await self.graph_service.health_check():
                # Находим связанные темы через граф для кластеризации
                topic_clusters = {}
                for candidate in candidates[:20]:
                    similar_topics = await self.graph_service.find_similar_topics(candidate.keyword, limit=5)
                    # Создаем кластер на основе похожих тем
                    cluster_key = candidate.keyword
                    if similar_topics:
                        # Группируем похожие темы в один кластер
                        for st in similar_topics:
                            if st.get('similarity', 0) > 0.7:
                                cluster_key = st['topic']  # Используем более популярную тему как ключ кластера
                                break
                    
                    if cluster_key not in topic_clusters:
                        topic_clusters[cluster_key] = []
                    topic_clusters[cluster_key].append(candidate)
                
                # Анализируем влияние каналов на тренды через граф
                for cluster_key, cluster_candidates in topic_clusters.items():
                    # Для каждого кластера находим каналы через граф
                    # (это упрощенная версия, в реальности нужен более сложный Cypher запрос)
                    logger.debug("Analyzing channel influence for cluster", cluster=cluster_key, candidates_count=len(cluster_candidates))
                
                logger.info("Neo4j community detection completed", clusters_count=len(topic_clusters))
        except Exception as e:
            logger.warning("Neo4j community detection failed, continuing without graph", error=str(e))
        
        # Стадия B: Используем multi-agent систему для классификации и формулирования
        logger.info(f"Stage B: Classifying {len(candidates)} candidates...")
        
        trends = []
        for candidate in candidates[:20]:  # Обрабатываем топ-20 кандидатов
            try:
                # Используем Trend Classifier для классификации
                trend_data = {
                    'keyword': candidate.keyword,
                    'frequency': candidate.frequency,
                    'growth_rate': candidate.growth_rate,
                    'engagement_score': candidate.engagement_score,
                    'channels_count': len(candidate.channels_affected),
                    'posts_sample': candidate.posts_sample[:3]
                }
                
                # Классификация через LLM
                classification_result = await self._classify_trend(trend_data)
                
                # Сохраняем в БД
                trend = TrendDetection(
                    trend_keyword=candidate.keyword,
                    frequency_count=candidate.frequency,
                    growth_rate=candidate.growth_rate,
                    engagement_score=candidate.engagement_score,
                    first_mentioned_at=datetime.now(timezone.utc) - timedelta(days=days),
                    last_mentioned_at=datetime.now(timezone.utc),
                    channels_affected=candidate.channels_affected,
                    posts_sample=candidate.posts_sample,
                    status="active"
                )
                
                # Генерируем embedding для тренда
                from services.rag_service import RAGService
                rag_service = RAGService(self.qdrant_url, self.qdrant_client)
                embedding = await rag_service._generate_embedding(candidate.keyword)
                if embedding:
                    # Сохраняем embedding (VectorType автоматически конвертирует list в vector)
                    trend.trend_embedding = embedding
                
                if db:
                    db.add(trend)
                    db.commit()
                    db.refresh(trend)
                
                trends.append(TrendResult(
                    trend_id=trend.id,
                    keyword=trend.trend_keyword,
                    frequency=trend.frequency_count,
                    growth_rate=trend.growth_rate or 0.0,
                    engagement_score=trend.engagement_score or 0.0,
                    channels_affected=trend.channels_affected,
                    posts_sample=trend.posts_sample,
                    detected_at=trend.detected_at
                ))
                
            except Exception as e:
                logger.error("Error processing trend candidate", keyword=candidate.keyword, error=str(e))
                continue
        
        processing_time = int((time.time() - start_time) * 1000)
        logger.info(
            "Trend detection completed",
            trends_count=len(trends),
            candidates_processed=len(candidates),
            processing_time_ms=processing_time
        )
        
        return trends
    
    async def find_similar_trends(
        self,
        trend_id: UUID,
        limit: int = 10,
        threshold: float = 0.7,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """
        Поиск похожих трендов по embedding.
        
        Context7: Использует cosine distance (<=>) для векторного поиска через pgvector.
        
        Args:
            trend_id: ID тренда для поиска похожих
            limit: Максимальное количество результатов
            threshold: Минимальная similarity (0.0-1.0)
            db: SQLAlchemy сессия
        
        Returns:
            Список похожих трендов с similarity scores
        """
        if not db:
            logger.warning("No database session provided for find_similar_trends")
            return []
        
        try:
            # Получаем embedding целевого тренда
            trend = db.query(TrendDetection).filter(TrendDetection.id == trend_id).first()
            if not trend or not trend.trend_embedding:
                logger.warning("Trend not found or has no embedding", trend_id=str(trend_id))
                return []
            
            # Используем прямой SQL для векторного поиска
            # Context7: cosine distance (<=>) для cosine similarity
            query = sa_text("""
                SELECT 
                    id,
                    trend_keyword,
                    frequency_count,
                    growth_rate,
                    engagement_score,
                    channels_affected,
                    posts_sample,
                    detected_at,
                    1 - (trend_embedding <=> :query_embedding::vector(1536)) AS similarity
                FROM trends_detection
                WHERE id != :trend_id 
                  AND status = 'active'
                  AND trend_embedding IS NOT NULL
                  AND 1 - (trend_embedding <=> :query_embedding::vector(1536)) >= :threshold
                ORDER BY trend_embedding <=> :query_embedding::vector(1536)
                LIMIT :limit
            """)
            
            # Конвертируем embedding в строку для vector типа
            embedding_str = '[' + ','.join(str(float(v)) for v in trend.trend_embedding) + ']'
            
            result = db.execute(query, {
                'trend_id': trend_id,
                'query_embedding': embedding_str,
                'threshold': threshold,
                'limit': limit
            })
            
            similar_trends = []
            for row in result:
                similar_trends.append({
                    'id': str(row.id),
                    'trend_keyword': row.trend_keyword,
                    'frequency_count': row.frequency_count,
                    'growth_rate': float(row.growth_rate) if row.growth_rate else None,
                    'engagement_score': float(row.engagement_score) if row.engagement_score else None,
                    'channels_affected': row.channels_affected,
                    'posts_sample': row.posts_sample,
                    'detected_at': row.detected_at.isoformat() if row.detected_at else None,
                    'similarity': float(row.similarity)
                })
            
            logger.info(
                "Similar trends found",
                trend_id=str(trend_id),
                count=len(similar_trends),
                threshold=threshold
            )
            
            return similar_trends
        
        except Exception as e:
            logger.error("Error finding similar trends", error=str(e), trend_id=str(trend_id))
            return []
    
    async def deduplicate_trends(
        self,
        threshold: float = 0.85,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """
        Дедупликация трендов по смыслу (cosine similarity).
        
        Context7: Находит группы похожих трендов и помечает дубликаты как archived.
        
        Args:
            threshold: Минимальная similarity для дубликатов (0.0-1.0)
            db: SQLAlchemy сессия
        
        Returns:
            Словарь с результатами дедупликации
        """
        if not db:
            logger.warning("No database session provided for deduplicate_trends")
            return {'duplicates_found': 0, 'trends_archived': 0}
        
        try:
            # Используем SQL для поиска дубликатов
            # Context7: CROSS JOIN для сравнения всех пар трендов
            query = sa_text("""
                WITH ranked AS (
                    SELECT 
                        id, 
                        trend_keyword, 
                        trend_embedding,
                        detected_at,
                        ROW_NUMBER() OVER (ORDER BY detected_at DESC) AS rn
                    FROM trends_detection
                    WHERE status = 'active' 
                      AND trend_embedding IS NOT NULL
                )
                SELECT 
                    r1.id AS id1,
                    r1.trend_keyword AS keyword1,
                    r2.id AS id2,
                    r2.trend_keyword AS keyword2,
                    1 - (r1.trend_embedding <=> r2.trend_embedding) AS similarity
                FROM ranked r1
                CROSS JOIN ranked r2
                WHERE r1.rn < r2.rn
                  AND r1.trend_embedding IS NOT NULL
                  AND r2.trend_embedding IS NOT NULL
                  AND 1 - (r1.trend_embedding <=> r2.trend_embedding) > :threshold
                ORDER BY similarity DESC
            """)
            
            result = db.execute(query, {'threshold': threshold})
            
            duplicates = []
            archived_ids = set()
            
            for row in result:
                # Архивируем более старый тренд (id2)
                if row.id2 not in archived_ids:
                    duplicates.append({
                        'keep_id': str(row.id1),
                        'keep_keyword': row.keyword1,
                        'archive_id': str(row.id2),
                        'archive_keyword': row.keyword2,
                        'similarity': float(row.similarity)
                    })
                    archived_ids.add(row.id2)
            
            # Архивируем дубликаты
            if archived_ids:
                db.query(TrendDetection).filter(
                    TrendDetection.id.in_(archived_ids)
                ).update({'status': 'archived'}, synchronize_session=False)
                db.commit()
            
            logger.info(
                "Trend deduplication completed",
                duplicates_found=len(duplicates),
                trends_archived=len(archived_ids),
                threshold=threshold
            )
            
            return {
                'duplicates_found': len(duplicates),
                'trends_archived': len(archived_ids),
                'duplicates': duplicates
            }
        
        except Exception as e:
            logger.error("Error deduplicating trends", error=str(e))
            db.rollback()
            return {'duplicates_found': 0, 'trends_archived': 0, 'error': str(e)}
    
    async def group_related_trends(
        self,
        trend_ids: List[UUID],
        similarity_threshold: float = 0.6,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """
        Группировка связанных трендов через векторный поиск и Neo4j.
        
        Context7: Использует векторный поиск для поиска похожих трендов и Neo4j для анализа связей.
        
        Args:
            trend_ids: Список ID трендов для группировки
            similarity_threshold: Минимальная similarity для включения в группу
            db: SQLAlchemy сессия
        
        Returns:
            Список групп связанных трендов
        """
        if not db or not trend_ids:
            return []
        
        try:
            # Получаем тренды
            trends = db.query(TrendDetection).filter(
                TrendDetection.id.in_(trend_ids),
                TrendDetection.status == 'active'
            ).all()
            
            if not trends:
                return []
            
            groups = []
            processed_ids = set()
            
            for trend in trends:
                if trend.id in processed_ids or not trend.trend_embedding:
                    continue
                
                # Находим похожие тренды
                similar = await self.find_similar_trends(
                    trend.id,
                    limit=20,
                    threshold=similarity_threshold,
                    db=db
                )
                
                # Фильтруем только из исходного списка
                group_ids = [trend.id]
                for sim in similar:
                    sim_id = UUID(sim['id'])
                    if sim_id in trend_ids and sim_id not in processed_ids:
                        group_ids.append(sim_id)
                        processed_ids.add(sim_id)
                
                if len(group_ids) > 1:
                    # Используем Neo4j для дополнительного анализа связей
                    try:
                        if await self.graph_service.health_check():
                            # Находим связанные темы через граф
                            related_topics = await self.graph_service.find_similar_topics(
                                trend.trend_keyword,
                                limit=5
                            )
                            
                            groups.append({
                                'trend_ids': [str(tid) for tid in group_ids],
                                'keywords': [t.trend_keyword for t in trends if t.id in group_ids],
                                'similarity_scores': [sim['similarity'] for sim in similar if UUID(sim['id']) in group_ids],
                                'related_topics': [rt['topic'] for rt in related_topics] if related_topics else []
                            })
                        else:
                            groups.append({
                                'trend_ids': [str(tid) for tid in group_ids],
                                'keywords': [t.trend_keyword for t in trends if t.id in group_ids],
                                'similarity_scores': [sim['similarity'] for sim in similar if UUID(sim['id']) in group_ids]
                            })
                    except Exception as e:
                        logger.warning("Neo4j analysis failed, using vector search only", error=str(e))
                        groups.append({
                            'trend_ids': [str(tid) for tid in group_ids],
                            'keywords': [t.trend_keyword for t in trends if t.id in group_ids],
                            'similarity_scores': [sim['similarity'] for sim in similar if UUID(sim['id']) in group_ids]
                        })
                
                processed_ids.add(trend.id)
            
            logger.info(
                "Related trends grouped",
                input_count=len(trend_ids),
                groups_count=len(groups)
            )
            
            return groups
        
        except Exception as e:
            logger.error("Error grouping related trends", error=str(e))
            return []
    
    async def cluster_trends(
        self,
        n_clusters: int = 10,
        min_similarity: float = 0.6,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """
        Кластеризация трендов по embedding.
        
        Context7: Использует K-means на векторах или иерархическую кластеризацию через векторный поиск.
        
        Args:
            n_clusters: Желаемое количество кластеров
            min_similarity: Минимальная similarity для включения в кластер
            db: SQLAlchemy сессия
        
        Returns:
            Список кластеров с представительными трендами
        """
        if not db:
            return []
        
        try:
            # Получаем все активные тренды с embedding
            trends = db.query(TrendDetection).filter(
                TrendDetection.status == 'active',
                TrendDetection.trend_embedding.isnot(None)
            ).order_by(TrendDetection.detected_at.desc()).limit(1000).all()  # Ограничение для производительности
            
            if not trends:
                return []
            
            # Используем упрощенную кластеризацию через векторный поиск
            # Для каждого тренда находим похожие и формируем кластеры
            clusters = []
            processed_ids = set()
            
            for trend in trends:
                if trend.id in processed_ids:
                    continue
                
                # Находим похожие тренды для формирования кластера
                similar = await self.find_similar_trends(
                    trend.id,
                    limit=50,
                    threshold=min_similarity,
                    db=db
                )
                
                cluster_ids = [trend.id]
                cluster_keywords = [trend.trend_keyword]
                
                for sim in similar:
                    sim_id = UUID(sim['id'])
                    if sim_id not in processed_ids:
                        cluster_ids.append(sim_id)
                        cluster_keywords.append(sim['trend_keyword'])
                        processed_ids.add(sim_id)
                
                if len(cluster_ids) > 1:
                    # Выбираем представительный тренд (самый популярный)
                    cluster_trends = [t for t in trends if t.id in cluster_ids]
                    representative = max(cluster_trends, key=lambda t: t.frequency_count or 0)
                    
                    clusters.append({
                        'cluster_id': len(clusters) + 1,
                        'representative_trend_id': str(representative.id),
                        'representative_keyword': representative.trend_keyword,
                        'trend_count': len(cluster_ids),
                        'trend_ids': [str(tid) for tid in cluster_ids],
                        'keywords': cluster_keywords,
                        'avg_similarity': sum([sim['similarity'] for sim in similar if UUID(sim['id']) in cluster_ids]) / len(cluster_ids) if cluster_ids else 0.0
                    })
                
                processed_ids.add(trend.id)
                
                # Ограничиваем количество кластеров
                if len(clusters) >= n_clusters:
                    break
            
            logger.info(
                "Trend clustering completed",
                clusters_count=len(clusters),
                trends_clustered=len(processed_ids)
            )
            
            return clusters
        
        except Exception as e:
            logger.error("Error clustering trends", error=str(e))
            return []


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_trend_detection_service: Optional[TrendDetectionService] = None


def get_trend_detection_service(
    qdrant_url: Optional[str] = None
) -> TrendDetectionService:
    """Получение singleton экземпляра TrendDetectionService."""
    global _trend_detection_service
    if _trend_detection_service is None:
        qdrant_url = qdrant_url or getattr(settings, 'qdrant_url', 'http://qdrant:6333')
        _trend_detection_service = TrendDetectionService(qdrant_url=qdrant_url)
    return _trend_detection_service

